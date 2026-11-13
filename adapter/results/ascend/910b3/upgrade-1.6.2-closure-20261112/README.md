<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ascend 1.6.2 升级闭环执行记录

此目录保存审查、构建诊断、现场准备与执行控制器，**不等于完整功能认证或正式发布**。只通过标准工作流的冻结 campaign、独立断言、三方对照及离线交付门禁后，才能合并收口。

## 冻结边界

- 候选代码：`2a239c2b500ea7e48c86b7216b9878158db4802a`。
- 较早现场镜像构建代码：`64815280cf5ec3db7a7c0570197f585ea004d323`。其 M01、editable、M03 结果保留真实源码身份。已从 `2a239c2b50` 重新构建 runtime/dev/tools 与 Operator，正在导出及导入，最终门禁使用源码提交完全匹配的镜像，不重新标记旧镜像。
- 必须通过：38 个功能；接受暂缓：G4、Ascend GMS、完整媒体/VL/Omni、DRA、功耗控制，共 7 个功能。见仓库 `adapter/upgrades/ascend-1.6.2-scope-20261112.md`。
- CUDA 与 MetaX 多机不在本轮现场验收范围；没有把未测平台标成通过。
- `heyi` 不在此阶段合并、推送或删除临时分支。

## 证据导航

| 文件或目录 | 用途及限制 |
| --- | --- |
| `semantic-review-r22.json`、`upstream-migration-audit.md` | 当前 946 项迁移审查及上游差异审查；不替代运行测试 |
| 升级会话 `checks/014` | 当前 `2a239c2b50` 的检查收据：161 项工作流测试与静态检查通过；`checks/013` 的生成视图过期失败保留 |
| `status-after-source-review.json` | 标准升级门禁状态：现场 campaign、三方性能/正确性对照、封存交付仍待闭环 |
| `host-identity-environment.json`、`host-identity-live-preflight.json` | 52、7.5 的实际 DMI UUID 预检；保留两台克隆 OS 的重复 machine-id，未修改主机 |
| `launch-build-2a239c2b50.py`、`build-images-2a239c2b50.py` | 最新候选的 ARM64 runtime/dev/tools 构建控制器与真实源码快照 |
| `build-operator-2a239c2b50.py`、`install-operator-2a239c2b50.py` | 最新 Operator 已构建并安装到专用 heyi-upgrade162 release，Pod 已就绪；不等于 M08 全部功能认证 |
| `distribute-2a239c2b50.py`、`import-images-2a239c2b50-55.py` | 等待构建完成后执行完整层导出、52 与 55 导入及摘要核验；不得把等待状态记成通过 |
| `m01-campaign-launch.json`、`m02dev-campaign-launch.json` | 648 镜像的三个 M01 功能与 M02-F01 已由标准工作流验证；不是最终 2a 镜像认证 |
| `engine-resume-r2.json`、`engine-resume-r2-*.log` | 648 镜像的 M03 基础、生命周期及路由；原编译缺少继承功能 recipe 的失败记录不覆盖 |
| `comparison-r2-recomputed.json`、`comparison-r2-remote/` | 旧 HEYI / 新 648 / 原生三方原始结果重算；比值达到冻结阈值，但仍缺最终 2a 源码绑定 |
| `start-foundation-2a239c2b50.py`、`foundation-launch-2a239c2b50.json` | 等待最终镜像与前序清理后，冻结并串行执行 15 项基础功能；遇未通过功能停止该队列以便诊断 |
| `collect-image-bytes-2a239c2b50.py` | 收集完整镜像字节供协调端核验；52 的正式归档位置保持不变 |
| `prepare-campaign-inputs-r2.py`、`prepare-foundation-recipes.py` | 新冻结输入生成器。原生内存用设备拓扑，真实推理用模型/TP/DP/PP；旧冻结环境不修改 |
| `pin-comparison-images.py` | 从 containerd 内容存储核验 manifest/config 摘要并添加本地摘要引用 |
| `native-50424baf69/` | 较早 50424baf69 镜像的内存/集体操作诊断，不能计入当前最终镜像验收 |

远端现场输出保存在 52 的 `/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/`。镜像构建临时目录在 `/data/dynamo-upgrade-1.6.2-20261111/`。正式验收由候选 worktree 中的 `adapter/results/ascend/910b3/` campaign 管理。

失败、早期镜像和被后续尝试取代的记录均保留。不得用退出码、归档文件存在或历史测试通过替代标准验收。

## 新增测试辅助程序的状态

`wheel-case-*`、`images-case-*`、`offline-case-*`、`memory-case-*`、`native-case-*` 与相应 `normalize-*` 是标准执行器的现场适配器。它们记录命令、镜像 ID、源码、实际文件哈希及精确容器清理，最后交由冻结的标准验证器判定。脚本通过语法检查不等于对应功能通过；以 campaign 的 `state.json` 和独立断言为准。

`transport-trace.py` 仅对 Mooncake 测试探针的实际 API 调用添加日志，原样转发参数及返回值，不改产品源码；它用来区分实际初始化协议与仅写在配置中的协议。尚未据此认证传输功能。

三方对照的旧 HEYI 使用 vLLM `0.23.0+empty`，原生和新 HEYI 使用 `0.30.0+empty`。这衡量整个版本栈的升级，不能归因于 Dynamo 单独的性能增益。完整结果、历史失败与未完成门禁均保留。

## 当前续跑队列

- `engine-collection-diagnosis.json`：旧 648 的 M03-F03 远端执行与清理成功，采集器误判入口导致标准任务失败。该失败不覆写；新的 `engine-case-controller-r2.py` 在最终 2a 镜像上重新执行。
- `start-foundation-r2-2a239c2b50.py` / `foundation-launch-r2-2a239c2b50.json`：等待前序结束且再次观察容器确已删除，才启动最终 15 项基础功能。不继承旧 648 镜像结果。
- `comparison-2a239c2b50/` / `start-comparison-2a239c2b50.py`：同一冻结工作负载、节点与压测客户端重测最终新 HEYI arm；原生和旧 HEYI 原始字节保留在 comparison-r2-remote，当前新 arm 还未完成；`collect-comparison-2a239c2b50.py` 完成收集后才调用独立重算器，不提前提交门禁。
- `start-transfer-2a239c2b50.py` / `transfer-launch-2a239c2b50.json`：基础阶段全部通过后，继承完全相同输入的依赖，执行 M05 布局、GPU 传输、2P3D 及取消/故障/30 分钟至少 1000 请求长稳。任何失败均停止该队列，不绕过门禁。
- `cache-case-*` / `normalize-cache-case.py`：准备中的 G2/G3 原生对照与 token/实际搬运字节检查，尚未启动，不计为通过。另有 `cache-routing-*`，记录两台实际原生 TP1 worker 的同请求回放、原生 resident-prefix 输出及 Dynamo 磁盘路由；尚未实测。

完整镜像归档 `image-bytes-2a239c2b50/images.tar.gz` 的主副本仍在华为 52。本机正在收集相同字节供交付门禁核验；SHA256 为 `a7f7809603a495a6c95e3e33bcada14f9e09396c644f08b0c2c986a33e25e034`，11,100,930,782 字节。大文件继续位于 results 中，本地 `.git/info/exclude` 仅排除 `images.tar.gz*`，避免把多 GB 镜像塞进 Git；收据、索引、校验和及测试结果仍可提交。

## 最终镜像对照门禁

`audit-comparison-2a239c2b50.py` 已逐请求重算最终镜像对照；标准升级会话的 `comparisons/ascend/001.json` 通过，绑定当前源码 `2a239c2b50`。每个 arm 256 个计量请求，输入 128、输出 32 token，并发 4，另含预热及语义检查。新 HEYI 吞吐 1.7970 req/s，TTFT p95 212.810 ms，TPOT p95 68.776 ms；相对旧 HEYI 分别为 0.95735、1.05406、1.06338，满足冻结的吞吐不低于 0.9、延迟不高于 1.1 的阈值。不能将这组数字写成性能提升。

完整原始对照及按字节复制的门禁输入位于升级会话 `comparison-inputs/2a239c2b50/`。该门禁通过不替代剩余 38 功能认证或封存交付。

基础执行输入 r2 曾因设备列表 `[0,1]` 不符合 topology 整数计数 schema，在 init 时拒绝；尚未启动对应 campaign。旧输入和错误日志保留，r3 使用 `devices: 2`，实际设备序号继续由 Docker inspect 与 SDK 输出独立验证。当前队列：`start-foundation-r3-2a239c2b50.py` → `start-transfer-r2-2a239c2b50.py` → `start-cache-r2-2a239c2b50.py`。使用 `campaign-inputs-r3-2a239c2b50/`；基础预检及封计划均已通过，正在进行 M01 实测。之前 r1/r2 队列退出记录均保留，不能覆写成成功。
