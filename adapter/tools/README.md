<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 工作流检查与项目统计

这里是维护 Adapter 的辅助工具，不负责启动推理服务。命令均从仓库根目录运行。

| 文件 | 用途 | 输出与统计口径 |
| --- | --- | --- |
| [check-workflow.py](check-workflow.py) | 检查目录迁移、入口、契约、历史证据摘要和生成视图 | 结构检查报告；`--require-ready` 同时检查工作流定版条件，不代表硬件测试通过 |
| [change-stats.py](change-stats.py) | 比较 `v1.5.1` 与当前工作树的适配改动 | JSON 增删统计，区分产品代码、验证代码、构建配置、文档和结果；物理行数包含注释、空行；使用临时 Git index |
| [project-stats.py](project-stats.py) | 按语言和模块统计某个已提交 Git revision 的项目规模 | 使用 Perl 与 cloc 2.06，生成 Markdown 和逐文件 CSV；不包含未提交工作树改动 |

```bash
python3 adapter/tools/check-workflow.py --require-ready
python3 adapter/tools/project-stats.py --help
```

`change-stats.py` 将完整统计写到标准输出，可重定向到本次 [results](../results/README.md) 目录。`project-stats.py` 需要显式提供 `--cloc` 和 `--output`，可通过 `--revision` 选择提交。两种统计分别回答“适配改动了多少”和“项目本身有多少代码”，不能混用，也不是工时估算。

[workload.md](../workload.md) 是已有适配工作量快照；[project-statistics.md](../project-statistics.md) 是项目规模报告。修改代码后如需最新数字，应重新统计并注明基准。
