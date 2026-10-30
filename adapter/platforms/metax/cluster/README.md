<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MetaX 集群适配交付入口

2026-10-30：本轮仅补齐实现、配置生成和 CPU 契约检查。**没有部署集群、构建新镜像或执行硬件回归，集群支持状态仍为待验证。** M10 使用最终镜像再认证。保持 vLLM-metax/vLLM 0.26.0；DRA、Kubernetes GMS/failover/checkpoint、功耗写入继续暂缓。

## 已补齐内容

- DGDR 的 Go SKU、CRD 和生成的 Python 枚举加入 `metax_c500`。这是本仓库的显式容量标识，不代表官方性能数据库或自动硬件发现。沿用现有 Hardware hub-only 转换存储，无需新转换字段，增加 alpha/beta 往返测试。
- `render.py` 复用 M08 公共渲染器，生成聚合推理、跨节点 Mooncake MACA PD、Grove、实时负载 Planner、thorough DGDR 和容量 ConfigMap。参数来自配置文件，无历史集群名、镜像标签或路径默认值。使用 Kubernetes 服务发现、TCP 请求面和 ZMQ 事件面，不要求 etcd/NATS。
- 资源名 `metax-tech.com/gpu`；CPU Frontend/Profiler/Planner 不申请设备、不加载厂商插件。PD 指定不同 P/D 节点，删除旧同节点 affinity；聚合 Worker 可在指定两节点伸缩。P/D TP 分别透传，候选 TP 不被 override 固定为 TP1/TP2。
- DGDR `autoApply=false`、`searchStrategy=thorough`；容量 YAML 必须使用现场容量，缺失时失败，不能回退 CUDA 曲线。提供真实 identity 后可启用 `planner_files`，调用既有插值、画像校验、ConfigMap 持久化及文件初始化链路。它不会生成虚构的采样结果。
- M09 provenance 支持 pymxsml 驱动/SDK、vllm-metax 插件、实际 Worker 模型路径；MetaX identity 必须显式传入节点设备数。
- `tools.Dockerfile` 从同一源码版本的 MetaX runtime 派生 CPU Profiler/Planner 工具镜像，安装必要依赖和配置合并工具，保护厂商包版本。此构建配方尚未经过实际镜像构建验收。

## 配置与清单生成

使用以下结构填写实际环境。数值仅展示格式，必须根据盘点修改；两节点的驱动/SDK差异应如实记录。`gpu_budget` 是本轮使用预算，不是整个集群总卡数。

```json
{
  "name": "metax-regression",
  "namespace": "dynamo-metax",
  "runtime_image": "YOUR_REGISTRY/dynamo-metax-runtime:YOUR_COMMIT",
  "tools_image": "YOUR_REGISTRY/dynamo-metax-tools:YOUR_COMMIT",
  "prefill_node": "YOUR_PREFILL_NODE",
  "decode_node": "YOUR_DECODE_NODE",
  "model": "/models/Qwen_Series/Qwen3-0.6B",
  "model_host_path": "/YOUR_MODEL_ROOT",
  "devices_per_node": 8,
  "vram_mb": 65536,
  "gpu_budget": 8,
  "prefill_replicas": 2,
  "decode_replicas": 3,
  "prefill_tp": 1,
  "decode_tp": 1,
  "scheduler_name": "kai-scheduler"
}
```

`model_host_path` 指向两节点都存在且权重一致的目录；使用 PVC 时删除它，改填 `model_pvc`，并确保卷支持所需跨节点访问。`runtime_class` 只在现场需要时填写。Grove/KAI 必须事先安装并启用，`scheduler_name` 不会安装调度器。两节点容量不同，先按较小容量声明本轮共同上限，不能把总显存当单卡容量。

可选 `network_env` 仅接收 `MCCL_SOCKET_IFNAME`、`GLOO_SOCKET_IFNAME`、`UCX_NET_DEVICES`、`UCX_TLS`、`UCX_PROTO_ENABLE`。不要从 CUDA 集群复制网卡名。Mooncake `maca` 是当前引擎配置，不据此宣称 RDMA 已启用或跨节点传输已通过；设备注入、Pod IP 可达性、bootstrap/动态数据端口、防火墙及 RDMA 注册均留给现场检查。

```bash
python3 adapter/platforms/metax/cluster/render.py \
  --config /var/tmp/metax-cluster.json --output /var/tmp/metax-manifests
```

输出六份 JSON 清单及 license。各服务使用独立名称，**M10 按场景依次部署和删除，不能一次 apply 整个目录**，否则会同时申请多组设备。

M10 操作顺序：更新 Operator/CRD → 安装或检查厂商 Device Plugin → 检查 runtimeClass、模型挂载、网络、镜像 → 单独部署 aggregate → pd-cross → grove → planner-load → profile-inputs + dgdr。复用 `adapter/m08/check-cluster.py` 检查显式资源和节点，再执行 server dry-run；这些检查不替代可用卡数和真实调度检查。

## 离线 Profiler 到 Planner

先用现场 Worker 采集身份（命令只供 M10 使用，本轮未执行）：

```bash
python3 adapter/m09/provenance.py --namespace dynamo-metax \
  --name metax-regression-aggregate --output /var/tmp/metax-provenance.json
python3 adapter/m09/profile_identity.py \
  --provenance /var/tmp/metax-provenance.json --device-model 'MetaX C500' \
  --devices-per-node 8 --nodes 2 --output /var/tmp/metax-identity.json
```

将 identity JSON 放入输入配置的 `identity` 字段，补充/核实精度、两节点的软件/设备身份和拓扑。单个 Worker 记录不是整个集群的自动认证；模型 revision 当前为 config 哈希，不是全部权重哈希。增加 `planner_files: true` 和 `prometheus_endpoint` 后重新生成。Prometheus 必须已抓取本命名空间的 Frontend、Worker 和 Planner 指标；不能填原 CUDA/Ascend 测试地址。

DGDR 先测量候选再插值，最终结果保留画像和配置 ConfigMap。`autoApply=false` 便于先检查输出，再按 M09 流程部署并验证 restart reuse；最终 Planner 使用同一工具镜像和 CPU 环境，保留生成的 `--config` 与画像挂载。没有真实采样、拟合失败或画像不匹配时不能声明规划可用。

## 工具镜像构建配方（M10 执行）

先按 M02 构建包含本次代码的 runtime。提供适合目标 Kubernetes 版本的 Linux amd64 `kubectl`；版本和 SHA256 写入 M10 产物记录。配置合并工具必须从同一 commit 编译：

```bash
mkdir -p artifacts/metax-cluster
(cd deploy/operator && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  go build -o ../../artifacts/metax-cluster/dgd-apply-overrides ./cmd/dgd-apply-overrides)
cp /PATH/TO/VERIFIED/AMD64/kubectl artifacts/metax-cluster/kubectl

docker build -f adapter/platforms/metax/cluster/tools.Dockerfile \
  --build-arg BASE_IMAGE=YOUR_REGISTRY/dynamo-metax-runtime:YOUR_COMMIT \
  -t YOUR_REGISTRY/dynamo-metax-tools:YOUR_COMMIT .
```

工具镜像复用已记录的 amd64 Planner Python依赖集合，但这不等于 MetaX 已完成依赖闭包验收；构建会锁住厂商包，M10 还需核对 pip 依赖、wheel 哈希和镜像启动。容量文件以 ConfigMap 挂载 `/opt/dynamo/platform-capacity/metax_c500.yaml`，镜像只放置 AISimulate 搜索路径到该文件的链接，不写入默认容量或预测数据。

Operator 镜像及离线 Helm 包沿用 [M08 构建说明](../../../m08/operator-image.md)，必须重建以携带新增 CRD。原 `adapter/m08/cluster/install.sh` 是 CUDA/Ascend 历史集群脚本；不要在新 MetaX 集群直接运行它。使用离线包中的 amd64 values，按目标命名空间/镜像地址安装，Grove/KAI 使用相应平台 Chart 配置。

## 本地检查与 M10 边界

本轮 238 项 Python CPU 检查通过，另通过 Go 硬件转换测试、配置合并工具编译和 CRD/Pydantic 生成检查；见 [本地检查记录](validation.json)。这些数量不包含历史硬件验收。

CPU 检查覆盖：生成清单的资源/平台/拓扑、2P3D、不同 TP 参数、Go override 合并、C500 DGDR 类型、显式容量和 files bootstrap 配置、alpha/beta 转换。测试不加载模型、不连接集群。

M10 尚须实际验证：工具镜像构建、CRD 准入、Grove gang 行为、跨节点数据传输与取消回收、Profiler 采样及拟合、Planner 扩缩容和重启复用。MetaX DRA/GMS/Kubernetes failover、功耗控制及生产 SLA 不在本轮交付范围。
