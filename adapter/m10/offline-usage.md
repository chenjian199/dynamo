<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M10 离线包使用步骤

本说明随平台包交付。以包内 `validation.json` 的实际结果和限制为准；存在 `IN_PROGRESS` 文件时，表示尚未完成封包。模型权重、宿主机驱动、厂商 Device Plugin 和 Kubernetes 不包含在包内。

## 校验和导入

将对应平台的包复制到所有参与运行的节点。CUDA、MetaX 使用 amd64，Ascend 使用 arm64；不要混用平台包。先校验外层校验文件，再解压：

```bash
sha256sum -c <平台包>.tar.sha256
tar -xf <平台包>.tar
cd <平台目录>
export PATH="$PWD/bin:$PATH"
bash import.sh containerd
# K3s 节点改用：bash import.sh k3s
# Docker 主机改用：bash import.sh docker
```

脚本首先检查 `SHA256SUMS`，然后导入 runtime、dev、tools、Operator 和随包的 Grove/KAI 镜像，不会自动部署服务。镜像中的共享层会被运行时去重。`image-archive-verification.json` 记录封包时逐个 OCI blob 的完整性校验，交付包不是依赖旧节点缓存的增量镜像包。

后续测试驱动调用 `kubectl` 时使用上面加入 PATH 的随包版本，不替换宿主机全局二进制。当前 CUDA 包对应 Kubernetes 1.35，Ascend 包对应 Kubernetes 1.32；迁移到其他版本集群时需核对客户端版本。

模型仍使用节点 `hostPath`。同一个模型在不同节点的宿主路径可以不同，但模型配置、权重和 tokenizer 必须一致。只校验 `config.json` 不能证明全部权重相同，正式迁移应对权重清单逐一校验。

## 安装 Operator

先用 `bin/helm list -A` 查看是否已有 Dynamo Operator。已有时沿用原 release 和命名空间进行升级，避免安装两个管理同一资源的控制器。新集群可执行：

```bash
bin/helm upgrade --install heyi-dynamo charts/dynamo-operator-1.5.1.tgz \
  --namespace dynamo-system --create-namespace \
  -f deployment/operator-values.yaml --wait --timeout 10m
```

`operator-values.yaml` 已锁定当前包的镜像和架构，使用 Kubernetes 服务发现，默认不安装 etcd/NATS。升级前保留旧 Helm values、revision 和 CRD；升级完成后检查控制器、Webhook 和 CRD 状态。

确认厂商 Device Plugin 已正常工作，并在节点上提供以下标准扩展资源：

| 平台 | Worker 资源名 | vLLM 基线 |
| --- | --- | --- |
| CUDA | `nvidia.com/gpu` | 0.28.0 |
| MetaX | `metax-tech.com/gpu` | 0.26.0 |
| Ascend | `huawei.com/Ascend910` | 0.23.0 |

Frontend、Profiler 和 Planner 为 CPU 组件，不申请加速卡。这里使用 Device Plugin 资源，不要求 DRA。厂商驱动、网络接口、RDMA 设备和 runtimeClass 按现场配置，不能从另一平台照抄。

## 逐个部署场景

`deployment/scenarios/` 保存本轮清单；CUDA/Ascend 文件含验收集群的节点名和模型路径，迁移时必须修改。MetaX 清单是未认证的集群模板，`REPLACE_*` 必须替换，卡数/容量也必须按现场填写。

每次只部署一个场景：聚合推理 → PD → 跨节点 PD → Grove/KAI → Profiler/Planner。先 `bin/kubectl apply --dry-run=server -f <单个文件>`，再实际 apply；不要对整个目录递归 apply。检查 Pod Ready 后，再验证 `/v1/models` 和真实生成请求，最后删除该场景并确认设备资源回收。

单独使用聚合或 PD 时不需要安装 Grove/KAI。需要这两个扩展时，先检查集群是否已有相同控制器；新集群的安装入口为：

```bash
bin/kubectl apply --server-side -f deployment/grove-crds/
bin/helm upgrade --install heyi-kai charts/kai-scheduler-v0.17.0.tgz \
  -n kai-scheduler --create-namespace -f deployment/kai-values.json --wait --timeout 10m
bin/helm upgrade --install heyi-grove charts/grove-charts-v0.1.0-alpha.13.tgz \
  -n grove-system --create-namespace -f deployment/grove-kai-values.json --wait --timeout 10m
```

随后在已有 Dynamo Operator release 的 values 中启用 `global.grove.enabled`、`global.kai-scheduler.enabled`，两个 `install` 保持 false，再执行 Helm upgrade。随包 KAI values 只选择标有 `heyi.io/kai-test=true` 的命名空间；需要 KAI 的工作负载命名空间应加此标签。Grove 默认调度与 KAI gang 调度是两个不同验收场景。

## Profiler 与 Planner

tools 镜像已经安装 Profiler、Planner、AIPerf 和部署工具，不需要挂载应用源码或临时 pip 安装。画像容量文件必须来自现场盘点，通过 ConfigMap 挂载到 `/opt/dynamo/platform-capacity/`，不能使用另一厂商的性能曲线。

需要离线画像的场景先执行真实采样、插值和画像持久化，再部署生成的 Planner 配置及画像 ConfigMap，最后验证在线扩缩容与重启复用。直接实时负载扩缩容和“文件画像初始化”分别记录；镜像能启动不等于画像拟合成功，也不等于生产 SLA 达标。

修改 DGD 名称时，同时更新监控清单的部署筛选条件，并确认 Prometheus targets 和实际流量指标可见。CUDA r1 清单位于 `deployment/services-r1/metrics.json`，Ascend r3 位于 `deployment/services/metrics.json`。随包画像属于验收证据；迁移到其他硬件、模型或超出画像有效期后，重新采样，不能直接复用旧曲线。

CUDA 包包含本轮独立 Prometheus 的镜像和清单。Ascend 本轮复用集群已有的 `kube-prometheus-stack`：`metrics.json` 是 PodMonitor，在线画像规划使用 `http://kube-prometheus-stack-prometheus.monitoring.svc:9090`。Ascend 包不包含整个共享监控栈；目标集群须先提供 PodMonitor CRD、接收该 PodMonitor 的 Prometheus，以及可达的查询地址。若名称或地址不同，调整监控选择器和 Planner 配置后再验收；仅导入 Dynamo 镜像不能满足此监控前提。

## 离线开发镜像

dev 从同一平台 runtime 派生，增加编译和调试工具。源码快照保存在 `build/source.tar.gz`；开发只修改挂载的源码，已验证 runtime 镜像保持独立。

```bash
mkdir -p source build-cache/cargo
tar -xzf build/source.tar.gz -C source
tar -xzf build/cargo-registry.tar.gz -C build-cache/cargo
docker run --rm -it --network none \
  -e CARGO_HOME=/build-cache/cargo -e CARGO_NET_OFFLINE=true \
  -v "$PWD/source:/workspace/dynamo" -v "$PWD/build-cache:/build-cache" \
  heyi/dynamo-vllm-<平台>-dev:<包内标签> bash
```

在容器中执行：

```bash
# 仅信任刚刚挂载的已校验源码目录，处理宿主机与容器 UID 不同的情况。
git config --global --add safe.directory /workspace/dynamo
/opt/dynamo/bin/dev-editable.sh python  # 只编辑 Python/GMS
# 或：/opt/dynamo/bin/dev-editable.sh all  # 重编 Rust 绑定和独立 KVBM 工具
```

上面的启动命令用于离线编译，不包含设备挂载。调试真实推理时，增加该平台所需的设备、驱动、共享内存和模型挂载。修改引擎版本、Rust 工具链或依赖后，需要重新构建并验收，不能继续沿用原包的认证结论。

## Ascend 分析器解释器

Ascend tools 镜像默认使用 `/opt/dynamo/tools-venv/bin/python`，这是独立 CPU 分析环境。`/opt/dynamo/venv/bin/python` 保留原厂推理依赖。手动执行 Profiler/Planner 时使用前者；不要在后者安装工具依赖。`deployment/` 中的 Planner 清单已采用对应解释器。CUDA/MetaX tools 继续使用 `/opt/dynamo/venv/bin/python`。
