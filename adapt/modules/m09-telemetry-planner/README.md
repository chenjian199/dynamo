<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M09 遥测与 Planner Profiler

状态：部分基础指标可用，平台适配待实现（MetaX 当前阶段）。依赖 M01/M03/M08；功耗与性能建模分为可独立交付的子能力。

## 实施进度（2026-10-15）

KV 验收已使用引擎容量和 Worker 指标；M01 只读取静态设备/软件身份。MetaX 动态遥测、功耗 API、Profiler/Planner 画像与伸缩尚未实现。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

## 当前功能与厂商绑定

Planner 使用负载、延迟、容量及部署状态调整资源；Profiler 生成搜索空间、运行测量并产出配置。硬件类型既影响资源计数，也影响 TP/DP/EP 配置可行性与性能曲线。

`planner/monitoring/dgd_services.py` 定义 `GPU_RESOURCE_KEY = nvidia.com/gpu`。`profiler/utils/config.py` 更新模板资源时也直接写入该键，模板包含 NVIDIA 镜像和资源。只改 Operator 会漏掉自动生成和规划链路。

Power Agent 已有 `Actuator` Protocol 与 NvmlActuator/DcgmActuator，不需要重建功耗服务。`power_agent.py` 的设备/PID 发现仍有 NVML 依赖。`common/config_dump/system_info.py` 使用 nvidia-smi，诊断信息也需平台化。

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

## 现有文件修改清单

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

## 拟新增文件清单

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
