<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M06 独立 KVBM 与 replicated 广播

2026-10-20 按用户要求继续独立调用链与 replicated 广播，G4/跨节点保留不做。
本记录对应独立 Rust 入口；已有 Python vLLM connector 仍使用前一轮验证的旧调用链。

## 实现

```text
PhysicalWorker / ReplicatedDataWorker
  → TransferManager::from_native → NativeTransfers
  → kvbm-kernels::native → M04 owned SDK copies/events

ReplicatedDataWorker
  → NativeCollectives → NativeCommunicator → NCCL / MCCL / HCCL
```

- 保留独立 engine 的 Worker 接口、块句柄、事件通知；本地 native manager 不初始化 CUDA/NIXL。
- 支持连续和按层分配的 dense NHD/HND、block-first/block-second；主机/文件支持 UniversalTP/PP。
  `from_allocations` 保留已有引擎缓冲区 owner，校验大小、平台、context、层间不重叠，不覆盖原内容。
- 通用 SDK pack/unpack 是正确性路径，未实现厂商融合内核，不承诺 CUDA 融合内核性能。
- G2 使用 SDK pinned 内存，G3 使用 buffered 匿名文件；Device↔Disk 经 pinned staging。
- 原生复制最多 32 个在途 span，独立物理执行及广播队列各最多 64 项；任务持有 owner 到实际完成。
- 补齐上游 `ReplicatedDataWorker::broadcast` 的未实现入口；只有 rank 0 持有 G2/G3，
  回载完成后广播，其他 rank 只持有 G1 和临时 staging。每 rank 保持自己的布局。
- 回载广播按提交顺序执行；取消等待不取消后台操作。部分层更新保留其他层，但不是层间流水优化。
- SDK 错误不得伪装成功；不能确认 DMA 完成时 fail-stop。跨进程初始化/异常退出由 supervisor
  限时清理，不声明 communicator 在原进程中透明恢复。
- CUDA 原 executor/NCCL 实现保留；新增 API 不把厂商地址或句柄传给 CUDA。
  `TransferManager::context()` / `nixl_agent()` 现在返回 `Result`，本地 native 模式明确拒绝。

## 验证方法与范围

`native_replicated` 使用两个独立进程、两张真实卡，覆盖 outer=1（MLA 风格）/2（K/V）
及原 LayoutConfig 接受的 2/4/8-byte 元素宽度，共六种配置。每例验证：

- G1→G2→G1、G1→G3→G1、G2↔G3、同设备块复制。
- rank 0 独占主机/磁盘缓存，非零 rank 的 NHD/HND 转换和广播回载一致性。
- 直接 SDK 读回设备原始字节，用独立索引公式核对，避免 pack/unpack 相互抵消错误。
- 部分层更新、取消等待后的任务完成、空批次，以及越界/重复目标块/缓冲区别名/G4/无注册远端导出拒绝。

这验证的是实际独立 KVBM 存储与广播路径，不是完整 MLA 模型端到端推理，也没有自动
开启现有 vLLM 入口的 replicated 模式。跨节点、G4、GDS/设备 RDMA、融合复制和性能对等
均不包含在上述结论中。

## 最终结果

| 平台 | 通信库 | 卡号 | 结果 |
| --- | --- | --- | --- |
| MetaX C500 | MCCL | 2、3 | [6/6 通过](results/metax/independent/summary.json) |
| Ascend 910B3 | HCCL | 2、3 | [6/6 通过](results/ascend/independent/summary.json) |
| NVIDIA H800 | NCCL | 1、3 | [6/6 通过](results/cuda/independent/summary.json) |

每例两个独立进程各自核对原始设备字节，并且全部 rank 成功退出才算通过：合计 18 例、36 个 rank 结果。
最终原始目录为各平台缓存卷 `m06-native-reviewed-20261020/`；此前 `probe` / `final`
目录属于开发迭代，不替代这批审查后证据。未加载模型，也未下载新权重。

[布局/溢出单测](results/independent-unit-final.txt) 2 passed；
[四个 crate 与例子的 Clippy](results/independent-clippy-final.txt) 在 `-D warnings` 下通过；
[旧 CUDA 测试编译](results/independent-legacy-compile.txt) 与
[独立 engine 测试编译](results/independent-engine-compile.txt) 通过。
后两项为 `--no-run`，不计为旧 CUDA 硬件测试通过；engine 测试仍有原有未使用项警告。
Black/isort、shell 语法及 `git diff --check` 通过；Flake8 沿用 88 字符配置并排除 W503。

审查补齐：超大布局返回错误而非整数溢出；不同 layout 不得复用重叠 allocation；
复制与广播队列有界；非零 rank 也拒绝 G4，避免不支持的操作返回成功。

## 复现

```bash
cd /workspace/dynamo
# 容器内构建；依赖未缓存时使用 ONLINE=1。
bash adapt/m06/native-build.sh metax
python3 adapt/m06/native-run.py --platform metax --devices 2,3 \
  --binary /var/cache/dynamo-metax/m06-independent-target/native-bin/native_replicated \
  --library-dir /var/cache/dynamo-metax/m06-independent-target/native-bin \
  --output /var/cache/dynamo-metax/m06-native-replay
```

Ascend 先加载 `/usr/local/Ascend/ascend-toolkit/set_env.sh`，构建/运行参数改为 `ascend`。
CUDA 可使用相同 x86_64 构建产物，运行时显式选择 `cuda`；将整个 `native-bin` 复制过去，
保留 `build-source.json`，并把镜像内 NCCL 库目录加入 `LD_LIBRARY_PATH`。
本机 CUDA 镜像对应 `/usr/local/lib/python3.12/dist-packages/nvidia/nccl/lib`。
不复制或替换厂商 torch、NCCL/MCCL/HCCL SDK。

收集仍使用 `python3 adapt/m06/collect.py 原始目录 新结果目录`。每例保留源文件 SHA256、
构建产物 SHA256、实际启动参数、每 rank 日志与汇总。构建时的源码快照和运行时快照分别保存。

## 调查记录

首次 CUDA 探测在生成 bootstrap 信息后发生进程崩溃，未造成 GPU 持续占用。
原因与通信库提前卸载有关：NCCL bootstrap 创建后台线程，返回 unique ID 不代表线程结束；
原临时 `Library` 在函数返回时被卸载。将所选厂商库固定为进程生命周期后，复测通过。
依据：[NCCL bootstrap 源码](https://github.com/NVIDIA/nccl/blob/master/src/bootstrap.cc)。
失败原始日志保留在 CUDA 缓存卷 `m06-native-probe/`，不计入通过证据。

## 构建与验证命令

```bash
# 与 native-build.sh 使用相同环境；不会触发 GPU 测试。
export PATH=/root/.cargo/bin:$PATH CUDARC_CUDA_VERSION=13000
export CARGO_TARGET_DIR=/var/cache/dynamo-metax/m06-independent-target
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_BUILD_JOBS=16
cargo test -p kvbm-physical --no-default-features --lib native::tests --locked --offline
cargo clippy -p kvbm-engine -p kvbm-physical -p kvbm-kernels -p dynamo-platform-runtime \
  --no-default-features --features kvbm-engine/collectives --lib --example native_replicated \
  --locked --offline -- -D warnings
cargo test -p kvbm-physical --features testing-kvbm --no-run --locked --offline
cargo test -p kvbm-engine --no-default-features --features collectives --no-run --locked --offline
```

构建日志中的 CUDA kernel stub 只用于链接保留的 CUDA 快路径符号；本例不调用这些符号。
实际复制与广播调用 CUDA/MACA/ACL 及 NCCL/MCCL/HCCL 动态库。`native-build.sh` 根据 Cargo
输出复制 C ABI 库，不能用 `debug/deps` 中同名的 Rust cdylib 替换。
