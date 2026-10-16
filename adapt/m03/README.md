<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M03 vLLM 引擎适配验收

本轮 vLLM 聚合模式适配与验收已完成，包含 TP/DP、生命周期、scheduler/KV 兼容、LoRA、量化、推测解码、MoE/EP 和聚合多模态。SGLang 按用户要求暂缓。日期：2026-10-16。

## 当前结果

| 能力/配置 | [MetaX C500 / 0.26.0](results/metax/) | [Ascend 910B3 / 0.23.0](results/ascend/) | [NVIDIA H800 / 0.24.0](results/cuda/) |
| --- | --- | --- | --- |
| Qwen3-0.6B，TP=2、DP=1 | 通过 | 通过 | 由下方四卡组合覆盖 |
| Qwen3-0.6B，TP=1、DP=2 + LoRA | 通过 | 通过 | 通过 |
| Qwen3-0.6B，TP=2、DP=2 | 通过 | 通过 | 通过 |
| W8A8 dynamic，compressed-tensors | 通过 | 通过 | 通过 |
| ngram，3 speculative tokens | 通过 | 通过 | 通过 |
| 独立 draft 模型（同 checkpoint 双实例，仅功能验证） | 通过 | 通过 | 通过 |
| Qwen3-30B-A3B，TP=2/DP=2/EP=4 | 通过 | 通过 | 通过 |
| Qwen3-VL-2B，聚合图文 | 通过 | 通过 | 通过 |
| Qwen3-8B，TP=2/DP=2 | 通过 | 通过 | 通过 |

“通过”仅针对列出的配置；不表示任意模型、精度、推测算法、并行拓扑及任意功能组合都支持。以上均为功能测试，不是性能或模型质量认证。

## 本轮实现

- M01 preflight 扩展到聚合 eager、多卡本地 multiprocessing TP/DP；可见设备不足、PP、Ray/external/hybrid DP 在就绪前拒绝。
- `vllm/platform.py` 保留自定义 scheduler，并在厂商处理后的有效配置上检查 KV/FPM 的兼容性；记录有效引擎配置。
- `main.py` 使用失败回滚清理已创建的引擎和指标临时目录；decode lifecycle 接管资源，支持部分初始化及重复清理。
- `handlers.py` 明确关闭 KV publisher/FPM relay，确保清理一个资源失败时仍尝试其他资源。
- `publisher.py` 修复只初始化最后一个 DP rank 的容量错误；每个 rank 都发布实际容量。

没有引入 `EnginePlatformHooks`，也没有修改 Rust 协议。本轮使用 M01 已有平台注册和 `ModelRuntimeConfig.engine_specific` 扩展字段。

## 容器与模型

MetaX/Ascend 沿用 [沐曦开发脚本](../metax/metax-dev.sh) / [华为开发脚本](../ascend/ascend-dev.sh) 创建的容器与 `/opt/dynamo/venv`。源码挂载到 `/workspace/dynamo`，模型与日志位于 `/var/cache/dynamo-${PLATFORM}`。

当前 NFS 测试容器分别为 `dynamo-metax-m03-nfs`、`dynamo-ascend-m03-nfs`、`dynamo-cuda-m03-nfs`，从已安装的开发环境快照创建，继承源码与缓存挂载后增加只读 NFS。它们是调试环境快照，不是 M02 正式发布镜像。Ascend 不可同时在旧、新容器占用同组 NPU；已停止空闲的 `dynamo-ascend-dev`，保留其环境。后续新建环境使用更新后的开发脚本即可自动挂载。

NVIDIA 使用独立容器 `dynamo-cuda-m03`、目录 `/home/inspur/CJ_workspace/dynamo-m03`。本地已有 vLLM 0.24.0 镜像固定到 `sha256:251eba5cc7c12fed0b75da22a9240e582b1c9e39f6fbc064f86781b963bd814f`，不是把浮动 `latest` 当作版本。复用了本分支在 Ubuntu 22.04 x86_64 编译的 `ai_dynamo_runtime-1.5.1-cp310-abi3-manylinux_2_35_x86_64.whl`，Python 层 editable 安装。

创建 NFS 容器时使用的本地环境快照（不包含挂载的模型/源码/缓存）：

| 容器 | 本地环境快照 image ID |
| --- | --- |
| `dynamo-metax-m03-nfs` | `sha256:4d5e20b9eb97e91330496cb7827b3fe24a8c2c5a2dbe9ebbb755426252c7f857` |
| `dynamo-ascend-m03-nfs` | `sha256:008209e4b72e773dca76e12420df287c1d4d0a90735e17af4e78aab6c06713dd` |
| `dynamo-cuda-m03-nfs` | `sha256:53d2d0cb076a6c640b3ba0b7ad6d0a27fa7e3e3218a9855a57d22044fdb24161` |

| 模型 | 用途 | 来源/版本 |
| --- | --- | --- |
| Qwen3-0.6B | TP/DP、LoRA、推测解码 | `Qwen/Qwen3-0.6B`，`c1899de289a04d12100db370d81485cdf75e47ca` |
| Qwen3-0.6B-M03-W8A8 | INT8 路径 | `create-quantized.py` 从上述权重生成；逐 channel 权重、逐 token 动态激活；不包含质量校准 |
| m03-lora | 加载/卸载/缓存隔离 | `create-lora.py` 生成固定种子的非零 rank-8 适配器；不是训练后的业务适配器 |
| Qwen3-30B-A3B | MoE/EP，128 experts | `/NFS_62/Models/Qwen_Series/Qwen3-30B-A3B` |
| Qwen3-VL-2B-Instruct | 图文 | `/NFS_62/Models/Qwen_Series/Qwen3-VL-2B-Instruct` |
| Qwen3-8B | 四卡 TP/DP | `/NFS_62/Models/Qwen_Series/Qwen3-8B` |

共享权重直接读取，不再下载或复制。NFS 来源为 `192.168.10.62:/data/HY/NFS`；容器用 `--mount type=bind,src=/NFS_62,dst=/NFS_62,readonly`。NFS 模型未附上游 revision 的，报告只记录真实路径、配置 SHA256 和权重文件大小/mtime，不虚构 revision，也不把这些信息当作全量权重校验和。

## 手动复现

以下命令在容器内仓库根目录执行。先选择平台和原始文本模型路径：

```bash
source /opt/dynamo/venv/bin/activate
export PLATFORM=ascend  # metax / ascend / cuda
export MODEL_PATH=/var/cache/dynamo-ascend/models/Qwen3-0.6B
export TP=2 DP=2 DEVICES=0,1,2,3
python adapt/m03/run-case.py --output /var/cache/dynamo-ascend/m03/manual-tp2dp2
```

MetaX 原模型路径为 `/var/cache/dynamo-metax/huggingface/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca`。NVIDIA 为 `/var/cache/dynamo-cuda/models/Qwen3-0.6B`。

`run-case.py` 占用容器内 8010/8090、20092 起的 KV 端口与 20094 起的 FPM 端口。一次只运行一个 case；脚本拒绝覆盖已有服务，独立进程组启动并在结束时清理。输出目录必须不存在，脚本不会覆盖旧证据。仅手动常驻服务可执行 `setsid bash adapt/m03/serve.sh ...`。

```bash
# 生成测试适配器和 INT8 权重；量化输出必须是新目录。
python adapt/m03/create-lora.py --model "$MODEL_PATH" \
  --output "/var/cache/dynamo-$PLATFORM/models/m03-lora"
python adapt/m03/create-quantized.py --model "$MODEL_PATH" \
  --output "/var/cache/dynamo-$PLATFORM/models/Qwen3-0.6B-M03-W8A8"

export TP=1 DP=2 DEVICES=0,1 DYN_LORA_ENABLED=true
python adapt/m03/run-case.py --output "/var/cache/dynamo-$PLATFORM/m03/manual-lora" \
  --check lora -- --enable-lora --max-loras 2 --max-lora-rank 8
unset DYN_LORA_ENABLED

export TP=1 DP=1 DEVICES=0
python adapt/m03/run-case.py --output "/var/cache/dynamo-$PLATFORM/m03/manual-ngram" \
  --check spec -- --speculative-config \
  '{"method":"ngram","num_speculative_tokens":3,"prompt_lookup_min":2,"prompt_lookup_max":4}'

MODEL_PATH="/var/cache/dynamo-$PLATFORM/models/Qwen3-0.6B-M03-W8A8" \
  python adapt/m03/run-case.py --output "/var/cache/dynamo-$PLATFORM/m03/manual-int8" \
  -- --quantization compressed-tensors

# 使用已挂载的 NFS 权重。无需任何下载命令。
export TP=2 DP=2 DEVICES=0,1,2,3
MODEL_PATH=/NFS_62/Models/Qwen_Series/Qwen3-30B-A3B \
  python adapt/m03/run-case.py --startup-timeout 1800 \
  --output "/var/cache/dynamo-$PLATFORM/m03/manual-moe" -- --enable-expert-parallel

MODEL_PATH=/NFS_62/Models/Qwen_Series/Qwen3-8B \
  python adapt/m03/run-case.py --startup-timeout 1800 \
  --output "/var/cache/dynamo-$PLATFORM/m03/manual-8b"

export TP=1 DP=1 DEVICES=0
MODEL_PATH=/NFS_62/Models/Qwen_Series/Qwen3-VL-2B-Instruct \
  python adapt/m03/run-case.py --startup-timeout 1800 \
  --output "/var/cache/dynamo-$PLATFORM/m03/manual-vision" --check vision -- \
  --enable-multimodal --limit-mm-per-prompt '{"image":1,"video":0}' \
  --mm-processor-kwargs '{"max_pixels":200704}'

```

## 验收语义与边界

`api.json` 覆盖生成、流式结束、4 请求并发、客户端取消及后续请求；`kv.json` 覆盖显式 rank、真实事件、重复前缀选路、缓存清空失效和每个 rank 的物理容量。LoRA 覆盖加载、失败回滚、卸载、重新加载、8 请求并发及不同适配器的缓存隔离。推测解码要求实际 draft/accepted-token 计数大于零。图文覆盖红/蓝图像识别、4 请求并发、流式结束、前缀命中、取消计数增长、非法图片 415 及后续正常请求。

高级能力会作为独立条件记录；平台注册中的 `available` 是该配置的接入能力，不是硬件认证状态。模型就绪仍取决于厂商引擎初始化成功。

本轮不认证 graph mode、跨节点、PP、Ray/external/hybrid DP、PD/KVBM 组合、独立编码器/NIXL 媒体传输、动态 EP 扩缩容和全部量化/推测算法。它们按 M04–M09 的依赖单独验证；SGLang 明确暂缓。

`case.json` 记录启动参数、软件版本和源码 SHA256。测试期间代码可能尚未提交，且华为目录保留原 checkout 的 Git HEAD，所以应同时核对源码哈希，不能仅看 `source_commit`。完整 server/checks 日志保存在各容器对应 case 目录。

## 回归与证据

原始结果按平台/配置存放于 [results/](results/)；`case.json` 是实际运行的参数、版本、源码与结果，`api.json` / `kv.json` / `advanced.json` 是对应检查。`engine-excerpt.txt` 保存专家分布、容量或实际 kernel 的日志摘录。完整日志仍在容器缓存中，并由 case 的 SHA256 引用。

本轮最终定向回归：MetaX **466 passed**，Ascend **465 passed / 1 skipped**，CUDA **465 passed / 1 skipped**。后两者跳过的是当前 vLLM 版本不存在的 `VLLMUnprocessableEntityError` 专项，其他错误映射仍执行。原始 pytest 输出见各平台 `unit.log`。最终生产代码与测试源码已核对三容器 SHA256 一致，见 [source-sync.json](results/source-sync.json)。

```bash
python -m pytest -q -W 'ignore:fla package is not installed:UserWarning' \
  components/src/dynamo/vllm/tests/test_vllm_unit.py \
  components/src/dynamo/vllm/tests/test_vllm_instrumented_scheduler.py \
  components/src/dynamo/vllm/tests/test_vllm_kv_events_api.py \
  components/src/dynamo/vllm/tests/test_vllm_publisher.py \
  components/src/dynamo/vllm/tests/test_platform_integration.py \
  components/src/dynamo/vllm/tests/test_vllm_worker_factory.py \
  components/src/dynamo/common/platform/tests
```

已知边界：聚合图文的引擎 prefix cache 已验证，但当前配置会记录前端未提供 canonical MM hash 的警告；本轮不把单 Worker 的缓存命中作为“跨 Worker 图像精确 KV 选路”的证据。多模态媒体契约和精确路由应在 M07 单独验收。手动测试退出先通知 Dynamo 进程清理引擎，45 秒未退出才对本测试进程组强制终止；`shutdown=graceful` 表示 launcher 在期限内退出，不等于厂商引擎内部完全没有强制子进程清理。
