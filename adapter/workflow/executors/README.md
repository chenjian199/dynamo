<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 命令、远端传输与资源生命周期

本包把任务计划中的命令落实到本地、SSH、已有容器或 Kubernetes 环境，并记录本次活动创建的资源。它不负责决定某个芯片功能是否通过。

| 文件 | 作用 | 关联命令 |
| --- | --- | --- |
| [commands.py](commands.py) | 按 executor 构造 argv、解析执行端路径、记录输出与超时，处理进程组退出 | `run` 内部调用 |
| [transfers.py](transfers.py) | 同步冻结源码与 attempt 输入，回收结果并核对字节摘要，拒绝路径越界和证据覆盖 | `stage`、`stage-inputs`、`collect` |
| [resources.py](resources.py) | 启动并跟踪自有 Job / 进程，观察归属、幂等恢复和清理证明 | `launch-job`、`launch-process`、`observe-resources`、`recover` |

命令使用 argv 数组，不隐含 shell。`{repo}`、`{campaign}`、`{attempt}` 根据执行端解析，`{coordinator_attempt}` 才表示协调器路径。节点、容器、context 和 namespace 都来自冻结的现场配置。

Job 使用 UID 和归属标签，进程使用 boot ID / start ticks 识别，避免误删同名资源或复用 PID。SSH 断开不能证明远端任务结束；cleanup 返回零也不能证明资源已经释放。中断恢复的完整顺序见 [执行与恢复手册](../README.md)。
