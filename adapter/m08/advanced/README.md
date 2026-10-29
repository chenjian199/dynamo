<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M08 扩展集群验收

2026-10-29：在 [基础验收](../cluster/README.md) 后继续执行。本页记录扩展范围，不覆盖或改写上一轮原始结果。

## 范围与环境

- MetaX：用户确认暂无可用集群，硬件 Kubernetes 验证暂缓；保留代码及本地测试状态。
- CUDA / Ascend：已完成本页范围的跨节点 Operator 2P3D、Grove/KAI、DGDR 验证，vLLM 仍固定 0.28.0 / 0.23.0。
- CUDA DRA：验证真实 DeviceClass / ResourceClaim / ResourceSlice 分配、DGD 设备计数、推理及释放，不能以 Device Plugin 申请或 mock 代替。
- Ascend DRA：用户接受第三方 HAMi 实验驱动，并将另行提供 Kubernetes ≥1.34 测试集群。当前九节点 K3s 1.32 集群不升级，不在该集群强行部署 DRA。

## 外部依赖核对

当前 Dynamo Chart 锁定 Grove `v0.1.0-alpha.13`、KAI Scheduler `v0.17.0`。Grove 必须区分 PodClique 编排与真实 gang scheduler，不把默认调度器成功当作 gang scheduling 通过。

[HAMi Ascend DRA 驱动](https://github.com/Project-HAMi/ascend-dra-driver)要求 Kubernetes ≥1.34、CDI 和 DRA；其整卡模式仍为实验性。当前华为 API Server 不提供 DeviceClass API，不能在现环境认证此路径，也不把此情况表述为华为官方稳定 DRA 已可用。

[NVIDIA DRA 驱动 v0.5.1](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu/releases/tag/v0.5.1)为本次核对到的稳定发布。部署前隔离与已有 Device Plugin 的分配，避免两套分配器独立分配同一设备。

## 执行记录

跨节点 PD 清单由 `../cluster/render.py --mode pd --second-node ... --second-model-host-path ...` 生成，Prefill 放第一节点、Decode 放第二节点，删除原同节点 Pod affinity；实际文件见 `manifests/`。沿用 Kubernetes 服务发现、2P3D 六组合、流式、取消、重建恢复验收。

DGDR 首次检查发现 M02 CUDA runtime 缺少 Profiler 依赖 `pandas`。使用独立测试镜像补齐 Profiler 环境；不修改推理镜像中的 vLLM 版本，不伪造厂商硬件为 H100。

## 已完成：跨节点 Operator PD

CUDA（worker06 → worker28）与 Ascend（7.7 → 7.5）均通过真实 2P3D：六种交接组合、两组 API/流式一致性、八次取消后恢复、Decode 删除重建，以及测试资源与发现记录回收。原始证据见 `results/{cuda,ascend}/pd-cross/`；执行 `python3 adapter/m08/advanced/summarize.py` 可复核节点分离、六组合和恢复结果，生成 [汇总](results/pd-cross-summary.json)。

CUDA 使用 NIXL/UCX TCP + CUDA copy，本结果不认证 GPUDirect/RDMA。Ascend 日志确认 Mooncake 的 `AscendDirectTransport`。两个平台都没有改动引擎版本。

## 验收结果与等待条件

- Grove：两平台 PodClique 编排的真实推理、2→3→2→0、重建恢复通过。KAI：两平台的资源不足 gang 等待、容量释放后的推理、2→3→2→0、删除重建恢复和取消均通过。证据见 `results/{cuda,ascend}/grove-kai/`，可运行 `summarize-orchestration.py` 复核。显式设置 `nvidia.com/kai-scheduler-queue=default-queue`；固定 KAI 版本的 staleness 参数使用带单位的 `-1s`。
- DGDR：CUDA / Ascend 均完成真实 `thorough` 画像、自动部署、API/SSE 和各四次取消后恢复。每个平台包含 TP1/TP2 的 Prefill 测量及 Decode 六档扫描；最终自动部署 1P1D。已补充 H800/Ascend 910B3 GPU SKU、再生成 CRD/Pydantic，并通过 Go 单测和 Pydantic 校验。新增明确指定 Ascend 时的默认设备资源与 Mooncake 配置转换，12 项针对性单测及最终 117 项 Profiler/部署工具回归通过。
- CUDA DRA：v0.5.1 已通过真实整卡分配、两 Worker 各见一张 H800、推理、2→3→2→0 和重建恢复。DGD 删除后 Claim 归零，驱动卸载，worker28 的 Device Plugin/taint 已恢复。首次清理脚本检查早于 driver Pod 退出，随后单独确认退出并恢复节点；脚本已改为有界等待，原始非零退出日志保留。
- Ascend DRA：等待用户提供 ≥1.34 测试集群。届时先核对准确 Kubernetes 版本对应的 DRA API、feature gates、containerd/CDI、设备/驱动和 HAMi 固定提交，再执行真实整卡 claim 分配、推理、释放及重复分配；不把仅有 CRD 或 Pod 启动当作通过。
- MetaX：无集群，记录暂缓。

以上状态按证据逐项更新，不将“依赖已下载”视为功能认证完成。

## DGDR 实测发现与边界

- M02 runtime 的旧 `preflight.py` 会把聚合模式 NIXL Connector 当作 KVBM 校验。当前 HEAD `858a564c39` 已将判断限定为 `DynamoConnector/PdConnector`；本次通过只读 ConfigMap 挂载该源码复测，最终 M10 镜像必须包含这项修复。
- Profiler 与推理镜像分离。测试 Profiler 补齐 pandas/AIPerf、`deploy.utils`、kubectl 和覆盖工具；输出收集脚本要求 shell 支持 `pipefail`。
- AISimulate 0.12 没有 H800/910B3 的系统性能数据库。枚举器缺少系统文件时默认采用 H200 的 141 GiB 容量；测试镜像仅补充实际设备容量，不提供虚构的带宽或算子性能数据，也不把 H800 改称 H100。
- 新增 GPU SKU 只保证身份字段能够传递；不能据此宣称自动画像、SLA 预测或自动部署已认证。

DGDR 回归中另外记录：

- CUDA 原生 vLLM 0.28 在生成候选的默认 block size 下发生 FlashAttention 预热非法显存访问；追加前序 PD 已验证的 `--block-size 128` 后候选就绪并完成测量。该参数选择不代表修复了上游算子。
- AIPerf 0.13.0 离线 tokenizer 的本地目录识别缺陷通过 [限定版本补丁](patch-aiperf.py) 修复，不更换 tokenizer 或下载模型。
- Profiler Job 的覆盖实现按 volume 名称合并 `volumeMounts`，同一 ConfigMap 的多个 subPath 会只保留一个挂载。本轮用不同 volume 名引用同一 ConfigMap，避免源码挂载丢失；失败的准入日志保留在 `dgdr-mount-collision/`。
- Ascend Profiler 使用 CPU，新增画像依赖只安装在独立工具镜像中，`TORCH_DEVICE_BACKEND_AUTOLOAD=0` 避免导入未挂载驱动的 NPU 后端；它不作为推理 Worker 镜像。

手动构建和运行步骤见 [复现说明](reproduce.md)。

- 华为集群域为 `cluster.xcoresigma`，原 Profiler 服务地址写死 `cluster.local`。改用 `<service>.<namespace>.svc`，通过 Pod DNS 搜索域解析；不修改集群 CoreDNS。[Kubernetes DNS 规则](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/)。
- CUDA 首轮 thorough 采样完成，但最终 Decode 缺少 `--kv-transfer-config`，被 PD preflight 拦下。修复 vLLM 最终配置生成器，为两个 PD 角色补齐默认 connector，同时保留用户指定 connector；独立聚合基准逻辑不变。
- 最终 Decode 的健康探针不携带远端 KV 交接，严格 PD 契约会拒绝该请求。本轮 DGDR 的 Decode 覆盖配置补上与手写 PD 清单相同的 `DYN_HEALTH_CHECK_PAYLOAD`，只为探针添加 `x-bypass-remote-prefill` 并执行真实本地 forward；普通业务 PD 请求仍检查远端交接契约。CUDA 缺失该设置的失败日志和完整测量归档保留，随后按修正清单重新执行全流程；华为正在进行的旧清单轮次提前终止，不计为完整通过。

- 华为输出收集工具使用 K3s 多调用程序，非 root 运行时缺少可写解包缓存导致进度 ConfigMap 更新失败。已在本次收集容器内修正缓存目录所有权并验证 API 访问，构建配方同步补齐；测量任务与 NPU 驱动未改动。后续新工具镜像需以 UID 1000 验证 kubectl，而不只检查二进制存在。
- 测试客户端对绝对路径模型 ID 的就绪查询增加前导斜线保护并进行 URL 编码，适配当前前端 catch-all 会去掉一个前导 `/` 的行为。普通模型 ID 和业务 completion payload 不变。这是测试客户端的兼容处理，不宣称修复了 Rust HTTP 路由。

## 本轮收口范围

模型为 Qwen3-0.6B。跨节点 PD 是 2P3D；Grove/KAI 是单节点真实 gang 等待及生命周期测试；DGDR 使用明确指定硬件与厂商覆盖配置，完成真实测量到自动部署，未认证 Ascend 自动硬件发现、预测数据库精度或 M09 Planner。CUDA DRA 仅认证整卡，未认证 MIG/共享分配。CUDA 跨节点 PD 此次使用 TCP，未认证 RDMA。

DGDR 原始请求数据在两台管理节点的 `m08-1029/profiler-m08-dgdr-final.tar.gz`，仓库保存汇总、配置和归档 SHA256。执行 `summarize-dgdr.py` 可逐项复核正式测量、独立 warmup、模型 API、镜像 ID 和清理结果；见 [DGDR 汇总](results/dgdr-summary.json)。错误尝试保留在独立目录，未混入成功计数。

最终生产镜像仍按计划在 M10 重建。本次使用明确记录的工具镜像、源码挂载和工具容器修复；不能直接将旧 M02 runtime 标记为已包含本轮源代码。

后续 [RDMA 专项](../rdma/README.md) 已补测两平台的设备直传与跨节点 2P3D；本页早先 TCP 轮次的原始范围不变。Ascend 动态注册压力另有原生 ADXL/HCCL 问题，详见专项记录。
