<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 部署清单渲染

| 文件 / 目录 | 作用 |
| --- | --- |
| [render.py](render.py)、[render_large.py](render_large.py) | 小模型和大模型在线 Planner 回归清单 |
| [render_metrics.py](render_metrics.py) | 限定测试 namespace 和 deployment 的指标采集清单 |
| [render_offline.py](render_offline.py)、[render_offline_large.py](render_offline_large.py) | 小模型和大模型的有界离线采样清单 |
| [render_profiler.py](render_profiler.py) | Profiler 详细测量复测清单与测试身份 |
| [manifests/](manifests/) | 按 CUDA / Ascend 保存的既有渲染清单；文件名区分模型、profile、metrics、tools 和修订 |

渲染前明确平台、模型路径、镜像、节点和工作目录；脚本默认值来自已有回归现场。模板需要适配时修改渲染入口并重新生成新清单，不把历史 manifests 当成任意集群的默认安装文件。

渲染不等于部署或测试通过。接下来执行 [离线分析](../offline/README.md) 或 [在线回归](../online/README.md)，按 [M09 功能契约](../../README.md) 采集证据。
