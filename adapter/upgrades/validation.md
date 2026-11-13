<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 升级控制层实现验证

日期：2026-11-10。工作分支原提交为 `119c309c9004a0c46dcca96037466444633729c2`；本次没有移动该分支，也没有修改生效的三平台版本锁。没有创建真实升级候选、拉取镜像层、调用真实模型账户或部署硬件服务。

| 检查 | 结果 | 解释 |
| --- | --- | --- |
| 原有工作流 CPU 单元测试 | 91 passed | 恢复缺失 core.py 后验证既有流程 |
| 原有 + 新升级控制层测试 | 116 passed | 其中新增 25 项临时 Git / 镜像 / 证据 / 交付契约测试，无真实 GPU |
| Ruff | passed | 新增代码、恢复基础文件和 CLI 接入 |
| 升级计划 schema | passed | 固定官方 1.6.2 commit；三平台目标 0.31.0 / 0.30.0 / 0.28.0 |
| 完整工作流审计 | passed | 45 features、4,554 historical artifacts、0 errors |
| 生成目录检查 | passed | modules/support-matrix/version-baselines 未发生版本表漂移 |
| Skill 校验 | passed | 34 个既有 skill，未新增另一套 AI SDK 或技能入口 |
| 本地 Markdown 链接、git diff --check | passed | 无路径错误或新引入空白错误 |
| 隔离 Git index 提交预检 | passed | 267 个变更路径通过空白检查；237 个恢复日志的 Git blob 与原 SHA256 一致，未修改真实暂存区 |

运行环境使用已有 `/var/tmp/adapter-workflow/venv` 中的 pytest/ruff；工作流运行时依赖仍为已有 requirements.txt。检查命令：

```bash
python3 -m pytest -c adapter/workflow/tests/pytest.ini -q adapter/workflow/tests
ruff check --no-cache adapter/workflow/upgrade adapter/workflow/core.py \
  adapter/workflow/__main__.py adapter/workflow/tests/test_upgrade.py
python3 adapter/tools/check-workflow.py --require-ready
python3 -m adapter.workflow report --catalog --check
python3 scripts/validate_skills.py
python3 -m adapter.workflow upgrade validate-plan --plan adapter/upgrades/targets/dynamo-1.6.2.yaml
git diff --check
```

## 恢复已有工作流的两个缺口

上游 `core.*` 忽略规则误覆盖 `adapter/workflow/core.py`，文件没有进入旧提交，导致 CLI 无法 import。已从本机历史实施记录恢复原代码，补充精确例外并通过原有测试；这不是重新设计旧功能。

`*.log` 忽略规则也使部分结果日志未进入提交。此次从现有本地备份中按索引 SHA256 精确匹配，恢复 237 份原始日志，没有改写、重新运行或重新标注硬件认证。另外从原先保存的测试输出恢复旧定版 `pytest.log`。结果日志现在有单独忽略规则例外，可以随索引正常提交；`.gitattributes` 保留原始日志字节，不做换行转换或为格式检查改写带哈希的观测。

恢复索引和完整审计保存在 [implementation-20261110](../results/workflow/upstream/implementation-20261110/README.md)。旧 pytest.log 的测试数量属于当时的历史执行，不能与本次 116 项结果混同；恢复历史材料不产生新平台支持结论。

## 待镜像准备后才能执行

真实三平台镜像 inspect/metadata、SSH/设备/模型/集群 doctor、1.6.2 冲突迁移、厂商 API/通信库适配、旧/新/原生性能配对、M01–M10 实机认证和新离线交付仍待执行。真实 AI 账号调用也未在本次重新验证。这些不是本次 CPU 通过即可替代的项目。
