<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 工作流自身的验证记录

此处验证执行器和工作流约束，不是 CUDA、Ascend 或 MetaX 的完整功能认证。

| 目录 | 内容 |
| --- | --- |
| [cpu-controls/](cpu-controls/) | 真实 SSH / Kubernetes CPU Job 控制、归属检查与清理、源码及输入传输、离线镜像导出导入、定版检查记录 |
| [plan-only/](plan-only/) | 已迁移的仅规划示例；没有执行成功的含义 |
| [packaging/20261105-path-fix/](packaging/20261105-path-fix/) | M02 测试包与 M10 补丁刷新路径修复的 CPU 回归；实际复制文件，替代 Docker 调用 |

`cpu-controls` 中不同日期和 `rN` 目录代表不同尝试。失败的调度、清理或镜像归档记录与后续修复后的记录共同保留，不能只看到后一个成功目录就抹去前一次失败。具体采用哪些记录、覆盖什么范围，见 [定版验证说明](../../workflow/validation.md) 和 [定版审计](../../workflow/finalization.md)。

这里的离线 AI 接口测试使用替身，不代表真实 Codex、Claude 或 GPT 账号调用已经通过。已封存目录内的文件及 SHA256 清单保持不变；说明文档放在本层，不插入封存内容。
