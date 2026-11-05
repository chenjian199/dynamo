<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 计划、执行与状态推进

本包将冻结的功能契约推进为可执行任务，并保存每一次尝试。通常从 `python3 -m adapter.workflow` 调用，不直接运行内部 Python 文件。

| 文件 | 职责 | 对应入口 |
| --- | --- | --- |
| [compile.py](compile.py) | 将逐 case 的现场配方编译成 tasks；检查精确入口、环境绑定并固定独立验证器 | `compile` |
| [plan.py](plan.py) | 校验任务结构与冻结范围，封存计划摘要 | `seal-plan` 及 runner 内部调用 |
| [runner.py](runner.py) | 检查输入时效、预检、依赖和资源锁，执行阶段命令，记录 attempts，处理恢复与发布门禁 | `run`、`resume`、`reconcile`、`release` |
| [loop.py](loop.py) | 有界 AI 实施、重新冻结、执行和诊断循环；保留中断与重试记录 | `automate` |

普通任务顺序是 prepare → run → observe → cleanup → verify。verify 与后续 qualify 使用 [独立证据逻辑](../evidence/README.md)，不会把进程退出码直接当成通过。源码、契约或资产变化后重新冻结；中断时先通过 [执行器](../executors/README.md) 观察自有资源，再恢复任务，不能盲目重复有副作用的命令。

实际命令、输入和状态文件见 [执行与恢复手册](../README.md)，任务字段见 [task-plan schema](../../contracts/schemas/README.md)。
