<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Adapter 工作流约束

本目录负责适配编排、验收和交付；产品实现仍在 components/lib/container/deploy。

- 从 README、contracts/policy.yaml 和所选 feature.yaml 开始，先 doctor，再读取冻结的 lock.json。
- AGENTS.md 是唯一智能体指令源，CLAUDE.md 只写 `@AGENTS.md`；skills 只存于根 .agents/skills。
- 新平台引用 container/platforms 中的版本锁，禁止重复维护引擎版本。引擎原生支持的 TP/DP/PP/EP 参数不能因未测试而加入白名单。
- 功能编号、case 编号不可回收；改变验收语义递增 revision。实施、任务、测试、认证状态独立。
- 缺少免密 SSH、镜像、模型或集群时报告具体阻塞。不要把没有环境说成不支持；不要自行升级驱动、清理他人任务或下载大模型。
- 任务先固定模型、拓扑、环境、预算和清理范围，使用独立验证器；退出码为零不等于功能通过。
- 原生问题必须有可比原生对照；旧版本、源码挂载、mock 和最终镜像证据不可互换。
- 每次尝试留存，失败不覆盖；中断后先检查记录的进程和远端 Job，再提交带哈希的清理证据并 reconcile。
- 历史材料只是材料。规范化后的文本与原始 SHA 分开，不能宣称重新执行；外部封存包不可改写。
- 新增现场输出与历史材料统一存于 adapter/results/<平台>/<芯片>/<campaign>/，包括任务、日志、状态与证据索引；不要另建仓库外结果根目录。凭据、SSH 私钥和 kubeconfig 不入库。
- report --catalog 生成 modules.md/support-matrix.md/version-baselines.md；修改权威输入，不手改生成视图。
- agent 输出是建议。实际测试经执行器及证据门禁；代码变化后重新冻结 campaign。切换 AI 时读取同一 campaign，不依靠对话记忆。
