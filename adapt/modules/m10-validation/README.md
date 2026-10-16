<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M10 契约测试与平台认证

状态：部分完成（MetaX 与 Ascend 当前阶段）。覆盖 M01–M09，验证随实施同步推进。

## 实施进度（2026-10-16）

已有基础推理、真实 KV 路由和 M01 契约/真机验收脚本及 JSON 证据。尚未建立完整硬件 CI、多机/多并行配置矩阵、压力与长期稳定性认证。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

Ascend 910B3 / vLLM Ascend 0.23.0 已完成 ARM64 editable 构建、M01、基础推理与真实 KV 路由验证；共享改动已在 MetaX 0.26.0 回归。环境、命令、证据及限制见 [华为验收](../../ascend/ascend-bringup.md)。

## 当前测试体系

仓库已有 Python 单元/集成测试、Rust 测试、KVBM、GMS、故障测试和 Operator 测试；pytest 使用严格 marker。CI 中已有 CUDA 与 XPU 的流程，硬件类型、卡数、显存和模型下载不能由普通单元测试默认假定。

测试需遵循 [tests/AGENTS.md](../../../tests/AGENTS.md)、[pytest 指南](../../../.ai/pytest-guidelines.md) 和 [模型大小约束](../../../.ai/test-model-size-guardrails.md)。现有默认共享 GPU 测试资源约束不能直接复制成所有厂商 runner 的内存额度；新 runner 要有明确配额和测试选择规则。

## 目标测试分层

| 层次 | 内容 | 通过能够证明什么 |
| --- | --- | --- |
| T0 无设备契约 | 注册、序列化、错误、状态机、配置与取消；mock provider | 公共逻辑及边界正确，不能证明 SDK/DMA 正确 |
| T1 构建与依赖 | 平台产物、CPU import、features、依赖一致性、镜像启动 | 软件可构建/加载，不能证明硬件功能 |
| T2 设备原语 | 分配、复制、event、host registration、IPC/VMM、collective | 指定型号/SDK 上的底层能力可用 |
| T3 集成功能 | 推理、KV 事件、PD、KVBM、多模态、GMS | 明确功能在指定组合通过 |
| T4 生产组合 | 故障、长稳、扩缩容、性能、升级与回滚 | 承诺的工作负载和部署范围通过 |

每个“已验证”能力必须同时关联构建标识、环境记录、测试配置、通过结果与已知限制。未选中、跳过、xfail、缺少设备均不能算通过。厂商验收 pipeline 对声明必须支持却跳过的测试应失败，普通无设备 PR job 可以显式跳过硬件用例。

## 关键用例设计

| 模块 | 需要验证的行为 |
| --- | --- |
| M01 | 旧发现描述往返、未知平台/版本、单平台选择、能力冲突、ready 前失败 |
| M02 | 非目标 SDK 不进入依赖/导入、两个 Python 绑定入口、锁文件和镜像一致 |
| M03 | 部分启动失败、cleanup 幂等、真实取消、流式结束、DP 容量、缓存事件 |
| M04 | 越界/对齐、owner 存活、线程 context、DMA 在途取消、释放顺序 |
| M05 | 实际采用远端 KV、P/D 对端失败、超时、释放确认、布局不兼容 |
| M06 | 字节回读、pack/unpack、块回收、多 rank、压力及 PD/offload 组合 |
| M07 | 共享 FD 生命周期、错误设备恢复、客户端退出、多模态缓存与 stage 清理 |
| M08 | 资源冲突、CPU 组件、DRA 计数、alpha/beta 转换、扩缩容和重建 |
| M09 | 缺失与零指标、UUID 重枚举、画像不匹配、功耗写失败、SLO 规划 |

同平台聚合与 PD 的生成结果使用明确随机性和精度容差；KV 复制测试可做字节校验。不要默认不同厂商模型输出必须逐 token 一致。测试优先断言结构化字段、API 和指标，日志保留为诊断。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [pyproject.toml](../../../pyproject.toml) | 注册所需平台/capability marker，保持严格 marker，区分测试生命周期和硬件需求 |
| [tests/conftest.py](../../../tests/conftest.py) | 增加平台选择、认证组合输入与 capability gate，保留现有 fixture 行为 |
| [gpu_args.py](../../../tests/utils/gpu_args.py)、[pytest_parallel_gpu.py](../../../tests/utils/pytest_parallel_gpu.py) | 先核实硬编码设备管理范围，再接 provider；CUDA runner 的现有调度保持兼容 |
| [profile_pytest.py](../../../tests/utils/profile_pytest.py) | 平台化内存观测和测试资源记录，不以 CUDA 显存估计其他平台 |
| [test_kvbm_vllm_integration.py](../../../tests/kvbm_integration/test_kvbm_vllm_integration.py) | 复用语义断言，平台相关创建/设备部分使用 fixture |
| [test_v1_checkpoint.py](../../../lib/gpu_memory_service/tests/test_v1_checkpoint.py) | GMS 状态机保留无设备测试，真机验证分开 |
| [pre-merge.yml](../../../.github/workflows/pre-merge.yml)、[nightly-ci.yml](../../../.github/workflows/nightly-ci.yml) | 将已确认的公共契约/平台认证任务接入既有流程；硬件密集任务按 runner 能力选择 |
| [xpu-ci.yaml](../../../.github/workflows/xpu-ci.yaml) | 参考已有平台作业结构，公共抽取不能破坏现有 XPU 流程 |
| [pytest action](../../../.github/actions/pytest/action.yml) | 按实际接入需求传递平台及能力选择，避免对所有 job 无条件施加新参数 |

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `tests/platforms/conftest.py` | 认证组合 fixture、平台 provider、能力 gate、结果元数据 |
| `tests/platforms/test_discovery_compat.py` | M01 发现/版本兼容的集成测试 |
| `tests/platforms/test_build_isolation.py` | M02 公共 import 和未选 SDK 隔离 |
| `tests/platforms/test_engine_contract.py` | M03 基础生成/事件/容量；与 M03 文件计划是同一个文件 |
| `tests/platforms/test_device_memory.py`、`test_pd_contract.py` | M04/M05 同一计划测试，后一文件同目录 |
| `tests/platforms/test_gms_recovery.py`、`test_multimodal_contract.py`、`test_telemetry_planner.py` | M07/M09 相应测试，均位于 tests/platforms |
| `tests/platforms/test_feature_combinations.py` | PD/offload、LoRA/cache、同平台多模态分离等已声明组合 |
| `tests/platforms/certification.schema.json` | 固定认证记录字段、测试状态和证据引用 |
| `tests/platforms/profiles/{cuda,ascend,metax}.yaml` | 由实际测试环境填写的认证输入，不预填虚构型号/版本 |
| `scripts/validate_platform_evidence.py` | 验证必需用例执行、组合匹配和结果完整性 |
| `.github/workflows/platform-certification.yml` | 认证工作流；仅在 runner、权限和镜像来源明确后启用 |

M01/M04/M06 等模块所属的 Rust/单元测试保留在原 crate，本目录聚合端到端证据，不复制同一测试。硬件故障注入保留 CUDA 专用实现，并为其他平台增加各自工具；不可直接把 CUDA 拦截器当通用故障注入。

## 证据与执行策略

认证记录至少包含 platform、chip、主机/网络、驱动/SDK、engine/plugin、Dynamo commit、镜像 digest、模型 revision、精度、并行/KV 配置、测试命令、结果与时间。单条结果要能回溯同一认证组合，避免把不同版本的通过结果拼成“全支持”。

PR 执行 T0/T1 与受影响平台的小型 smoke；平台 nightly 执行可承载的 T2/T3；发布认证执行承诺组合的 T4。模型优先使用可验证语义的小模型；大模型和超出默认显存预算的用例只放到具备相应资源的专用层级。

长稳时长、性能阈值、首批硬件型号和可承诺功能组合需要根据实际环境填写，当前不虚构固定 SLA。硬件缺失时可完成文档、mock 契约和构建验证，但支持状态保持待验证。
