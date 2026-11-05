<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 执行与恢复手册

工作流将 M01–M10 拆为 45 个功能、68 个用例。AI 负责实施和提出现场执行配方，冻结器、执行器与独立验证器负责身份、执行记录和验收。新平台仍需要真实硬件信息、厂商版本锁、模型资产及有权限的测试环境；自动化不能从芯片名称推导这些事实。

## 实现文件导航

使用工作流可直接阅读下面的操作步骤；维护实现时按此表定位。默认入口是仓库根目录的 `python3 -m adapter.workflow`。

| 文件 / 包 | 职责 |
| --- | --- |
| [__main__.py](__main__.py)、[operations.py](operations.py) | CLI 参数与命令分派 |
| [core.py](core.py) | 功能目录、campaign 初始化、冻结身份、原子记录和锁 |
| [config.py](config.py)、[bindings.py](bindings.py) | 环境声明校验与逐 case 的节点、镜像、模型、拓扑解析 |
| [preflight.py](preflight.py)、[assets.py](assets.py)、[runtime_probe.py](runtime_probe.py) | 只读环境预检、模型资产摘要和运行环境包版本探测 |
| [scope.py](scope.py)、[identity.py](identity.py) | 范围/依赖选择、每功能输入指纹和相关功能失效判断 |
| [state/](state/README.md) | 计划编译、执行、重试恢复和有界 AI 循环 |
| [executors/](executors/README.md) | 本地/远端命令、文件传输和自有资源管理 |
| [harnesses/](harnesses/README.md) | Codex、Claude、API 接入与源码修改范围 |
| [evidence/](evidence/README.md) | 独立验证、认证和证据继承 |
| [reporting/](reporting/README.md) | 生成目录/矩阵/版本视图与定版审计 |
| [delivery.py](delivery.py)、[delivery-plan.md](delivery-plan.md) | 交付阶段编排及配方格式 |
| [regression/](regression/README.md) | 跨模块集群回归脚本 |
| [tests/](tests/README.md) | CPU 单元与契约测试，各文件覆盖内容及运行方式 |
| [requirements.txt](requirements.txt) | 工作流自身的 Python 依赖，不是推理引擎依赖 |
| [implementation-plan.md](implementation-plan.md)、[validation.md](validation.md)、[finalization.md](finalization.md) | 实施计划、验证记录和定版审计说明 |

输入格式见 [contracts/schemas](../contracts/schemas/README.md)，现场文件参考 [environments](../environments/README.md)，输出结构见 [results](../results/README.md)。

## P00 环境预检

所有新现场数据都存入 `adapter/results/<platform>/<chip>/<campaign>/`，包括日志、锁、源代码快照、远端回收材料和交付记录。远端工作区下也使用同样的 `adapter/results/` 相对结构。私钥、密码、API key 和 kubeconfig 不写入这些目录；环境清单只引用 SSH config 路径和 kube context。

`doctor` 使用 BatchMode 与严格 known_hosts 验证免密 SSH，检查工作区、真实 machine-id、设备、容器运行时、镜像、模型文件摘要及选中用例的 Kubernetes API/节点/RBAC。仅操作明确选择的节点/模型；缺少视频权重不能阻塞无关的文本用例。允许不同节点驱动版本不同，记录实际版本后回归。缺环境是 blocked，不是不支持。

每个 case 在 `environment.case_bindings` 中声明 `nodes`、`topology`、`image`、`models`，可指定 `containers` 或 `pods` 用于预检已有运行环境。镜像必须使用 digest，模型采用 ID、revision、manifest 与 manifest_sha256。运行中的包版本通过 `runtime_probe.py` 与 canonical stack 精确比对；稍后创建的运行环境由最终 observations 再核对。`tp/dp/pp/ep` 接受正整数，不设置已验证组合白名单。

模型 manifest 必须覆盖 config、权重分片及索引、tokenizer 等影响结果的文件；`asset_probe_timeout_seconds` 是全量摘要检查的有界预算。超时不算检查通过。预检不自动升级驱动、下载模型、分发私钥或删除工作负载。

## 冻结范围与精确配方

```bash
python3 -m adapter.workflow init --platform metax --chip C500 \
  --campaign onboarding --environment adapter/environments/site.yaml \
  --scope adapter/environments/scope.yaml
python3 -m adapter.workflow doctor --campaign adapter/results/metax/C500/onboarding
python3 -m adapter.workflow compile --campaign adapter/results/metax/C500/onboarding \
  --recipes adapter/environments/recipes.yaml
python3 -m adapter.workflow seal-plan --campaign adapter/results/metax/C500/onboarding
```

`site.yaml`、`scope.yaml`、`recipes.yaml` 需要由现场填写；参考 `environments/examples/`。`init --features M01-F01` 可代替 scope 文件。完整 scope 支持 required、optional、deferred、out_of_scope、not_applicable；暂缓/不适用必须有 reason、accepted_by、reference，不能视为通过。`dependency_choices` 对每个 any-of 组选定一个依赖；all-of 与选定的依赖进入闭包，不能排除必需依赖。

`compile` 从 case binding 中的 commands 或独立 recipes 文件构建任务。每个 case 必须有 run/observe/cleanup，prepare 可空；run 必须调用契约列出的精确入口。`observe` 将原始测量规范化到 `cases/<CASE>/observations.json`，字段由 `feature.yaml/checks` 定义，不能写固定 passed 布尔值。原生对照、数值、输出 token、二进制字节、资源数量等由受信任验证器重新计算；qualify 会再计算一次，不接受 AI 的自报通过。

`tasks.json` 保存 prepare → run → observe → cleanup → verify，verify 固定为 `adapter.workflow.evidence.verify`。每条命令使用 argv 与 local/ssh/container/kubernetes executor；无隐含 shell。`{repo}`、`{campaign}`、`{attempt}`、`{identity}` 按执行端展开，`{coordinator_attempt}` 指协调器路径。容器必须有 container_workspace，Kubernetes 固定 context/namespace。任务计划是授权的可信操作输入，不是恶意脚本沙箱。

源码、版本锁、环境、用例 revision 与计划分别冻结。每功能指纹包括实现路径和公共构建/验证输入；只让直接受影响功能及下游失效。公共验证器变化会保守地使所有功能失效。`inherit --source ... --feature ... --reviewed-by ... --rationale ...` 仅在版本、模型、拓扑、契约及相关代码输入完全相同时继承，并持续校验原始执行记录和字节。继承不是重新测试。

## 同步、执行与恢复

```bash
python3 -m adapter.workflow stage --campaign adapter/results/metax/C500/onboarding --node worker-1
python3 -m adapter.workflow run --campaign adapter/results/metax/C500/onboarding
python3 -m adapter.workflow resume --campaign adapter/results/metax/C500/onboarding
```

`stage` 上传有摘要的冻结源码，只写入本工作流拥有的独立 workspace；拒绝覆盖已有他人目录。创建 attempt 后可在 prepare 调用 `stage-inputs --node ... --feature ... --attempt attempt-001` 上传 task/envelope/preflight/resources。`collect --node ... --feature ... --attempt ... --promote-cases` 在 observe 阶段回收字节并校验；路径穿越、symlink 和覆盖既有证据都被拒绝。多节点共同生成同一 case 时，使用显式聚合程序，不能让后一次覆盖前一次。

有持续资源的任务通过 `launch-job --manifest ...` 或 `launch-process --node ... --name ... --argv ...` 启动；资源名须预先列入 tasks.resources。Job 记录 UID 与 campaign/feature/attempt 标签，进程记录 boot ID/start ticks。协调器丢失创建确认后可以按归属重新发现，重调启动接口不会重复创建。只按实际 UID 和进程身份清理，不按名称误删后来创建的资源。

```bash
python3 -m adapter.workflow observe-resources --campaign ... --feature ... --attempt attempt-001
python3 -m adapter.workflow recover --campaign ... --feature ... --stop-owned
```

`recover` 先观察，必要时清理本次资源，再生成带摘要的 absence proof，最后 reconcile；未知或仍运行的资源阻止重试。远端断连不证明任务结束。现场自定义资源类型可继续提供独立观察证据给 `reconcile --proof`。正常运行也检查资源是否确已释放，cleanup 返回零不代替观察。

进程或机器中断后保留原 attempt。新尝试另建目录，失败、日志与恢复证据不覆盖。run/resume/release 都重新检查已通过依赖和执行摘要，不只相信 state 中的状态。

## 有界 AI 自动循环

```bash
python3 -m adapter.workflow agent --campaign ... --feature M05-F02 --harness codex
# 上条仅输出 handoff，不调用模型。
python3 -m adapter.workflow automate --campaign ... --harness codex \
  --model YOUR_EXPLICIT_MODEL --max-cycles 3 --wall-seconds 7200
```

单次 `agent --invoke` 产生实施/配方建议；`automate` 才把实施、重新冻结、预检、执行、诊断及重试连起来。调用须已有现场执行授权，并在清单中启用 execution_enabled。每次实施使用 results 中的独立源码副本，只将允许路径的变更在原文件 SHA 未变化时应用。厂商锁、契约、验证器、指令和技能属于保护路径，AI 不能通过改验收门槛让任务通过。CLI 本身的主机权限仍由本机配置控制；快照和应用门禁不等于 OS 安全沙箱。

Codex、Claude、OpenAI Responses 使用相同 envelope 与持久化历史。API 提供有范围和 SHA 保护的文件工具，最多 20 轮，单轮输出与执行时间有界。切换 harness 时读取同一个 campaign/parent/loop 记录，不依赖聊天记忆。未自动选择模型、不绕过 CLI 审批；密钥只从环境读取。

循环预算冻结最大次数和墙钟时间；受供应商计费影响，**不是金额硬上限**，费用上限应在账户侧配置。原生引擎失败要求带摘要的原生对照后记录并停止自动修复；环境缺失、证据不充分及未知资源也停止，不通过改版本或隐藏断言推进。中断的 AI 循环保留 inflight，不盲目重放模型调用；先检查该次提案/补丁及资源，重新冻结一个 campaign 后继续。

本轮用户已明确暂缓真实 AI 账号验证；离线接口和编排契约测试不冒充 Codex/Claude/API 的真实模型调用。

接口依据：[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[Claude CLI](https://code.claude.com/docs/en/cli-reference)、[OpenAI Responses 状态](https://developers.openai.com/api/docs/guides/conversation-state)。

## 交付与定版

`report --catalog` 生成 modules、support-matrix、version-baselines：历史材料数量、原发布结论和当前 campaign 的逐 case 状态分开显示，附 freshness 与证据来源。`report --catalog --check` 检查漂移。

`deliver --campaign ... --plan ...` 按已冻结的交付配方调用既有 M02/M10 工具，顺序为 build、dependencies、export、verify_archive、import、smoke、seal。每步都有命令、日志和输出摘要；中断的副作用步骤不自动重复执行。导入 smoke 必须比对 runtime/dev/tools/operator 的实际 image ID、包版本、接口输出与对照，之后才调用既有 bundle.py。目录与最终 archive 都放在该 campaign 下。具体配方见 [delivery-plan.md](delivery-plan.md)。

交付状态 artifacts_sealed 仅表示完成归档。随后执行 M10 功能门禁并 `release --campaign ... --output <campaign>/release.json`，重新核验必需功能/依赖，记录 optional 失败和已接受暂缓。不能以 tar 存在宣称全部模块或所有拓扑已认证。外部历史封存包保持原字节，不为目录迁移改写旧包。

完整检查：`python3 adapter/tools/check-workflow.py --require-ready`。验收范围与仍暂缓的事项见 [validation.md](validation.md) 和 [finalization.md](finalization.md)。
