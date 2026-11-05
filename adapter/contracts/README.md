<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 工作流契约

本目录定义所有芯片、所有模块共同遵守的规则，以及机器可校验的数据格式。它回答“怎样才算完成适配”，具体功能要求在 [模块功能契约](../modules/README.md)，执行代码在 [workflow](../workflow/README.md)。

| 文件 / 目录 | 作用 | 什么时候查看或修改 |
| --- | --- | --- |
| [policy.yaml](policy.yaml) | 默认执行方式、重试限制、证据要求和各类状态；规定原生引擎参数透传、仅清理自有资源等公共约束 | 开始适配前阅读；工作流公共政策变化时修改 |
| [schemas/](schemas/README.md) | 功能、环境、任务计划、结果和问题清单的 JSON Schema | 新增或修改结构化数据时，对照字段定义 |
| [issues.yaml](issues.yaml) | 按平台和功能编号登记尚未解决、有限支持或暂缓的事项 | 记录阻塞、复测结论或已接受的暂缓范围 |
| [workflow-audit.yaml](workflow-audit.yaml) | 工作流自身的定版检查项、证据入口和剩余项 | 发布工作流版本时审核；它不代表所有芯片已经通过测试 |

功能要求 → 环境与用例绑定 → 冻结任务 → 执行证据 → 独立认证，是这些文件参与的主流程。`policy.yaml`、选中的功能契约和环境会进入 campaign 的 `lock.json`；修改源文件后应重新冻结，不能改写已完成任务的身份。

`implementation`、`task`、`test`、`qualification` 是不同状态：代码已实现、命令退出成功、测试通过与功能已认证不能互相替代。历史证据和继承证据也不等于本次重新测试。`issues.yaml` 是公共问题台账，具体 campaign 的暂缓决定仍需写入其范围声明。

在仓库根目录检查契约及生成视图：

```bash
python3 adapter/tools/check-workflow.py --require-ready
python3 -m adapter.workflow report --catalog --check
```

返回 [Adapter 首页](../README.md)。
