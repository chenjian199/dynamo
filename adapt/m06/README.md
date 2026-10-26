<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M06 KVBM 适配与验收

> 2026-10-21 基线变更：NVIDIA 后续固定官方 Dynamo 1.5.1 / vLLM 0.28.0。本页 CUDA 0.24 结果为历史证据，不代表 0.28 已通过；详见[版本基线](../version-baselines.md)。

2026-10-20 已闭环当前 vLLM 调用链的三平台本地分层 KV 缓存验收。
随后按用户要求补齐独立 Rust 本地调用链与 replicated 广播，详见 [独立 KVBM 验收](independent.md)；G4/跨节点暂缓。
**当前路径已验收；完整 M06 设计仍为部分完成**。范围是 eager、BF16、
Qwen3-0.6B、TP1/TP2、DP1；SGLang 不在本轮范围。不能由这些结果推导所有
模型、全部集合通信组合或存储快路径均已支持。

## 实现与生命周期

```text
vLLM DynamoConnector / prefill PdConnector
  → connector_worker.py → PyO3 KvConnectorWorker
  → dynamo-llm/block_manager/distributed
  → M04 DeviceRuntime → CUDA / MACA / ACL 原生 SDK
```

保留原有 leader、逻辑块池、调度器、ZMQ 通信及多 rank 完成协议。新增的物理
`PlatformStore` 管理设备张量、pinned 主机池和本地临时文件；非 CUDA 指针不进入 cudarc。

- 从真实张量校验设备、dtype、shape/stride、block 轴和分片，保留 Python tensor owner。
  Ascend 的每层 K/V 张量分别注册；只接受 ND dense 格式，拒绝 blocked/NZ 格式。
- Python 创建并持有原生 SDK event，Rust 调用该对象同步；不跨平台解释原始 CUevent。
- NHD/HND 按显式 strides 打包，每批最多 32 个异步 span，每个 span 都有真实完成事件。
  这是通用正确性实现，MetaX HND 小片段复制仍有明显开销，不宣称与融合 CUDA 内核等速。
- G2 使用厂商 pinned allocation；G3 使用 `DYN_KVBM_DISK_CACHE_DIR` 内匿名临时文件和
  buffered I/O。Device→Disk 仍经一块 pinned staging，不是设备直写或 GDS。
- ACK、调度完成 owner 和复制工作放在同一个 blocking job 内；放弃异步等待不会提前释放块。
  SDK 失败会 poison executor，不能确认 DMA 完成或无法表达失败 ACK 时终止 worker。
  当前 success-only wire protocol 下不支持在原进程中透明恢复坏掉的 executor。
- 修复原有容量不足分支：Scheduled 卸载的空操作仍以 Scheduled 完成，避免计数不一致。
- CUDA 默认保留原物理快路径；`DYN_KVBM_TRANSFER_BACKEND=platform` 显式验证相同通用实现。
- TP2 是每个 rank 保存自己的分片、等全部 rank 完成；不等于 MLA replicated 模式的 NCCL/MCCL/HCCL 广播。

## 复现

在已有开发容器中操作，保持原厂 torch/vLLM 版本，不通过安装依赖升级推理引擎。
容器和源码目录沿用 M03/M05；NVIDIA 源码已统一为 `/home/inspur/CJ_workspace/dynamo`。
模型默认 `/NFS_62/Models/Qwen_Series/Qwen3-0.6B`，不从外网下载。

```bash
cd /workspace/dynamo
# 在 MetaX / Ascend 各自容器中构建；首次缺 Cargo 缓存时可加 ONLINE=1。
bash adapt/m06/build.sh metax
# bash adapt/m06/build.sh ascend

# 使用新的输出目录；同一台机器按顺序运行，避免多个引擎初始化互相干扰。
DEVICE=0 TP_DEVICES=0,1 bash adapt/m06/run-suite.sh metax /var/cache/dynamo-metax/m06-replay
# DEVICE=0 TP_DEVICES=0,1 bash adapt/m06/run-suite.sh ascend /var/cache/dynamo-ascend/m06-replay
```

CUDA 原物理路径需要匹配当前 Rust 绑定的 NIXL C API。使用独立
`/opt/dynamo/m06-venv`，不覆盖 M03/M05 的 `/opt/dynamo/venv`：

```bash
# 在具备 Rust 的 x86_64 开发容器中，从相同 checkout 构建 wheel。
cd /workspace/dynamo/lib/bindings/kvbm
CUDARC_CUDA_VERSION=13000 CARGO_PROFILE_DEV_DEBUG=0 \
  maturin build --locked --offline -i /opt/dynamo/venv/bin/python -o /tmp/m06-wheel
# 将该 wheel 复制到 CUDA 容器，再执行：
cd /workspace/dynamo
bash adapt/m06/install-cuda.sh /tmp/m06-wheel/kvbm-1.5.1-cp310-abi3-linux_x86_64.whl
DEVICE=1 TP_DEVICES=1,3 DISABLE_CUSTOM_ALL_REDUCE=1 bash adapt/m06/run-suite.sh cuda /var/cache/dynamo-cuda/m06-replay
```

`install-cuda.sh` 仅在隔离环境安装 KVBM 和 `nixl==1.3.2` / `nixl-cu13==1.3.2`；
实际引擎仍为 CUDA vLLM 0.24.0。MetaX 为 0.26.0，Ascend 为 0.23.0。
可以用 `MODEL=/已有本地副本` 覆盖模型路径。本次 NVIDIA 主机重启后 NFS 挂载丢失，
挂载随后已由用户恢复；当时的顺序复测使用从该 NFS 复制到 `/var/cache/dynamo-cuda/models/Qwen3-0.6B` 的同一模型。

Dynamo frontend/worker 与 PD 的组合测试复用 M05 harness。KVBM 不支持 HMA；
NIXL 把该设置包含在握手 hash 中，因此 **prefill 和 decode 都必须显式设置
`--disable-hybrid-kv-cache-manager`**。harness 的 `--kvbm` 已设置此项，不能通过
关闭 `enforce_handshake_compat` 绕过布局契约。

```bash
/opt/dynamo/venv/bin/python adapt/m05/run.py --platform metax --kvbm --cancel \
  --devices 2,3 --output /var/cache/dynamo-metax/m06-pd-replay
# 聚合服务使用 --aggregate --kvbm；Ascend 通过 bash adapt/m05/ascend-python.sh 启动。
# CUDA 使用 /opt/dynamo/m06-venv/bin/python。
```

双 worker 分层选路独立运行，每次使用新输出目录：

```bash
/opt/dynamo/venv/bin/python adapt/m05/run.py --platform metax \
  --aggregate --kvbm --kv-routing --kv-routing-tier host --devices 2,3 \
  --output /var/cache/dynamo-metax/m06-routing-host-replay
# 改为 --kv-routing-tier disk 验证磁盘层；Ascend/CUDA 使用上面的对应 Python 环境。
```

harness 显式启用 KVBM event consolidator 的 `passthrough` 模式及 frontend KV 路由。
`routing.py` 先把 640-token 前缀固定发给一个 worker，再逐出其设备副本；磁盘用例还
逐出主机副本。直接查询 router 的分层 overlap，确认 G2/G3 的独有命中块，再发送
不指定 worker 的请求，要求路由选中缓存持有者、实际回载、输出与 resident 结果一致，
最后恢复 G1 命中。另一个 worker 必须无该前缀命中，避免两个副本同时命中造成误判。
router 的 `disk_blocks` 是累计匹配长度，不能单独证明 G3 驻留；检查独有层级
`disk_extension_blocks`，并要求实际 Host→Disk、Disk→Device 日志及 KV 字节回读。

## 验收方法

`run-suite.sh` 依次运行无 KVBM 基线、G2 四轮淘汰/回载、G3、无 G2 直达磁盘、
TP2 基线、TP2 KVBM、极小主机池。G2/TP2/小池穿插 3 次流式取消和恢复。
四轮共 24 个普通请求的 RSS/FD 检查是有界回收检查，**不是 30 分钟长稳**。

`DYN_KVBM_VERIFY_TRANSFERS=1` 是验收专用：每次回载后再次从实际引擎设备块读回，
与缓存源逐字节比较，成功后才 ACK。该开关默认关闭，开启会增加复制成本。
G3 用例必须同时看到 Host→Disk 和 Disk→Device；直达磁盘用例必须同时看到
Device→Disk 和 Disk→Device。仅配置了磁盘、仅生成文本或仅观察到写盘均不能通过。

输出校验区分冷 prefill 与 resident prefix。BF16 的完整 prefill 和缓存后短 prefill
可能因为内核形状不同而改变接近并列 token 的排序；本次 CUDA/Ascend 无 KVBM 基线
也重现了这一现象。测试严格比较“回载结果”和“同配置 resident-cache 基线”，并独立
执行真实 KV 字节回读；没有放宽输出比较来掩盖拷贝问题。跨平台文本不要求逐 token 相同。

## 证据

结果位于 [results](results/)，每例保留 summary、源码 SHA256、有限日志摘录及原始
日志路径/SHA256。使用 `python adapt/m06/collect.py 原始目录 结果目录` 收集；不手工
修改生成结果。测试失败的调查日志仍保留在各容器缓存卷，不作为通过证据。

三平台原生复制均通过 18 种布局/宽度组合 × 4 轮独立 pack/unpack 字节校验。

| 用例 | MetaX 0.26.0 | Ascend 0.23.0 | CUDA 0.24.0 |
| --- | --- | --- | --- |
| G2 淘汰/回载、24 请求及取消恢复 | 通过，7488 external hits、126 次字节回读 | 通过，6656 hits、56 次回读 | 原 CUDA 路径通过，7488 hits；不使用通用 SDK 回读探针 |
| G3 分层回载 | 通过，9 次回读 | 通过，4 次回读 | 通用 SDK 路径通过，9 次回读 |
| 无 G2 的 G1↔G3 | 通过，9 次回读 | 通过，4 次回读 | 通用 SDK 路径通过，9 次回读 |
| TP2、取消恢复 | 通过，36 次回读 | 通过，16 次回读 | 原 CUDA 路径及通用 SDK 路径均通过；通用路径 36 次回读 |
| 主机池不足退化 | 通过，无外部命中，仍可推理及取消恢复 | 通过，无外部命中，仍可推理及取消恢复 | 通过，无外部命中，仍可推理及取消恢复 |
| Dynamo frontend 聚合 + KVBM | 通过，8 次回读 | 通过，4 次回读 | 双 worker 聚合及 G2 选路通过，10 次回读 |
| prefill KVBM + PD + 取消恢复 | Mooncake maca / NIXL 均通过，各 8 次回读 | Mooncake 通过，4 次回读 | NIXL 通过，8 次回读 |
| G2 分层选路、回载及输出一致性 | 通过，10 个 G2 块 | 通过，5 个 G2 块 | 通过，10 个 G2 块 |
| G3 分层选路、回载及输出一致性 | 通过，10 个 G3 独有块 | 通过，5 个 G3 独有块 | 通过，10 个 G3 独有块 |

PD 修复包括：MultiConnector 仅向子连接器绑定一次 metadata；peer contract 比较实际
传输子连接器，不把本地 offload wrapper 当成协议；Mooncake 没有 Prometheus exporter
时不安装空 MultiConnector exporter。原有模型/精度/布局/版本拒绝规则仍保留。

Ascend 复测发现 ADXL 先启动时会选中尚未绑定的测试端口，导致后启动的 Mooncake
握手监听报 `Address already in use`。harness 改为在前一个引擎就绪后再为下一个
worker 分配 bootstrap 端口，并在启动前落盘命令，便于失败追溯。

NVIDIA 已于 2026-10-20 恢复，最终通过证据位于 `results/cuda/recovered/`。
使用正常 NVIDIA runtime 的 `dynamo-cuda-m03-nfs` 容器和 GPU 1、3，顺序执行七项
suite、通用 SDK TP2、PD 和路由测试。TP2 基线和 KVBM 均关闭 custom all-reduce、
使用相同 NCCL 配置，严格比较 resident-cache 输出。此前 `tp2_blocked/`、runtime-blocker
及隔离复测中断记录保留为历史调查证据，不纳入通过计数，也不能据此确认故障根因。
具体恢复过程见 [NVIDIA 复测记录](cuda-isolation.md)。

三平台分层路由证据分别保存在 `routing/`、`routing-disk/`（CUDA 在 `recovered/` 下）。
首轮 G3 检查曾因日志颜色转义码漏匹配传输方向而失败；修正解析后完整重跑通过，
没有修改旧失败 summary。所有原始日志仍保留在各平台缓存卷。

定向 Python 回归：MetaX 548 passed；Ascend 546 passed / 1 skipped（当前引擎版本无对应异常类型）。CUDA 恢复后重新执行为 546 passed / 1 skipped，已收集；此前结果不替代最新结果。已收集输出见各平台 `unit.txt`。复现命令：
`bash adapt/m06/check-unit.sh metax`（另选 `ascend` / `cuda`）。该脚本沿用 M03 的
厂商 warning 过滤，清除全局 `DYN_PLATFORM`，让单测自行设置平台；真机脚本仍显式选平台。
Rust layout 单测、scheduler 单测、native crate clippy、Black/isort 和 `git diff --check`
分别记录。Flake8 排除与 Black 换行规则冲突的 W503；仓库原 hook 启用 W503 时仍会报告
既有及新增的操作符换行，不能声称未调整参数的全部 pre-commit 已通过。

## 明确边界

- 活动 vLLM 仍使用旧 distributed worker；新增独立 `kvbm-engine` / `kvbm-physical` /
  `kvbm-kernels` 的本地 native API 单独验证，不表示原 CUDA-only RPC/服务入口均已迁移。
- 独立 replicated 广播通过实际 NCCL/MCCL/HCCL 验证；现有 Python vLLM 入口尚未
  开启 replicated 模式，也没有完整 MLA 模型端到端验收。
- G4 远端缓存、跨节点、GDS、设备直连 RDMA、厂商融合 pack/unpack 内核未验收。
- 混合 KV 形状、LoRA/量化/推测解码/多模态与 KVBM 的组合、DP>1、TP>2 未认证。
  M03 的高级能力验收不能自动扩展到 KVBM 组合。
- KV event consolidator 的分层选路使用显式 passthrough 模式验收；默认 dedup 模式
  不据此声明等价层级信息。层间传输流水仍未单独验收。
- NVIDIA 曾出现驱动等待及开发容器 SIGTERM，无法归因于单个代码改动。
  这些被中断的并行运行不计入验收；恢复后改为顺序重跑。
