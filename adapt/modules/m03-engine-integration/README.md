<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M03 引擎与 KV 事件接入

状态：部分完成（MetaX 当前阶段）。依赖 M01/M02；PD 协议由 M05 负责，设备内存由 M04 负责。

## 实施进度（2026-10-15）

C500 上 Qwen3-0.6B 聚合文本推理与单机双 Worker 的真实 KV 路由已验收；包含事件、前缀选择、淘汰和清空。已接入 M01 预检查及平台注册；TP/DP 扩展、PD、多模态及其他高级组合尚未验证。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

## 当前功能与实际入口

通用后端已有 `BaseEngine`、`LLMEngine`、`RawEngine` 和 Python `Worker`。Python Worker 把配置传给 `_backend.Worker`，经 `lib/bindings/python/rust/backend.rs` 进入 `lib/backend-common`。LLM 输入为 token，输出是含结束原因与用量的流式 chunk。

既有 vLLM 入口通过 `main.py::worker()`、`WorkerFactory` 创建不同用途 Worker，并通过 `register_vllm_model()` 注册。SGLang 的 `main.py::worker()` 分派到 `init_llm.py`、`init_multimodal.py` 等入口。不能只修改通用 Worker 就认为所有现有入口都已接入。

KV 事件有 `ZmqSource` 和 `PushSource` 两种契约；`ComponentSnapshot` 承载 per-rank 使用块数与容量。`kv_cache_hit_rate=None` 和 `0.0` 意义不同。vLLM 的 `InstrumentedScheduler` 继承内部 `AsyncScheduler`，与厂商调度器的组合是实际兼容风险。

## 目标设计

保留 engine 与 platform 的两级选择。第一阶段在现有 vLLM/SGLang 入口加入统一的平台 preflight、注册数据和 hook；不为每家厂商复制 main/handlers，也不要求同一提交完成全后端迁移。

新增 `EnginePlatformHooks` 职责：验证 engine/software-stack 组合、配置引擎平台、安装版本限定的事件/指标 hook、返回有效能力、执行卸载。模型执行继续由厂商引擎插件承担。无法兼容的调度器扩展要停止启用相应指标能力，不覆盖厂商调度器以冒充成功。

生命周期按现有契约执行：`from_args → start → metrics/snapshot 绑定 → generate/abort → drain → cleanup`。`cleanup` 必须能处理部分初始化；PushSource 生产线程必须先停止再释放 publisher。`is_quiescent` 的现有轮询范围和预算不能在新平台路径中悄悄改变。

注册前确认模型配置、tokenizer、实际 KV block size、物理 KV 容量和 router-visible DP rank。多个 DP rank 分别报告，不能用进程总量冒充单 rank 容量。没有真实 KV 事件来源时只声明可用的基础路由能力。

高级能力拆分为 LoRA、量化、推测解码、MoE/EP、多模态等条件项；每项记录引擎、模型和配置约束，而不是统一 `supports_advanced=true`。

| 子能力 | Dynamo 的适配与校验责任 | 外部引擎责任 |
| --- | --- | --- |
| LoRA | 在 worker_factory 的加载/卸载路由保持适配器身份、失败回滚和缓存隔离；验证多请求并发 | 对应平台的 LoRA 算子、权重格式及动态加载 |
| 量化 | args 与模型注册携带实际精度；PD/KVBM 校验 KV dtype 和必要 scale 元数据 | 量化权重加载、计算内核及支持模型 |
| 推测解码 | 校验 draft/target 的配置、设备需求、用量统计及 PD 组合；不重写采样算法 | draft/target 执行、验收算法及平台 kernel |
| MoE/EP | 注册实际 DP 容量、并行配置与可用控制能力；动态扩缩容另验收 | 专家通信、算子、负载均衡和并行执行 |
| 多模态 | 复用 M07 的媒体描述、stage 生命周期和传输完成语义 | 对应编码器/模型执行和平台算子 |

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [engine.py](../../../components/src/dynamo/common/backend/engine.py)、[worker.py](../../../components/src/dynamo/common/backend/worker.py) | 接收 M01 平台结果、校验有效能力；保留既有生命周期和请求形状 |
| [publisher.py](../../../components/src/dynamo/common/backend/publisher.py) | 复用 ZmqSource/PushSource 与 ComponentSnapshot；必要扩展明确缺失值语义 |
| [backend.rs](../../../lib/bindings/python/rust/backend.rs)、[Rust engine.rs](../../../lib/backend-common/src/engine.rs) | 跨语言传递 hook 结果及注册信息，和 M01 共同维护 |
| [vllm/main.py](../../../components/src/dynamo/vllm/main.py) | 在引擎创建前调用 preflight；在注册模型时携带有效平台能力 |
| [vllm/worker_factory.py](../../../components/src/dynamo/vllm/worker_factory.py) | 在 encode/prefill/decode 等真实分支绑定对应 hook，统一清理 |
| [vllm/args.py](../../../components/src/dynamo/vllm/args.py) | 校验厂商参数与 scheduler_cls；不强制覆盖已有自定义调度器 |
| [instrumented_scheduler.py](../../../components/src/dynamo/vllm/instrumented_scheduler.py) | 在可组合位置提取事件/统计 hook，保留调度语义；版本不兼容明确报错 |
| [vllm/cache_info.py](../../../components/src/dynamo/vllm/cache_info.py) | 以实际引擎布局返回 block size/容量，不假定 CUDA 引擎结果 |
| [sglang/main.py](../../../components/src/dynamo/sglang/main.py)、[init_llm.py](../../../components/src/dynamo/sglang/init_llm.py) | 接入 preflight 和 hook；保持 snapshot/GMS 启动顺序 |
| [sglang/register.py](../../../components/src/dynamo/sglang/register.py)、[capacity.py](../../../components/src/dynamo/sglang/capacity.py)、[publisher.py](../../../components/src/dynamo/sglang/publisher.py) | 统一平台信息、per-rank 容量与事件来源，保留引擎特定实现 |

如果需要改变请求/响应字段，先核对 [协议类型](../../../lib/llm/src/protocols)、预处理、引擎转换和响应投影的现有行为，并补充端到端兼容用例；基础硬件接入优先不改变请求协议。

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `components/src/dynamo/common/backend/platform_hooks.py` | 引擎平台 hook 契约与集中分派 |
| `components/src/dynamo/vllm/platform_hooks.py` | vLLM 配置、调度器与事件兼容层；不包含厂商 SDK 实现 |
| `components/src/dynamo/sglang/platform_hooks.py` | SGLang 配置和事件兼容层 |
| `plugins/platforms/ascend/src/dynamo_platform_ascend/engine_hooks.py` | 已验证引擎版本的 Ascend hook |
| `plugins/platforms/metax/src/dynamo_platform_metax/engine_hooks.py` | 已验证引擎版本的 MetaX hook |
| `components/src/dynamo/common/backend/tests/test_platform_lifecycle.py` | 部分启动失败、取消、producer 停止、清理幂等性 |
| `components/src/dynamo/vllm/tests/test_platform_scheduler_compat.py` | 厂商 scheduler 与指标 hook 的组合/拒绝行为 |
| `tests/platforms/test_engine_contract.py` | 同一契约在不同平台镜像分别执行 |

## 实施与验收

先运行厂商原生引擎，再接入 Dynamo 基础服务，随后接入 KV 事件与容量，最后验证高级能力和调度器扩展。用 mock 验证生命周期只能证明控制流，真实事件和容量必须在硬件上核对。

验收覆盖：流式结束、并发、取消、加载失败、模型注册一致性；prefix cache 命中/移除事件；DP rank 和容量；默认/自定义 scheduler；SDK/引擎版本不匹配的失败位置；停止服务后无生产线程持有已释放 publisher。

CUDA 有现有集成基础但需回归；Ascend/MetaX 已有生态调查入口，当前 Dynamo 组合、事件来源和高级功能仍待验证。具体版本锁定归 M02，不在本模块假定任意厂商插件兼容 vLLM 0.28.0。
