<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M01 平台契约与能力注册

状态：详细设计草案。依赖：无；向 M02–M10 提供公共身份、能力和错误语义。

## 当前功能与调用关系

运行时以 Namespace、Component、Endpoint 组织服务，注册 `Instance` 后供客户端发现。`component.rs` 中的 `DeviceType` 目前只有 `Cpu` 与 `Cuda`。`component/endpoint.rs::endpoint_device_type()` 检查 CUDA/NVIDIA 可见设备环境变量，没有明确 CPU 覆盖时返回 CUDA。

设备信息还经过 `DiscoverySpec` 序列化和 PyO3 暴露：`lib/bindings/python/rust/lib.rs` 中的 `device_type` getter 只映射这两类。`push_router.rs::device_aware_candidates()` 主要区分 CPU 和 Accelerator，不能承担平台能力校验。

现有关系为：端点构建 → 推断设备类型 → DiscoverySpec → Instance → Python/路由消费者。适配需要贯通此链，不能只在 Python 增加一个环境变量。

## 目标设计

建议新增无厂商 SDK 依赖的 Rust `dynamo-platform` 契约 crate 与 Python `dynamo.common.platform` 包。平台 ID 使用经校验的字符串，如 `cuda`、`ascend`、`metax`；设备种类独立表示 CPU/Accelerator。ID 可扩展，核心不维护完整型号枚举。

| 拟议结构 | 关键字段与约束 |
| --- | --- |
| `PlatformDescriptor` | `schema_version`、`platform_id`、`plugin_version`、驱动/SDK 版本、认证组合引用 |
| `DeviceDescriptor` | 平台 ID、UUID、可见 ordinal、架构/型号、内存、可选 PCI/NUMA；ordinal 不充当跨进程全局身份 |
| `CapabilityReport` | 能力 ID、`available/unavailable/unknown`、实现者、适用条件、不满足原因；不包含“硬件已认证”的推断 |
| `PlatformError` | 规范化类别、provider、原始错误码、诊断信息；是否可重试由具体操作策略判断 |

能力按组合表达，例如 `pd.transfer` 可要求 engine 版本、connector、dtype、KV layout、网络和并行配置匹配。`gms.vmm`、`device.rdma`、`power.control` 分开声明。文件拷贝中转可提供基础传输，但不能自动声明直接 RDMA。

平台选择拟采用引擎启动参数或部署注入的 `DYN_PLATFORM`。显式值优先；仅旧 CUDA 路径保留受控兼容默认值并给出诊断。公共 CPU 服务不导入厂商包，未选择/未探测不等于 CUDA。

加载流程：解析平台配置 → 加载唯一 provider → 校验插件接口版本 → 探测基础能力 → 校验引擎组合 → 引擎初始化后确认有效能力 → 注册就绪端点。出现配置冲突或必需能力 unknown/unavailable 时启动失败。

## 兼容与错误处理

- 在发现协议中新增带版本的可选平台描述，不直接向旧 `DeviceType` 枚举塞入新值并假定旧客户端可解析。
- 旧 CUDA/CPU 字段保留兼容；新客户端优先读新描述。新非 CUDA 部署要求支持新契约的控制面，不向旧路由器假装 CUDA。
- 未知可选字段可忽略；未知必需版本/能力不得默默降级。旧字段为空也不能推断平台身份。
- 同一进程的平台 provider 一经初始化不可切换；设备故障可以撤销 readiness 或更新状态，但不能在线切换厂商。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [Cargo.toml](../../../Cargo.toml) | 由 M02 注册契约 crate 与依赖版本 |
| [runtime Cargo.toml](../../../lib/runtime/Cargo.toml)、[backend-common Cargo.toml](../../../lib/backend-common/Cargo.toml)、[Python 绑定 Cargo.toml](../../../lib/bindings/python/Cargo.toml) | 引入公共契约依赖，由 M02 检查 feature 隔离与两个 workspace 的版本一致性 |
| [component.rs](../../../lib/runtime/src/component.rs) | 扩展 Instance/Endpoint 的可选平台描述；保留旧字段语义 |
| [component/endpoint.rs](../../../lib/runtime/src/component/endpoint.rs) | 接收显式平台信息，集中构造注册描述；替换新平台路径的 CUDA 默认推断 |
| [discovery/mod.rs](../../../lib/runtime/src/discovery/mod.rs) | 扩展 DiscoverySpec 与转换分支，补默认值、版本检查及序列化往返 |
| [push_router.rs](../../../lib/runtime/src/pipeline/network/egress/push_router.rs) | 使用规范化 CPU/Accelerator 分类；不从未知平台回退为 CUDA；不增加跨厂商选路 |
| [Python rust/lib.rs](../../../lib/bindings/python/rust/lib.rs) | 暴露平台描述及端点配置入口，保留已有 `device_type` getter 的兼容边界 |
| [backend.rs](../../../lib/bindings/python/rust/backend.rs) | 将 WorkerConfig/EngineConfig 平台信息映射到 Rust Worker |
| [Rust Worker](../../../lib/backend-common/src/worker.rs)、[Rust EngineConfig](../../../lib/backend-common/src/engine.rs) | 在引擎启动及端点注册之间校验和发布平台信息 |
| [Python Worker](../../../components/src/dynamo/common/backend/worker.py)、[Python EngineConfig](../../../components/src/dynamo/common/backend/engine.py) | 增加可选平台配置与有效能力结果，M03 负责具体引擎接入 |

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `lib/platform/Cargo.toml`、`lib/platform/src/lib.rs` | 契约 crate，只依赖公共类型和序列化库 |
| `lib/platform/src/descriptor.rs` | 平台、设备及软件栈身份 |
| `lib/platform/src/capability.rs` | 能力状态、约束和组合校验 |
| `lib/platform/src/error.rs` | 跨语言可映射的错误结构 |
| `components/src/dynamo/common/platform/__init__.py` | 稳定导出，禁止顶层加载厂商 SDK |
| `components/src/dynamo/common/platform/{descriptor,capabilities,registry}.py` | Python 类型、条件校验、provider 注册和按需加载 |
| `components/src/dynamo/common/platform/preflight.py` | 集中启动检查，返回结构化报告 |
| `lib/platform/tests/descriptor_compat.rs` | 旧字段/新字段/未知版本兼容测试 |
| `components/src/dynamo/common/platform/tests/test_registry.py` | 单平台选择、重复注册、未知平台、按需导入测试 |

## 实施与验收

先定义序列化样例和错误分类，再完成 CUDA/CPU 描述映射；随后贯通 Python → Rust → discovery → 客户端；最后接入厂商能力探测。先发布契约可用性，不先宣称厂商硬件可用。

验收至少包括：CPU-only import、CUDA 兼容、未知平台明确失败、配置冲突、旧描述可读、新非 CUDA 描述不误判、缺失必需能力不进入 ready、重复初始化一致性。

Ascend/MetaX 的设备 UUID、拓扑与 SDK 版本查询由具体 provider 验证。详细 SDK 函数映射和最低控制面版本是实施前需要锁定的事项。
