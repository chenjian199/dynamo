<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 完整工作流收口实施记录

用户已确认：新现场材料全部写入 adapter/results，补齐四组缺口，更新工作量并定版。

1. 结果路径、作用域、替代依赖、功能输入指纹与证据继承。
2. 精确 case/recipe、版本与环境绑定、独立可执行验证。
3. stage/collect、资源 UID/进程恢复、AI 有界循环与工作区约束。
4. 当前矩阵、关联遗留、M02/M10 交付调用与导入验收。
5. CPU 负向测试、可用现场验证、统计与定版清单。

保留原始失败；不修改已封存包；不提交用户尚未要求提交的工作树。真实 AI/远端条件不足时记录事实，不以模拟通过替代。

2026-11-05：五项均完成。验收与范围例外见 validation.md / finalization.md；真实 AI 账号测试由用户明确暂缓。未创建 Git 提交。
