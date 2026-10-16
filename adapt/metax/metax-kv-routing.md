<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MetaX KV-aware 路由适配

本阶段使用[基础推理环境](metax-bringup.md)中的官方 vLLM-metax 0.26.0 镜像、Dynamo v1.5.1 与同一份 Qwen3-0.6B 权重。2026-10-15 已通过单机两个独立 TP=1 Worker 的真实 KV 事件路由验收；不涉及 Worker 间 KV 数据传输。源码基于提交 `8bb9bd25ca` 加本次改动，具体文件 SHA256 记录在结果中。镜像 digest 与基础阶段一致：`sha256:e7e59c3713ad4ef1290cc0d5029759f2844b2695f36de0bca2f4630c671add95`。


后续进度：已完成 [M01 平台契约](metax-platform.md)。当前脚本的 Worker 以 `metax` 身份注册，frontend 为 CPU；本页原始 JSON 保留首次验收版本，新身份下的完整回归见 [M01 KV 结果](metax-platform-kv-results.json)。

## 启动

以下在已完成 editable 安装的 `dynamo-metax-dev` 容器中执行：

```bash
export MODEL_PATH=/var/cache/dynamo-metax/huggingface/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca
setsid --wait bash /workspace/dynamo/adapt/metax/metax-kv-serve.sh
```

默认配置：

| 项目 | 值 | 覆盖变量 |
| --- | --- | --- |
| GPU | 1、2，各一个 TP=1 Worker | `WORKER_GPU1`、`WORKER_GPU2` |
| 前端 | 容器 8001；现有容器映射到宿主机 18001 | `DYN_HTTP_PORT` |
| Worker metrics | 8082、8083 | `DYN_SYSTEM_PORT1`、`DYN_SYSTEM_PORT2` |
| vLLM KV 事件 | 20082、20083，ZMQ `kv-events` topic | `KV_EVENT_PORT1`、`KV_EVENT_PORT2` |
| Forward-pass metrics | 20084、20085，启用 `InstrumentedScheduler` | `FPM_PORT1`、`FPM_PORT2` |
| 发现 | file；`metax-kv` namespace | `DYN_FILE_KV`、`DYN_NAMESPACE` |
| 数据目录 | `/tmp/dynamo-metax-kv` | `DYN_FILE_KV` |
| 请求 / 事件传输 | TCP / ZMQ，无需 etcd、NATS | 固定用于本次验收 |
| 引擎 | eager、prefix caching、BF16，DP=1 | 仅当前组合完成验收 |
| KV block | 64 tokens | `BLOCK_SIZE` |
| 每 Worker KV 上限 | 1 GiB | `_PROFILE_OVERRIDE_VLLM_KV_CACHE_BYTES` |
| 上下文 / 并发序列 | 4096 / 4 | `MAX_MODEL_LEN`、`MAX_CONCURRENT_SEQS` |

脚本显式使用 `--router-mode kv` 和 `DYN_ROUTER_USE_KV_EVENTS=true`，为每个 Worker 开启 `enable_kv_cache_events`。`PYTHONHASHSEED=0` 固定 Python hash seed；Dynamo 从事件中的 token IDs 构建路由哈希，不能将两端内部 block hash 直接视为同一编号。

基础推理服务可继续在 GPU 0 / 8000 上运行。启动前确认所选 GPU 和端口空闲；若修改容器端口，宿主机映射不会自动变化。停止时向该启动脚本的进程发送 SIGTERM，等待同组子进程全部退出后再启动。不要同时运行两个默认 KV 启动脚本。

## 验收

另开一个容器终端，先确认返回 `ready: true` 且 aggregated workers 为 2：

```bash
curl -fsS http://127.0.0.1:8001/v1/models/Qwen3-0.6B/ready
/opt/dynamo/venv/bin/python /workspace/dynamo/.agents/skills/dynamo-router-starter/scripts/check_router_health.py \
  --base-url http://127.0.0.1:8001

timeout 300 /opt/dynamo/venv/bin/python /workspace/dynamo/adapt/metax/metax-kv-smoke.py \
  --output /var/cache/dynamo-metax/kv-smoke.json
```

验收脚本会清空两个 Worker 的缓存，并向第一个 Worker 填充超过缓存容量的请求；只对专用开发服务运行。它连接容器内部的 metrics、事件和 file discovery 地址，不能直接在宿主机仅凭映射后的前端端口执行。修改启动参数时同步传入 `--url`、`--worker-urls`、`--event-endpoints`、`--namespace`、`--discovery-dir`、`--block-size`。其余默认值与启动脚本一致。输出 JSON 的 `passed` 必须为 `true`。

脚本使用固定 seed 的 token IDs，准确控制公共前缀和分叉边界；预热时指定 Worker，后续路由请求不指定 Worker，也不设置 session affinity：

1. 分别在 Worker A、B 预热 `A+C+D` 与 `A+C+E`。延长前者时应选择 A，延长后者时应选择 B；独立路由观察者核对两者匹配深度为 3 对 2 个块。
2. HTTP 响应的 `nvext.worker_id` 核对实际执行 Worker；`usage.prompt_tokens_details.cached_tokens` 核对引擎实际命中；`nvext.timing.kv_hit_rate` 核对前端路由命中信息。
3. 交替重复两条分支各 3 次，核对缓存重用；全新前缀必须零命中。
4. 填满并超过一个 Worker 的缓存，观察真实 `BlockRemoved`，确认该 Worker 旧分支匹配归零，另一个 Worker 的公共前缀仍保留。
5. 通过 `clear_kv_blocks` RPC 清空两端，**不发送额外推理来推动事件发布**，等待索引归零。首次后续请求必须零命中，再次请求恢复缓存重用。
6. 独立订阅 vLLM 原始事件，核对 `BlockStored`、`BlockRemoved`、`AllBlocksCleared`、block size 和 DP rank。观察者使用真实事件模式，不使用近似索引。

## 修复与边界

实际发现：vLLM 0.26.0 的缓存重置会将 `AllBlocksCleared` 留在事件队列，通常直到 `update_from_output` 才发布。空闲引擎返回重置成功后，路由索引仍可能无限期保留旧匹配；修复前测试等待 30 秒仍看到 4 个旧块。

`InstrumentedScheduler.reset_prefix_cache` 在父类重置返回后立即排出并发布引擎及 connector 的真实待发布事件。它保留父类返回值，不伪造清空事件；即使 connector 重置失败，也会发布已经成功清空的设备缓存事件。该行为不依赖厂商版本字符串，使用已有的 scheduler 扩展点，未修改镜像中的 vLLM 或沐曦插件。启动脚本为每个 Worker 设置独立的 `DYN_FORWARDPASS_METRIC_PORT`，使 Dynamo 注入此 scheduler；未启用它或用 `--scheduler-cls` 覆盖时，不包含本修复。

当前边界为 C500、官方 0.26.0 镜像、Qwen3-0.6B、单机双 Worker、TP=1 / DP=1、eager 文本推理。跨节点、DP 多 rank、TP 多卡、混合注意力、多模态、LoRA、事件丢失/乱序恢复、Worker 故障切换和性能收益均未在本阶段认证。KV 路由只选择已有缓存的 Worker，不说明 NIXL/Mooncake 或 P/D KV 传输已可用。

## 实测结果

实测输出见 [metax-kv-results.json](metax-kv-results.json)，包含版本、源码哈希、匹配深度、实际命中和事件计数。这是功能 smoke 验收，单次请求的时间不用于性能结论。

| 检查 | 结果 |
| --- | --- |
| 容量与 rank | 每 Worker 146 个块，每块 64 tokens，DP rank 0 |
| 分叉选择 | 两个方向均通过；缓存深度 3 对 2 个块，实际命中 192 tokens |
| 重复请求 | 6/6 选中预期 Worker，实际命中 256 tokens |
| 全新前缀 | 路由 overlap、路由命中率、引擎 cached tokens 均为 0 |
| 自然淘汰 | 11 个压力请求后收到 37 个块的移除事件，旧分支在该 Worker 的 overlap 归零 |
| 空闲清空 | 两端均收到 2 次 `AllBlocksCleared`；清空后索引归零、首请求零命中、再次请求命中 256 tokens |
| API 回归 | completion、4 个并发请求、chat 流式响应及 `[DONE]` 通过；本阶段未重测取消 |
| 单元与回归测试 | 新增 6 项通过；现有 scheduler、worker factory、错误映射 286 项通过 |

单元与回归测试命令（容器内）：

```bash
cd /workspace/dynamo
/opt/dynamo/venv/bin/python -m pytest -q \
  components/src/dynamo/vllm/tests/test_vllm_kv_reset_events.py \
  components/src/dynamo/vllm/tests/test_vllm_instrumented_scheduler.py \
  components/src/dynamo/vllm/tests/test_vllm_worker_factory.py \
  components/src/dynamo/vllm/tests/test_vllm_errors.py
```

API 回归使用已有脚本：

```bash
/opt/dynamo/venv/bin/python /workspace/dynamo/adapt/metax/metax-smoke.py \
  --url http://127.0.0.1:8001 --output /var/cache/dynamo-metax/kv-api-smoke.json
```

已执行 shell 语法、Black、isort、Ruff、Flake8 与补丁空白检查。此处未运行完整仓库 CI，也未对上游 vLLM 0.28.0 环境做硬件回归。
