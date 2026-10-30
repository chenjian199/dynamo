<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M08 Kubernetes 部署与集群验收

2026-10-30 MetaX 补充：已提供跨节点 PD、Grove、DGDR/C500 容量及工具镜像构建入口；仅做本地契约检查，集群仍待 M10 验证。见 [MetaX 集群适配](../platforms/metax/cluster/README.md)。

状态：2026-10-29 阶段性收尾。已在 CUDA H800 / Ascend 910B3 集群安装 Operator 并验证基础 Device Plugin 路径，详见 [集群验收与证据](cluster/README.md)。MetaX 保留本地逻辑测试通过、硬件集群待验证状态。未升级宿主机驱动。扩展验收已通过两平台跨节点 Operator 2P3D、Grove/KAI、DGDR 自动部署和 CUDA DRA，见 [扩展证据](advanced/README.md)。华为 DRA 等待用户提供 ≥1.34 测试集群；DGDR 预测精度未认证。

## 阶段性收尾与 M09 交接

本阶段已验收 CUDA/Ascend 的 Operator 基础部署、KV 路由、扩缩容与重建、跨节点 2P3D、Grove/KAI、DGDR 实测到自动部署，以及 CUDA DRA 整卡生命周期。指定 RDMA 路径的持久设备缓冲区与 2P3D 见 [专项结果](rdma/README.md)。本状态仅覆盖已记录版本、硬件与用例，不表示完整目标设计全部完成。

保留以下边界：

- MetaX：无可用 Kubernetes 集群，硬件集群回归暂缓。
- Ascend DRA：等待用户另行提供 Kubernetes ≥1.34 测试集群，采用已接受的 HAMi 实验驱动；现有集群不升级。
- Ascend 动态内存注册/注销压力：原生 ADXL/HCCL 失败未修复；持久 KV 缓冲区与 PD 路径通过，不能相互替代。
- 厂商自动硬件发现、共享设备、拓扑/GMS/failover 等未认证组合仍按原能力边界保留；本轮 RDMA 不覆盖全 rail 性能或集体通信性能。

Profiler 主体归属 M09。本轮为打通 DGDR，提前实现了 SKU 传递、平台资源/connector 配置生成、工具镜像与测量回传；这些成果进入 M09 基线。画像精度、配置选择效果、SLA 达标和 Planner 动态扩缩容尚未验收，详见 [M09 交接与顺序](../modules/m09-telemetry-planner/README.md)。最终镜像统一重建与完整回归仍归 M10。

## 资源契约与来源

| 平台 | 整卡资源名（大小写敏感） | 固定引擎 / 当前镜像架构 | 参考 |
| --- | --- | --- | --- |
| CUDA | `nvidia.com/gpu` | vLLM 0.28.0 / amd64 | [NVIDIA Device Plugin](https://github.com/NVIDIA/k8s-device-plugin) |
| MetaX | `metax-tech.com/gpu` | vLLM-metax 0.26.0 / amd64 | [llm-d MetaX 维护者提供的接入文档](https://github.com/llm-d/llm-d/blob/main/docs/getting-started/accelerators.md#metax-c500x)；[沐曦官方 K8s 发布说明](https://developer.metax-tech.com/api/client/document/preview/发布说明/K8s_发布说明/曦云C500系列/0.16.1/split_files/新增特性及变更.html) |
| Ascend | `huawei.com/Ascend910` | vLLM-Ascend 0.23.0 / arm64 | [华为官方整卡调度说明](https://www.hiascend.com/document/detail/en/mindcluster/2610/clustersched/schedulingug/docs/en/scheduling/04_usage/03_basic_scheduling/03_full_npu_scheduling.md) |

资料核对日期：2026-10-28。沐曦的精确资源键来自上游 llm-d 的 MetaX 接入文档；厂商发布说明确认 Device Plugin、CDI、虚拟化与超配模式，不能把该发布说明当作资源键的直接出处。

本轮契约是 **Device Plugin 整设备分配模式**。资源请求数量是插件分配单位，必须先确认插件没有启用共享/超配模式，才能解释为物理设备数。尤其华为软切分也可能沿用 Ascend910 键；显式 softShareDev 注解会被拒绝。sGPU、vNPU、显存/算力配额及自定义资源别名均不冒充整卡数，未知厂商单位返回错误，不按零计数。NVIDIA 原有 MIG/DRA 行为保留，不代表新增厂商 DRA 已实现。

## 实现范围

- 复用 v1beta1 `components[].podTemplate` 声明设备资源、镜像、`DYN_PLATFORM`、runtimeClass、节点条件和挂载。非 CUDA Worker 必须声明一个字面量平台 ID，并请求正数整卡；不从镜像名称猜测平台。
- 共用资源解析进入已有 DRA/标量计数入口，保留 limits/requests 去重、init 峰值、常驻 init sidecar 和多 Pod 数量语义。状态中既有 `gpusPerEngine`、`gpusPerReplica` 字段名不改。
- 同一 DGD 的加速 Worker 不得混用厂商；CPU Frontend 不申请加速卡。准入和 Pod 渲染均检查冲突，显式资源不会被悄悄替换成 NVIDIA 资源。
- MetaX/Ascend 的 Kubernetes GMS、failover、checkpoint 与 DRA 明确拒绝。Docker 下已验证的 GMS 不等于 Operator 共享卡部署已可用。
- 沿用既有 `component` 工作负载路径、健康探针、服务发现、手动扩缩容和 Pod 重建逻辑，不复制三套控制器。
- 基础路径没有新增 Go API 字段。后续 DGDR 扩展新增 H800/Ascend 910B3 SKU 枚举，并重新生成 CRD/Pydantic；未新增字段，deepcopy 无需变化。alpha 自定义资源和环境变量经过现有转换链；API 包回归验证往返行为。设计草案中的 `spec.platform` 暂不落地，避免与 PodTemplate 同时维护两份资源声明。
- Helm 已支持替换 Operator 镜像，DGD 已支持 PodTemplate，无需新增厂商 Helm 模板。DGDR 自动硬件发现仍是原 NVIDIA 路径；非 CUDA 的自动硬件发现、SLA 预测数据库、Planner、DCGM 替代与拓扑发现留给后续工作，不通过本轮基础部署暗中开启。

## 准备需要导入的镜像

除 M02 的平台 runtime 镜像外，集群必须运行包含本轮修改的 **Operator 镜像**。只导入推理镜像、继续使用原官方 Operator，不会获得华为资源计数和平台校验。

复用原生 Operator Dockerfile，在对应架构主机从仓库根目录构建；不要把 amd64 镜像作为 arm64 镜像导入：

```bash
docker buildx build --load --platform linux/amd64 \
  --build-context compliance=container/compliance \
  --build-context operator-chart=deploy/helm/charts/platform/components/operator \
  -f deploy/operator/Dockerfile \
  -t heyi/dynamo-operator:1.5.1-m08-amd64 deploy/operator

# 在 arm64 构建主机将上面的 amd64 换成 arm64。
docker save -o /var/tmp/dynamo-operator-m08-amd64.tar \
  heyi/dynamo-operator:1.5.1-m08-amd64
# 在各推理主机导出相应平台；不要导入 dev 作为运行镜像。
docker save -o /var/tmp/dynamo-metax-m02-runtime.tar \
  heyi/dynamo-vllm-metax-runtime:1.5.1-m02
```

Operator 离线交付与验证记录另见 [镜像构建、导入和使用说明](operator-image.md)，附带原生构建脚本和独立 Operator Helm Chart 的 values。目标节点使用 containerd 时，镜像需要导入 Kubernetes 使用的 namespace，例如 `ctr -n k8s.io images import ...`；只做 Docker load 不保证 kubelet 可见。集群若有内部仓库，可改用推送/拉取路径。10 月 29 日已在两集群导入对应架构镜像，没有推送公共仓库。

安装或升级平台 chart 时沿用现有部署步骤，覆盖以下 values（`amd64`/`arm64` 与 Operator 所在节点一致）：

```yaml
dynamo-operator:
  controllerManager:
    manager:
      image:
        repository: heyi/dynamo-operator
        tag: 1.5.1-m08-amd64
        pullPolicy: IfNotPresent
```

继续使用仓库对应的 CRD/chart。设备插件与厂商 runtime 由管理员安装，必须报告表中的资源；runtimeClass 名称由集群配置决定，本轮不虚构统一的厂商名称。网络允许 Pod 间 TCP/ZMQ 与传输端口，模型 PVC 预先创建并绑定，多节点访问模式需要匹配实际存储。

## 生成与预检查

`render.py` 输出 JSON 格式的 Kubernetes YAML，直接交给 kubectl；配置源仍是 M02 平台 JSON，不重新选择引擎版本。`--image` 可以改为导入后标签或内部仓库 digest，但必须对应同一平台 M02 runtime 内容。

```bash
# 双 Worker 聚合推理与 KV 路由；平台可换 cuda / ascend。
python3 adapter/m08/render.py \
  --platform metax --model-pvc models \
  --name m08-metax --namespace dynamo-system \
  > /var/tmp/m08-metax.json

# 同节点 2P3D，TP1；使用已适配的 CUDA NIXL / MetaX MACA Mooncake / Ascend Mooncake。
python3 adapter/m08/render.py \
  --platform metax --model-pvc models --mode pd \
  --prefill 2 --decode 3 --tp 1 \
  --name m08-metax-pd --namespace dynamo-system \
  > /var/tmp/m08-metax-pd.json

python3 adapter/m08/check-cluster.py \
  --context YOUR_CONTEXT --manifest /var/tmp/m08-metax.json
kubectl --context YOUR_CONTEXT apply --dry-run=server -f /var/tmp/m08-metax.json
```

清单默认模型为 `/models/Qwen_Series/Qwen3-0.6B`。Frontend 使用同架构的平台 runtime 镜像，但设置 `DYN_PLATFORM=cpu` 且不申请卡。保留 M02 entrypoint，确保 CANN 等环境初始化；GPU 卡号由插件注入，不设置 `CUDA_VISIBLE_DEVICES`、`MACA_VISIBLE_DEVICES` 或 `ASCEND_RT_VISIBLE_DEVICES` 为宿主机编号。Ascend 保留只读宿主机 driver/dcmi/npu-smi 挂载，设备节点由插件管理，不添加全量 `/dev` 挂载。

PD 清单通过 Pod affinity 将 Worker 放在同一节点；同节点必须容纳 `(P+D)*TP` 个分配单位。不同 Pod 使用独立网络命名空间，bootstrap 端口可以相同，不使用 hostNetwork。CUDA NIXL 与 Ascend Mooncake 的同节点跨 Pod 2P3D 已实测；基础轮跨节点测试是聚合推理/KV 路由；后续 [扩展轮](advanced/README.md) 已通过两平台跨节点 2P3D。该扩展轮 CUDA 使用 TCP + CUDA copy；后续 [RDMA 专项](rdma/README.md) 已通过两平台指定链路的持久设备缓冲区与跨节点 2P3D，另记录 Ascend 动态注册压力失败。MetaX 的 Kubernetes 传输仍待实测。

KV 事件明确启用 `enable_kv_cache_events=true`，ZMQ publisher 使用 `tcp://*:20080` 监听；容器网络的回环地址形式在当前引擎中表示 connect，不能作为发布端绑定地址。Decode 健康请求携带 `x-bypass-remote-prefill`，通过引擎本地前向检查就绪；普通 PD 请求继续检查真实远端 handoff，具体修复见 [集群记录](cluster/README.md#本轮定位的问题)。

预检查只读：检查 v1beta1 CRD、Bound PVC、runtimeClass 存在及 Ready 节点资源/架构。不代表空闲卡、共享模式、驱动、镜像可拉取或真实调度已经验证。`allocatable` 是容量，不是当前空闲量。

## 目标集群验收清单

CUDA / Ascend 的本轮结果见 [验收表](cluster/README.md#验收结果)。以下清单仍用于 MetaX 后续集群验收。

1. 服务端 dry-run 通过后部署聚合清单，记录 Operator/runtime imageID、插件版本、节点资源、驱动、DGD/Pod 和事件。
2. 等待 Frontend 与两个 Worker Ready，通过端口转发调用 `/v1/completions`，验证输出和重复前缀 KV 命中/选路。
3. 用 DGD 的 `spec.components[].replicas` 将 Worker 从 2 改为 3 再改回 2；确认 Pod 数量与 `gpusPerEngine`/`gpusPerReplica` 单位正确，零副本不会被解释为硬件未知。
4. 删除本测试 DGD 的一个 Worker Pod，验证重建、重新注册、readiness 与后续请求恢复。不是修改设备插件或重启宿主机。
5. 清理聚合 DGD 后再部署同平台 2P3D；验证生产/消费 Worker 数量、输出、取消与故障恢复。三平台分别留存证据。
6. 拒绝测试：平台与资源冲突、混合厂商 Worker、sGPU/vNPU 配额、非 CUDA GMS/DRA。CPU Frontend 不应消耗设备资源。

只有这些目标集群项目通过后，才能把相应平台标记为 Kubernetes 已验证。M02 CUDA 原生构建收敛、GDS、Ascend GMS 和 Omni 的历史限制继续保留。

## 本地验证

在 `deploy/operator` 下使用 `go.mod` 指定的 Go 工具链：

```bash
make setup-envtest
go test ./internal/platform ./internal/dra ./internal/dynamo \
  ./internal/webhook/validation ./api/v1alpha1 ./api/v1beta1
CGO_ENABLED=0 go build -o /var/tmp/dynamo-operator-manager ./cmd/main.go
```

测试包含三平台真实清单生成、生产 Pod 渲染、CRD/schema/CEL + webhook 准入链、旧 CUDA 路径及 API 转换。envtest 使用本地 apiserver/etcd，不是有设备插件的硬件集群。结果及源码哈希见 [本地验证记录](results/local.json)。

Helm 的实际安装/查看命令、Profiler/DGDR、DRA 和 SLA 的关系见 [使用说明](helm-profiler-dra.md)。
