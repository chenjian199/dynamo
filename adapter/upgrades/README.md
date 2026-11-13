<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 跟随上游版本的自动升级工作流

本入口在原有 M01–M10 之上增加升级控制层：冻结旧上游 B、HEYI 适配 H、目标上游 T，分析两组差异，在隔离分支迁移适配，再交给现有功能执行器、AI harness 和交付流程验收。不会把合并成功或旧结果复制解释为升级通过。

升级控制层已启动实际迁移。2026-11-11 按用户确认的 [Ascend 优先范围](targets/dynamo-1.6.2-ascend.yaml)，在隔离 worktree 合并 Dynamo 1.6.2，并适配 vLLM-Ascend 0.30.0。**候选 `1aabe435ad` 的主要现场回归和候选归档已完成，正式升级门禁尚未收口，未认证发布。** 合并后以 `container/platforms` 的版本锁为准；[目标意向](targets/dynamo-1.6.2.yaml) 不是第二份生效版本锁。详见 [实测汇总和限制](../results/ascend/910b3/upgrade-1.6.2-preflight-20261111/upgrade-summary.md)、[华为 52 离线包索引](../results/ascend/910b3/upgrade-1.6.2-preflight-20261111/offline-candidate-index.json) 和 [11 月 12 日门禁快照](../results/ascend/910b3/upgrade-1.6.2-preflight-20261111/upgrade-gate-20261112.json)。CUDA 回归及 MetaX 多机测试按用户要求暂缓。

**2026-11-13 暂停交接：** 华为机器已按用户要求备份、清理并退出测试。用户确认以当前状态保存和合并源码，保留本地 `heyi` 和 `main`，只删除本次创建的两个 `upgrade/...` 临时分支，后续继续。最新候选为 `2a239c2b50`，946 项迁移审查与最终候选三方对照已有证据；最终基础 campaign 的 7 项通过，剩余功能及正式交付门禁未闭环，仍未认证发布。此前 `1aabe435ad` 的材料是历史快照，不代表当前源码验收。详见 [暂停状态与续跑步骤](pause-20261113.md) 和 [更新后的合并命令](merge-heyi-1.6.2.md)。

2026-11-12 已按用户确认的 [暂缓范围](ascend-1.6.2-scope-20261112.md) 建立 `dynamo-1.6.2-ascend-scoped-20261112` 会话：38 项必验、7 项明确暂缓。暂停合并不改变该范围，不改写旧会话或失败记录。

2026-11-10 进展：沐曦和华为目标镜像的元数据核验通过；用户确认 H800 4.6 镜像仍在下载，CUDA 回归暂缓。详见 [镜像准备状态](image-readiness.md)。

| 位置 | 内容 |
| --- | --- |
| [validation.md](validation.md) | 本次 CPU 验证、历史材料恢复及尚未执行的实机范围 |
| [design.md](design.md) | U00–U08、状态、隔离方式和完成标准 |
| [targets/](targets/README.md) | 每次升级的待验证目标、静态检查与回归预算 |
| [images-1.6.2.md](images-1.6.2.md) | 官方拉取地址、架构选择、拉取后版本观察 |
| [工作流代码](../workflow/upgrade/README.md) | CLI、差异分析、镜像探测、回归与交付门禁 |
| [计划 Schema](../contracts/schemas/upgrade-plan.schema.json) | 计划的机器校验格式 |
| `adapter/results/workflow/upstream/<session>/` | 全局升级记录：lock、analysis、state、events、checks、comparisons、handoffs |
| `adapter/results/<platform>/<chip>/<campaign>/` | 原有逐平台 M01–M10 功能证据；在候选 worktree 中执行时落在该 worktree 的同一路径 |

## 操作顺序

以下为**镜像准备好之后**的执行示例。先提交本次工作流代码，确保选中的 HEYI 提交包含工作流实现；不要将未提交的工作区视为升级源。主仓库使用 `heyi` 工作分支；上游通过固定标签和提交引用，不要求另建本地 `main`。

```bash
python3 -m pip install -r adapter/workflow/requirements.txt
python3 -m adapter.workflow upgrade --help
python3 -m adapter.workflow upgrade validate-plan \
  --plan adapter/upgrades/targets/dynamo-1.6.2.yaml

# 可选：只查看官方 latest stable 元数据，不下载或自动升级。
python3 -m adapter.workflow upgrade discover

# 明确从官方仓库取固定标签；不会移动本地分支，也不使用 --force。
git fetch https://github.com/ai-dynamo/dynamo.git refs/tags/v1.6.2:refs/tags/v1.6.2

python3 -m adapter.workflow upgrade init \
  --plan adapter/upgrades/targets/dynamo-1.6.2.yaml --name dynamo-1.6.2-r1
```

`init` 把 HEAD 解析成完整 SHA，验证目标标签为计划中的官方 SHA，固定平台目标、旧版本锁、功能目录、必测范围及检查预算。工作区存在源码改动时拒绝冻结；结果目录的增长不会被误判为源码改动。维护版 B 与 T 可以分叉，但 B 必须是 H 的祖先，H/T 必须有共同历史。

在各平台机器执行 [probe-image](images-1.6.2.md)，将观察结果回收至本会话目录，然后绑定三个 receipt：

```bash
UPGRADE_SESSION=adapter/results/workflow/upstream/dynamo-1.6.2-r1
python3 -m adapter.workflow upgrade bind-image --session "$UPGRADE_SESSION" \
  --platform cuda --receipt "$UPGRADE_SESSION/images/cuda.json"
python3 -m adapter.workflow upgrade bind-image --session "$UPGRADE_SESSION" \
  --platform ascend --receipt "$UPGRADE_SESSION/images/ascend.json"
python3 -m adapter.workflow upgrade bind-image --session "$UPGRADE_SESSION" \
  --platform metax --receipt "$UPGRADE_SESSION/images/metax.json"

# 此命令创建隔离 worktree 并执行三方合并。
python3 -m adapter.workflow upgrade prepare --session "$UPGRADE_SESSION"
```

候选位置为 `.worktrees/dynamo-1.6.2-r1`，分支为 `upgrade/dynamo-1.6.2-r1`。合并使用 `--no-ff --no-commit`；保留冲突供人工或当前 AI 会话逐项迁移。重复 `prepare` 只报告已拥有的 worktree，不重新合并、不清理冲突、不覆盖同名目录。

## 迁移与审查

读取 `analysis.json`：`ledger` 每个 `AD-...` 项对应一个 HEYI 改动路径，包含所属功能、影响平台和与上游是否重叠。`upstream` 保留 B→T 的所有路径变化。重命名按删除＋新增计入，避免只看到其中一端。历史结果单独列在 `historical_paths`，仍保留在 Git 历史中，不会当作本轮验证。

智能体按台账阅读原意、B/H/T 文件、上游接口与当前功能契约，实施必要修改。在候选 worktree 内依据 receipt 更新唯一生效的 `container/platforms/<platform>.json`：实际 RepoDigest、架构、实际包版本、Python 路径，以及经过审查的构建选项。不能仅修改 vLLM 数字，也不能直接套用旧 Mooncake/NIXL patch。新上游能力需要映射到契约或登记延期，不会因文件无冲突而自动变成支持。

在候选 worktree 内完成修改、`git diff --check` 和必要检查后，用 `git commit -s` 保存。再回主工作区登记审查：

```bash
python3 -m adapter.workflow upgrade review --session "$UPGRADE_SESSION" \
  --item AD-<analysis中实际ID> --disposition rewrite \
  --reason '说明保留的行为、目标接口、替换方式和关联测试' --reviewer '<审查者>'
python3 -m adapter.workflow upgrade review --session "$UPGRADE_SESSION" \
  --item upstream --disposition reviewed \
  --reason '记录新增功能、删除接口、依赖/CRD变化与契约映射或延期依据' --reviewer '<审查者>'
python3 -m adapter.workflow upgrade check --session "$UPGRADE_SESSION"
```

路径处理方式为 `retain / rewrite / upstream_replaced / remove / blocked`。`remove` 必须说明原能力如何保留或为何明确退出范围；台账记录本身不能让功能通过。审查和检查都绑定候选 SHA，后续提交会失效，失败日志按 attempt 留存。

## 交接现有 M01–M10 和 AI 执行器

```bash
python3 -m adapter.workflow upgrade handoff --session "$UPGRADE_SESSION" \
  --platform ascend --chip 910B3 --environment /path/to/ascend-site.yaml
```

返回候选 cwd、建议 scope 和 argv 命令。先审核 `scope.proposed.json`，根据实际部署补 `dependency_choices`（例如 M07 的传输实现），补充各 case 的 site binding 和预算。生成器不会替现场猜测网络、模型、SSH、驱动或 Kubernetes。缺环境保留阻塞，尤其不能把 MetaX 暂无集群当作已认证。

在返回的候选 cwd 中依次 `init → doctor → compile → seal-plan/run`，或使用现有 `automate --harness codex|claude|openai --model <明确模型> --max-cycles ... --wall-seconds ...` 执行有限循环。stage、collect、deliver 仍使用 [现有操作手册](../workflow/README.md)。候选有后续源码修改时，先提交并重新冻结 campaign；旧数据不修改为新结果。此控制层不新增另一套 AI SDK，也不代为选择收费模型或调用账户。

本次三个平台的引擎都改变，因此生成器默认保守选中全部功能及依赖。若用户明确批准暂缓，可在新计划中声明 `deferred_features`，使用 `replan-scope` 创建新会话；不能编辑旧 lock、analysis 或失败记录。命令拒绝暂缓 mandatory 功能或必需依赖，也拒绝同时更换版本、镜像或降低检查门槛。只复用同一候选提交的源码审查，现场验收、比较和交付门禁重新绑定。暂缓项单独列示，不记为通过；其余功能的必测用例和断言保持不变。

## 比较、离线交付与最终收口

对每个平台执行旧 HEYI、新 HEYI、新版本原生引擎三组对照。固定权重/量化、硬件与卡数、并行拓扑、负载、采样及指标口径。按 [比较输入说明](../workflow/upgrade/README.md) 写入标准化结果，原始输出、分析过程与 SHA 必须一起留存；吞吐比较不是每卡数不同或缓存容量不同条件下的简单除法。

```bash
python3 -m adapter.workflow upgrade compare --session "$UPGRADE_SESSION" \
  --platform ascend --input "$UPGRADE_SESSION/comparison-ascend.json"

python3 -m adapter.workflow upgrade attach-campaign --session "$UPGRADE_SESSION" \
  --platform ascend --campaign <候选worktree内已验收campaign的绝对路径>
python3 -m adapter.workflow upgrade attach-delivery --session "$UPGRADE_SESSION" \
  --platform ascend --input <该campaign内的delivery-state.json>
python3 -m adapter.workflow upgrade status --session "$UPGRADE_SESSION"
python3 -m adapter.workflow upgrade promotion-plan --session "$UPGRADE_SESSION" \
  --rollback <已审查的rollback.json>
```

CUDA、MetaX 同样执行。attach-campaign 调用候选版本的真实 `release` 审计器重新检查身份、独立断言、文件摘要和新鲜度；不接受手写一个 `passed: true`。交付复用 M02/M10 七步程序，复核 runtime/dev/tools/operator smoke、归档哈希与各阶段证据。

`rollback.json` 必须包含旧 `source_commit`、`reviewed_by`，以及非空的 `image_restore`、`crd_schema`、`persistent_data`、`traffic_switch`、`verification`。CRD/数据发生不可逆变化时先解决回退策略，不能只写“换回旧镜像”。

最后生成 `promotion.json`，其状态是 `ready_for_human_promotion`，不会推送、移动 main/heyi、创建发布标签或切换线上服务。**完成适配和验证、生成可发布提案、实际发布，是三个不同状态。**
