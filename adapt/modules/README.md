<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# 模块适配详细设计

这些文档基于 Dynamo `v1.5.1`（`2093662a4d4e5fd318732935213dde08dc7be818`）源码分析，面向同厂商部署。每个模块分别说明当前功能、实际调用关系、目标设计、现有文件修改、新文件建议、实施步骤和验收条件。

当前进度已按 2026-10-15 的实现与验收更新。M01 第一版完成 MetaX 平台接入；M02/M03/M10 部分完成，其余工作见各模块状态。“拟新增文件”仍表示设计计划，不应视为已经存在。硬件结论限定于记录中的平台、模型和配置，详见 [M01 验收](../metax/metax-platform.md)。

| 模块 | 详细设计 | 主交付 | 当前进度 |
| --- | --- | --- | --- |
| M01 | [平台契约与能力注册](m01-platform-contract/README.md) | 平台身份、能力条件、注册与兼容规则 | 第一版完成：MetaX 身份/能力/发现；CUDA 真机待验证 |
| M02 | [构建与依赖隔离](m02-build-packaging/README.md) | features、依赖配置、镜像与版本锁定 | 部分完成：官方镜像 + editable；正式产物待实现 |
| M03 | [引擎与 KV 事件](m03-engine-integration/README.md) | 引擎接入、生命周期、路由事件与容量 | 部分完成：基础文本 + 真实 KV 路由 |
| M04 | [设备运行时与内存](m04-device-memory/README.md) | 分配、注册、上下文、流和事件 | 待实现 |
| M05 | [PD 协调与数据传输](m05-pd-transfer/README.md) | 协议策略、传输提供者与完成语义 | 待实现/验证 |
| M06 | [KVBM 与集合通信](m06-kvbm-collectives/README.md) | 缓存调用链、布局内核、跨语言桥接 | 待实现 |
| M07 | [GMS 与多模态](m07-gms-multimodal/README.md) | VMM 扩展、共享恢复、媒体数据面 | 待实现 |
| M08 | [Kubernetes 部署](m08-kubernetes/README.md) | 平台配置、资源解析、调度和生成产物 | 待实现 |
| M09 | [遥测与 Planner](m09-telemetry-planner/README.md) | 设备观测、功耗、性能画像与伸缩 | 基础引擎指标可用；平台遥测/Planner 待实现 |
| M10 | [测试与认证](m10-validation/README.md) | 契约测试、硬件矩阵与证据规范 | 部分完成：契约及当前硬件冒烟验收 |

## 公共设计约定

1. 每个部署只选择一个加速平台；CPU 控制面可以独立运行。平台配置不会启用跨厂商 P/D、TP/EP 或混合调度。
2. 引擎和平台分别配置。`vllm + ascend` 是一个需要版本验证的组合，不新增一套完全复制的 Dynamo 主流程。
3. 各模块引用 M01 的平台身份与能力结构，不各自维护一份厂商枚举或设备探测逻辑。
4. 运行时能力状态使用 `available / unavailable / unknown`，与支持矩阵的“源码已有/待验证/已验证”等研发证据状态分开；两者不能互相替代。
5. 公共契约只含厂商无关类型；实现拥有厂商句柄。Python 平台包通过注册入口按需加载；Rust 当前只提供 SDK 无关契约；设备 provider 的编译期 features 与实现选择由 M02/M04 后续扩展，不承诺稳定 Rust 动态 ABI。
6. M01 文档已列出实际 API 和 schema 1 兼容边界；其余未实现接口仍属拟议 API。

## 共享文件的责任边界

| 位置或契约 | 主责模块 | 其他模块如何使用 |
| --- | --- | --- |
| 已实现 `lib/platform/src/lib.rs` | M01 | M04/M05/M06 引用公共类型，不反向依赖业务模块 |
| 拟新增 `lib/platform/src/{device,memory,event}.rs` | M04 | 在同一契约 crate 扩展设备接口，不依赖 `lib/memory` 的具体实现 |
| 已实现 `components/src/dynamo/common/platform/` | M01 | M03/M07/M09 注册各自能力；M01 管加载、身份及组合校验 |
| 引擎 `start/generate/abort/cleanup`、KV 事件 | M03 | M05/M07 增加必要 hook，保持相同生命周期 |
| 内存对象与事件所有权 | M04 | M05 维护传输注册租约，M06 管块占用，M07 管 GMS 映射 |
| PD connector 协议与取消语义 | M05 | M03 提供引擎 hook，M06 不重复实现 PD 握手 |
| Operator 平台配置与资源计数 | M08 | M09 使用其规范化字段和语义，不再推断资源名 |
| `Cargo.toml`、Python 依赖、容器模板 | M02 | 其他模块提出依赖，由 M02 统一配置与锁定 |
| 跨模块认证与证据格式 | M10 | 各模块提交自身契约测试和硬件结果 |

## 推进顺序

先实现 M01/M02 的最小公共契约及 CUDA 回归，再接入 M03 的基础推理与 M08 的基础部署。M04 原语验证与 M05 引擎原生 PD 验证可以分别开展；M06 使用经过验证的实际内存/传输路径。M07 的 GMS 与多模态分别交付，M09 的基础遥测与高级功耗也分别交付。M10 从第一阶段开始执行。

每个模块的“待验证”事项是该功能的具体依赖条件，不自动阻塞其他无关工作。必须能力缺失时拒绝启用；显式降级记录在 [支持矩阵](../support-matrix.md) 中。
