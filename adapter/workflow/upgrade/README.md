<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 升级执行器

入口为 `python3 -m adapter.workflow upgrade --help`，操作流程见 [升级手册](../../upgrades/README.md)。当前实现只在显式命令下观察本地镜像、创建隔离 worktree、执行有界检查和接入已有功能验收；不会自行部署、改驱动、下载权重或发布版本。

| 文件 | 职责 |
| --- | --- |
| `cli.py` | 参数、计划校验、官方稳定版本发现、命令分发、非零退出码 |
| `gitops.py` | Git 完整 SHA、三方历史、含删除/新增的差异、worktree 归属 |
| `analysis.py` | 本地/上游双差异、AD 台账、功能映射、依赖闭包、旧证据失效 |
| `session.py` | 会话冻结、锁与事件、镜像绑定、隔离合并、审查、检查、M01–M10 handoff 与验收挂接 |
| `images.py` | inspect 后以镜像 ID 读取真实包版本，要求官方 RepoDigest 和架构匹配 |
| `gates.py` | 三方配对比较、七步离线交付复核、最终发布提案与回退记录 |
| `replan.py` | 根据明确接受的暂缓范围新建会话；保留旧 lock/state 原文，不继承运行验收通过状态 |

`status` 有未完成项时退出 2，完整状态仍输出 JSON；这是调度器可消费的 pending，不是异常成功。`prepare` 遇冲突也退出 2，保留合并现场。初始化、冻结、绑定镜像不接受覆盖式重试；已经存在的候选由 `prepare` 识别并保留。固定输入变化要新建 session，候选源码变化要重新审查、检查和冻结平台 campaign。

## 批量审查登记

大量迁移项可用 `review-batch --session ... --input review.json --reviewer ...` 原子登记。
输入包含完整 `candidate` 提交号和非空 `records`；每条要求 `id`、`path`、`sha256`、
`disposition`、`reason`。必须先做逐文件语义审查，不能由“无合并冲突”批量生成通过结论。
任一 ID、文件摘要、候选提交或理由无效时，整批不写入；缺失路径仅允许明确的删除/替代决定，
摘要为 null。输入原文保存到会话 `reviews/`，后续 `status` 检查摘要；这只登记源码审查，不替代实机门禁。

## 显式范围重冻结

计划可提供 `deferred_features`，每项必须包含 `disposition: deferred`、原因、接受人和接受依据。
必测基础项不可暂缓；保留功能的必需依赖不可暂缓；不能排除全部备选依赖。
其余被影响功能仍然必测，且其中每个 case 的断言和修订号保持不变。

```bash
python3 -m adapter.workflow upgrade replan-scope \
  --session <旧会话> --plan <经确认的新计划> --name <新会话名> \
  --reviewer <范围审核者> --reason <用户确认的范围依据>
```

此命令只允许变更计划名称和暂缓项；拒绝顺带修改版本、镜像、必测基础项、检查及性能阈值。
新会话保留旧 lock/state 的带摘要快照，共用原候选 worktree。只保留绑定同一候选提交的源码审查；
checks、comparison、campaign、delivery 均为空，必须重新执行或通过正常入口显式复核。
暂缓项会出现在 handoff 和 status 中，挂接 campaign 时必须逐项保留相同例外说明，不能被标为已通过。

## 比较文件

`compare --input` 接收会话内 JSON/YAML，结构为 `runs` 对象，恰好包含 `old_heyi`、`new_heyi`、`native` 三项。每项字段：

| 字段 | 含义 |
| --- | --- |
| `source_commit` | old_heyi 为冻结 H；new_heyi 为当前候选；native 为 null |
| `vllm` | 运行时观测版本；旧组与旧 stack 一致，新组与目标版本一致 |
| `conditions` | 必填 workload_sha256、weights_sha256、hardware（型号/卡数）、parallelism、load、sampling、metric_definition；三组完全相同 |
| `correct`、`failed_requests` | 配对正确性观察和失败请求数，必须 true / 0 |
| `metrics` | 正数且有限的 throughput、ttft_p95_ms、tpot_p95_ms |
| `artifacts` | 非空的原始文件列表，每项 path（相对比较文件目录）和 sha256；不能逃出会话目录 |

例如一组 `metrics` 为 `{"throughput": 1000.0, "ttft_p95_ms": 200.0, "tpot_p95_ms": 20.0}`；这些仅说明字段格式，禁止作为测量值填入现场结果。指标由相应 benchmark 工具及分析步骤规范化，不由升级执行器重跑 AIPerf。人工/AI 必须保留从原始输出到规范指标的分析记录；此门禁校验身份、可比条件、字节完整性和预算，不声称能够从任意格式日志自动证明统计值正确。真正功能认证仍要求独立 M01–M10 断言。

ratio = new / old。吞吐检查 ratio ≥ min_throughput_ratio；延迟检查 ratio ≤ max_latency_ratio。另报告 new / native，不强制 Dynamo 必须比原生更快。三个运行同时改变引擎时，输出明确写为整体软件栈升级差异；需要隔离 Dynamo 净收益时，应另跑同一引擎版本的原生/Dynamo 配对 campaign。

输入或原始文件摘要变化会使状态失效；比较失败保留编号记录。新增候选 SHA 后，旧配对报告不得复用。缺少对应旧模型/环境时报告阻塞，不用其他硬件或数据替代。

## 校验与边界

```bash
python3 -m pytest -c adapter/workflow/tests/pytest.ini -q adapter/workflow/tests
python3 -m adapter.workflow upgrade validate-plan --plan adapter/upgrades/targets/dynamo-1.6.2.yaml
```

`test_upgrade.py` 使用临时 Git 仓库模拟合并和冲突，测试版本冻结、镜像身份、scope 防缩小、证据失效及失败保留；CPU fixture 不认证任何芯片。历史缺失的 `workflow/core.py` 已恢复，并增加精确 gitignore 例外，防止上游 `core.*` 转储规则再次漏掉源码。
