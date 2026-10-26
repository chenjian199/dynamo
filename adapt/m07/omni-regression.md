<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M07 原生 Omni 并发、取消与持续运行回归

2026-10-26。CUDA、MetaX 本轮原生聚合 Omni 回归通过，M07 按当前约定范围阶段性收口；
模块总体仍为有限支持，暂缓项不计入已完成能力。

## 实测结果

| 平台 | 持续负载 | 正常请求成功数 | 取消 / 后端 abort | RSS 增量 | FD 增量 | 媒体文件增量 |
| --- | --- | --- | --- | --- | --- | --- |
| CUDA 0.28.0 | 1809.42 秒 | 1099 / 1099 | 110 / 110 | 74.71 MiB | +2 | 0 |
| MetaX 0.26.0 | 1814.23 秒 | 499 / 499 | 50 / 50 | 167.34 MiB | -1 | 0 |

两平台最终恢复请求成功、进程集合不变、无 abort monitor 错误；测试进程已结束。
负载期间稳定阶段的 GPU 采样未见显存持续增长。主机 RSS 确有增长，CUDA 主要在
Code2Wav 进程，必须保留曲线而不能宣称完全无泄漏；本轮只认证下面的有界条件。
结果见 [CUDA 汇总](results/followup/cuda/omni-soak-1/soak-summary.json) 和
[MetaX 汇总](results/followup/metax/omni-soak-1/soak-summary.json)。相邻目录保留
逐请求结果、进程资源曲线、GPU 采样、运行配置、版本与源码哈希。

## 固定范围

- CUDA：官方 Dynamo 1.5.1 镜像，vLLM 0.28.0、vllm-omni 0.28.0rc1。
- MetaX：vLLM 0.26.0、vllm-omni 0.26.0 及已固定的 MetaX Omni 插件。
- 模型：NFS 上的 Qwen2.5-Omni-7B；Thinker/Talker/Code2Wav 分别使用本机物理卡 1/2/3。
- 部署：一个 Dynamo Omni worker 管理原生三阶段，前置 Dynamo frontend；eager，阶段 `max_num_seqs=2`。
- 压力：HTTP 并发度 2；预热后至少 1800 秒，每五轮发起一个请求并在一秒后取消，随后验证两个正常请求恢复。
- 输出：逐请求验证 HTTP 200、可读、非空、非静音的 24 kHz WAV。该检查不认证语音内容、自然度或音频质量。
- 资源：逐轮记录进程树 RSS、FD、输出文件数及 PID；独立采样 GPU 显存和利用率。RSS 是进程之和，包含共享页的重复计算。

每轮成功请求计数包括 6 个预热请求和末尾恢复请求，不包括最初 smoke；
取消请求另计。请求超时为 180 秒，不允许用超时或失败请求凑满持续运行时间。

## 判定条件

- 达到规定持续负载时长，全部正常请求完成，最终恢复请求成功。
- 客户端确实在完成前取消，后端记录对应数量的 abort，且无 abort monitor 错误。
- 结束时相较预热后，进程树 RSS 增量不超过 2 GiB，FD 增量不超过 32，输出文件无增长，进程集合不变。
- 保留逐轮曲线及原始 GPU 采样，以便复核阈值内的增长；有界通过不等于无限运行无泄漏。
- 后端 abort 日志不等于逐阶段 KV 块计数证明；本轮不认证厂商内部所有分配逐项归零。

## 复现

在对应开发容器中使用 `/opt/dynamo/venv/bin/python`，输出路径必须新建：

```bash
# MetaX 需要 overlay；CUDA 使用镜像内 Omni，不添加 overlay。
export PYTHONPATH="/opt/dynamo/omni-overlay:${PYTHONPATH:-}"
python adapt/m07/omni-check.py --platform metax --mode aggregated \
  --devices 1,2,3 --concurrency 2 --soak-seconds 1800 \
  --output /tmp/m07-omni-soak

# 测试目录创建后，可在另一个终端同步采样；检测到 result.json 后退出。
python adapt/m07/sample-gpu.py --platform metax --devices 1,2,3 \
  --run /tmp/m07-omni-soak

# 测试与采样结束后汇总。
python adapt/m07/summarize-omni-soak.py --run /tmp/m07-omni-soak
```

CUDA 将 `--platform` 改为 `cuda`，省略 overlay 设置。
复现脚本、原始源码哈希、请求结果与资源采样共同构成证据；不倒填历史运行哈希。

## 暂缓和保留范围

独立 Dynamo Omni stage full-payload 通道、华为 GMS/NIXL/Omni 问题按用户决定暂缓。
永久失联的 DMA 自动回收、完整 GMS checkpoint/CRIU、硬件视频解码、跨节点及
E-P-D 联合部署不属于本轮认证，沿用 [后续验收边界](followup.md)。
