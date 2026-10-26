<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M07 媒体生命周期、WRITE、视频与 Omni 后续验收

2026-10-26。本轮继续四项工作；Ascend GMS 自研仍暂缓。
引擎固定为 CUDA 0.28.0、MetaX 0.26.0、Ascend 0.23.0+empty，未降级或替换厂商 torch。

按用户最新决定，华为相关未完成项记录后暂缓：Ascend GMS、NIXL READ/WRITE
环境补齐和 Omni 用户态退出崩溃。保留已有通过结果、失败证据和能力拒绝逻辑；
暂缓不代表验收通过，也不将这些问题归因于宿主机驱动。

独立 Dynamo Omni stage 的 full-payload/hidden-state 通道也按用户决定记录后暂缓。
当前 CUDA 0.28.0 与 MetaX 0.26.0 组合均未通过该独立路径；本轮保留原生聚合
Omni 的已验证能力，不为平台对齐额外补建这条通道，也不推断所有上游版本均不支持。

## 已完成的实现与验证

| 分项 | 实现 | 验证范围 |
| --- | --- | --- |
| LOCAL 异常回收 | 原子发布、读/写 POSIX flock、TTL 扫描 `.partial` 和 `.safetensors`，进程退出由内核释放锁 | 三平台单元；真实子进程中断写入后回收；读者持锁时不删除；超时未接管文件回收 |
| NIXL WRITE v2 | 明确握手/终态确认、字节/版本校验、源副本保活、接收内存对齐、超时/取消隔离未确认 DMA、内存预算 | CUDA/MetaX 真机三种 dtype、晚提交 DMA 在接收超时后完成并回收、下一请求恢复；失败终态使用可控 fake agent 单测 |
| 视频及跨卡 | 限制后台解码并发，取消后等线程真正完成才归还配额；编码器与 LLM 可指定不同物理卡 | 三平台 600 秒/6000 帧 VP9，22 轮抽样解码、40 次取消及恢复；同机不同卡 Qwen3-VL-2B 图文、并发/流式/取消/非法输入恢复 |
| Omni 接入基础 | 平台设备可见性、M01 runtime 配置、0.23/0.26/0.28 factory 接口隔离、语音输入、取消 abort/晚完成清理、NIXL 发布及接收内存预算 | CUDA/MetaX 44 项单元通过；华为 38 项断言通过但退出 SIGABRT，整轮判失败；完整生成边界见下文 |

通用 M07 单元：CUDA 141、MetaX 141、Ascend 117 项通过。最后补充的非普通文件拒绝、接收总超时与跨进程读者释放竞态修正，三平台各 25 项生命周期/张量契约定向回归通过。
跨卡回归结束时三平台 LOCAL embedding 文件均无残留。
视频检查验证 CPU 抽帧解码、像素和内存/FD 的有界增长，不等于模型处理完整十分钟视频，也不代表厂商硬件解码已支持。

## 原生 Omni 三阶段与独立 stage 的区别

CUDA 0.28.0 和 MetaX 0.26.0 已通过原生聚合 Omni 三阶段：Dynamo 前端 → 一个 Omni worker →
Thinker/Talker/Code2Wav 三个引擎进程分别使用物理卡 1/2/3，返回 24 kHz、61440 帧非空 WAV。
两平台均返回 61440 帧（2.56 秒）。验收记录为
`cuda/omni-aggregated-3/result.json` 和 `metax/omni-aggregated-2/result.json`。
这验证了实际跨阶段生成，不等于三个独立 Dynamo stage 服务已通过。
独立 stage 的价值是由 Dynamo 分别发现、路由和部署各阶段，并支持分别配置副本数。
Qwen2.5-Omni 的 Thinker→Talker 除 token 外还需要中间特征张量（hidden states）；
现有传输 connector 不等于已经接通引擎内部 producer/consumer。
只使用原生聚合模式时无需这条独立通道；要验收独立 stage 服务则必须补齐。
部署必须显式使用 eager，并保留 Qwen2.5-Omni Talker 的 `repetition_penalty=1.05`；
测试脚本初版覆盖原生 sampling 默认值所导致的初始化/推理失败也保留在证据中。
随后补齐 CUDA/MetaX 并发度 2、各 30 分钟持续负载与周期取消恢复：正常请求分别
1099/499 个全部成功，取消及后端 abort 分别 110/50 次。详见
[原生 Omni 稳定性回归](omni-regression.md)。不认证音频质量或无限运行无泄漏。

## 仍不能声明完成的部分

- **Ascend NIXL WRITE/READ**：固定镜像没有 NIXL Python。没有向昇腾镜像安装依赖 CUDA 的 NIXL wheel；本轮媒体传输认证为 LOCAL。要补齐需独立构建 CPU NIXL/UCX 并完成同一组故障测试。
- **永久失联**：WRITE 取消/超时不会让未知状态的 DMA 缓冲区重新用于其它请求。晚到终态可回收；永久没有终态时有界隔离，预算耗尽拒绝新传输，需重启拥有者。不是无限故障自动回收。MetaX 底层 UCX peer-failure abort 的 M05 限制不因此消失；本轮没有完成杀死远端进程的原生传输验收。
- **LOCAL TTL 边界**：默认 `DYN_LOCAL_MEDIA_TTL_SECONDS=300`，用于同机共享本地目录；不是 NFS 分布式锁协议。所有 worker 都退出后，下一 worker 启动才恢复 GC。超过 TTL 才首次读取的请求明确失败，持锁中的正常读者不被删除。
- **独立 Omni 三阶段**：CUDA 0.28/MetaX 0.26 的 Qwen2.5-Omni 原生 pipeline 使用 `requires_full_payload_input`。现有 Dynamo 独立 stage RPC 传递控制信息，但没有桥接引擎 worker 的 hidden-state payload 通道。真实 Thinker→Talker 请求出现 896/3584 维度不匹配。现在对该类独立 stage 提前拒绝，避免 token-only 回退导致引擎崩溃。解决需要对接 worker producer 的完整 hidden-state 累积和 consumer intermediate buffer、请求 ID/完成确认与取消协议；不能仅改 dtype 或降级 CUDA。
- **华为 Omni 用户态问题**：0.23.0rc1 overlay 在 `import vllm_omni.entrypoints.async_omni` 后退出 SIGABRT，模型 registry 的检查子进程也因此失败。GDB 栈落在 CANN 9.1 `liberror_manager.so` 的 `ErrorManager::ErrorInfoConfig` 容器析构/free。单独 torch_npu+av、torch_npu+DiffusionPipeline 正常。崩溃位置不等于已证明最早写坏内存的代码；尚不能归因于宿主机驱动，也不能将断言成功标为通过。

## Omni 环境

CUDA 使用官方 Dynamo 1.5.1 镜像内已有的 vllm-omni 0.28.0rc1。
MetaX 增量 overlay 使用 vllm-omni 0.26.0 和官方插件
[MetaX-MACA/vllm-omni-metax](https://github.com/MetaX-MACA/vllm-omni-metax/tree/35cc3687bba127a760331bea9466bf554dce028b)。
Ascend 的对应公开包为 vllm-omni 0.23.0rc1，本轮没有找到 0.23.0 正式 wheel；这与已锁定的 **vLLM 0.23.0** 是不同包。

overlay 位于 `/opt/dynamo/omni-overlay`，仅在实验进程前置 `PYTHONPATH`。
依赖安装参考 [prepare-omni.sh](prepare-omni.sh)；它使用 `--no-deps`，拒绝覆盖已有 overlay。
安装完成不代表厂商组合已获得端到端认证。

## 复现与证据

容器中使用 `/opt/dynamo/venv/bin/python`，源码为 `/workspace/dynamo`。
昇腾先加载 CANN 环境，再追加 overlay，不能覆盖 CANN 的 `PYTHONPATH`：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONPATH="/opt/dynamo/omni-overlay:${PYTHONPATH:-}"
```

所有输出目录必须新建。以沐曦为例：

```bash
python adapt/m07/run.py --platform metax --device 0 --suite unit --output /tmp/m07-unit
python adapt/m07/run.py --platform metax --device 0 --suite media --output /tmp/m07-media
python adapt/m07/run.py --platform metax --device 2 --encoder-device 3 \
  --suite encoder --output /tmp/m07-cross-card
python adapt/m07/run.py --platform metax --device 0 --suite omni-unit --output /tmp/m07-omni-unit
python adapt/m07/video-check.py --generate --video /tmp/long.webm
DYN_PLATFORM=metax python adapt/m07/video-check.py --video /tmp/long.webm --output /tmp/m07-video.json
PYTHONPATH="/opt/dynamo/omni-overlay:${PYTHONPATH:-}" python adapt/m07/omni-check.py \
  --platform metax --mode aggregated --devices 1,2,3 --output /tmp/m07-omni-aggregated
```

最后一条使用已通过 smoke 的原生 Omni 三阶段模式。
`omni-check.py --mode stages` 用于复现独立阶段限制，当前不应预期通过；两种部署的结果必须分别解读。
模型均来自 NFS，使用 Qwen3-VL-2B-Instruct 和 Qwen2.5-Omni-7B，未下载权重。

原始证据位于各开发容器的 `/var/cache/dynamo-{cuda,metax,ascend}/m07-followup/`。
[results/followup](results/followup) 保存测试结果和有限日志摘录；`manifest.json` 保存原始日志 SHA256。
历史结果中的源码哈希保持原样，不把后来新增的 guard 或格式化倒填为此前已测试。
