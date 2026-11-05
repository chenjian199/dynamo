<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# GMS 与多模态检查工具

| 文件 / 分组 | 作用 |
| --- | --- |
| [probe-vmm.cpp](probe-vmm.cpp)、[probe.sh](probe.sh) | 厂商虚拟内存能力探针与运行入口，辅助判断 GMS 的底层前置条件 |
| [run.py](run.py)、[test_multimodal.py](test_multimodal.py) | 在开发容器中运行 M07 检查并记录源码，覆盖真实设备的 host-staged embedding |
| [serve-mm.sh](serve-mm.sh) | 多模态测试服务启动 |
| [make-video.sh](make-video.sh)、[video-check.py](video-check.py)、[red.webm](red.webm) | 视频测试素材生成、解码检查和固定素材 |
| [prepare-omni.sh](prepare-omni.sh)、[omni-check.py](omni-check.py) | Omni 环境准备及 Qwen2.5-Omni 三阶段手工检查，依赖对应权重和专用设备 |
| [omni_soak.py](omni_soak.py)、[sample-gpu.py](sample-gpu.py)、[summarize-omni-soak.py](summarize-omni-soak.py) | Omni 并发/取消、设备采样与结果汇总 |
| [collect.py](collect.py)、[collect-followup.py](collect-followup.py)、[sources.txt](sources.txt) | 按源码身份收集原始与后续证据，保留失败结论 |
| [ascend-gms-design.md](ascend-gms-design.md)、[ascend-hdk2611.md](ascend-hdk2611.md) | Ascend GMS 设计与驱动相关调查 |
| [followup.md](followup.md)、[omni-regression.md](omni-regression.md) | 既有补测过程与 Omni 回归范围 |

先按 [M07 契约](../../README.md) 选择能力和模型；没有对应模型或 VMM 原语时记录阻塞或已确认暂缓。聚合 Omni 成功不证明独立 Dynamo stage 的 hidden-state 通道已经完成。新输出保存到 [results](../../../../results/README.md)。
