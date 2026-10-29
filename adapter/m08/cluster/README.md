<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M08 CUDA / Ascend 集群验收

状态：2026-10-29 CUDA / Ascend 的 M08 基础 Device Plugin 路径集群验收完成。原始证据与机器可读汇总见 [results/summary.json](results/summary.json)，可用 `python3 adapter/m08/cluster/summarize.py` 重新检查。

## 安装位置与边界

| 平台 | 管理入口 | 工作目录 | Operator 固定节点 |
| --- | --- | --- | --- |
| CUDA | 4.6 / `worker06` | `/home/bedicloud/CJ_workspace/m08-1029` | `worker06`，amd64 |
| Ascend | 52 / 192.168.7.7 | `/root/CJ_workspace/m08-1029` | `master-02-45d4a763`，arm64 |

两边使用 namespace `dynamo-m08-1029`、Helm release `heyi-m08`。Operator 使用 10 月 28 日交付的 `1.5.1-m08-amd64/arm64`；已核对构建快照中的 595 个文件，当前差异仅为两处 Go 测试的 `adapt` → `adapter` 路径，不涉及镜像内控制器实现。

现场清单已留存于 [CUDA manifests](manifests/cuda/) 和 [Ascend manifests](manifests/ascend/)，包括聚合、PD、跨节点聚合和 Operator 节点 overlay。推理清单由本目录 renderer 生成；下次换集群时应重新生成节点与路径，不照搬本次地址。

DGD 使用 `component` 工作负载路径、Kubernetes 服务发现和 TCP/ZMQ，不依赖独立 etcd、NATS、Grove。CRD 由 Operator init container 安装，webhook 证书由控制器管理。本轮不验证 Grove gang scheduling、DGDR 自动画像、厂商 DRA/vNPU/共享卡、GMS 或 M09 Planner。

CUDA vLLM 固定为 0.28.0；Ascend 为 0.23.0（包元数据 `0.23.0+empty`）。沿用用户确认的 M02 runtime 镜像，不在 Pod 内安装软件或覆盖生产源码。Kubernetes 分别为 1.35.6 / K3s 1.32.2，设备资源为 `nvidia.com/gpu` / `huawei.com/Ascend910`。

Qwen3-0.6B 用只读 hostPath：CUDA 两节点均为 `/ssd/models/dynamo-regression/Qwen3-0.6B`；Ascend 7.7 为 `/data/weight/Qwen3-0.6B`，7.5 为 `/data/dynamo-regression-models/Qwen3-0.6B`，容器内统一为 `/models/Qwen3-0.6B`。不创建模型 PVC，不改宿主机驱动或设备插件。大模型/跨节点引擎回归沿用 [M01–M07 集群记录](../../m02/cluster-regression-plan.md)，本轮小模型用于隔离 Operator 行为，不是性能认证。

## 验收结果

| 项目 | CUDA H800 | Ascend 910B3 |
| --- | --- | --- |
| 原生架构 Operator、CRD、webhook、DGD reconcile | 通过，amd64 | 通过，arm64 |
| 整卡资源计数、Frontend 零卡 | 通过 | 通过 |
| Kubernetes WorkerMetadata 注册、发现与清理 | 通过 | 通过 |
| 聚合推理、真实 router KV 命中 | 通过 | 通过 |
| 手动扩缩容 2→3→2→0，零副本保留每副本设备数 1 | 通过 | 通过 |
| Worker 删除重建、重新注册与 API 恢复 | 通过 | 通过 |
| 同节点独立 Pod 2P3D，全部 2×3 实际传输组合 token 一致 | 通过，NIXL | 通过，Mooncake |
| Decode 删除重建与 PD 请求恢复 | 通过 | 通过 |
| 两节点聚合推理 / KV 路由 | 通过，worker06 + worker28 | 通过，7.7 + 7.5 |
| 非流式、SSE 一致性、取消与后续恢复 | 每平台 7 组 API 验收，28 次取消 | 每平台 7 组 API 验收，28 次取消 |
| 六类非法配置 server dry-run 拒绝 | 6/6 | 6/6 |

各平台 `aggregate/summary.json` 是修复状态等待逻辑后完整重跑的通过记录；`aggregate-first-run/summary.json` 保留原始失败结果，不能单独当作通过证据。`aggregate-zero-recheck.json` 是首轮的独立补查。`pd/pairs.json` 包含六种真实交接组合，不以随机路由请求推断覆盖全部组合。`cross/summary.json` 记录实际两节点、路由命中和流式/取消结果。

测试 DGD、Pod 和 WorkerMetadata 已回收，Operator 保留在原 namespace 内就绪运行，见各平台 `cleanup.json`。没有留下模型服务占卡。MetaX Kubernetes、跨节点 Operator PD、设备直连 RDMA、Grove、DGDR、厂商 DRA/共享设备、GMS/failover 和 M09 Planner 不由本轮结果认证。

## 工具与手动复现

- [install.sh](install.sh)：验证离线包、导入指定架构镜像、安装 Helm release。执行前检查现有 Operator，不能并行安装第二个全局控制器。
- [render.py](render.py)：复用 [M08 通用 renderer](../render.py)，应用现场 hostPath、节点和资源预算，生成聚合/同节点 2P3D 清单。
- [regress.py](regress.py)：等待 DGD 更新收敛，检查设备计数，运行真实 API/流式/KV/取消恢复；聚合模式执行 2→3→2→0，删除 Worker 验证重建。
- [reject.py](reject.py)：先验证有效清单，再通过 server dry-run 检查六种无效配置；不创建这些负例资源。
- [pairs.py](pairs.py)：通过 Kubernetes 发现两 P 三 D，指定每个实际生产者/消费者组合并检查 token 一致性。
- [cross.py](cross.py)：检查 `m08-cross` 的两节点放置、同一 runtime namespace 和真实 API/KV 请求。
- [collect.py](collect.py)、[cleanup.py](cleanup.py)：收集资源/日志与 SHA256；只删除本次 DGD，检查发现记录清理并保留 Operator。
- [summarize.py](summarize.py)：从原始文件验证完整回归结果并生成汇总与证据哈希。

安装命令（工作目录内已具备离线包、修订源码包及各自 SHA256、平台 overlay；华为另有 Helm 3.17.3 arm64 包及官方校验和）：

```bash
# 4.6
bash install.sh cuda /home/bedicloud/CJ_workspace/m08-1029
# 52：脚本为 Helm 设置 K3s kubeconfig，Helm 保留在工作目录中。
bash install.sh ascend /root/CJ_workspace/m08-1029
```

源码包 `m08-source.tar.gz` 解压到工作目录的 `source/`，包含 Operator、独立 Chart、平台镜像配置与 M08/M02 回归工具及本轮证据，不包含模型和完整开发工具链。`SOURCE.json` 记录基准提交和工作树文件哈希；本轮未创建 Git 提交。最初的源码包保留为 `m08-source-base-858a564c39.tar.gz`，169 MB 的历史 Operator 离线包保持原校验和。现场 `aggregate.json`、`pd.json`、`cross.json` 和修订源码 renderer 才是本轮已验证版本；不要直接复用历史离线包中未修订的示例清单。

以下在仓库或已同步源码根目录生成清单，然后把清单与回归工具复制到对应工作目录执行。Ascend 替换平台、节点、模型 hostPath：

```bash
python3 adapter/m08/cluster/render.py --platform cuda --node worker06 \
  --model-host-path /ssd/models/dynamo-regression/Qwen3-0.6B > aggregate.json
kubectl apply --dry-run=server -f aggregate.json
kubectl apply -f aggregate.json
python3 adapter/m08/cluster/regress.py --name m08-aggregate \
  --client adapter/m02/cluster/client.py --output evidence/aggregate
python3 adapter/m08/cluster/reject.py --manifest aggregate.json \
  --output evidence/admission.json
```

完成聚合回归并删除其 DGD，等待 Pod 与 WorkerMetadata 回收，再生成 `--mode pd --name m08-pd` 清单并以 `regress.py --mode pd --name m08-pd` 验证。2P3D 需要同节点五个空闲整卡资源单位。脚本不会调整 Device Plugin 或通过显式宿主机卡号越过资源分配。

在 PD 服务仍运行时覆盖全部六种交接，再清理：

```bash
python3 adapter/m08/cluster/pairs.py --client adapter/m02/cluster/topology.py \
  --output evidence/pd
python3 adapter/m08/cluster/collect.py --output evidence/pd-final
python3 adapter/m08/cluster/cleanup.py --names m08-pd \
  --output evidence/cleanup.json
```

跨节点聚合清单（CUDA）；华为使用上表两个节点及各自模型路径：

```bash
python3 adapter/m08/cluster/render.py --platform cuda --name m08-cross \
  --node worker06 --model-host-path /ssd/models/dynamo-regression/Qwen3-0.6B \
  --second-node worker28 \
  --second-model-host-path /ssd/models/dynamo-regression/Qwen3-0.6B > cross.json
kubectl apply -f cross.json
python3 adapter/m08/cluster/cross.py --client adapter/m02/cluster/client.py \
  --output evidence/cross
python3 adapter/m08/cluster/cleanup.py --names m08-cross \
  --output evidence/cleanup.json
```

`cleanup.py` 面向独立测试 namespace，会等待全部 WorkerMetadata 清理；多个测试 DGD 同时存在时，应一起收集后一起清理。不要用它等待仍有意运行的另一组测试服务的记录消失。

## 本轮定位的问题

1. 原通用清单配置了 KV publisher，但没有 `enable_kv_cache_events=true`。已显式启用。
2. 原 endpoint `tcp://127.0.0.1:20080` 被当前 vLLM 的 ZMQ publisher 解释为 connect；Dynamo 订阅端也 connect，导致没有监听端口。原始 ZMQ 订阅无事件，路由器 Stored 计数为零，虽然引擎本身已有 cached tokens。修正为 `tcp://*:20080` 后两平台真实 router KV 命中通过，不能以引擎缓存命中替代路由验收。
3. K3s 的 `kubectl` 自动使用集群配置，但独立 Helm 不会；安装脚本在 Ascend 设置 `KUBECONFIG=/etc/rancher/k3s/k3s.yaml`，不修改该文件。
4. Operator 滚动更新期间新旧 Worker 并存，运行时 namespace 带 revision 后缀。回归工具等待切换完成，读取 `status.components` 中当前 namespace，而非把 Frontend 的基础 namespace 当作 Worker namespace。
5. Decode 默认健康请求没有远端 handoff，被严格 PD 契约拒绝。清单通过 `DYN_HEALTH_CHECK_PAYLOAD` 配置单 token 本地前向与 `x-bypass-remote-prefill`；不关闭健康检查，也不伪造 KV 交接。普通 PD 请求仍走真实远端传输，六种交接及重建恢复已验证。
6. 缩零时 Pod 删除早于 DGD 状态计数更新。回归脚本现在同时等待 observedGeneration、滚动更新结束、各 component 的 replicas/readyReplicas 及实际 Pod 数一致。两平台完整重跑，未删除首轮失败记录。

以上是清单/工具问题；没有为通过测试降低引擎版本、关闭 KV 命中断言或修改厂商引擎。
