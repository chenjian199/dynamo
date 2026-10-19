<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# 模块细化与实施工作清单

本清单将总体设计拆为可逐项讨论和实现的工作包。M01–M10 已建立详细设计草案，包含现有功能、适配方案和逐文件计划，详见 [模块详情目录](modules/README.md)。M01 第一版平台契约已接入 MetaX 与 Ascend；M04 基础原语第一版已完成三平台验收；M02/M03/M05/M10 已交付当前配置的部分能力，厂商支持结论按证据记录。每个工作包的验收只覆盖同一厂商平台的部署。

## 依赖顺序

M01 平台契约与 M02 构建是公共基础。M03 引擎、M04 设备运行时和 M08 部署可在契约明确后分别推进；M05 PD 依赖引擎和所选传输路径；M06 KVBM 依赖内存、传输和跨语言事件契约；M07 高级能力按其实际原语依赖推进；M09 运维使用引擎、部署和遥测数据。M10 验证贯穿全部阶段。

依赖关系不要求厂商引擎原生 PD 必须经过 Dynamo 的全部 KVBM 内存层；只为实际调用路径建立依赖。

## M01 平台契约与能力注册

当前状态：第一版完成：MetaX/Ascend/CUDA 身份/能力/发现已做当前配置真机回归。

详细设计：[功能、接口、兼容策略与文件计划](modules/m01-platform-contract/README.md)。

- 源码入口：[component.rs](../lib/runtime/src/component.rs)、[endpoint.rs](../lib/runtime/src/component/endpoint.rs)、[push_router.rs](../lib/runtime/src/pipeline/network/egress/push_router.rs)。
- 工作：确定平台 ID、设备描述、能力条件、版本协商及规范化错误；替换默认 CUDA 推断；明确服务发现与 Python/Rust 绑定字段兼容策略。
- 已交付：`DYN_PLATFORM`、延迟加载的 provider、schema 1、`configure_platform`、发现与 Python 绑定、能力条件检查；MetaX/Ascend 不再标为 CUDA。
- 剩余扩展：各后续模块自己的能力探测；CUDA H800 回归见 M03 验收记录。MetaX/Ascend 需使用支持新契约的控制面。
- 验收：所选平台正确注册；不依赖其他厂商 SDK；不支持的必要能力在就绪前明确失败；旧 CUDA 行为兼容。

## M02 构建与依赖隔离

当前状态：部分完成：官方镜像 + editable；正式产物待实现。

详细设计：[依赖图、产物设计与文件计划](modules/m02-build-packaging/README.md)。

- 源码入口：[pyproject.toml](../pyproject.toml)、[context.yaml](../container/context.yaml)、[memory Cargo.toml](../lib/memory/Cargo.toml)、[kvbm-physical Cargo.toml](../lib/kvbm-physical/Cargo.toml)、[Python 绑定](../lib/bindings/python/Cargo.toml)、[KVBM 绑定](../lib/bindings/kvbm/Cargo.toml)。
- 工作：绘制 SDK 传递依赖；拆分公共依赖与平台 extras/features；确定镜像、wheel、厂商库加载和版本锁定方式。
- 待细化：同一厂商引擎版本与 Dynamo 集成接口如何匹配；平台包注册方式；x86_64/aarch64 产物范围；SDK 分发条件。
- 验收：公共包构建/导入不触发未选 SDK；各平台镜像可复现；已有容器配置由源模板生成。

## M03 引擎与 KV 事件接入

当前状态：本轮 vLLM 聚合模式范围完成：TP/DP、生命周期及高级能力三平台验收；SGLang 暂缓。

详细设计：[真实入口、生命周期、事件与文件计划](modules/m03-engine-integration/README.md)。

- 源码入口：[engine.py](../components/src/dynamo/common/backend/engine.py)、[worker.py](../components/src/dynamo/common/backend/worker.py)、[publisher.py](../components/src/dynamo/common/backend/publisher.py)、[vLLM](../components/src/dynamo/vllm)、[SGLang](../components/src/dynamo/sglang)。
- 工作：核对实际入口与统一契约的关系；接入厂商引擎的生命周期、取消、健康、KV 容量、事件和指标；处理调度器扩展的组合关系。
- 已交付：保留自定义 scheduler 并校验 KV/FPM 兼容性；逐 rank 物理容量、LoRA 缓存隔离、四卡 TP/DP 与高级能力验证见 [M03 验收](m03/README.md)。精确多模态前端选路、PD/独立编码器组合另行验收。
- 验收：流式、并发、取消与清理正确；不支持的模型/精度提前拒绝；KV-aware 路由有真实事件和容量证据。

## M04 设备运行时与内存

当前状态：基础原语第一版完成，三平台真机验收通过；[证据与复现](m04/README.md)。

详细设计：[内存所有权、流与事件、文件计划](modules/m04-device-memory/README.md)。

- 已交付：SDK 无关契约与 CUDA/MACA/ACL provider，拥有/借用内存、host 注册、stream/event、异步复制与有限回收；lib/memory 可禁用 CUDA/NIXL。
- 已验收：三平台复制字节一致性、64 轮分配/注册、owner、context、取消/关闭；错误注入与 SDK 无关测试；CUDA 旧 API 回归。
- 边界：跨设备 P2P 明确拒绝；引擎事件桥接、物理 layout 平台身份和 KVBM executor 接入归 M06；厂商传输注册归 M05；NUMA 性能与长稳另验收。

## M05 PD 协调与数据传输

当前状态：部分完成：三平台单机 vLLM 原生 PD（含 2P3D）已验证；补充 TP1/TP2 双向转换、传输中故障与重启恢复，完整模块尚未完成；[补充证据与长稳结果](m05/completion.md)。

详细设计：[控制面与数据面、传输状态、文件计划](modules/m05-pd-transfer/README.md)。

- 源码入口：[kv_connector_protocols.py](../components/src/dynamo/vllm/kv_connector_protocols.py)、[handlers.py](../components/src/dynamo/vllm/handlers.py)、[SGLang disagg](../components/src/dynamo/sglang/_disagg.py)、[NIXL 内存接口](../lib/memory/src/nixl.rs)。
- 工作：将所选厂商 connector 的真实协议映射到 Dynamo；确定 NIXL backend 或其他传输实现；定义注册、握手、提交、完成和取消语义。
- 已交付：按名称 + 模块解析协议，启动前角色/配置检查，交接前平台/版本/模型/精度/block/并行度契约；正常 PD、取消和参数不匹配三平台验证；2P3D 三平台验证，MetaX NIXL 的 UCX 旧协议崩溃已修复并完成端到端复验。
- 本轮补充：所选主路径完成受限 TP1/TP2 转换、native pending 故障、失败退出/重启恢复及至少 30 分钟资源回收验收（CUDA 4480、MetaX 3680、Ascend 1728 请求）；保留修复前失败对照。
- 剩余：跨节点、Dynamo 自有 TransferProvider/KVBM；MetaX 可选 NIXL 的传输中断 UCX abort 未解决，主路径使用 Mooncake；SGLang 暂缓。
- 验收：同平台单机/跨节点 PD 正确；完成与块释放时序正确；超时、对端退出及取消有明确行为和可回收资源。

## M06 KVBM 与集合通信

当前状态：待实现。

详细设计：[活动调用链、布局与事件桥接、文件计划](modules/m06-kvbm-collectives/README.md)。

- 源码入口：[现有 block-manager](../lib/llm/src/block_manager)、[kvbm-physical](../lib/kvbm-physical)、[kvbm-engine](../lib/kvbm-engine)、[kvbm-kernels](../lib/kvbm-kernels)、[KVBM 绑定](../lib/bindings/kvbm)。
- 工作：确认新旧实现调用图；保留逻辑管理算法；抽象物理布局、拷贝内核、传输与完成通知；扩展 `CollectiveOps`；改造 Python/Rust 事件桥接。
- 待细化：各层缓存的必要语义与平台快路径；引擎张量布局、MLA/MoE 等适用范围；复制与分片场景；错误恢复及缓存隔离。
- 验收：设备/主机/SSD/远端所声明路径可正确回读；数据一致性、块回收、压力、取消及 PD 组合通过。直接 RDMA/存储独立验收。

## M07 GMS 与多模态高级能力

当前状态：待实现。

详细设计：[现有 VMM 抽象、媒体数据面与文件计划](modules/m07-gms-multimodal/README.md)。

- 源码入口：[gpu_memory_service](../lib/gpu_memory_service)、[multimodal](../components/src/dynamo/common/multimodal)、[media_nixl.py](../components/src/dynamo/common/utils/media_nixl.py)、[vLLM Omni](../components/src/dynamo/vllm/omni)。
- 工作：分成 GMS 与多模态两个子工作包；GMS 验证 VMM、IPC 和共享内存生命周期；多模态平台化设备描述、embedding 传输、编解码与缓存。
- 待细化：厂商是否具有必要的 VMM/句柄/恢复原语；CPU 解码与厂商加速的支持边界；编码器与推理阶段的同平台配置约束。
- 验收：声明的 GMS 共享与恢复语义通过；多模态输入、缓存、传输与释放正确。普通重启不得冒充 GMS 等价恢复。

## M08 Kubernetes 与平台部署

当前状态：待实现。

详细设计：[资源语义、API 兼容与生成文件计划](modules/m08-kubernetes/README.md)。

- 源码入口：[graph.go](../deploy/operator/internal/dynamo/graph.go)、[资源发现](../deploy/operator/internal/gpu/discovery.go)、[DRA](../deploy/operator/internal/dra)、[校验](../deploy/operator/internal/webhook/validation/shared_v1beta1.go)、[Helm](../deploy/helm)。
- 工作：统一平台资源映射、资源计数、设备可见性、调度/拓扑约束；接入厂商 Device Plugin/DRA；改造 GMS 和 failover 特殊路径。
- 待细化：资源字段兼容策略、型号约束表达、平台镜像选择、Grove/调度器集成、设备故障和服务就绪关系。
- 验收：生成、准入、调度、计数、伸缩、重建都使用正确资源；CRD API 兼容；不设计混合厂商调度。

## M09 遥测与 Planner/Profiler

当前状态：基础引擎指标可用；平台遥测/Planner 待实现。

详细设计：[指标、功耗、性能画像与文件计划](modules/m09-telemetry-planner/README.md)。

- 源码入口：[planner](../components/src/dynamo/planner)、[profiler](../components/src/dynamo/profiler)、[dgd_services.py](../components/src/dynamo/planner/monitoring/dgd_services.py)、[Power Agent](../deploy/power-agent)、[系统信息](../components/src/dynamo/common/config_dump/system_info.py)。
- 工作：规范化设备指标与健康信息；扩展现有功耗 actuator；平台化资源计数、性能搜索和模板；重建每种认证组合的性能画像。
- 待细化：缺失指标表达、采样频率、单位与指标语义；功耗控制是否可选；画像键与更新策略；SLO 与成本目标。
- 验收：指标不错误归零；按真实资源与画像伸缩；同平台内完成 SLO 验证；功耗能力未经实测不启用。

## M10 契约测试与平台认证

当前状态：部分完成：契约及当前硬件冒烟验收。

详细设计：[测试分层、认证记录与文件计划](modules/m10-validation/README.md)。

- 源码入口：[tests](../tests)、[KVBM 集成](../tests/kvbm_integration)、[故障测试](../tests/fault_tolerance)、[工作流](../.github/workflows)。
- 工作：定义公共契约测试、CUDA 回归、各平台硬件测试和组合用例；锁定可复现环境；将证据关联到能力矩阵。
- 待细化：首批型号、模型、精度、容差、并行度、长稳时长和性能目标；哪些用例每次 PR 执行，哪些定期执行。
- 验收：每个“已验证”结论都有环境、配置、命令、结果和已知限制；未执行或跳过不计作通过。

## 单模块细化模板

后续更新模块设计或新增子能力详情时，继续维护以下内容，并从该模块入口链接过去：

1. 目标、边界、依赖模块和用户可见行为。
2. 当前真实调用链、源码入口、可复用接口与确定的厂商绑定。
3. 接口签名、数据结构、状态机、错误处理、取消和资源所有权。
4. CUDA 实现迁移方式，以及 Ascend/MetaX 的版本组合和能力证据。
5. 具体文件修改清单、兼容策略、构建/配置变更和必要的生成流程。
6. 契约测试、硬件测试、组合测试、性能与故障验收条件。
7. 待确认问题、外部依赖、已知限制、交付状态与结果链接。

设计中的未知项可以保留，但进入依赖该项的实现前必须给出明确行为；不得用空实现、假完成事件或静默降级绕过必要语义。
