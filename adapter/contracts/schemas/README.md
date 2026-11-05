<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 结构化文件格式

这里的 JSON Schema 约束工作流输入和证据的结构。被校验的数据可以来自 YAML 或 JSON；加载为对象后，再按对应 schema 检查。

| 文件 | 校验对象 | 主要内容 |
| --- | --- | --- |
| [feature.schema.json](feature.schema.json) | 各模块 `features/<功能>/feature.yaml` | 稳定功能编号、revision、依赖、环境要求、case 和断言 |
| [environment.schema.json](environment.schema.json) | 现场环境清单 | 节点、执行开关、预算、SSH 配置引用、逐 case 的环境绑定 |
| [task-plan.schema.json](task-plan.schema.json) | campaign 的 `tasks.json` | 功能任务、prepare/run/observe/cleanup/verify 命令、executor、资源、超时和绑定摘要 |
| [result.schema.json](result.schema.json) | 每次 attempt 的 `result.json` | 冻结身份、功能 revision、证据来源、带 SHA256 的文件索引、逐 case 的断言及证据引用 |
| [issues.schema.json](issues.schema.json) | [issues.yaml](../issues.yaml) | 问题编号、平台、功能编号、状态与原因 |
| `*.json.license` | 同名 JSON 文件的版权元数据 | JSON 不支持注释，故单独记录 SPDX 版权与许可证；不参与运行时配置 |

例如，`task-plan.schema.json.license` 只属于 `task-plan.schema.json`，不是另一份任务定义。编辑 schema 时编辑 `.json`；仅版权信息变化才需要调整其 `.license`。

Schema 通过只表示结构符合要求。功能依赖是否完整、模型和镜像身份是否一致、命令是否调用规定入口、证据是否真实且满足断言，还分别由 [范围解析](../../workflow/scope.py)、[任务校验](../../workflow/state/README.md) 和 [证据认证](../../workflow/evidence/README.md) 检查。结果中的 `passed: true` 本身不能让功能通过。

编写输入可先参考 [环境模板](../../environments/examples/README.md) 和已有 `feature.yaml`。格式变更需要同步生产者、消费者及 [契约测试](../../workflow/tests/README.md)；验收语义变化还需要递增相应功能 revision。公共结构检查入口见 [contracts](../README.md)。
