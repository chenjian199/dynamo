<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Dynamo 1.6.2 / Ascend 0.30.0 升级现场记录

状态：**主要现场回归结束，正式升级门禁待完成；尚未发布或完成全套认证**。本轮优先华为，CUDA 与沐曦多机按用户要求暂缓。

优先阅读 [升级验收汇总](upgrade-summary.md) 和 [候选离线包使用说明](CANDIDATE-USAGE.md)。
下文按时间保留迁移过程，早期“尚未完成”描述对应当时的候选，不能替代最新汇总。

- `inventory.json`：节点、设备、运行负载与磁盘盘点。
- `npu-probe.json`：固定镜像下的原生 NPU 张量探测。
- `native-small-run.py` / `native-small.json`：Qwen3-0.6B TP1 原生基线执行脚本、完整命令与两条请求 token 输出。
- `native-small-tp2-run.py` / `native-small-tp2.json`：相同权重和请求的 TP2 原生基线，记录正常生成、进程退出与容器清理。
- `freeze-model-manifest.py` / `model-small-{52,55}.json`：两节点现有模型文件的完整 SHA256 清单，内容一致；未下载或改写模型权重。
- `local-checks/`：按尝试保留的构建与 CPU 测试日志，包括失败和修复后的结果；不能替代最终镜像的硬件认证。
- `vendor-api-sources.json` / `vendor-api-extra.json`：固定镜像内厂商源码及摘要，用于逐项迁移 0.23→0.30 补丁，包含原始许可证声明。
- `progress.json`：范围、候选工作树、正式升级 session 和未完成门禁。
- `*.stderr.txt`：对应 SSH 操作 stderr，保留原始观察。

宿主机驱动与厂商建议测试组合不同，但原生张量和小模型已实际通过；不据此推断多卡、多节点、大模型或 GMS 全部受支持。未修改宿主机驱动，未停止其他业务。

当前主 `heyi` 工作区保持原提交。升级在隔离 worktree 完成，上游合并、源码审核和新证据通过后才推进验收。

截至候选提交 `c753fad8eec66440770c3101606517cc9e3fb181`，升级工作流四项冻结检查通过，使用新 1.6.2 Rust 运行库的 Profiler 单元测试 452 项通过，生命周期与启动失败清理测试 8 项通过。原始较宽 vLLM 单元测试中的无设备 MetaX 插件探测失败仍保留；将使用华为最终运行环境补测，不据此修改原生设备探测逻辑。ARM64 构建、最终镜像、Dynamo/NPU 推理、多节点及大模型回归尚未完成。

## 2026-11-11 后续构建与原生诊断

候选 `a97c71d2b17feba59ce73da5b2844e604b3b684b` 已通过冻结的四项升级检查。新增完整 wheel 依赖解析，修复厂商镜像 Pydantic 2.14.1 超出 Dynamo 上限的问题；解析结果选择 Pydantic 2.13.5、redis 8.1.0，厂商引擎版本不变。此前 r4 的 runtime/KVBM/native helper 编译完成；r5 成品镜像仍在构建，不能以编译代替硬件验收。

DeepSeek V4 Flash W8A8 原生 TP8/EP 第二次测试完成 22 次请求并清理容器，固定种子出现 4 种输出，严格确定性断言失败。详见 `native-deepseek-tp8-r2-summary.json` 和原始记录；这是无 Dynamo 的原生诊断，后续对比仍需相同输入与调度条件。第一次测试的参数校验失败亦保留。

Operator 前两次构建失败于镜像或 Go 模块网络下载。第三次使用可访问的模块镜像，保持 go.sum 校验和原生许可证/SBOM 构建阶段。没有绕过构建门禁。

## 运行时配置兼容性修复

`a97c71d2b1` 成品 runtime 已构建并通过安装审计。实机 CPU 平台检查通过，独立 KVBM 的两卡 replicated 6 组数据校验通过；KV 路由启动失败，原始记录见 `remote-r5/candidate-a97c/runtime-smoke-r1/`。根因是 Ascend 0.30.0 的严格配置 dataclass 拒绝 Dynamo 的 `consolidator_endpoints` 扩展键。

候选 `5cbe424879e88f3da56e3f99054f8bcfde49165c` 在精确摘要保护的 Ascend 补丁中，仅过滤 Ascend 构造参数中的三个 Dynamo 自有键，保持原始 VllmConfig 字典以及其它未知字段的拒绝行为。补丁应用、输出摘要、幂等性、完整还原、未知源码/版本拒绝均已验证，见 `vendor-patches-r2.json`。同一候选修正了旧 M10 验收脚本写死的引擎版本和提交号，并通过 121 个工作流测试及其余冻结检查。其 runtime 已重建，硬件复测仍在进行。

Operator 新镜像 `heyi/dynamo-operator:1.6.2-heyi-upgrade-5cbe424879-arm64` 已完成原生构建、许可证/SBOM 生成、三个 CLI smoke 和归档，镜像配置 ID 为 `sha256:e47bd27dacdd78bf76b5ab3bd0f2ddae3b84d7668da474ae3db34c284c2eed24`。尚不能以镜像构建成功代替 Kubernetes 验证。

## 09:14 UTC 现场进度（未最终认证）

`5cbe` runtime 已通过 CPU、独立 KVBM 六组实机校验、双 worker KV 路由断言及聚合推理。M07 本地张量/媒体四项通过；七项已知不在本轮 Ascend 支持范围的 NIXL 用例未执行，不能计为通过。runtime/dev/operator 均已构建，runtime 与 Operator 已在 52、7.5 的 containerd 导入，归档哈希一致。新版 Operator 已通过 Helm 安装与就绪检查，DGD 集群端校验通过，7.5 两 worker 正在预热。

验收脚本的初次失败完整保留：runtime 身份检查误导入可选 Planner 依赖；高级能力 harness 漏拷贝两个启动辅助脚本；KVBM 的旧块数设置未留出新版 vLLM 的 null block；在只映射两张卡的容器内误用了宿主机卡号而非逻辑 0/1。修正只影响测试驱动/环境绑定，未放宽缓存淘汰、输出一致性或版本锁断言。各次输出使用不同目录。

集群存在旧 `heyi-m08` 测试遗留的 Webhook/RBAC，而对应 namespace 已无 Pod 且全局无 DGD/DGDR/DCD。先备份，再用 UID precondition 删除 24 个该旧测试对象；其它工作负载与 CRD 数据未删除。Profiler/Planner tools 镜像构建受不可达 IPv6 下载链路影响，r3 采用 build-scoped IPv4 DNS 与包镜像重试，未修改宿主机网络或依赖版本锁。

## 09:50 UTC 增量证据（未最终认证）

`remote-0948/RAW_SHA256.json` 固定了本次回收的 328 个原始文件，归档 SHA256 为 `f8dacff416f5dc74a0c2bfeb159f25a4ca3e87ed0f437d1d905428391e313d96`。运行中的长稳文件只是此时刻快照，不能当作结束结果。

`5cbe` 镜像的 2P3D 六条交接组合、Qwen3-32B TP4 六条 API 请求、Operator 扩缩容后的原请求回放通过。LoRA DP2 与 n-gram 复测通过；tools 镜像在隔离解释器中通过 115 项测试及 CLI/CPU smoke。tools 初次下载镜像站缺少精确 tfp-nightly 版本，改用官方 PyPI 后成功，未替换锁定版本。

Ascend 0.30 将 K/V 暴露为共享存储的交错页视图。候选 `538ad1bd21` 新增有边界、格式、步长和互补地址校验的零拷贝合并，用于 KVBM 注册；异宽 TP 补丁保留实际 block stride。CPU 布局测试 10 项、精确厂商补丁安装/恢复检查 6 项通过；NPU ND 格式双向写入诊断通过，但最终镜像 host/disk KVBM 尚待复测。非 ND 诊断被按预期拒绝，记录保留。

`49ef509336` 修复最终镜像测试脚本对 NPU 容量日志的识别，正在构建 runtime/dev/tools。`ce62a9c227` 仅修正 tools 测试子进程的运行时平台环境，冻结升级检查通过；它不是正在构建的 49ef 镜像提交，两者结果不混用。旧 H `bd801c6234` 已按 Ascend 0.23 官方基础镜像重建，其本轮输出与历史 1.5.1 报告分开保留。

新增算术问法 `What is 2+3? Answer only the number.` 在小模型返回 `2`，尚未完成原生归因，不作为已修复或已通过。缩容使用缩容前完全相同的六条请求回放，保持原有输出一致性断言。DeepSeek 原生 CLI API TP8/EP 正在 7.5 启动，后续还需升级镜像与 Hybrid PD 对照。

## 2026-11-11 10:48 UTC — DGDR 候选接入修复与新构建

新候选 `1aabe435ad56922cf7e12f92f64df2dc39531ccb` 补齐 AISimulate 0.13 的后端身份、单 worker 按阶段覆盖和厂商默认传输转换。真实 Go 合并器集成通过，保留严格未知组件检查和自定义 connector；未启动 CUDA 回归。完整 Profiler 单测首次为 465 通过、1 项失败（无覆盖配置时额外新增后端字段）；修正后相关 136 项通过，失败日志保留。交付渲染的旧路径已修正，两平台静态渲染通过。新 runtime/dev/tools 正在构建，不能将 49ef 镜像结果冒充为新候选结果。

49ef 镜像的跨节点 2P3D 六组合、故障恢复、取消及 Grove/KAI gang/扩缩容/重建已通过并清理；传输日志显示 AscendDirectTransport。TP2 磁盘 KVBM 和 dev 完整可编辑安装通过。227 个原始文件收集到 `remote-1042`，归档 SHA256 为 `8e2c501985847a8b2f05669a1076d78a4cabcd9c913a1129f4563783f6db2515`。离线 Prefill 测量完成，Decode 正常逐点测量。

## 2026-11-11 11:21 UTC — 最终镜像与三组对照

`1aabe` runtime/dev/tools/Operator 已构建；runtime 在52与7.5导入，tools在52导入，Operator就绪。最终tools安装审计和129项测试通过。旧HEYI `bd801`/vLLM0.23、新HEYI `1aabe`/vLLM0.30、原生vLLM0.30，使用同一910B3节点、TP1、512MiB KV、ISL128/OSL32、并发4，每组两个128请求测量轮次，均零失败。独立重算后升级比较门禁通过：新/旧吞吐1.03097、TTFT p95 0.95815、TPOT p95 0.98255；新/原生吞吐0.98950。此为小模型固定负载整体软件栈回退检查，不是Dynamo独立收益或大模型性能结论。完整原始证据位于升级session的`comparison-evidence/comparison-r1`，入口`comparison-gate-r1.json`固定其路径及摘要。

最终镜像的双卡硬件/KVBM、离线插值、DGDR TP1/TP2搜索仍在执行。49ef旧候选的离线5/3网格和复用已通过；其快速15秒在线决策出现新worker就绪前再次缩容，保留为未通过的扩容观察，不能以desired副本增加代替ready验收。

## 2026-11-11 11:50 UTC — 最终镜像实机验收与离线候选包

`final-acceptance-52-1140` 和 `final-acceptance-55-1140` 分别收集282和89个文件；解包后逐文件复算 `RAW_SHA256.json` 全部一致。最终 `1aabe` runtime 的独立KVBM、双卡推理/KV路由、主存与磁盘卸载、本地媒体检查通过；dev Python与完整Rust editable安装通过。Qwen3-32B TP4的六条请求与原对照逐字一致；合成LoRA DP2与ngram测试通过。合成LoRA不能替代真实训练适配器精度验收，本地媒体检查不能替代完整VL/Omni模型。

`rdma-final-r1` 使用最终runtime、两节点各一张NPU，READ/WRITE各覆盖4B、4KiB、512KiB、4MiB、16MiB与两轮。双端共40项字节校验通过、释放后分配量为0；HCCN RC收发计数交叉一致，空闲对照RC增量为0。该结果证明实际AscendDirectTransport数据路径，不代表Hybrid PD或带宽/SLA验收已完成。

四角色离线候选目录为华为52上的 `/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate`。主镜像归档SHA256为 `5d54a16e7625e837b44eb9dda84d5cca8eaedcea8c248571a132227105f712aa`；完整OCI校验覆盖77个blob、28,734,688,184字节。另附1.6.2 Operator、Grove alpha.14、KAI0.17 Helm包与11个ARM64依赖镜像，依赖层校验通过。候选包明确标记 `release_qualified:false`；正式功能campaign、全部语义迁移台账、交付绑定及promotion门禁尚未通过，不能称为正式发布。

最终DGDR TP1/TP2四候选的78份AIPerf输出已通过独立完整性审计，自动部署API验收仍在进行。DeepSeek最终镜像r1已Ready，但控制机缺少客户端脚本，因此回放未执行；失败完整保留并清理UID，r2补齐脚本后重跑。Hybrid清单预检发现厂商模块实际导出类为 `MooncakeConnector`，已在部署前修正显式module与类名组合，保留未部署的错误清单及摘要。

## 2026-11-11 13:32 UTC — 最终现场结果与清理

最终 `1aabe` 的异宽 TP、30 分钟 PD 长稳、两节点 DeepSeek Hybrid PD 全部完成。长稳为 1817.415 秒、1920 请求、60 次取消与 3 次故障；Hybrid 覆盖七个输入边界、两轮并发、四次取消及恢复，并独立复核 token 计数、镜像 ID 和八个 Decode rank 的传输日志。Hybrid 删除 Pod 时的析构告警另记，未当作已解决。

`final-campaigns-final1aabe/RAW_SHA256.json` 对应 3215 个实测文件，回收后摘要一致。清理后两节点 16 张 NPU 健康、无进程，DGD/worker 元数据为空，控制器保留。按功能编号查看 `feature-evidence-map.json`；它不改变正式认证状态。`upgrade-gate-final.json` 仍有 805 项迁移审查、上游语义审查、新冻结 campaign 和 sealed delivery 门禁。候选源码与镜像保留在隔离分支和华为 52，未替换主工作区 `heyi`。

最终候选离线归档约 11.8 GB，存放华为 52 的 `/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate.tar`。独立核验 436 个文件摘要通过；位置、完整 SHA256、下载校验文件和使用说明入口见 [离线包索引](offline-candidate-index.json)。它仍标记 `release_qualified:false`，不是正式晋级交付。
