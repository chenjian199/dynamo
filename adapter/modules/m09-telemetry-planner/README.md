<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M09 遥测与 Planner Profiler

MetaX 2026-10-30 补充：Planner/Profiler 集群入口、MACA 身份采集、现场容量 ConfigMap 及工具镜像配方已补齐；硬件采样、画像拟合、伸缩与重启复用留给 M10。见 [交付入口](../../platforms/metax/cluster/README.md)。

状态：实时指标路径已完成本轮有界功能回归；2026-10-30 已实现并测试[离线画像到在线 Planner](../../m09/offline-bootstrap.md)：四组通过、CUDA Qwen32 有限通过，Ascend DeepSeek prefill 拟合未通过；完整离线验收不标为收口。实际通过项及剩余边界以 [M09 验收记录](../../m09/README.md) 为准。依赖 M01/M03/M08；功耗写入与生产性能建模仍是独立能力，不能由功能测试推导为已认证。

## 本轮交付（2026-10-29 至 2026-10-30）

- 平台资源计数统一到 `common/platform/kubernetes.py`，保留 Operator 当前 generation 的权威设备数量；Profiler 修改正确的资源键，Planner 验证目标平台，禁止混合厂商或错误身份。CUDA 原有镜像及兼容路径保留。
- `common/platform/telemetry.py` 提供只读 exporter/可见设备内存观测，区分 available/unavailable/stale/error，使用 bytes/W/percent 和采样时间。CUDA/Ascend exporter 与 MetaX 本地内存查询有真机证据。健康信息暂不自动改变 readiness；功耗写入不启用。
- `common/platform/profile.py` 记录画像身份、时效与内容 SHA256，区分端到端 benchmark 与 Planner FPM bootstrap。非 CUDA 禁止未认证 AIC 数据库与 rapid 路径，使用 thorough 实测和实时 FPM 回归；搜索容量必须有实际 YAML，不允许未知硬件隐式使用 H200 容量。
- Planner/Profiler 的单测及集群扩缩容、SLA 决策、TP1/TP2 搜索和大模型验证见 [结果](../../m09/README.md)；[复现步骤](../../m09/reproduce.md) 包含源码覆盖、独立 Planner 依赖和归档方法。最终镜像验收留给 M10。

本轮六项模型扩缩容运行和两项 SLA 决策运行均满足各自功能条件，API 错误为零，删除后四台节点无残留设备进程。DeepSeek 分组 KV 容量改为引擎原生每引擎容量，保持路由事件宽度和旧版兼容；MetaX 本机验证容量发布。Ascend 冷扩容延迟未达目标、CUDA DeepSeek 一次瞬态缩扩容震荡继续记录，生产 SLA 和稳定伸缩不标记通过。

## M08 交接与本轮实施顺序（2026-10-29）

Profiler/Planner 主体归本模块。M08 负责 Kubernetes 编排；为验证 DGDR，已提前交付 H800/910B3 SKU、明确 Ascend 时的资源/connector 转换、PD 配置生成修复、集群服务地址兼容及独立工具镜像。CUDA/Ascend 均通过真实 `thorough` 测量、生成 DGD、自动部署与 API 回归，见 [M08 证据](../../m08/advanced/README.md)。复用这些成果，不从零重新实现，也不把功能通过当作配置最优、画像准确或生产 SLA 达标。

本轮采用的实施顺序（未认证项继续保留）：

1. 审计 Planner 的设备资源计数、配置和 Kubernetes 读写路径，保证伸缩保留厂商资源、镜像与部署语义。
2. 接入平台遥测及健康信息，区分有效零值、不可用、过期和错误；核对现有 exporter 与引擎指标。
3. 按平台/型号、驱动/SDK、引擎、模型及拓扑固定画像身份，验证 Profiler 搜索空间与测量结果；没有可靠数据库时采用实测，不借用其他平台的性能曲线。
4. 在 CUDA/Ascend 集群验证动态负载下的 Planner 扩缩容、资源回收和延迟目标；正式性能比较先固定工作负载、目标及资源预算。MetaX 可继续本机指标和工具验证，集群回归等待环境。
5. 功耗读取和控制独立能力门控、独立验收；未验证写入能力不启用控制。完成后交给 M10 重建最终镜像并做全量回归。

M08 保留的 Ascend DRA 环境等待、MetaX 集群缺失和 ADXL/HCCL 动态注册压力问题继续单独跟踪，不因切换到 M09 而关闭。

## 历史实施进度（2026-10-15）

KV 验收已使用引擎容量和 Worker 指标；M01 只读取静态设备/软件身份。MetaX 动态遥测、功耗 API、Profiler/Planner 画像与伸缩尚未实现。

证据见 [平台验收](../../m01/metax-platform.md)、[基础推理](../../platforms/metax/metax-bringup.md)、[KV 路由](../../platforms/metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

## 当前功能与厂商绑定

Planner 使用负载、延迟、容量及部署状态调整资源；Profiler 生成搜索空间、运行测量并产出配置。硬件类型既影响资源计数，也影响 TP/DP/EP 配置可行性与性能曲线。

适配前 `planner/monitoring/dgd_services.py` 和 `profiler/utils/config.py` 直接使用 NVIDIA 资源键。本轮已替换这两条运行路径，仍保留 CUDA 模板默认值和旧版行为；厂商平台必须明确提供匹配的 worker 身份、镜像和资源。

Power Agent 已有 `Actuator` Protocol 与 NvmlActuator/DcgmActuator，不需要重建功耗服务。`power_agent.py` 的设备/PID 发现仍有 NVML 依赖。`common/config_dump/system_info.py` 本轮已接入 exporter/平台只读诊断，保留 CUDA 原有 nvidia-smi 回退。

## 目标设计

设备观测由 TelemetryProvider 输出规范化值；引擎队列、KV 与 token 指标继续由 M03 提供。每项指标带有可用性和时间戳，未知/失效与有效零值区别处理。

| 数据 | 规范化要求 |
| --- | --- |
| 内存 | total/used/available 用 bytes，区分设备总内存与引擎 KV 容量 |
| 利用率 | 保留测量含义和采样区间；不能把厂商不同定义的值当作相同计算能力 |
| 健康 | 规范化 severity/reason 并保留厂商错误码；影响 readiness 的规则明确 |
| 功耗 | 测量值、配置上限、有效上限、可设置范围分别表示，单位 W |
| 资源 | 使用 M08 的平台/数量/分配单位，未知不当作零 |

功耗写入扩展现有 Actuator；设备 UUID 与进程归属必须在读 PID 和写功耗时一致。仅支持读取遥测的平台不声明 `power.control`。沿用单写入者、恢复默认值和失败处理原则，不能将“计算出目标上限”当作硬件已成功应用。

Profiler 搜索空间先根据平台能力过滤无效 precision、parallelism、PD/KVBM 组合，再测量可行候选。画像键至少包含平台/型号、驱动/SDK、引擎/插件、模型 revision、精度、并行度、KV 配置和拓扑。

Planner 只在该平台的有效画像覆盖范围内使用预测；缺少数据时明确进入测量或受限策略，不使用 NVIDIA 曲线作为其他设备的隐式默认。AISimulate 等外部模型如果未支持认证组合，需要外部扩展或独立实测画像，不能只改 GPU 名称。

## 原设计文件计划（并非全部已实现）

| 文件 | 具体修改 |
| --- | --- |
| [system_info.py](../../../components/src/dynamo/common/config_dump/system_info.py) | 通过 provider 获取设备/版本信息；CPU-only 与缺失 SDK 可明确返回 unavailable |
| [power-agent/actuator.py](../../../deploy/power-agent/actuator.py) | 保留 Actuator，增加厂商实现选择和能力校验；明确写入结果 |
| [power_agent.py](../../../deploy/power-agent/power_agent.py) | 将 NVML 设备/PID 发现委托 provider，保持 UUID 校验和恢复流程 |
| [power-agent values](../../../deploy/helm/charts/power-agent/values.yaml) | 平台镜像、设备访问、nodeSelector 和 actuator 配置；其模板同步消费 |
| [dgd_services.py](../../../components/src/dynamo/planner/monitoring/dgd_services.py) | 使用 M08 资源描述，替换硬编码计数；未知设备数阻止依赖该值的规划 |
| [kubernetes_api.py](../../../components/src/dynamo/planner/connectors/clients/kubernetes_api.py) | 读写平台字段，缩放时保持设备资源映射与功耗语义 |
| [search_space_autogen.py](../../../components/src/dynamo/profiler/utils/search_space_autogen.py) | 用平台容量/拓扑/引擎能力生成合法搜索空间 |
| [profiler/utils/config.py](../../../components/src/dynamo/profiler/utils/config.py) | 更新正确平台的资源键，避免新增 NVIDIA 键造成双重请求 |
| [vLLM agg 模板](../../../components/src/dynamo/profiler/templates/dgd/vllm/agg.yaml)、[disagg 模板](../../../components/src/dynamo/profiler/templates/dgd/vllm/disagg.yaml) | 将镜像/资源交给平台配置解析；其他引擎模板同样审计 |
| [aic_adapter.py](../../../components/src/dynamo/planner/core/perf_model/aic_adapter.py) | 检查外部预测模型支持范围，拒绝未知平台的无依据映射 |
| [planner_config.py](../../../components/src/dynamo/planner/config/planner_config.py) | 校验功耗能力、画像和规划模式的组合，不让未支持模式进入循环 |

## 原设计新增文件计划（以本轮交付为准）

| 拟新增路径 | 内容 |
| --- | --- |
| `components/src/dynamo/common/platform/telemetry.py` | 规范化设备观测契约与可用性 |
| `plugins/platforms/cuda/src/dynamo_platform_cuda/telemetry.py` | NVIDIA SDK 查询实现，收敛现有 NVML 观测 |
| `plugins/platforms/ascend/src/dynamo_platform_ascend/telemetry.py` | 经验证的 Ascend SDK 查询与单位映射 |
| `plugins/platforms/metax/src/dynamo_platform_metax/telemetry.py` | 经验证的 MetaX SDK 查询与单位映射 |
| `deploy/power-agent/platform_registry.py` | Actuator/provider 选择及 capability guard |
| `deploy/power-agent/actuators/{ascend,metax}.py` | 功耗/设备原语有证据后实现；不支持写入时不注册控制能力 |
| `components/src/dynamo/profiler/utils/platform_profile.py` | 性能画像键、版本及有效范围 |
| `components/src/dynamo/planner/core/perf_model/profile_compat.py` | 校验输入画像与当前部署软件/硬件组合 |
| `components/src/dynamo/planner/tests/unit/test_platform_resources.py` | 设备计数、未知值与错误画像拒绝 |
| `tests/platforms/test_telemetry_planner.py` | 真机指标、同平台扩缩容与 SLO；功耗测试单独能力门控 |

## 实施与验收

先完成资源计数与基础遥测，随后采集原生引擎和 Dynamo 的性能差异，再接入有效画像和 SLO 规划；功耗控制在最后独立验收。实时指标变化不能替代画像版本兼容检查。

验证缺失指标、采样延迟、设备重枚举、同一 UUID 的 PID 归属、功耗写失败、Profiler 模板生成、扩缩容计数和画像过期。性能比较明确模型/精度/负载/硬件口径，不要求不同厂商绝对数值一致。

厂商 exporter、SDK 查询、功耗权限及模型预测能力仍需版本与硬件证据。源码存在 NVIDIA 实现不代表其他平台可以直接复用其指标含义。
