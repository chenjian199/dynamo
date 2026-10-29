<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 本轮 Helm、Profiler、DRA 与 SLA 的关系

## Helm 在哪里使用

Helm 是 Kubernetes 应用的安装、配置与升级工具；Chart 是安装模板包，release 是一次受管理的安装。它不承载推理流量，也不替代容器运行时。`ctr images import` 导入镜像，Helm 使用 Chart 创建 Deployment/RBAC/Service 等对象；DGD/DGDR 再由 Operator 调谐。

本轮管理节点：

| 平台 | 入口 | 工作目录 | Helm |
| --- | --- | --- | --- |
| CUDA | 192.168.4.6 | `/home/bedicloud/CJ_workspace/m08-1029` | `helm` |
| Ascend | 52 / 192.168.7.7 | `/root/CJ_workspace/m08-1029` | `./linux-arm64/helm`，`KUBECONFIG=/etc/rancher/k3s/k3s.yaml` |

初始安装的真实脚本是 [cluster/install.sh](cluster/install.sh)，核心命令如下（在已核验离线包的工作目录执行）：

```bash
# CUDA
helm upgrade --install heyi-m08 \
  dynamo-m08-operator-bundle/dynamo-operator-1.5.1.tgz \
  --namespace dynamo-m08-1029 --create-namespace \
  -f dynamo-m08-operator-bundle/operator-values-amd64.yaml \
  -f cuda-operator-overlay.json --wait --timeout 10m

# Ascend
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
./linux-arm64/helm upgrade --install heyi-m08 \
  dynamo-m08-operator-bundle/dynamo-operator-1.5.1.tgz \
  --namespace dynamo-m08-1029 --create-namespace \
  -f dynamo-m08-operator-bundle/operator-values-arm64.yaml \
  -f ascend-operator-overlay.json --wait --timeout 10m
```

以上是初装记录，不要为查看安装情况而重跑旧 overlay，避免覆盖后续升级配置。当前 release 已升级到加入 DGDR SKU 支持的 Operator 标签。

扩展测试另外安装了 `heyi-grove`（`grove-system`）和 `heyi-kai`（`kai-scheduler`），固定 Chart 版本 `v0.1.0-alpha.13` / `v0.17.0`。以下命令表示最终配置的复现方式；先准备 Chart、离线镜像、对应 CRD 与测试 namespace 标签，具体 values 见 [advanced/manifests](advanced/manifests)：

```bash
helm upgrade --install heyi-kai kai-scheduler-v0.17.0.tgz \
  -n kai-scheduler --create-namespace -f kai-values.json --wait --timeout 5m
helm upgrade --install heyi-grove grove-charts-v0.1.0-alpha.13.tgz \
  -n grove-system --create-namespace -f grove-kai-values.json --wait --timeout 5m
helm upgrade heyi-m08 dynamo-m08-operator-bundle/dynamo-operator-1.5.1.tgz \
  -n dynamo-m08-1029 --reuse-values \
  --set global.grove.enabled=true --set global.kai-scheduler.enabled=true \
  --wait --timeout 5m
```

Ascend 替换为 `./linux-arm64/helm`。本轮 Grove 先用 default-scheduler 初装，再升级启用 KAI；失败尝试与修正保留在 `advanced/results/<platform>/`。KAI 仅处理带 `heyi.io/kai-test=true` 标签的 namespace。

NVIDIA DRA 为临时测试安装，完整隔离、安装、回收步骤在 [advanced/dra-cuda.sh](advanced/dra-cuda.sh)。测试后已卸载该 driver 并恢复原 Device Plugin；不能直接复制安装命令让两套分配器同时分配同一张卡。

无需变更集群即可查看实际 release：

```bash
helm list -A
helm history heyi-m08 -n dynamo-m08-1029
helm get values heyi-m08 -n dynamo-m08-1029
helm get manifest heyi-m08 -n dynamo-m08-1029
helm get values heyi-grove -n grove-system
helm get values heyi-kai -n kai-scheduler
```

查看本轮模型声明：

```bash
# 使用 kubectl 提交，不是 helm install 一个模型。
kubectl apply --dry-run=server -f pd-cross.json
kubectl apply -f pd-cross.json
kubectl apply -f dgdr.json
```

本轮 Dynamo 使用 Kubernetes 服务发现、TCP 请求平面及 ZMQ 事件平面，没有额外安装 Dynamo 使用的 etcd/NATS。K3s 自身的 etcd 属于 Kubernetes 控制平面，与 Dynamo 的发现后端不是同一个概念。

## Profiler 镜像是什么

它提供 `python -m dynamo.profiler` 及 AIPerf、配置搜索、Kubernetes 客户端等依赖。当前测试镜像：

- CUDA：`heyi/dynamo-profiler-cuda:1.5.1-m08-advanced3`。
- Ascend：`heyi/dynamo-profiler-ascend:1.5.1-m08-advanced`。

测试配方基于 M02 runtime 增加工具，参见 [构建步骤](advanced/reproduce.md#独立-profiler-镜像)。Profiler 负责调度测试和分析，真实推理由独立 Worker Pod 完成；Ascend 的 Profiler 本身使用 CPU。工具镜像没有替换 Worker 的 vLLM：CUDA 仍是 0.28.0，Ascend 仍是 0.23.0。

```text
DGDR：模型 + 工作负载 + 硬件 + SLA 目标
  → Operator 创建 Profiler Job
  → 生成候选 DGD，启动 Worker，使用 AIPerf 实测（thorough）
  → 选择配置，回传最终 DGD
  → autoApply=true 时 Operator 自动创建最终 DGD
```

DGDR 是“根据需求生成部署”的请求，因此需要 Profiler；直接提供完整 DGD 时不需要这个分析步骤。`rapid` 使用离线估计，`thorough` 进行在线测量。本轮完成的是实际 thorough 流程，不是只创建了 Job。

## SLA 是否就是 Profiler 的功能

SLA 是服务约定；这里的 `sla.ttft`（首 token 延迟）、`sla.itl`（相邻 token 延迟）是配置选择的目标输入。Profiler 测量并生成建议/性能数据，Planner 可据此结合运行时指标调整容量。完整 SLA 达标还需要负载测试、容量冗余、监控及故障验证。

本轮 DGDR 功能验收不等于生产 SLA 已认证，也不等于 M09 Planner 已认证。H800/910B3 离线性能数据库精度没有在本轮得到认证。

## DRA 缺失会影响什么

DRA（Dynamic Resource Allocation）是 Kubernetes 的动态资源分配机制，以 DeviceClass、ResourceClaim、ResourceClaimTemplate、ResourceSlice 和厂商驱动表达设备选择、分配与回收。

现有 Device Plugin 的 `limits: nvidia.com/gpu` / `huawei.com/Ascend910` 仍能分配整卡，正常运行推理、TP/DP、PD、KV 路由以及通过相应网卡资源提供的 RDMA。没有 DRA，缺少的是基于 Claim 的设备选择/生命周期以及明确依赖 DRA 的部署方式；不代表 GPU/NPU 推理不能工作。共享、MIG、特殊拓扑等能力是否可用仍取决于具体驱动，不能由“安装了 DRA”一概推断。

本轮 CUDA 只认证了 DRA 整卡路径。Ascend DRA 等待用户提供 ≥1.34 的独立测试集群，HAMi 实验驱动的接受不等于已经完成认证。

## 官方参考

- [Helm upgrade](https://helm.sh/docs/helm/helm_upgrade/)
- [NVIDIA Dynamo Profiler](https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/profiler/overview)：概念参考；本仓库的具体配置以 1.5.1 代码与本轮清单为准，不照抄 dev 文档镜像版本。
- [Kubernetes DRA 工作流程](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/how-dra-works/)
- [Kubernetes DRA API](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/dra-api/)
