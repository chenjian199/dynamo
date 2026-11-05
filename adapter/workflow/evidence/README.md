<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 测量校验、功能认证与证据继承

本包决定某次执行能否成为有效认证证据。它消费冻结契约、原始测量、执行回执和文件摘要，不依赖 AI 的文字结论。

| 文件 | 作用 |
| --- | --- |
| [verify.py](verify.py) | 从每个 case 的 observations 计算契约规定的身份、行为、数值、输出一致性和清理断言，产生结果材料 |
| [qualify.py](qualify.py) | 再次校验结果结构、执行身份、原始文件 SHA256、回执与断言，给出认证结论 |
| [inheritance.py](inheritance.py) | 在版本、环境、模型、拓扑、契约及相关源码身份相同的前提下继承已有证据，并重新检查来源记录 |

原始材料放在 [results](../../results/README.md)。结果字段由 [result.schema.json](../../contracts/schemas/result.schema.json) 约束，但 schema 合法或 `passed: true` 不能替代真实测量。历史材料、继承证据、本次执行三种来源保持区分；继承不是重新测试。

认证一般由 runner 自动调用。排查失败时先查看对应 attempt 的测量和校验错误，不直接修改 `state.json` 或 `result.json`。操作入口与 `inherit` 使用条件见 [手册](../README.md)。
