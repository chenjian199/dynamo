<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 实测记录（2026-10-29 至 2026-10-30）

各运行的 `contract.json` 固定负载，`result.json` 记录实际副本与回收，`latency.json` 记录客户端测量，`scaling.jsonl` 保留时间序列。`archive.json` 给出完整远端日志归档的位置、大小和 SHA256；仓库只保留筛选日志及结构化证据。复现见 [操作步骤](../reproduce.md)。

## 已完成运行

| 平台 / 模型 | Planner 副本变化 | 负载请求数 / API 错误 | 负载 TTFT / TPOT p95（ms） | 冻结延迟目标 |
| --- | --- | --- | --- | --- |
| CUDA Qwen3-0.6B | 1→2→3→2→1 | 1507 / 0 | 55.39 / 17.73 | 通过 |
| Ascend Qwen3-0.6B | 1→2→3→2→1 | 230 / 0 | 6447.13 / 110.14 | 未达标；包含冷扩容 |
| CUDA Qwen3-32B TP4 | 1→2→1 | 2063 / 0 | 717.42 / 37.19 | 通过 |
| Ascend Qwen3-32B TP4 | 1→2→1 | 409 / 0 | 1025.31 / 187.57 | TTFT 超过 1000ms |
| CUDA DeepSeek-V4-Flash TP4 | 1→2→1→2→1 | 760 / 0 | 940.10 / 136.48 | 通过；存在瞬态伸缩震荡 |
| Ascend DeepSeek-V4-Flash TP8/EP | 1→2→1 | 168 / 0 | 14093.24 / 542.24 | TTFT 超过 1000ms |

以上六项均验证 Planner 自行扩缩容，最终 1 个 ready worker、无 terminating Pod。恢复低负载后的延迟均达到对应冻结目标。副本列来自 desired 时间序列，各轮都达到目标峰值 ready 数；不是所有中间副本都持续就绪。删除 DGD 后的最终设备释放另列，不与缩容后的 1 副本混淆。

DeepSeek 两平台使用 ISL 1024 / OSL 128、负载并发 16；CUDA TP4、FP8 KV 16GiB，Ascend TP8/EP、W8A8、DSA-CP、FlashComm1。两者按流式 token ID 到达时间计时，校验数量与 usage 相符，不代表可见文本 TTFT 或回答质量认证。CUDA 有 8 个无可见文本请求，Ascend 为 0。负载分别持续 900 / 600 秒，恢复阶段各 90 秒、仅 6 / 2 个完成请求，不能作为稳定生产 SLA 的统计证据。正式 MDC 原生容量分别为 41692 / 31108 token，见各自 `deepseek-final/capacity-mdc.json`。

CUDA DeepSeek 的瞬态震荡已有直接决策证据：2026-10-29 11:54:47 UTC 两引擎 FPM 的 scheduled/queued KV 均为 0，原生 easy-load 策略按 2% 阈值从 2 缩到 1；11:55:17 KV 上升到 8462，按 12% 阈值重新扩到 2。最终负载结束后再次缩到 1。见 `cuda/deepseek-final/oscillation.log`；本轮保留原策略，生产稳定性未认证。日志不能确定空闲采样是正常间隙还是首次编译所致。

SLA 模式使用独立小模型运行：CUDA 配置 500ms TTFT / 25ms ITL，负载实测 85.70 / 12.22ms；Ascend 配置 1000ms / 100ms，实测 236.41 / 74.60ms。两者均为 1 副本 HOLD，日志分别有 55 / 52 个非零 ITL 预测决策；实测负载与恢复阶段达到该目标。聚合 Planner 的 TTFT 估计为 0，不能据此宣称 TTFT 预测准确。

两平台 Profiler 均完成 prefill/decode × TP1/TP2 四候选，AIPerf 聚合输出无错误/取消，候选配置与结果哈希一致；见各自 `profile/verification.json`。`autoApply=false`，本轮认证测量、选取和产物生成流程，不证明推荐最优。运行期间存在同平台其他本轮测试，不能用这些结果作隔离性能排名。

## 失败与修复证据

- CUDA `deepseek-visible-text-failure`：合成输入可在完成 128 个 token 后无可见文本。原始 SSE 保留在 `deepseek-*-stream.txt`。后续按实际 `completion_token_ids` 测量，要求数量与 usage 相符，单列空文本次数；该测量不认证回答质量，也不冒充可见文本 TTFT。
- CUDA `deepseek-capacity-failure`：旧 MDC 用共享块池 4285 × 路由事件块宽 256 推导出 1,096,960 token；vLLM 原生分组容量实际仅 10,422 token。Planner 因而低估占用、未扩容。负载下首次 TileLang 编译同时触发原 3 秒 canary 超时，frontend 出现 503/404，但没有引擎崩溃证据。此轮被主动终止，只有日志与契约、没有完成的 result.json，不能算通过。
- 修复：vLLM 在现有 runtime metadata 中发布独立 `kv_cache_capacity`（每引擎），Planner 优先读取；0.28 使用原生字段，旧引擎在 worker 上调用原生分组容量函数。接口缺失保留旧契约并告警，不改 KV 路由事件宽度，不降低引擎版本。正式 CUDA DeepSeek KV 改为 16GiB，扩容/缩容阈值 12%/2%，canary 超时 120 秒、间隔 30 秒；这是功能测试配置，不是生产性能推荐。
- Ascend `deepseek-rpc-failure`：0.23 默认序列化禁止传递 Python 函数，容量查询退回旧契约；主动停止该轮，新增具名 `get_dynamo_kv_capacity` worker 扩展，以原始整数返回容量。没有开启不安全序列化；原生字段和已有用户扩展不被覆盖。已有自定义扩展若未实现此方法，保留旧契约并告警，不宣称该组合的混合 KV 容量已认证。
- Ascend `small-v2`：默认 5 秒退出等待短于在途请求，复现 EngineShutdown；改为小模型 60 秒 / Pod 120 秒，大模型 180 / 240 秒。超过该有界等待仍可能中止长请求，不宣称无限 drain。
- Ascend Profiler：测量进程正常退出，但旧工具镜像内 K3s kubectl 无法以 UID 1000 创建默认解压目录，阻止 output-copier 发布结果。现场在本轮 copier 内创建并授权目录后成功完成；新版测试清单设置 `K3S_DATA_DIR=/tmp/m09-k3s`。与宿主机 NPU 驱动无关，日志在 `ascend/profile/profiler-copier-directory-failure.log`。

## 源码与边界

`source/v4.json` 对应小模型、SLA 和 Qwen 运行，`source/v5.json` 对应 Profiler，`source/v6.json` 对应 CUDA DeepSeek 的分组容量修复，`source/v7.json` 对应 Ascend 的具名 worker RPC 修复及无 KV 缓存兼容；归档哈希在 `source/archives.json`。后续单测与文档修改不回写既有运行源码快照。`unit/pytest.log` 为 452 passed（6 个既有 AsyncMock warning 和 1 个只读 pytest cache warning）；`unit/vllm-metadata.log` 为 22 passed、147 deselected（14 个依赖弃用警告、1 个只读 cache warning），合计 474 项通过。

MetaX 本机使用现有缓存中的 Qwen3-0.6B，vLLM 0.26.0、TP1、512MiB KV。API 产生非空文本、32 个完成 token；具名 worker 扩展发布容量 4672，与引擎日志一致，见 `metax/capacity/`。这验证旧版接口兼容，不是 MetaX 集群 Planner 回归，也不将容器隔夜空闲算作长稳压力测试。

## 最终资源回收

2026-10-30 已删除两集群本轮 DGD/DGDR、工具 Pod 和临时指标采集资源，保留 M08 Operator 与 namespace。`cleanup/` 记录删除后的资源列表和四台节点设备状态：两台 CUDA 共 16 卡显存占用 0MiB、无计算进程；两台 Ascend 共 16 卡无 NPU 进程，驱动仍报告约 3417–3419MiB 基础 HBM 占用，不将其写成 0。MetaX 测试容器退出，四卡无进程，基础占用各 858MiB。源码目录、依赖缓存、归档和镜像保留用于复现。

只读设备指标有 CUDA/Ascend exporter 与 MetaX 本地采样。MetaX 无集群，未认证 Planner 真机扩缩容。非 CUDA 功耗控制和 rapid 预测数据库未认证，显式禁用；健康数据不直接控制 readiness。本轮是源码覆盖回归，M10 仍须把 Planner 依赖及全部适配编入新镜像，再执行最终验收。
