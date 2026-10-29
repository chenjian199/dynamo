<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M01–M07 集群回归执行记录

本轮按 [回归方案](../cluster-regression-plan.md) 于 2026-10-28 执行。
**本轮可执行回归已结束，但 M01–M07 尚未全通过。** 下表区分已取得的通过证据、失败和覆盖缺口。

两个集群使用独立 namespace `dynamo-regression-1028`，普通 Deployment/Job/Service，
etcd 注册、TCP 请求、ZMQ 事件；未部署 M08 Operator/Grove。
CUDA vLLM 固定 0.28.0，Ascend 固定 0.23.0，使用用户导入的 M02 runtime 镜像。
没有更换引擎、安装生产包或用宿主机源码覆盖生产实现。测试工具及 observer 单独挂载。
华为保留两节点现有的不同驱动。CUDA 模型使用只读节点 hostPath。

## 2026-10-28 已执行矩阵

PASS 只针对该行的配置和检查，不等于整个模块完成。
本表保留冻结镜像的原始结果；2026-10-29 的诊断修正和候选源码验证见下节。

| 用例 | CUDA | Ascend | 范围 / 证据文件名 |
| --- | --- | --- | --- |
| M01 / M02 环境 | PASS，两节点 | PASS，两节点 | `initial.json`：版本、镜像、设备身份、复制/流/事件/算子 |
| M04 跨节点集合通信 | PASS，NCCL/IB | PASS，HCCL | 4 KiB / 4 MiB / 64 MiB，all-reduce、broadcast |
| M05 跨节点设备传输 | PASS，NIXL UCX rc_mlx5 | PASS，Mooncake AscendDirect/ADXL | CUDA READ+WRITE；Ascend READ；不是同样的方向覆盖 |
| M06 独立 KVBM / replicated | PASS，两节点各两卡 | PASS，两节点各两卡 | native 6 组用例；不是 G4 或完整 MLA 模型推理 |
| Qwen0.6 聚合双 worker | PASS | PASS | KV 命中、SSE、4 次取消及恢复 |
| Qwen32B TP2 双 worker | PASS | PASS | `dense-tp2-api.json`；两个独立模型实例 |
| Qwen32B 原生 vLLM 对照 | PASS | PASS | 同平台短/长前缀各 3 次与 Dynamo 输出一致 |
| Qwen32B TP4 | PASS | FAIL | Ascend 重复 / SSE 输出不一致，原生引擎也可复现 |
| TP8 / 跨节点单实例 TP2×PP2 | BLOCKED | BLOCKED | 平台预检不接纳 TP8、PP>1、headless，未进入通信初始化 |
| Qwen32B DP2×TP2 | PASS | PASS | `dense-client-dp2*.json`、`dense-dp-ladder.json`；单 worker 内两 DP ranks，不是两 worker 冒充 DP |
| 长度 / 并发阶梯 | PASS，已测范围 | PASS，已测范围 | 实际输入 113 / 905 / 7205 tokens，另补精确 8192-token 原始 token 输入；并发均为 1 / 4 / 16，见 `dense-dp-exact-long.json` |
| Qwen32B 30 分钟 API | PASS，9232 请求 | PASS，2112 请求 | `dense-load-30m.json`；并发 4，资源观察另列 |
| 小模型跨节点 1P1D / 2P3D | PASS | PASS | 六对定向 P→D token 一致，API/SSE/取消恢复 |
| 小模型 PD TP2→1 / TP1→2 | PASS | PASS | `split-pd-*-pairs.json`；与原 TP1→1 token 对照另归档 |
| 小模型 P/D 节点互换 2P3D | PASS，端口修正后 | PASS | `reverse-pd-*` |
| Qwen32B TP2 跨节点 1P1D | PASS | PASS | `large-pd-*-1p1d.json` |
| Qwen32B TP2 跨节点 2P3D | PASS，端口修正及重连复测后 | PASS | 每 worker TP2，4 张 P 卡 + 6 张 D 卡 |
| Qwen32B PD 错误握手 / 不可达端 | PASS | PASS | `large-pd-faults.json`：明确错误及新请求恢复，不是在途无感迁移 |
| 小模型 2P3D 首轮 30 分钟 API | PASS，14576 请求 | PASS，4560 请求 | `pd-load-30m.json` |
| 32 次取消 + 3 轮 Pod 重建 | PASS | PASS | `pd-recovery.json`；每轮重验六对 PD |
| 预热后独立 PD 资源观测 | PASS，14528 请求 | PASS，4576 请求 | `pd-observed-*`；5 分钟预热、30 分钟负载、取消、34 次资源采样 |
| vLLM KVBM 小模型卸载/回载 | PASS，6 组 | PASS，6 组 | baseline TP1/2、host、disk、direct disk、TP2；含取消 |
| Qwen32B TP2 KVBM | PASS | PASS | `large-kvbm.json`：原生基线、host 周期/取消、disk、输出对照和回载字节检查 |
| Dynamo PD + KVBM | PASS | PASS | `cache-system.json`；两卡单 Pod，跨节点 PD 在其它行验证 |
| G2 主存感知路由 | PASS | PASS | 同一组合 harness 的 host case |
| G3 磁盘感知路由 | FAIL，间歇复现 | PASS | CUDA 逐出后应有 10 块，实测有时仅 8 块，详见问题清单 |
| LoRA 加载/卸载/并发/重载 | PASS，合成附件 | PASS，合成附件 | 非零 adapter、缓存隔离；不是训练质量验收 |
| ngram 推测解码 | PASS | PASS | `ngram.json`：关闭/开启输出一致，draft/accepted 指标均大于零 |
| DeepSeek V4 Flash TP4 | 部分通过，长稳 FAIL | BLOCKED，TP8 | CUDA 顺序/API/SSE/取消通过；并发 greedy 输出不稳定，30 分钟负载未完成 |
| MoE / EP | PASS，Qwen3.6 TP2 EP | BLOCKED，DeepSeek TP8 | `vision-ep-client.json`；不能推广至 DeepSeek EP/MTP/PD |
| 聚合多模态 | PASS，Qwen3.6-35B-A3B | PASS，Qwen2.5-VL-72B TP4 | 图像、视频、并发、SSE、取消、坏图片 415 后恢复 |
| LOCAL / NIXL 媒体原语 | PASS，LOCAL + READ/WRITE | PASS，LOCAL | CUDA 三 dtype 共 9 项及迟到 WRITE 隔离；Ascend 不声称 NIXL |
| CPU 视频 / 取消长测 | H.264 PASS；VP9 FAIL | VP9 PASS | Ascend 22 轮十分钟、40 次取消；CUDA H.264 单独解码通过，不代替十分钟 VP9 用例 |
| CUDA GMS 无模型 | PASS，两节点各 3 项 | DEFERRED | 共享所有权、异常客户端退出、mempool suspend/resume |
| CUDA GMS 模型 sleep/wake | PASS，功能 | DEFERRED | 两轮 token parity；仍有 resource_tracker semaphore 清理警告 |
| 独立视觉 encoder | BLOCKED，注册缺口 | BLOCKED，现有模型容量 | `encoder-preflight.json`，只做静态/运行时预检，没有 encoder 推理 PASS |
| Omni / hidden-state stage / G4 | BLOCKED / DEFERRED | BLOCKED / DEFERRED | 无 Omni 权重；既有暂缓项保持暂缓 |

## 2026-10-29 后续诊断与修正

详见 [G3 与原生拓扑排查](debug-g3-native-topology.md)，证据在 `results/followup-1029/`。

- G3 原用例先确认 G1 命中就开始逐出，未确认目标前缀完整落盘。事件日志显示 G2 有 10 块，
  其中 2 块在卸载完成前被回收，磁盘只有 8 块；路由器返回与之相符。
  卸载队列持弱引用，允许源块复用。因此撤回“已确认路由器丢失磁盘块”的归因。
  加入落盘完成前置检查后，原冻结镜像连续三轮通过 G3=10、自动选路、640 cached tokens 和回载字节校验。
- 移除普通引擎的 TP/DP 白名单、eager 强制、PP/headless 拒绝和全局卡数与本地卡数比较。
  原生引擎负责校验模型和拓扑；平台身份、版本、scheduler 和专属连接器契约仍检查。
- CUDA Qwen3-0.6B 单机 TP8、双机 TP2×PP2/headless 均通过重复请求、长前缀 KV 命中、SSE、取消恢复。
  这两项只读挂载候选 Python 源码，vLLM 保持 0.28.0，属于开发补丁验证，不是新镜像或大模型验收。
- 本地平台测试 68 passed；CUDA runtime 内平台/生命周期/scheduler/PD 契约测试 102 passed。
- Ascend Qwen32B TP4 和 CUDA DeepSeek 原生可复现的确定性问题继续暂缓。
  Ascend DeepSeek TP8、大模型 PP 和其它组合仍需独立实测，不能由小模型结果代替。

## 原始失败与覆盖边界

1. **CUDA G3 路由间歇性失败。** `cache-system.json` 首次出现 8/10 块；
   `cache-disk-diagnostic.json` 同参数单独重测通过；`cache-disk-repeat.json` 第一轮通过、第二轮再次 8/10 失败，按失败即停，第三轮没有执行。
   当时 G1/G2 前缀均为零，G3 extension=8，等待 45 秒后仍未达到 10；没有把断言改成 8。
   小/大模型磁盘回载其它用例通过，不能抵消这个路由问题。
   原生卸载与路由事件链仍需进一步定位；清理阶段的 EngineDeadError 不被当作最初失败原因。
2. **Ascend Qwen32B TP4 确定性失败。** Dynamo 重复请求出现不一致；
   `native-repeat-tp4-v2.json` 在同镜像、同节点的原生 vLLM 中出现 3 种 token 序列。
   因此不需要 Dynamo 即可复现，但尚未证明是驱动、算子还是数值路径的具体问题。
   2026-10-29 按用户决定，作为原生引擎可复现的已知问题记录，暂缓修复；原始 FAIL 结果保留。
3. **CUDA DeepSeek 并发确定性失败。** Dynamo 并发 4 出现 3 种输出；
   `native-deepseek.json` 原生 TP4、单条/批量 4 也出现 2 种输出。
   原生调用没有完全复制 Dynamo 动态调度，结论限定为原生侧也可复现。
   2026-10-29 按用户决定，暂缓该确定性问题的修复；原始 FAIL 结果保留。
   30 分钟 Job 在预热一致性断言失败，未完成长稳。后续可将服务稳定性与逐 token 确定性分开验收，
   但尚未执行的新长稳及 EP/MTP/PD 用例不能因此记为通过。
4. **冻结镜像的 TP8 / PP / headless 限制。** M01 预检当前只接纳 TP1/2/4。
   因此 Ascend DeepSeek TP8、跨节点单实例 PP/TP 未获得正向验收；底层集合通信通过不能替代。
   这是本仓库平台适配层的认证范围限制，不是原生引擎或硬件不支持的证据。
   `providers.py` 仅允许 TP1/2/4；`preflight.py` 拒绝 PP>1 和 headless worker。
   Ascend `/data/DeepSeek-V4-Flash-w8a8-mtp` 已完成资产检查，尚未完成实际模型推理回归。
5. **资源结论有窗口限制。** Ascend 首轮 PD Decode RSS 增量 477–667 MiB，超过 256 MiB；
   重测固定 5 分钟预热，门槛不变，后续窗口通过。两次结果都保留，不能删除首轮失败。
   Qwen32B 资源采样中的 SSH 中断造成约 12 分钟空白，只作为过程观察，不是完整无泄漏证明。
   第二轮 PD 采样最大间隔 CUDA 60.4 秒、Ascend 62.4 秒，无该缺口；worker 组 RSS 增量最大分别约 5.1 / 64.6 MiB，原生传输和 KV 占用排空。
6. **媒体与模型限制。** CUDA 冻结 runtime 无可用 VP9 解码后端；H.264 解码通过。
   CUDA Qwen3.6 的 `Qwen3_5MoeForConditionalGeneration` 未匹配独立 encoder 的 family registry。
   Ascend 当前 encoder 初始化先加载全模型且没有在该加载器传入 TP，已有 VL-72B 权重约 146.8 GB，不能用单张 64 GiB NPU 验收该路径。
   这两项是预检/容量阻塞，不能说已经运行独立 encoder 并通过。
7. **CUDA NIXL 旧实例的握手线程异常。** 最初 32B 1P1D 的 Prefill 在首次连接之后记录
   `identity, _, msg = sock.recv_multipart()` 的 `ValueError: too many values to unpack (expected 3)`。
   已有连接继续可用，后来新 Decode 连接超时，因此首次 2P3D 请求失败。
   尚未确认额外帧的来源，不能把它归因于驱动，也不能声称修复了厂商代码。
   反向测试另有 33032 端口占用，属于测试监听端口落入临时端口区间。
   后续将固定传输端口改为低端口独立区间并重建本轮 worker，六对 PD 和 API 均通过。
   再次重建 Decode-0，六对重新握手及 token 对照也通过，相关日志未见握手线程异常；原始失败保留。
8. **性能结果不作 SLO 承诺。** 本轮同节点有并行回归用例，原生与 Dynamo 短测主要验证正确性。
   长稳记录端到端延迟分位数；同一 Pod / 同两卡的原生与 Dynamo 三轮 TTFT/TPOT 与吞吐对照已通过，见 `results/paired-latency-summary.json`。
   每阶段测量 3×32 请求，128 输入 / 64 输出 tokens，并发 4；另有 8 个预热请求。
   两阶段顺序执行，输出完全一致；另一个节点的长上下文加载可能重叠，因此不作整集群独占性能承诺。

## 模型检索与准备

按用户要求遍历四台机器可访问路径，排除 `/proc`、`/sys`、`/dev`、`/run` 等虚拟文件系统，
不跟随目录 symlink，并对重复 inode 去重。少量不可读取/解析路径有计数。
见 `results/model-discovery.json`；“未发现”不等于证明绝对不存在。

没有发现精确的 Qwen3-VL-2B-Instruct、Qwen2.5-Omni-7B 或现成 LoRA 权重。
华为已有 VL-72B；CUDA 后续按模型配置检索又确认 Qwen3.6-35B-A3B 包含 vision_config，
已作为现有的 MoE 视觉模型补测，不能再沿用早期盘点“CUDA 没有 VL”的笼统说法。

Qwen0.6 从已有资产复制并逐文件 SHA256 验证；未重新下载。
Qwen32B CUDA 4.6 补齐到 `/ssd/models/dynamo-regression/Qwen3-32B`，4.14 使用 `/root/Qwen3-32B`。
华为 Qwen32B 保留来自 7.3 的既有 NFS 存储依赖，7.3 不参与计算。
大型模型检查了配置、tokenizer、分片索引及文件存在/大小，不声称逐权重内容 SHA256 全量验证。

## 测试工具与环境修正

- CUDA NCCL 初次 `ibv_create_qp` 报内存分配失败；测试 Pod 增加 `IPC_LOCK` 后通过。
  日志 NET/IB、GDR=0，不能据此声称 GPUDirect RDMA。
- 模型复制清单曾放在模型目录内部，被 MDC extra_files 纳入校验，导致两副本不一致。
  已移到目录外，修正 `stage-model.py` 后双 worker 通过；未关闭一致性检查。
- media 初测缺 pytest async 插件，失败或跳过结果无效；后续直接 await 测试函数并计数。
- Ascend 原生 TP4 初测误给 Pod 网络设置 host-only `bond1`，修正后另开 `native-repeat-tp4-v2`，仍复现输出不一致。
- 初次 TP 不对等清单端口偏移使 `DYN_SYSTEM_PORT` 超过 i16 上限，worker 在初始化前失败。
  现使用不超过上限的系统端口，并串行复用 split 用例端口；不是引擎 TP 能力失败。
- 早期 `cache-system` runner 的退出码没有反映子用例失败，故 Kubernetes Job Complete 不等于通过。
  以结构化 `passed` 为准；后续 runner 已修正失败退出码。

## 复现与证据

`render*.py` 只生成普通 Kubernetes 资源。当前环境示例：

```bash
python3 adapter/m02/cluster/render.py --platform cuda --stage env > /tmp/cuda-env.json
# 在 CUDA 集群执行，使用前核对 namespace 和节点：
kubectl apply -f /tmp/cuda-env.json

python3 adapter/m02/cluster/render-large-pd.py --platform ascend \
  --prefill 2 --decode 3 > /tmp/ascend-large-pd.json
kubectl apply -f /tmp/ascend-large-pd.json

python3 adapter/m02/cluster/render-client.py --platform ascend \
  --name large-pd-pairs-2p3d --kind pairs --prefix large-pd \
  --prefill 2 --decode 3 > /tmp/ascend-large-pd-pairs.json
kubectl apply -f /tmp/ascend-large-pd-pairs.json

python3 adapter/m02/cluster/collect-jobs.py --ssh-config /path/to/ssh-config \
  --host huawei-52 --output /path/to/evidence --raw-output /path/to/logs \
  large-pd-pairs-2p3d
```

部署前运行相应平台 env 清单，创建测试 ConfigMap/namespace；确认模型映射及资源余量。
`large-pd`、`reverse-pd`、`split-pd-*` 使用不同逻辑 namespace；两个 split 用例必须串行。
测试 harness 只读挂载，生产模块始终来自冻结镜像。M05 厂商补丁工具只检查已有补丁，不执行 apply。
Qwen32B KVBM harness 仅将测试进程的显存预算从 0.15 调到 0.85，以容纳实际大模型权重。

精选结果在 `results/cuda/` 与 `results/ascend/`，provenance 记录实际镜像、Pod UID、节点、Job 状态和日志哈希。
`initial.json` 是阶段快照，包含失败尝试，不能当最终通过总表。
现场原始清单、日志及逐分钟快照在 `/var/tmp/dynamo-regression-1028/`；后续汇总应以具体用例结果及证据索引为准。
不导出 Secret、kubeconfig 或无关业务日志；阶段间只缩容本轮命名资源。

## 阶段收口与下一步

本轮没有修改生产代码或镜像，没有提交 Git commit，也没有开始 M08/M09。
2026-10-28 已缩容两个 namespace 内本轮所有 Deployment，保留 Completed/Failed Jobs、ConfigMaps 和 Services 以便查阅；模型副本和已导入镜像保留。
`cleanup.py` 只选择 campaign 标签匹配的资源，存在活动 Job 时拒绝提前清理。

2026-10-29 调整：Ascend Qwen3-32B TP4 与 CUDA DeepSeek 并发确定性均已有原生引擎复现，
按用户要求记录并暂缓修复，不再作为其它适配工作的前置阻塞。
G3 已完成上述诊断和修正后的三轮验证；普通引擎 TP8 / PP / headless 已修订预检，
CUDA 小模型实际通过。当前先用只读候选源码覆盖补 Ascend DeepSeek；按用户最新安排，M08/M09 完成后统一构建新镜像并进行 M10 验收。
旧 NIXL 监听线程异常仍需追踪额外消息帧来源，不能仅凭换端口后通过便宣称根因已经修复。
TP8 / PP / headless 应区分未验收与真实不支持，检查引擎拓扑、rank 与 worker 注册后修订预检、构建新镜像并验收；
不能仅移除限制便宣称通过。独立 encoder 的剩余问题单独跟踪。
Omni 资产不足和此前约定暂缓的 GMS/G4/hidden-state 项保持单列，不算通过。

最终证据入口：

- `results/evidence-index.json` 与 `results/evidence.tar.gz`：清单、日志、逐分钟快照及 SHA256。
- `results/{cuda,ascend}/job-ledger.json`：包括早期失败/无效尝试；不能直接把 Job Complete 数量当通过用例数。
- `results/{cuda,ascend}/pd-observed-resources.json`：固定资源门槛、采样完整性、原生句柄与最终 KV 占用。
- `results/paired-latency-summary.json`：同两卡、顺序执行原生和 Dynamo 的三轮短测；不是生产容量报告。
- `results/{cuda,ascend}/final-state.json`：清理后的本轮资源状态。

早期部分 worker 已在完整 Pod provenance 快照前缩容；保留的 Job provenance 不能替代缺失的 worker 快照，证据索引中明确注明这个限制。
当前脚本经过格式化和测试工具修正，历史精确执行版本以归档清单内的 ConfigMap 内容为准。
本地检查包括 Python 编译、Black/isort/Ruff、十组渲染资源约束、JSON/许可证和归档哈希校验。


## 华为 DeepSeek 后续验证

2026-10-29 的固定 vLLM 0.23.0 TP8/EP、W8A8 和 MTP 补测见
[华为 DeepSeek 记录](ascend-deepseek.md)。该轮使用明确记录的候选 Python 覆盖，
保留原生确定性失败，区分 API 功能与输出一致性，不替代 M10 新镜像验收。
