<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 工作流 1.0.0 定版

2026-11-05，四组收口工作已落实。权威检查项为 [workflow-audit.yaml](../contracts/workflow-audit.yaml)。这里定版的是适配工作流及其验收规则，不是把所有芯片、模型、拓扑或历史材料重新认证为通过。

| 组 | 落地内容 | 验证入口 |
| --- | --- | --- |
| 功能与环境 | 45 功能 / 68 用例；精确脚本入口、模型/节点/拓扑/镜像绑定、实际包版本检查、独立测量断言、负向场景 | contracts、bindings、compile、verify、CPU 契约测试 |
| 范围与依赖 | required/optional/deferred/out_of_scope/not_applicable，all-of/any-of，相关代码与下游失效，原证据持续校验的等价继承 | scope、identity、inheritance 及负向测试 |
| 执行与 AI | stage/input/collect，Job UID/归属标签、PID boot/start 身份、丢失确认后恢复、防重复；独立源码快照、有界实施/再冻结/执行/诊断循环 | CUDA/Ascend 真实 CPU 控制路径演练；离线模型接口和编排测试 |
| 报告与交付 | 逐 case 当前状态及失效视图、失败尝试关联；M02/M10 构建/依赖/导出/校验/导入/回归/封存编排 | 交付编排测试、真实小镜像离线往返、结构与历史摘要审计 |

所有新现场记录与历史材料都在 `adapter/results/`。历史 plan-only 示例已迁入 results；不再使用仓库外的工作流结果根目录。大镜像/源码快照可保存在 results 的本地磁盘中而不进入 Git，清单、摘要、日志和状态纳入版本管理。既有外部封存包没有修改。

## 本轮现场发现并修复的问题

- Job 删除后不能立即视为资源消失，增加有界等待和 UID 再检查。
- 相同源码/环境的不同 campaign 不能共用资源归属标签，加入 campaign、feature、attempt 身份。
- 丢失本地确认记录后，从原有 Job/进程重新确认归属，避免再次创建；进程启动增加远端互斥。
- Docker 多架构索引可能引用本机没有的分片：显式选择目标架构导出，并对比该架构的镜像 ID。归档校验继续拒绝缺失 blob；旧版 Docker 通过能力探测使用兼容参数。
- 可选分支的依赖不自动升级为必需项；可选命令失败保留日志与 limited 结果，但资源清理仍须通过。

## 明确保留的边界

1. **真实 AI 账号验证暂缓。** 用户于本轮明确回答“否，暂时不需要验证”。WF-13 为 unverified、非本次定版阻塞。Codex/Claude/GPT 的离线接口和循环测试已覆盖；不声称做了真实模型调用或付费链路验收。
2. **没有在这次目录/工作流定版中重跑 GPU 推理或重建三平台大镜像。** Kubernetes 演练使用小型 CPU Job，离线往返使用小型 registry 镜像的测试别名；均带 fixture 标记，不进入芯片功能通过项。
3. **现场配方仍需真实参数和测量程序。** 智能体可按契约编写/调整它们；缺少权重、API、镜像或硬件时明确阻塞。工作流不承诺仅输入芯片名就能完成适配，也不能独立证明任意新写验证器的业务语义。
4. **已有芯片遗留保持原状态。** MetaX 集群、Ascend GMS/DRA、G4 远端缓存、独立 Omni 通道等见 [issues.yaml](../contracts/issues.yaml)，没有因工作流定版而变成通过。
5. **自动循环的硬预算是次数和时间。** 账户金额上限由供应商账户控制；中断后保留 inflight 与已产生资源，不自动重放不明副作用。

## 定版门禁

```bash
python3 -m pytest -c adapter/workflow/tests/pytest.ini -q \
  adapter/workflow/tests adapter/modules/m10-validation/common/offline/test_delivery.py
python3 -m adapter.workflow report --catalog --check
python3 adapter/tools/check-workflow.py --require-ready
python3 scripts/validate_skills.py
```

`--require-ready` 检查已声明定版范围，不代替测试。相关真实日志与范围说明见 [validation.md](validation.md)。后续改变验收语义应递增契约 revision；厂商版本、模型或相关代码变化应创建新 campaign，不改写旧证据。
