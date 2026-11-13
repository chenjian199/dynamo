<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 华为 Dynamo 1.6.2 升级候选验收

状态：下表列出的实机场景已结束；正式升级门禁尚未收口，未晋级发布。CUDA 和沐曦多机回归暂缓。

## 版本和边界

| 项目 | 冻结身份 |
| --- | --- |
| 上游目标 | Dynamo v1.6.2，`28f9765d98ded77149a94d0defee6d54e97f6741` |
| 原 HEYI 对照 | `bd801c6234aae2827042c8abf268395d4582e5eb`，Ascend 0.23 |
| 新候选 | `1aabe435ad56922cf7e12f92f64df2dc39531ccb` |
| 官方基础镜像 | `quay.io/ascend/vllm-ascend:v0.30.0` |
| 基础镜像摘要 | `sha256:28351d337b7f9367073344240fa4e28d99794d549d577eaf317689950064150a` |
| 实际引擎 | vLLM `0.30.0+empty` / vLLM-Ascend `0.30.0` |
| 依赖 | Python 3.12.16、torch 2.10.0+cpu、torch-npu 2.10.0.post7、CANN 9.2.1、Mooncake 0.3.13 |
| 现场 | 华为 7.7 与 7.5，两节点各 8 张 Ascend 910B3 / 64 GB，ARM64，K3s 1.32.2 |

未修改宿主机驱动；7.7 和 7.5 的原有驱动版本不同。候选分支在隔离 worktree 中，主 `heyi`
未被升级分支替换。下列实测结论不等同于整个功能目录的正式认证。

## 本次迁移内容

1. 合并 1.6.2 的 Rust/Python/Operator 接口及依赖，保留平台抽象和设备资源规范。
2. 针对 Ascend 0.30 严格配置构造器，以精确源摘要保护的补丁隔离 Dynamo 扩展键；未知厂商键仍被拒绝。
3. 适配共享存储、交错 K/V 页视图，检查格式、尺寸、步长和互补地址后零拷贝注册，保留异宽 TP 的真实页跨度。
4. 按 AISimulate 0.13 接口更新后端身份、阶段配置覆盖和厂商传输转换，保留自定义 Connector 及原生并行参数。
5. 重建 runtime/dev/tools/Operator，修正安装审计、版本识别及交付清单渲染。

## 已完成的最终镜像实测

以下标为最终镜像的证据均对应 `1aabe`；此前候选的结果在下一节单独列出。

| 范围 | 实际验证 | 证据入口 |
| --- | --- | --- |
| M01/M03 | NPU 推理、KV 路由，LoRA DP2、n-gram；合成 LoRA 只检查功能 | `final-acceptance-55-1140` |
| M02 | 四角色构建、tools 129 项测试、dev Python/Rust editable；四镜像归档导入 ID 一致 | `final-acceptance-52-1140`、`final-acceptance-55-1140`、`final-campaigns-final1aabe/offline-candidate` |
| M03 PP2 | 原生 0.30 与候选各两张 NPU，TP1/PP2，六条请求精确输出一致 | `final-campaigns-final1aabe/pp2-final-r1` |
| M03 大模型 | Qwen3-32B TP4 六条请求与原对照一致；DeepSeek V4 Flash W8A8 TP8/EP 22 条 API 成功 | `final-acceptance-52-1140`，`final-campaigns-final1aabe/dynamo-deepseek-api-final-r2` |
| M04/M06 | 独立 KVBM replicated 六组实机校验，TP2 host/disk 卸载、重载字节检查与取消恢复 | `final-acceptance-55-1140` |
| M05 异宽 TP | Prefill TP1 → Decode TP2，基线、传输、取消及恢复通过 | `final-campaigns-final1aabe/candidate-1aabe/pd-split-final-r1` |
| M05 长稳 | 1817.415 秒、1920 请求、60 次取消、3 次故障；145 个原始文件摘要及资源采样独立复核通过 | `pd-soak-final-independent-audit.json`、`pd-soak-final-55.tar.gz` |
| M05 RDMA | 两节点 READ/WRITE、五种尺寸、两轮，40 项字节校验；HCCN RC 收发交叉一致、释放后分配量为零 | `rdma-final-r1` |
| M05/M08 PD | 跨节点 2P3D，六条 P/D 配对、取消与 worker 重建恢复 | `final-campaigns-final1aabe/cluster-services/pd-cross-final-r1` |
| M05 Hybrid PD | DeepSeek TP8 Prefill + TP8 Decode；1/127/128/129/512/1024/2048 token 边界、两轮并发、4 次取消与恢复通过 | `final-campaigns-final1aabe/hybrid-pd-r1` |
| M07 | 本地张量/媒体四项；不等同于完整 VL/Omni 模型验收 | `final-acceptance-55-1140` |
| M08/M09 DGDR | TP1/TP2 × P/D 四候选，78 份 AIPerf 输出独立审计；autoApply Ready；完全相同选中 spec 回放三条 API 成功 | `final-campaigns-final1aabe/dgdr-r2` |
| M08 Grove/KAI | 预留 7/8 张卡时两 worker 保持 Pending；释放后推理、`2→3→2→0`、worker 重建与 API 恢复通过 | `final-campaigns-final1aabe/cluster-services/grove-final-r1` |
| M09 离线 | 完整 5/3 网格，54 份 AIPerf 输出审计通过；存储/复用插值，Planner 重启指纹一致 | `final-campaigns-final1aabe/profiling-r3`、`profiling-r5` |
| M09 在线 | 固定 75 ms ITL / 95% 缩容阈值，`1→3→2→1`，退出 Pod 回收及重启复用通过 | `final-campaigns-final1aabe/profiling-r5` |

DGDR 原始自动部署达到 Ready 后，API 辅助脚本先后遇到组件名称大小写与发现尚未完成的问题。
修正测试脚本后用完全相同的选中 spec 重建并回放成功；不是原 UID 下完成 API 验收。
选中 spec SHA256：`935d02148fb53aedb45ecc53c58065c9eb882f806b224638bc123a2d14cc8e59`。

## 三组软件栈对照

同一 7.5 节点、同一 Qwen3-0.6B 权重、BF16/TP1、KV 512 MiB、并发 4、ISL 128、OSL 32，
每组 8 次预热、两轮各 128 次测量，均 256 次成功。原始日志重新计算后，升级比较门禁通过。

| 测量项 | 原生 0.30 | 原 HEYI / 0.23 | 新候选 / 0.30 |
| --- | ---: | ---: | ---: |
| 请求吞吐（req/s） | 1.84664 | 1.77237 | 1.82726 |
| TTFT p95（ms） | 205.306 | 215.981 | 206.942 |
| TPOT p95（ms） | 65.794 | 68.824 | 67.623 |

新/旧吞吐约 `1.0310`，TTFT p95 约 `0.9581`，TPOT p95 约 `0.9825`；新/原生吞吐约 `0.9895`。
这是固定小模型负载的整体软件栈回退检查，不能归因成 Dynamo 单独带来的性能收益，也不能推断大模型 SLA。
入口 `comparison-gate-r1.json` 指向升级 session 中完整、带摘要的原始数据。

## 保留的失败和历史候选结果

- DeepSeek 原生及 Dynamo 均出现固定种子下的不同输出。最终候选 22 次请求有 9 种输出；只记录 API 功能通过，不声明数值确定性。
- 最终 150 ms ITL 场景 API 正常，峰值 Ready 为 1，扩容断言未通过。75 ms / 默认 80% 缩容阈值场景达到 Ready 3，但缩容保护要求预测 ITL 小于 60 ms，低负载预测仍高于它，所以缩容断言未通过。保护逻辑与上游 1.6.2 相同。
- 独立 75 ms / 95% 阈值场景完整生命周期通过：负载阶段 795 请求、恢复阶段 15 请求，均无错误。负载阶段 TTFT p95 为 11263.24 ms、TPOT p95 为 93.97 ms，延迟目标未达到；恢复阶段分别为 240.35 ms、69.27 ms。该场景只认证功能闭环，不宣称 SLA 达标。
- `49ef` 候选的离线分析通过，但 15 秒决策场景在新 worker Ready 前缩容，在线断言未通过。
- `49ef` Grove/KAI gang、扩缩容、恢复以及异宽 TP 已通过；`5cbe` 的 30 分钟 PD 长稳完成 1856 请求、58 次取消和 3 次故障。它们保留各自提交和镜像身份，不重新标记为最终 `1aabe` 验收。
- Hybrid PD 请求全部完成并删除 DGD 后，Decode 退出时观察到 `resource_tracker` 提示 10 个共享内存对象，以及 `FATAL: exception not rethrown`。Pod 已回收且 NPU 无残留进程，但析构原因尚未完成原生配对归因，不能宣称优雅退出无异常。该交互观察与回收后的日志缺失分别记录于 `hybrid-termination-observation.json`，未伪造为完整原始尾日志。
- 构建网络、测试辅助脚本缺失、版本识别及此前 API 等待失败均按 attempt 保留。

## 尚未关闭的门禁

最终收集 `final-campaigns-final1aabe` 含 3215 个逐字节摘要文件，归档 SHA256：`b970091955feb3b63e4b76fd9727604534a4c7abfd5f8bae43c8b516be0201c9`。55 节点的长稳原始日志另存 `pd-soak-final-55.tar.gz`，未截断大日志。

清理记录为 `final-campaigns-final1aabe/cleanup-final`：17 个本轮对象按 UID 删除，DGD 与 worker 元数据为零；两节点共 16 张 NPU 健康且无运行进程。保留 Operator、Grove、KAI 控制器。

按 45 个功能编号索引现场材料见 `feature-evidence-map.json`。该文件只是证据入口映射，不是冻结 campaign 或认证结果。

2026-11-12 补审 19 项 Rust/VMM 迁移后，语义迁移台账为 946 项中的 160 项已审查、786 项待审。正式升级流程仍需完成余下台账、上游新增能力审查、带功能编号的全量新 campaign 与 sealed M10 delivery。当前状态见 `upgrade-gate-20261112.json`；旧 `upgrade-gate-final.json` 保持 11 月 11 日原始快照不改写。
不会用手动改状态、旧报告或整体验收摘要替代这些门禁。
历史暂缓项包括 Ascend GMS/VMM、完整 Omni hidden-state、缺少资产的 VL/真实 LoRA 精度、G4、功耗写入，
以及需要新 Kubernetes 环境的 DRA。保留这些边界，不把整个 M01–M10 目录宣称为全绿。

## 离线候选目录

华为 52：`/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate`。

主镜像归档 SHA256：`5d54a16e7625e837b44eb9dda84d5cca8eaedcea8c248571a132227105f712aa`。
已验证 77 个 OCI blob；附 Grove/KAI 11 个 ARM64 依赖镜像、Helm 包、源码和实际分析数据。
导入在现有 containerd 中完成，不是空存储冷安装证明。使用步骤见 `CANDIDATE-USAGE.md`。

## 2026-11-12 源码合并准备

补充审查见 `semantic-review-r9-runtime.json`。升级 session 的 90 个文件已按字节复制到主仓库 `adapter/results/workflow/upstream/dynamo-1.6.2-ascend-20261111`，逐文件摘要见 `session-preservation-20261112.json`；未修改旧 lock 中的绝对路径。合并操作与只保留本地 heyi 的步骤见 [操作说明](../../../../upgrades/merge-heyi-1.6.2.md)。这不是正式 release promotion，也不会改变候选镜像的源码身份。
