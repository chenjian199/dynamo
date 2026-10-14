<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Dynamo 多厂商生态适配设计

状态：总体设计草案。基线：Dynamo `v1.5.1`，提交 `2093662a4d4e5fd318732935213dde08dc7be818`。设计日期：2026-10-14。

本设计将硬件与厂商依赖收敛到明确的适配边界，使同一套 Dynamo 核心代码能够分别部署到 NVIDIA CUDA、Ascend、MetaX 等生态。首批平台用于共同验证接口的通用性，后续厂商通过实现约定接口、提供构建配置和通过契约测试接入。

## 范围与完成定义

### 部署范围

- 每个推理部署选择一个厂商平台，部署中的推理 Worker、Prefill、Decode 及参与推理的编码器使用该平台经过验证的软件栈。
- 支持该平台内的单卡、多卡、多节点和 P/D 分离；CPU 服务、主机内存和存储不受此限制。
- 核心设计不绑定某一家厂商的某一个型号。具体型号仍需能力探测和硬件认证；同厂商不同型号之间的组合也不自动视为兼容。
- 不设计跨厂商 P/D、跨厂商 TP/EP、跨厂商 KV 格式转换、跨厂商统一性能调度或混合设备通信组。
- 不要求一个 Worker 镜像同时安装所有厂商 SDK，也不要求一个进程同时加载多个设备运行时。

### 功能对等

以经过验证的 CUDA 部署为参照，逐项定义用户可见语义、前提条件和验收方法。功能项包括基础推理、KV-aware 路由、P/D 分离、多层 KV 缓存、弹性伸缩、监控、故障恢复和适用的高级推理能力。

功能对等不要求内部使用相同 SDK，也不表示不同硬件有相同绝对性能。每项能力分别记录：标准实现、等价实现、显式降级、未支持或待验证。经主机内存中转的数据传输可以验收数据正确性，但不能同时声明完成设备直连 RDMA 的性能能力。

若硬件或 SDK 缺少必要原语，该能力保持未支持或待验证，进入厂商依赖清单。其他模块可以继续交付，但该平台不能因此被标记为全功能对等。

## 当前源码基础

| 观察 | 源码证据 | 设计影响 |
| --- | --- | --- |
| 运行时设备类型只有 CPU/CUDA，端点默认推断为 CUDA | [component.rs](../lib/runtime/src/component.rs)、[endpoint.rs](../lib/runtime/src/component/endpoint.rs) | 增加平台身份及能力描述，避免仅靠 CUDA 环境变量推断 |
| 已有统一引擎生命周期与 KV 事件接口 | [engine.py](../components/src/dynamo/common/backend/engine.py)、[publisher.py](../components/src/dynamo/common/backend/publisher.py) | 复用既有契约；具体引擎入口仍逐一梳理，不假设已经全部迁移到统一入口 |
| vLLM 的 PD 协调已区分 NIXL 与 Mooncake 等协议 | [kv_connector_protocols.py](../components/src/dynamo/vllm/kv_connector_protocols.py) | 扩展已有协议策略，避免在 handler 中增加厂商分支 |
| `InstrumentedScheduler` 继承 vLLM 内部调度器 | [instrumented_scheduler.py](../components/src/dynamo/vllm/instrumented_scheduler.py)、[args.py](../components/src/dynamo/vllm/args.py) | 厂商调度器与指标扩展必须验证共存关系 |
| 设备内存与固定页内存直接使用 cudarc | [device.rs](../lib/memory/src/device.rs)、[pinned.rs](../lib/memory/src/pinned.rs) | 抽象运行时、内存、流、事件和错误类型 |
| KVBM 已有集合通信 trait | [collectives/mod.rs](../lib/kvbm-engine/src/collectives/mod.rs) | 优先扩展 `CollectiveOps`，不重复建立同义接口 |
| KVBM Python 入口仍引用 dynamo-llm 的 block-manager | [Cargo.toml](../lib/bindings/kvbm/Cargo.toml)、[worker.rs](../lib/bindings/kvbm/src/block_manager/vllm/connector/worker.rs) | 必须追踪新旧目录的实际调用关系，不能仅修改独立 kvbm crate |
| Python 事件句柄被作为 CUDA event 传入 Rust | [connector_worker.py](../lib/bindings/kvbm/python/kvbm/vllm_integration/connector_worker.py)、[worker.rs](../lib/bindings/kvbm/src/block_manager/vllm/connector/worker.rs) | 跨语言接口需要携带平台、类型、所有权与完成语义 |
| GMS 已有 VMMDevice 抽象及 CudaVMM；当前 XPU 工厂分支仍未实现 | [device.py](../lib/gpu_memory_service/common/vmm/device.py)、[VMM 工厂](../lib/gpu_memory_service/common/vmm/__init__.py)、[cuda_utils.py](../lib/gpu_memory_service/common/vmm/cuda_utils.py) | 复用已有接口，先验证厂商 VMM/共享句柄原语及抽象之外的 CUDA 依赖 |
| Operator 部分支持自定义资源，但计数和特殊路径仍有 NVIDIA 假设 | [graph.go](../deploy/operator/internal/dynamo/graph.go)、[dgd_services.py](../components/src/dynamo/planner/monitoring/dgd_services.py)、[shared_v1beta1.go](../deploy/operator/internal/webhook/validation/shared_v1beta1.go) | 通用资源名必须贯穿生成、校验、计数和伸缩 |
| 容器已有 XPU 配置；Python extras 含 CUDA 专用依赖 | [context.yaml](../container/context.yaml)、[pyproject.toml](../pyproject.toml) | 复用构建体系，拆分平台依赖；已有配置不代表完整平台认证 |

## 架构边界

参见 [适配架构图](architecture.svg)。以下接口名描述职责，最终类型签名、注册方式和落点由对应模块细化确定。

### 核心层

保留 Frontend、服务发现、请求生命周期、路由策略、KV 逻辑管理和 Planner 的公共算法。核心只消费规范化的注册信息、事件、指标与能力，不调用厂商 SDK。

引擎维度与平台维度独立。例如 `engine=vllm`、`platform=ascend` 描述一个组合；是否可运行取决于该组合的版本矩阵与能力条件。TensorRT-LLM 等引擎可保留其已有平台范围，不要求所有引擎与所有平台做笛卡尔积适配。

### 平台描述与能力注册

建议定义 `PlatformDescriptor`、`DeviceDescriptor` 和 `CapabilityReport`，至少包含以下信息：

| 类别 | 字段语义 |
| --- | --- |
| 平台身份 | 平台 ID、插件版本、接口版本、驱动/SDK 版本 |
| 设备身份 | 设备 UUID、架构、型号、进程可见序号、主机拓扑信息 |
| 引擎组合 | 引擎及插件版本、模型配置指纹、精度、并行配置 |
| 能力条件 | 支持状态、实现提供者、限制条件、验证范围、降级路径 |
| 诊断信息 | 探测结果、缺失依赖、原始厂商错误码及规范化错误类别 |

同名能力可能在不同型号、SDK、模型或组合配置下不可用，不能只用全局布尔值表示。身份使用可扩展平台 ID；已认证型号的列表作为验证记录，不成为核心代码中的型号分支表。

启动过程：加载所选平台 → 校验接口/版本组合 → 探测设备能力 → 校验所请求的功能组合 → 初始化引擎和资源 → 完成就绪检查 → 发布有效注册信息。未满足必要能力时在接收请求前失败。

扩展现有服务发现字段时，定义序列化版本及旧字段兼容规则；旧客户端不能把未知平台默认为 CUDA。部署级平台校验用于发现误配置，不引入跨厂商路由或运行中平台切换。

### 六类适配职责

| 适配职责 | 核心接口要求 | 不应承担的责任 |
| --- | --- | --- |
| `EngineAdapter` | 复用 `BaseEngine/LLMEngine` 生命周期；生成、取消、健康、容量、KV 事件、控制及指标 | 在 Dynamo 中重复实现 Attention、量化和模型算子 |
| `DeviceRuntime` | 枚举与上下文；设备/主机注册内存；异步拷贝；流与事件；拓扑；可选 IPC/VMM | 在核心层暴露 CUDA 特有句柄或无类型裸指针 |
| `TransferProvider` | 内存注册、元数据、传输提交、完成、失败、取消与资源释放；能力/方向选择 | 将所有 KV connector 的协议一律当作 NIXL 协议 |
| `CollectiveProvider` | 复用并补齐 KVBM 的 `CollectiveOps`，实现所需广播等语义 | 重写推理引擎内部 TP/EP 通信栈 |
| `TelemetryProvider` | 规范化设备、内存、利用率、健康、拓扑和可选功耗控制 | 将缺失指标填成零，或假定不同厂商利用率含义完全相同 |
| `DeploymentProvider` | 平台资源映射、镜像、可见设备环境、调度约束、DRA/Device Plugin 和检查规则 | 为每家厂商复制一套完整 Operator 或更名全部 CRD |

这些职责不要求全部实现为运行时动态插件。Python 可使用注册机制与按需导入；Rust 优先用 trait、独立 crate 和 Cargo features；Go 侧使用可扩展 provider/配置映射。需要动态链接的厂商库放在实现内部，独立加载协议若确有需要再定义版本化 C ABI。

### 内存与异步安全契约

- 内存描述应记录平台/设备、空间类型、长度、对齐、所有权和关联运行时；不把任意地址当作可注册设备内存。
- 完成通知必须区分提交成功、设备操作完成、远端可用。不同 API 的完成含义由适配层规范化。
- 取消请求不意味着 DMA 已停止。内存、注册和事件必须保留到安全完成或运行时确认可释放。
- 上下文与流的线程归属、可跨线程操作范围和同步责任要明确；不得只替换类型名并沿用 CUDA 假设。
- 对外错误至少区分不支持、版本不兼容、资源不足、设备故障、超时、可重试传输错误和协议错误。
- KV 的层数、dtype、block size、stride、布局、量化元数据和分片信息由引擎适配提供；同厂商部署也需要验证两端兼容性。

### PD 与 KVBM 分开验收

PD connector 负责 Prefill/Decode 的协调与 KV 交接；KVBM 负责多层缓存的物理管理和策略执行。厂商引擎已有 PD 或 KV pool，不能直接视为 Dynamo KVBM 已完成适配。

优先复用可满足要求的 NIXL backend 或厂商原生传输实现。仅在当前抽象不能表达真实需求时增加边界；不强制为所有平台实现同一种传输库。主机内存中转可作为显式功能路径，但零拷贝、设备 RDMA、直接存储分别验收。

KVBM 的逻辑策略尽量保留；改造重点是实际调用链中的内存、布局、拷贝内核、事件桥接、传输与集合通信。对 `lib/llm/src/block_manager`、其 `v2` 目录和独立 `lib/kvbm-*` crate 建立调用关系后决定统一落点。

## 平台生态接入

| 平台 | 起点 | 必须核实的外部依赖 |
| --- | --- | --- |
| CUDA | 现有实现作为首个适配实现和回归参照 | 当前镜像、引擎、NIXL/NCCL、驱动与 CUDA 组合 |
| Ascend | 评估选定版本的 vLLM Ascend；其他引擎另列认证组合 | CANN、PyTorch/torch_npu、引擎插件、调度器、传输/通信、内存与 VMM 原语 |
| MetaX | 评估 vLLM-metax 及 MACA 运行环境；其他引擎另列认证组合 | 引擎插件版本、MACA API/ABI、通信、注册内存、IPC/VMM、RDMA 能力 |
| 后续厂商 | 实现所需职责并提供平台配置 | 依照相同契约、版本矩阵和能力测试接入 |

外部材料只用于确定验证入口，不构成 Dynamo 集成通过的证据：

- [vLLM Ascend 版本策略](https://docs.vllm.ai/projects/ascend/en/main/community/versioning_policy.html)：软件栈需要按已验证组合匹配。
- [vLLM Ascend PD 设计](https://docs.vllm.ai/projects/ascend/en/v0.20.2rc/developer_guide/Design_Documents/disaggregated_prefill.html)：不同 connector 有不同 push/pull 流程；此版本文档用于解释协议差异，不指定实际采用版本。
- [vLLM-metax](https://github.com/MetaX-MACA/vLLM-metax) 与 [插件设计](https://github.com/MetaX-MACA/vLLM-metax/issues/4)：插件接入及 CUDA-like 兼容背景，不证明 cudarc、VMM、事件 ABI 全部兼容。
- [NIXL v1.3.2](https://github.com/ai-dynamo/nixl/blob/v1.3.2/README.md)：可扩展传输后端；目标平台仍需单独验证。

## 构建与部署

产物按公共控制面、引擎集成和平台实现分层。一个部署选择一组锁定版本的镜像；同一套代码可以分别产出 CUDA、Ascend、MetaX 镜像。

- 检查公共模块的顶层导入及 Rust 传递依赖，避免未选平台的 SDK 在构建或启动时成为必需依赖。
- 不直接使用含 CUDA extras 的依赖组构建所有平台；定义平台依赖与锁定策略。
- 当前 `lib/memory` 和 `lib/kvbm-physical` 直接依赖 cudarc，需要处理依赖隔离及公共错误类型。
- 复用 `container/context.yaml` 与模板渲染机制；生成产物通过源配置再生成，不手改。
- 保持 CRD API group 兼容，平台化实际资源名称、计数、调度约束和特殊功能校验。
- 非目标平台构建无需下载或加载目标厂商 SDK；硬件相关集成测试在对应 runner 执行。

候选源码落点包括 Python 的平台契约模块、Rust 的平台契约 crate 和厂商实现 crate，以及 Operator 的平台 provider。最终命名与目录结构在 M01/M02 细化中确定，避免提前建立空插件和重复抽象。

## 前期准备

| 准备项 | 必须形成的结果 |
| --- | --- |
| CUDA 基线 | 可复现镜像/配置、模型与功能清单、实际通过记录、性能数据 |
| 厂商软件栈 | 驱动、SDK、引擎、插件、通信/传输库和镜像 digest 的兼容性记录 |
| SDK 原语验证 | 内存、事件、异步拷贝、IPC/VMM、网络传输与集合通信的最小程序和结果 |
| 硬件环境 | 各平台代表性单机多卡环境；跨节点测试所需服务器、NIC 和网络；持续 CUDA 回归环境 |
| 模型与请求集 | 文本、多模态和 MoE 等所需代表性负载；明确精度和输出容差 |
| 厂商协作 | SDK 文档/分发条件、问题升级入口、缺失 API 与版本升级责任 |
| 维护流程 | 模块归属、版本升级策略、持续集成 runner、发布认证标准 |

设计不锁定具体型号，但任何“已支持”结论都必须附带实际测试型号和版本。跨平台功能对比可使用符合各平台能力的相同模型与明确精度；不能隐去量化差异后比较结果。

## 分阶段交付

| 阶段 | 主要内容 | 退出条件 |
| --- | --- | --- |
| P0 设计与准备 | 细化契约、版本矩阵、CUDA 基线和原语 PoC | 关键依赖有证据，首批测试环境与组合明确 |
| P1 通用骨架 | 能力注册、依赖隔离、CUDA 接入同一契约 | 公共包可独立构建；CUDA 既有行为回归通过 |
| P2 基础推理 | 厂商引擎、注册、KV 事件与基础部署 | 流式、取消、并发、健康、路由及容量报告通过 |
| P3 PD | 协议、数据面与资源生命周期 | 同平台单机/跨节点 PD、超时、取消、故障清理通过 |
| P4 KVBM | 内存、内核、跨语言事件和集合通信 | offload/reload、并发回收、压力及 PD 组合通过 |
| P5 运维与高级能力 | Planner、Profiler、遥测、功耗、多模态、GMS | 各已声明能力及组合验收；长稳、故障和升级回归通过 |

阶段允许交叉开展原语验证和模块细化，但不能跳过依赖能力的证据。新增平台首先运行相同契约测试，再执行该平台的硬件套件。具体交付的功能范围以支持矩阵为准。

## 验证原则

- 基础语义：生成、流式结束、取消、并发、错误传播、模型与 tokenizer 配置一致性。
- KV 正确性：哈希与事件语义、布局、块所有权、完成后再释放、回收与重启一致性。复制链路可做字节级检查；跨平台模型输出按约定精度容差判断。
- 性能：分别测量原生引擎和 Dynamo 集成结果；记录 TTFT、TPOT、吞吐、尾延迟、传输和 offload 开销。画像键包括硬件、软件栈、模型、精度、并行方式和拓扑。
- 运维：资源计数、扩缩容、就绪检查、故障恢复、监控缺失与功耗控制的可选性。
- 故障：进程退出、设备错误、传输超时、网络中断、请求取消及注册内存清理。
- 组合：PD 与缓存、LoRA 与路由、多模态与分离式推理等实际承诺组合，不能只验证各开关单独可用。

## 后续设计与治理

M01–M10 的详细设计与逐文件计划已进入 [modules/](modules/README.md)，后续按 [modules.md](modules.md) 的模板持续更新；每次新增证据同步更新 [support-matrix.md](support-matrix.md)。首轮优先收敛 M01/M02，并核实 M03 引擎版本与调度器兼容性。涉及修改请求协议时核对现有字段的端到端语义；进入架构代码变更时按仓库要求准备 DEP。此目录当前作为本地设计材料，不代表已创建上游议题或获得上游接受。
