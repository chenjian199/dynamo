<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M01–M07 CUDA / Ascend Kubernetes 回归方案

状态：2026-10-28 本轮可执行回归已结束，保留明确失败和阻塞项，不能宣布 M01–M07 全部验收通过。两平台的大小模型 TP2/DP2、跨节点 2P3D、30 分钟 PD 资源回收、8192-token 阶梯等已取得证据；三项主要失败及其它限制见 [cluster/README.md](cluster/README.md)。
2026-10-29 补充：G3 已定位为测试未等待卸载完成，补齐前置检查后三轮通过；候选源码的 CUDA 小模型 TP8、双机 TP2×PP2/headless 通过。两项原生引擎确定性问题暂缓。详见 [后续排查](cluster/debug-g3-native-topology.md)，不替代新镜像与大模型验收。
2026-10-29 华为 DeepSeek 补测完成：固定 0.23.0 的 TP8/EP、W8A8、MTP、流式/取消恢复及自然语言 smoke 通过；30 分钟 864 请求稳定性通过，原生确定性差异保留暂缓。详见 [补测记录](cluster/ascend-deepseek.md)。
2026-10-29 顺序确认：先补华为 DeepSeek，之后完成 M08/M09 适配和验证，最后统一构建新镜像并执行 M10 最终验收。当前候选 Python 修改采用明确标识的只读 ConfigMap 覆盖，不替代最终镜像验证。
目标：验证已交付的 M02 runtime 镜像在实际集群上的 M01–M07 能力，补充大小模型及跨节点证据；M08 Operator 验收、M09 暂缓。

## 1. 目标节点与现场结论

| 平台 | 主机地址 | Kubernetes 节点名 | 物理设备 | 当前驱动 | Kubernetes |
| --- | --- | --- | --- | --- | --- |
| CUDA | 192.168.4.6 | worker06 | 8 × H800，约 80 GiB/卡 | 580.178.04 | v1.35.6 |
| CUDA | 192.168.4.14 | worker28 | 8 × H800，约 80 GiB/卡 | 580.178.04 | v1.35.6 |
| Ascend | 192.168.7.7，公网入口 39.156.164.52 | master-02-45d4a763 | 8 × 910B3，64 GiB/卡 | 24.1.rc3 | v1.32.2+k3s1 |
| Ascend | 192.168.7.5 | master | 8 × 910B3，64 GiB/卡 | 25.2.0 | v1.32.2+k3s1 |

- 四台机器均可通过现有跳板免密访问，节点 Ready，未报告 taint。4.14 的操作系统主机名是 worker14，但 Kubernetes 名称是 worker28。
- CUDA 每节点 allocatable `nvidia.com/gpu=8`；Ascend 每节点 `huawei.com/Ascend910=8`。启动前还要确认插件没有共享/超配配置，不能仅从资源键认定整卡隔离。
- 采样时未发现申请上述加速资源的活动 Pod；硬件采样也未见推理进程。这是瞬时观察，不是独占资源承诺，运行前重新检查。
- CUDA 各节点约 2 TiB 主存，Ascend 各约 502 GiB。Ascend 两节点同时承担控制平面/etcd，测试必须限制主存和 CPU，不执行主机重启、驱动复位、整机网络中断等故障注入。
- CUDA 存在 RDMA 设备和 RoCE 扩展资源，Ascend 存在活动 RDMA 链路。链路 ACTIVE 不等于 Pod 内传输已通过；后续必须验证设备注入、双向通信和实际数据通道。
- **7.3 不作为计算节点**。目前华为两台机器的 `/data/Qwen3-32B` 来自 7.3 的 NFS，只保留这一既有存储依赖；报告必须注明它不是完全独立的两机系统。

结论：硬件规模及 Kubernetes 资源具备回归条件。直接使用用户确认的现有镜像和华为驱动组合，不以驱动版本统一或历史镜像 ID 差异解释为启动前置条件。逐节点及跨节点设备/通信、模型完整性检查仍纳入回归。

## 2. 镜像与环境锁定

两台 CUDA 节点均已导入：

```text
docker.io/heyi/dynamo-vllm-cuda-runtime:1.5.1-m02
CRI image ID: sha256:1ae6d6e604c1782888d9eae2ee9719fdd94555e319e0357d3b515ff45ebbd0a9
```

两台 Ascend 节点均已导入：

```text
docker.io/heyi/dynamo-vllm-ascend-runtime:1.5.1-m02
CRI image ID: sha256:a0d44e6ad9e0d4e1607fe39d677429cb48d4fa5b3618f8cd9d819eacf6ebbf82
```

`crictl inspecti` 确认分别是 amd64 / arm64，入口均为 `/opt/dynamo/bin/entrypoint.sh`，历史层包含 Dynamo wheel 安装。这里的 CRI 是节点镜像存储，不是共享镜像仓库。

用户已确认导入镜像没有修改，本轮直接以以上现有镜像为回归基线，不要求重建、重新导入或先追查 CUDA 历史 ID 差异。CUDA 的 CRI ID 与历史 [M02 image.json](results/cuda/image.json) 中的 ID 不同，这一观察保留在记录中，不推断镜像被修改。启动时记录实际 imageID、包版本、模块路径及已有 artifact 信息，用于结果追溯；只有实际加载错误或版本不符合固定基线时才定位问题。

固定引擎基线沿用 [版本约束](../version-baselines.md)：CUDA vLLM 0.28.0，Ascend vLLM 0.23.0+empty / vllm-ascend 0.23.0。旧 API 兼容代码保留，但不为通过测试而替换成另一引擎版本。

预检 Job 记录 Python 包版本、模块实际路径、Rust 扩展及 KVBM/GMS 产物、厂商补丁校验、容器内 CANN/CUDA 与宿主机驱动。生产验证不进行 editable 安装，不通过 PYTHONPATH 用宿主机源码覆盖镜像实现；测试工具可以单独只读挂载。

华为保留现有驱动：7.7 使用 24.1.rc3，7.5 使用 25.2.0。用户反馈此前该组合已能测试，本轮不要求统一版本或升级驱动，也不把版本不同本身判为失败。先分别运行 runtime/分配/复制/算子检查，再进行双机 HCCL、Mooncake 和模型回归，以实测结果判断本轮配置是否通过；若失败，结合日志定位驱动、镜像库、设备注入和网络原因后再提出具体修改。报告记录两个节点各自的驱动/固件版本，测试结论限定于这一组合，不扩展成所有混合驱动版本均受支持。

官方 v0.23.0 安装说明要求 CANN 9.1.0，并把 HDK 配套要求指向 CANN 发行说明；这些资料作为兼容性排查参考，不作为要求用户先统一驱动的依据。

参考：[vLLM Ascend v0.23.0 安装说明](https://docs.vllm.ai/projects/ascend/en/v0.23.0/installation.html)、[CANN 9.1.0 发行说明入口](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/910/softwareinst/releasenote/9.1.0/release-notes.md)。后者本次网页读取没有取得详细配套表，因此不填写未经确认的最低 HDK 版本。

## 3. Kubernetes 部署方式

每套集群新建独立测试 namespace，例如 `dynamo-regression`，资源带 campaign/test-case 标签。使用普通 Job、Deployment、StatefulSet 和 Service，直接运行 Dynamo frontend 与 worker，不安装 M08 Operator/Grove，不修改现有业务资源。

| 组件 | 部署建议 | 用途 |
| --- | --- | --- |
| etcd | 独立单副本 Deployment + Service，临时 emptyDir | 本轮 Dynamo 注册/发现；不是 Kubernetes 控制面 etcd，不验收持久化/HA |
| Frontend | 无加速卡 Deployment + ClusterIP Service | OpenAI HTTP 接口、路由 |
| 聚合 / P / D Worker | 固定副本 Deployment 或 StatefulSet，显式绑定目标节点 | 模型执行、KV 事件、PD |
| 跨机引擎 ranks | 稳定名称的 StatefulSet/Headless Service，按引擎 executor 组织 | 同一模型实例的跨节点执行 |
| 回归客户端及原语测试 | 有时限 Job | 请求、取消、指标和底层测试 |

两集群目前均没有 `DynamoWorkerMetadata` CRD。首轮选择 `DYN_DISCOVERY_BACKEND=etcd`、`DYN_REQUEST_PLANE=tcp`、`DYN_EVENT_PLANE=zmq`，避免依赖 Operator。NATS 不是该组合的必需组件；若追加 NATS 事件通道测试，再部署专用实例。不得复用其他业务的 etcd/NATS，也不得把原单机脚本的 `DYN_FILE_KV=/tmp/...` 原样用于跨节点。

CUDA 两节点已有 `registry.k8s.io/etcd:3.6.8-0`；华为 7.7 有该标签，7.5 未发现 etcd 镜像。可将首轮华为 etcd 固定在 7.7；若允许在两节点重建，需先补齐并校验 arm64 镜像。单副本 etcd 的高可用不属于本轮结论。

部署约束：

- 离线导入镜像使用完整标签和 `imagePullPolicy: Never`，同时固定目标节点并核对实际 imageID；当前无 repoDigest，不能把 CRI 配置 ID 直接当拉取 digest 使用。此方式要求每个可调度节点已有镜像，见 [Kubernetes 镜像说明](https://kubernetes.io/docs/concepts/containers/images/)。
- CUDA 使用 `nvidia.com/gpu`，Ascend 使用 `huawei.com/Ascend910`，requests/limits 一致。Frontend 不申请加速卡；容器只使用插件分配的设备，不填宿主机全局卡号越过分配。
- CUDA 已有 `nvidia` 等 RuntimeClass；Ascend 不能照搬 CUDA 的 runtimeClass。按现有插件和运行时配置验证设备及驱动库注入，不挂宿主机整套 CANN 覆盖镜像版本。
- 第一阶段使用 Pod 网络验证 TCP/ZMQ；RDMA/HCCL 检查实际数据网、网卡和 GID/IP 配置。必要时单独选择 hostNetwork 并分配不冲突端口；不能把管理网连通当 RDMA 验收。
- CUDA 模型使用节点各自的 `hostPath`，设置 `type: Directory`、只读挂载，并通过节点选择条件绑定有对应文件的节点；不新建统一 NFS/PVC 模型挂载。宿主机路径可以不同，在各节点的 Pod 模板中分别映射到容器内 `/models/<model>`。缺失目录应明确失败，不用 `DirectoryOrCreate` 创建空目录冒充权重。华为保留现有模型访问方式。
- 缓存、日志、结果与只读模型分开。G3 缓存用节点本地 SSD/PVC，不把模型 NFS 当成本地磁盘层。`/dev/shm` 使用有容量限制的内存卷，并计入内存预算。
- CPU/内存 requests、limits、临时存储和启动超时逐用例固定。初始小模型 worker 可从 8 CPU / 32 GiB 主存上限起步；大模型按加载峰值预估后提升。华为每节点全部测试 Pod 主存上限初始合计不超过 400 GiB，为控制面及系统保留余量；若不足则串行/减少副本，不直接放开限制。
- 本轮手动串行安排大任务；普通 Kubernetes 没有隐含的 gang scheduling 保证。跨机 ranks 未全部获得资源时不进入性能采样。

## 4. 模型准备

| 层级 | 首选模型 | 已观察到的位置 | 尚需完成 |
| --- | --- | --- | --- |
| 小模型 | Qwen3-0.6B | 华为 7.7 `/data/weight/Qwen3-0.6B` | CUDA 查找或从已有权重复制到各节点本地目录，通过 hostPath 读取；华为补齐缺失节点的访问；不重新下载 |
| 小模型备选 | Qwen2.5-0.5B-Instruct | CUDA 4.6 `/data/models/Qwen2.5-0.5B-Instruct` | 仅作为启动诊断，不能代替同模型的成对比较 |
| 中大稠密模型 | Qwen3-32B BF16 | CUDA 4.14 `/root/Qwen3-32B`；华为两节点 `/data/Qwen3-32B` | CUDA 4.6 查找或复制已有权重到本地目录，两节点各用 hostPath；检查 tokenizer/index 与分片 |
| 大型 MoE | DeepSeek-V4-Flash W8A8-MTP | 华为 7.5 `/data/DeepSeek-V4-Flash-w8a8-mtp`，7.7 同路径通过 NFS 访问 | 固定版本引擎、量化插件、并行和 KV 布局检查 |
| 大型 MoE | DeepSeek-V4-Flash，CUDA 适用权重 | CUDA 已有 PV 引用 `192.168.10.62:/data/HY/NFS/Models/DeepSeek_Series/DeepSeek-V4-Flash`，仅作权重来源线索 | 查找或复制到两节点本地目录，通过 hostPath 读取，不依赖该 PV 部署；核对权重格式及 H800 内核支持 |
| 多模态 / Omni | 优先历史验收用的 Qwen3-VL-2B-Instruct / Qwen2.5-Omni-7B | 本次定向盘点尚未确认这些精确权重在目标两集群均可用 | 从已有权重补齐；CUDA 使用各节点 hostPath；同时准备固定图片、视频、音频样本 |
| 高级能力附件 | LoRA、草稿模型或模型自带 MTP | 尚未确认完整附件集 | 匹配 base model/revision，不能用空 adapter 冒充真实 LoRA |

不同节点不要求相同宿主机路径或统一存储，但同一分布式实例及成对对照用例必须使用同一版本的权重、配置和 tokenizer。节点路径映射单独记录；本地副本补齐前先核对目标磁盘空间，不覆盖已有不同版本的模型。

本次读到 Qwen3-32B 的 17 个 safetensors 文件，合计约 65.5 GB；华为 DeepSeek 70 个文件，合计约 300 GB，索引为 `quant_model_weights.safetensors.index.json`。这不是完整性认证：执行前核对索引列出的每一分片、配置和 tokenizer 哈希，并锁定量化版本。

华为 [v0.23.0 DeepSeek 教程](https://docs.vllm.ai/projects/ascend/en/v0.23.0/tutorials/models/DeepSeek-V4-Flash.html) 列出 W8A8-MTP 的 A2 8 × 64 GB 配置，因此这两台 910B3 有值得验证的硬件基础；仍须确认本地权重与冻结镜像的真实兼容性。CUDA [官方 recipe](https://github.com/vllm-project/recipes/blob/main/models/deepseek-ai/DeepSeek-V4-Flash.yaml) 对 Hopper 路径记录了版本相关限制，不能直接把 Blackwell 的启动参数套给 H800，也不能承诺当前权重必定可用。

## 5. 分阶段用例

| 阶段 / ID | 模型与规模 | 核心检查 | 对应模块 |
| --- | --- | --- | --- |
| A / ENV | 每节点先 1 卡，通信再 2 卡/2 节点 | 版本、模块来源、设备身份/能力、驱动、内存复制/事件、Pod 内 NCCL/HCCL、NIXL/Mooncake 原语 | M01、M02、M04 |
| B / SMALL | Qwen3-0.6B：单卡、TP2、两个 worker | 原生 vLLM 对照 Dynamo；非流式/流式、取消、停止与恢复、KV-aware 路由及事件 | M01、M03 |
| C / DENSE | Qwen3-32B：TP2 起步，TP4/TP8；双 worker；DP2 | 大模型加载、并行结果、调度与路由、长度/并发阶梯；DP 用例与独立 worker 副本分开记录 | M03 |
| D / PD | 小模型跨机 1P1D → 2P3D；再 Qwen3-32B TP2 的 1P1D/2P3D | 握手、真实 KV 传输、路由、输出、取消、故障、恢复；P/D 节点互换 | M05 |
| E / DIST | Qwen3-32B，同一个引擎实例跨两节点 | 优先验证每节点 TP2、PP2；先确认该版本 executor/模型支持；另测受支持的跨机 TP 配置 | M03、M04 |
| F / CACHE | 小模型与 Qwen3-32B，单卡/TP2，随后与已通过的 PD 组合 | G1/G2/G3 命中、卸载/回载、逐出、取消/清理；独立 KVBM physical 链路和两进程 replicated 广播 | M06 |
| G / ADVANCED | 实际 LoRA/量化/推测附件；DeepSeek 从单节点 8 卡起步 | 高级能力逐项开启，与关闭功能的同模型基线比较；MoE/EP、MTP、双节点模型实例/双副本、受支持的 PD | M03、M05 |
| H / MEDIA | VL / Omni 专用权重和媒体 | 图像/视频请求、独立编码器、LOCAL 媒体生命周期；CUDA GMS 与聚合 Omni | M07 |
| I / SOAK | 已通过的跨机 PD + KV 路由代表配置 | 至少 30 分钟且至少 1000 个正常请求，另做取消、故障恢复用例 | M03、M05、M06；M07 另按媒体用例验收 |

执行顺序 A → B → C → D → E → F → G → H → I；两平台可在各自前置条件通过后独立推进。失败停在对应组合，不反复重启掩盖问题。

### PD 与多机必须区分的范围

- 小模型 2P3D：节点 A 两个 TP1 Prefill，节点 B 三个 TP1 Decode，共 5 卡，再反向测试。
- Qwen3-32B 2P3D：每 worker TP2，节点 A 使用 4 卡，节点 B 使用 6 卡，共 10 卡；以短上下文启动，验证显存余量后扩长。显存不足会记录为容量限制，不偷改量化方式维持“相同基线”。
- 副本数不对等与 TP 不对等是两个用例。TP2→TP1 / TP1→TP2 继续只在已支持的 Qwen BF16 contiguous NHD MHA/GQA 布局上测试，不把这些结论推广到 DeepSeek V4 的混合注意力布局。
- 两个 TP8 worker 分别放在两节点，不等于单个 TP16 引擎。跨机单实例必须记录完整 rank/world-size/节点映射及集合通信证据。
- DeepSeek 先聚合模式成功，再评估可用的 DP/EP/TP 和 KV connector；每节点 8 卡的两机 1P1D 是候选，不预先承诺 2P3D 大模型配置能装进 16 卡。2P3D 的数量语义由小模型和 Qwen32B 覆盖。
- NIXL/Mooncake 要记录实际 backend、数据网卡、计数器和传输日志。TCP fallback 可以成为诊断结果，但不能标记 RDMA 通过。

### KVBM / GMS / 多模态边界

- M04 原语、M06 独立 KVBM/native replicated 使用真机 Job，不能用一次 HTTP 推理成功代替。replicated 的 MLA 风格布局测试，不代表 vLLM 已自动接入完整 MLA replicated 推理。
- G4 远端缓存此前未实现，本轮列为覆盖缺口；有两台机器不会自动补齐该功能。跨节点 PD 也不能算 G4 验收。
- CUDA GMS 先在同节点、同一个获分配 GPU 的测试容器内运行服务及多个客户端进程，验证 FD/UDS、共享映射、writer 生命周期及回收。不要让多个独立 Pod 各申请一张卡，却误称它们共享了同一张卡；跨 Pod 共享部署属于另外的编排契约。
- Ascend GMS 继续记录能力拒绝及底层限制；不因驱动升级或普通推理成功宣称 GMS 完成。华为 Omni 已知问题、独立 Dynamo Omni stage 的完整 hidden-state 通道继续暂缓。
- LOCAL 媒体通道先限定同节点；共享 NFS 不自动提供跨节点句柄/租约/回收语义。跨机多模态传输只有实现具备对应 transport 时另开用例，不复用 LOCAL 结果作证。
- CUDA 聚合 Omni 要测试真实音频输出和取消恢复；Ascend 的文本/图像/独立编码器用例与暂缓的 Omni/GMS 分开计数。文本大小模型不能覆盖整个 M07。
- SGLang、M08 Operator/Grove/调度控制器、M09 自动伸缩均不纳入本轮通过声明。

## 6. 请求、指标及验收

先冻结用例参数再执行，避免失败后随意降低门槛：

1. 正确性：同平台、同权重、同 tokenizer、固定 seed/greedy，原生 vLLM 与 Dynamo、聚合与 PD、冷缓存与命中路径成对比较；先用确定性短输出验证 token 序列。分布式数值差异另外定位并记录，不用“跨厂商文本不完全一致”掩盖同路径回归。
2. 基础负载：短输入约 128 tokens/输出 64，常规 1024/128，长输入 8192/128；只在模型上下文和显存允许时执行。并发从 1、4、16 逐级增加；实际 token 数、输出上限和模型模板归档。
3. 路由：冷请求、重复前缀命中、worker 增减/重建后事件清理；记录实际 worker 路由及命中证据，不能只看总吞吐。
4. 稳定性：代表性小模型跨机 PD 用例并发 4，持续至少 1800 秒且完成至少 1000 个正常请求，两项都满足。Qwen32B/DeepSeek 各追加至少 30 分钟代表性负载；大模型请求总数按预检吞吐在正式运行前冻结，不把小模型结果冒充大模型长稳。
5. 故障：单独执行至少 32 个流式取消，以及 3 轮测试 worker 退出/Pod 重建/恢复。只操作本次资源。检查明确失败、超时有界、新请求可恢复；不要求当前未实现的在途请求无感重试。
6. 回收：在无重启的稳定窗口比较 RSS、FD、线程、设备/KV 块、传输句柄和缓存文件；请求排空后 KV 占用返回预期基线。预分配设备内存不要求归零。小模型沿用既有 M05 门槛：预热后 RSS 增量 ≤256 MiB、FD ≤8、线程 ≤4，并检查原生资源计数；不同大模型/媒体用例在预检后另行冻结门槛，不能事后放宽。
7. 性能：报告成功率、TTFT、TPOT、端到端延迟 P50/P95/P99、吞吐、通信及设备利用率；同平台同配置原生引擎与 Dynamo 至少三次短测。当前没有约定吞吐 SLO，性能先作比较证据，不凭空宣称达标，也不把不同量化/硬件的绝对速度比较当适配正确性。

每个结果必须标记 `PASS / FAIL / BLOCKED / DEFERRED / NOT_APPLICABLE`。预期的“不支持”错误可以通过负向契约测试，但相应正向能力仍是 BLOCKED/DEFERRED。每模块单列应测、已测、未测项，未准备 VL/LoRA 等资产时不能宣布 M01–M07 全通过。

## 7. 交付与下一步

按修订方案先交付：节点/版本锁文件、各节点模型 hostPath 映射清单、普通 Kubernetes manifests、渲染检查与部署/采集/清理脚本。直接使用当前镜像和现有华为混合驱动组合进入 A 阶段，记录运行时版本并验证设备/通信；通过后依次启动模型回归，不再要求预先统一驱动、统一模型挂载或重新确认镜像来源。

每个 run 保存镜像/产物哈希、驱动/固件、模型元数据、参数、manifest、实际节点/设备分配、请求和响应摘要、事件/日志/指标、故障时间线以及清理前后资源统计。敏感配置和完整 kubeconfig 不入库。

本次只读盘点原始文件暂存在 `/var/tmp/dynamo-cluster-proxy-check-kpgxtdzf/` 的 `*.inventory.json`、`*.k8s-plan.json`、`*.plan-extra.json`，该路径仅用于当前工作现场，不作为长期交付依赖。后续归档应提取本次节点和用例必要信息，避免把无关业务资源清单提交到仓库。
