<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 遥测、Profiler 与 Planner 回归

2026-10-30 MetaX 补充：增加不依赖历史 CUDA/Ascend 清单的 Planner/thorough DGDR 入口、真实身份采集与显式容量配置。无集群实测，离线拟合和在线规划仍未认证；见 [MetaX 集群适配](../platforms/metax/cluster/README.md)。

状态：基线 b24f6dbe07。实时指标路径的有界功能回归及离线画像接入已实现。2026-10-30 六组离线补充测试结束：四组通过、一组有限通过、一组未通过，因此完整 M09 离线闭环仍不能标为全部收口。生产 SLA、伸缩稳定性及下列未认证能力仍保留。

补充链路与复现见 [离线画像到在线 Planner](offline-bootstrap.md)。

后续修正：已移除异宽 TP 的已测组合白名单，由 vLLM/connector 校验实际布局；187 项相关单测通过，未将未实测组合标为传输认证通过。见 [TP 参数透传](tp-delegation.md)。

离线补充中，两平台小模型、Ascend Qwen32B 和 CUDA DeepSeek 已闭环；CUDA Qwen32B C2 有限通过，保留在线拟合间歇不可用；Ascend DeepSeek 完成 6P/9D 采样及每点 16 次 prefill 重测，但原生拟合仍为 insufficient_data，在线与重启阶段未运行；见 [独立结果](results/offline/README.md)。本补充定向测试 704 项通过，包含画像拒绝回退、配置转换、PD 门控、decode 活跃序列上限及标准 Profiler 入口。前轮结果与本轮结果分开统计。

## 三步范围

1. Planner/Profiler 的厂商资源计数、平台一致性校验、只读设备遥测；保留当前 Operator 权威设备数量及 DRA/MIG 语义。
2. 性能画像身份、版本和数据完整性校验；未知平台数据库不回退到 CUDA 曲线。沿用实测及实时引擎 FPM；不得将未认证预测称为已达标。
3. CUDA H800 / Ascend 910B3 的真实 Planner 动态扩缩容与回收。先 Qwen3-0.6B，再集群已有 Qwen3-32B、DeepSeek-V4-Flash。逐平台冻结负载和延迟目标；大模型原生引擎确定性问题继续单列。

CUDA vLLM 0.28.0、Ascend 0.23.0、MetaX 0.26.0 不变。测试采用源码覆盖，M10 才统一重建最终镜像。MetaX 无集群，集群回归暂缓；本机遥测及控制平面单测继续。功耗读写分离，本阶段不向硬件写入功耗上限。

## 已处理问题

- Planner 的标量资源计数、辅助设备识别和 Profiler 的资源更新原有 NVIDIA 资源键硬编码已替换。
- CUDA DCGM、Ascend NPU exporter 已在目标集群运行。华为 exporter 含设备采样时间，不能把过期数据解释成空闲；DCGM 无时间戳时仅声明抓取时间，不证明设备 SDK 缓存新鲜。
- Planner 原有配置文件和 AIC 身份没有厂商/SDK/模型版本完整匹配校验。本轮增加显式 target_platform 和画像身份；保留原 CUDA 默认路径。
- DeepSeek 的分组 KV 容量不能用共享块数乘路由事件宽度推导。引擎通过 runtime metadata 发布原生每引擎容量，Planner 优先读取；CUDA 0.28 使用原生字段，Ascend 0.23 / MetaX 0.26 使用具名 worker 扩展，不开启不安全序列化、不覆盖用户已有扩展。

## 本轮验收与保留项

- CUDA / Ascend 的 Qwen3-0.6B、Qwen3-32B、DeepSeek-V4-Flash 均完成 Planner 自动扩容、缩容与请求回归，六项负载运行 API 错误为零；另有两项小模型实时 FPM/SLA 决策回归通过。测试脚本不手工修改副本数。
- 两平台 Profiler 各完成四候选测量及配置/结果哈希校验；定向单测共 474 项通过。MetaX 本机 Qwen3-0.6B 推理及 4672-token 容量发布验证通过，但无集群扩缩容证据。
- Ascend 小模型、32B 和 DeepSeek 的冷扩容负载含延迟未达标项；DeepSeek TTFT p95 为 14.09 秒，目标 1 秒。恢复低负载后达到本轮目标，不能替代生产 SLA 验收。
- CUDA DeepSeek 出现 `1→2→1→2→1`：两副本 FPM 同时上报空闲时，原生 easy-load 策略触发缩容，随后负载恢复又扩容。保留该瞬态震荡问题，尚未认证稳定伸缩策略；日志未证明空闲采样究竟源于正常间隙还是首次编译。
- 前轮两集群测试工作负载与本地 MetaX 推理容器已退出，当时四台集群节点无残留设备计算进程；离线补充测试资源也已回收，未通过项及原始归档保留。保留 M08 Operator。非 CUDA 功耗控制、rapid 预测数据库和 MetaX 集群继续未认证；M10 还需重建最终镜像并复测。

正式结果见 [实测记录](results/README.md) 和 [汇总数据](results/summary.json)。

## 记录规则

只把实际通过的测试列为通过。保留源码 SHA256、镜像 ID、引擎/插件/驱动版本、输入负载、原始结果、Planner 决策和 API/设备释放证据。手工改副本数不算 Planner 自动伸缩；功能扩容通过不等于生产 SLA 认证。

## 单位与测试边界

- `decode_scale_up_kv_rate` / `decode_scale_down_kv_rate` 单位为百分数，小模型、Qwen 和正式 DeepSeek 回归均为 `12` / `2`。早期误用 `0.12` / `0.02` 的尝试单独保留，不混入正式证据。
- 退出等待必须覆盖在途请求：小模型使用 `DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS=60`、Pod 退出时间 120 秒；大模型为 180 / 240 秒。原默认 5 秒在 Ascend 约 16 秒的单请求下复现 `EngineShutdown during generation`，不是驱动故障。超过配置等待上限的长请求仍不保证完整退出；不是无限 drain。
- 延迟先单并发预热 2 次、测量 16 次，再冻结目标：TTFT p95 为 max(1000ms, 基线的 1.5 倍)，TPOT p95 为 max(50ms, 基线的 1.5 倍)。正式负载包含冷扩容时间，失败目标如实保留；功能通过不等于达到目标。SLA 模式另设 CUDA 500ms/25ms、Ascend 1000ms/100ms，检查实时 FPM 决策。
- ProfileIdentity 的驱动/SDK/模型配置哈希来自本轮 worker 取证；模型配置哈希不代表全部权重逐字节校验。Profiler 每个候选记录实际并行度及 config.yaml/AIPerf 文件哈希。`thorough` 的端到端测量与 Planner 的旧版 FPM bootstrap 数据有不同类型，不能互相冒充；非 CUDA 使用现场 FPM 拟合，不隐式采用 NVIDIA 预测数据库。
- 设备遥测只读，不控制 readiness，不设置功耗上限。DCGM XID 保留历史错误码，不能单独据此判定设备当前故障；Ascend exporter 健康值采用其显式 health gauge。MetaX 本轮验证可见设备内存查询，利用率和功耗未查询时明确 unavailable。
- DeepSeek 的合成输入可能生成无可见文本的输出。正式功能测试使用 `nvext.completion_token_ids` 测量 token 到达时间，验证 token ID 计数与 usage 一致，单列空文本次数；不宣称回答质量通过。首轮可见文本计时失败与原始 SSE 均保留。详见 [复现步骤](reproduce.md)。

参考：[Ascend NPU exporter 指标](https://www.hiascend.com/doc_center/source/en/mindcluster/730/clustersched/schedulingug/dlug_api_005.html)、[MetaX pymxsml](https://github.com/MetaX-MACA/pymxsml)。实际采样 JSON 保存在 results；历史版本与新代码分开记录。
