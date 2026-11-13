<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 将暂停中的 1.6.2 升级保存到 heyi

2026-11-13 用户确认：按当前状态先合并提交，暂时停止适配，之后再继续。
这取代此前“闭环后才合并”的操作安排，**不改变验收标准，不标记正式发布**。
完整现状、未完成项和恢复顺序见 [暂停交接](pause-20261113.md)。

## 执行命令

```bash
cd /home/metax/CJ_workspace/dynamo

# 保存当前材料，再合并固定候选；脚本中的两个提交均带 DCO sign-off。
bash adapter/upgrades/checkpoint-heyi.sh merge

# 上一步成功之后推送；不需要 force，也不会发布镜像或标签。
git push origin heyi

# 推送成功后，备份所有 Git 引用，仅删除两个 upgrade/... 临时分支。
bash adapter/upgrades/checkpoint-heyi.sh cleanup

git branch --list
git status --short
```

每一步失败即停止，不继续后续步骤。脚本是给用户执行的具体操作，不会由生成文档的过程自动运行。
`merge` 固定前置 HEAD 为 `119c309c90`、候选为 `2a239c2b50`；它不是可对任意新分支复用的升级工具。
如果提交 hook、合并或推送失败，应先查看 `git status`；脚本拒绝在不匹配的 HEAD 或未完成合并上重跑，不会 reset/覆盖工作。

## merge 保存的内容和保护条件

- 提交当前工作流、计划、暂停说明、现场结果及备份/清理记录；两个临时 worktree 中的未提交证据已按 SHA256 校验复制到主工作区。
- 不包含根目录的 H200 Word 报告、`reference`、私钥或 kubeconfig。新增 tar/zip/wheel/bundle 本体留在磁盘，提交对应索引和校验记录，不把大归档作为 Git blob。
- 合并 `upgrade/dynamo-1.6.2-ascend-20261111`，保留上游 1.6.2 和适配历史。源分支已是候选祖先，无需再合一次，也不合入更晚的 `main`。
- 仅自动处理演练确认的四个冲突路径：`.gitattributes`、`.gitignore`、升级入口 README 和范围说明。当前版本已包含上游生成文件规则以及新的暂停安排。其它冲突立即停止供检查。
- 合并前后检查产品目录 `components/container/lib/deploy` 及 `Cargo.toml/Cargo.lock/pyproject.toml` 与 `2a239c2b50` 完全一致，防止旧工作流或旧文件覆盖升级代码。
- 执行 `git diff --cached --check`；冻结原始结果使用按字节保留属性，不能为修复空白告警而重写原始证据。

提交信息：

```text
chore(adapter): checkpoint paused Ascend upgrade and cleanup evidence
merge: preserve Dynamo 1.6.2 Ascend adaptation at paused checkpoint
```

## cleanup 的范围

脚本先确认已合入候选、已推送至 `origin/heyi`、没有未提交的跟踪文件修改或未知本地分支。
随后生成并验证 `../dynamo-before-temp-branch-cleanup-<UTC时间>.bundle`，同时保存 SHA256 和验证日志。
只有备份成功后才执行：

1. 两个临时 worktree 转为 detached HEAD，保留原目录、原提交和未跟踪的原始材料。
2. `git branch -d` 删除已合入的两个 `upgrade/...` 分支名。
3. 核对本地 `main` 的提交与清理前一致，保留该分支，不合并、不删除、不移动它。

最终 `git branch --list` 保留 `heyi` 和 `main`。远端分支和已有标签不删除。
不删除临时 worktree 目录，因为冻结会话仍引用这些路径。转为 detached 后，旧升级会话只读；恢复执行应重新创建候选分支或从 heyi 新建冻结会话。

Git bundle 只包含已提交历史，不包含忽略的归档和未跟踪报告。四组独立下载备份及本地归档应另行保管。
