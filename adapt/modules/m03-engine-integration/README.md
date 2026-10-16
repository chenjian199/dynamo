<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M03 引擎与 KV 事件接入

状态：本轮 vLLM 聚合模式适配与逐项高级能力验收完成；按用户要求暂缓 SGLang。依赖 M01/M02；PD 协议由 M05 负责，设备内存由 M04 负责。

## 实施进度（2026-10-16）

C500 / vLLM-metax 0.26.0、910B3 / vLLM Ascend 0.23.0 已完成基础推理和双 Worker KV 路由。本轮已实现本地 multiprocessing TP/DP、生命周期回滚、调度器兼容，并在 C500、910B3、H800 三平台验证 LoRA、W8A8、ngram/draft 推测解码、Qwen3-30B-A3B 静态 EP=4 与 Qwen3-VL-2B 聚合图文。H800 回归固定 vLLM 0.24.0。逐项配置与证据见 [M03 验收记录](../../m03/README.md)。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

Ascend 910B3 / vLLM Ascend 0.23.0 已完成 ARM64 editable 构建、M01、基础推理与真实 KV 路由验证；共享改动已在 MetaX 0.26.0 回归。环境、命令、证据及限制见 [华为验收](../../ascend/ascend-bringup.md)。

## 当前功能与实际入口

通用后端已有 `BaseEngine`、`LLMEngine`、`RawEngine` 和 Python `Worker`。Python Worker 把配置传给 `_backend.Worker`，经 `lib/bindings/python/rust/backend.rs` 进入 `lib/backend-common`。LLM 输入为 token，输出是含结束原因与用量的流式 chunk。

既有 vLLM 入口通过 `main.py::worker()`、`WorkerFactory` 创建不同用途 Worker，并通过 `register_vllm_model()` 注册。SGLang 的 `main.py::worker()` 分派到 `init_llm.py`、`init_multimodal.py` 等入口。不能只修改通用 Worker 就认为所有现有入口都已接入。

KV 事件有 `ZmqSource` 和 `PushSource` 两种契约；`ComponentSnapshot` 承载 per-rank 使用块数与容量。`kv_cache_hit_rate=None` 和 `0.0` 意义不同。vLLM 的 `InstrumentedScheduler` 继承内部 `AsyncScheduler`，与厂商调度器的组合是实际兼容风险。

## 目标设计

保留 engine 与 platform 的两级选择。vLLM 复用现有入口、平台 preflight 和模型注册，不为每家厂商复制 main/handlers。SGLang 后续单独接入。

不实施原草案的 `EnginePlatformHooks`：通用 backend 的 `AGENTS.md` 禁止引入新 hook 生命周期。实际实现为 `dynamo.vllm.platform` 中的普通配置/注册工具，生命周期继续由 `setup_vllm_engine` 和 `WorkerFactory` 持有。厂商 SDK 留在 M01 provider，引擎执行由厂商 vLLM 插件承担。自定义 scheduler 不被覆盖；请求 KV/FPM 时，若最终 scheduler 不继承 `InstrumentedScheduler`，在启动引擎进程前明确拒绝。

本轮修复了 DP 指标工厂只更新最后一个 rank 的问题：保存所有 rank 的 logger，分别初始化真实容量。硬件验收把每个 rank 的指标与引擎日志中的物理 KV token 数/block size 对比，不能只检查容量大于零。新增模型 `engine_specific.platform_engine` 元数据记录实际 dtype、量化、TP/DP、EP、LoRA、推测解码和多模态配置，无需新增 Rust 协议字段。

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

## 实际文件与责任

| 文件 | 本轮落地 |
| --- | --- |
| `common/platform/preflight.py`、`providers.py` | 聚合 eager、本地 TP/DP、可见设备检查、独立高级能力条件 |
| `vllm/platform.py` | 有效 scheduler 校验与实际引擎元数据；普通函数，不引入 hooks 生命周期 |
| `vllm/main.py` | 创建失败回滚、有效配置校验及注册 |
| `vllm/worker_factory.py`、`handlers.py` | 资源所有权、部分启动清理、幂等清理及生产线程停止 |
| `vllm/publisher.py` | 每个 DP rank 初始化实际容量 |
| `vllm/tests/test_platform_integration.py` 等 | scheduler、生命周期、容量及厂商版本差异回归 |
| `adapt/m03/` | 三平台手动硬件验收、NFS 模型选择与原始结果 |

本轮没有修改通用 backend 生命周期、Rust binding、引擎调度算法或 SGLang。原草案中的 `platform_hooks.py` / 厂商 `engine_hooks.py` 文件计划撤销。PD、独立编码器传输和动态 EP 控制按各模块边界继续实施。

## 实施与验收

先运行厂商原生引擎，再接入 Dynamo 基础服务，随后接入 KV 事件与容量，最后验证高级能力和调度器扩展。用 mock 验证生命周期只能证明控制流，真实事件和容量必须在硬件上核对。

验收覆盖：流式结束、并发、取消、加载失败、模型注册一致性；prefix cache 命中/移除事件；DP rank 和容量；默认/自定义 scheduler；SDK/引擎版本不匹配的失败位置；停止服务后无生产线程持有已释放 publisher。

支持结论以逐项硬件记录为准。配置能力 `available` 表示当前请求的接入路径启用，不能替代模型就绪与测试认证；本轮维持 eager、聚合生成、本地 multiprocessing DP。Pipeline parallel、Ray/external/hybrid DP、PD、KVBM、独立编码器传输及动态 EP 扩缩容另属后续组合，不由本轮结果自动继承支持。具体版本锁定归 M02，不假定任意厂商插件兼容 vLLM 0.28.0。
