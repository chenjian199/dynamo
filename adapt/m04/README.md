<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M04 设备运行时与内存验收

2026-10-16：设备内存基础原语第一版已在 MetaX C500、Ascend 910B3、NVIDIA H800 上实现并验收。这里交付供 M05/M06 使用的 Rust 内存与完成语义；尚未启用非 CUDA KVBM、PD 或设备 RDMA。

## 实现

- `lib/platform/src/device.rs`：SDK 无关的设备/context 身份与原语契约，复用 M01 `PlatformError`。
- `lib/platform/src/memory.rs`：拥有/借用 allocation、主机注册租约、拥有/借用 stream、真实 completion、有限在途队列及 `drain`/`close`。
- `lib/platform-runtime`：显式选择 `cuda`、`metax`、`ascend`，动态加载对应 SDK。共用生命周期代码，分别声明原生 ABI；没有把 MACA/ACL 指针交给 cudarc。
- `lib/memory/src/platform.rs`：`PlatformStorage` 接入现有 Buffer、OffsetBuffer、ArenaAllocator。包装与类型擦除保留平台/context 身份。
- `dynamo-memory` 的 `cuda`、`nixl` 是可选 feature，默认同时启用以保持既有行为。`--no-default-features` 的 System/Disk、公共内存池与描述符不依赖设备 SDK/NIXL。
- 旧 `DeviceStorage`、`PinnedStorage`、`CudaMemPool` 继续服务 CUDA；新的跨平台调用使用 `PlatformStorage`。NIXL 注册入口拒绝非 CUDA provider 内存，厂商传输注册留给 M05。
- 修复 CUDA NUMA 查询未初始化 driver 的问题：只查询 PCI 拓扑、尚未构造 CudaContext 时也能工作。

设备身份不能从 `StorageKind::Device(ordinal)` 单独推断。该枚举表示空间类别；跨平台调用必须读取 `MemoryDescriptor::device_identity()`。新的 `MemoryDescriptor::cuda_device_index()` 会拒绝非 CUDA 身份。旧 `StorageKind::is_cuda()/cuda_device_index()` 仍是兼容分类助手，不能用于跨平台分派；物理 layout 的序列化/传输调用迁移属于 M06。

## 所有权与关闭

`copy_async` 返回的是实际完成回执。后台回收队列保留源、目的、流和借用 owner，丢弃回执或 Runtime 不会提前释放 DMA 使用的对象。提交/事件查询失败时改查 stream，只有确认空闲后才回收；无法确认时保留资源并施加在途数量限制。

`close(timeout)` 关闭后续复制提交并等待在途操作退出，可以重复调用；超时返回 false，可稍后重试。它不强制释放调用方仍持有的 allocation。注册注销/资源销毁失败会记录错误并保留必要 owner/SDK 句柄，避免失败后悬空；不可恢复的设备故障需要进程级恢复，不能承诺自动清空所有驱动资源。

原始导入、读写和复制接口为 unsafe：调用方仍需保证没有冲突访问。收到错误或取消回执后，必须等 `drain`/`close` 返回 true 才能复用对应内存。导入引擎张量/流时传入真实 owner；使用 ArenaBuffer 的子区间做 DMA 时，owner 必须保留该子区间，不能仅保留池的底层大块而提前归还槽位。

SDK 分配/释放、提交和有限等待是同步调用，应放在阻塞工作线程。Runtime 的 Drop 不等待 DMA；回收线程在没有 Runtime 引用且在途队列清空后退出。每次 SDK 操作在当前线程绑定正确 context，并恢复已有 context。ACL 首次使用无 context 的线程会留下所选 context；不重置设备、不销毁引擎 context、不 finalize 共享 SDK。禁止带活跃 SDK 句柄 fork 后复用。

## 验收范围

| 检查 | MetaX | Ascend | CUDA |
| --- | --- | --- | --- |
| 自有 device / pinned-host allocation | 通过 | 通过 | 通过 |
| H2D → D2D → D2H，1 MiB 字节一致性 | 通过 | 通过 | 通过 |
| 64 轮分配/复制/释放 | 通过 | 通过 | 通过 |
| 64 轮外部 host 注册/注销与 owner 释放 | 通过 | 通过 | 通过 |
| 借用 device allocation / stream 的 owner | 通过 | 通过 | 通过 |
| 跨线程操作、已有 context 恢复 | 通过 | 通过 | 通过 |
| 零/异常长度、越界、重叠、错误设备 | 通过 | 通过 | 通过 |
| ACL D2D 64 字节对齐拒绝 | 不适用 | 通过 | 不适用 |
| 取消观察、重复 drain/close、Buffer/arena 身份 | 通过 | 通过 | 通过 |
| M01/M03 Python 定向回归 | 466 passed | 465 passed / 1 skipped | 465 passed / 1 skipped |
| Qwen3-0.6B，TP=2/DP=2，KV 事件/选路与 API | 通过 | 通过 | 通过 |

Rust：公共契约 7 项生命周期测试 + 3 项 M01 身份测试；无 SDK 内存 101 项；默认 CUDA/NIXL 配置 147 项；仅 CUDA feature 106 项；仅 NIXL feature 111 项。NVIDIA 上旧 CUDA 内存 API 的硬件回归 165 项通过。MetaX x86_64 与 Ascend aarch64 均通过新 crate 的 clippy `-D warnings`。

硬件释放占用检查比较预热后与第 64 轮的设备可用内存，阈值为 8 MiB：MetaX 相差 4 KiB，Ascend/CUDA 无减少。这是短周期功能检查，不宣称长时间零增长或驱动故障认证。人为提交/完成错误、延迟完成、在途上限、Runtime 提前销毁通过可控 fake provider 验证；未人为复位真机或注入硬件故障。

原始 Rust/原生测试及源码哈希见 [results](results/)。引擎 case/API/KV 报告也保存在各平台目录；完整 server.log 留在对应容器 `/var/cache/dynamo-<platform>/m04-engine/`。引擎退出有上游 multiprocessing resource_tracker 的共享内存/信号量清理警告，三次 launcher 均记录 graceful，不将它视为已解决。

引擎回归沿用已有 editable Python 与 runtime wheel，验证 M01/M03 没有回退；它并不调用新 M04 接口。M04 的硬件结论来自本轮源码编译的独立 Rust 测试程序。

## 固定环境与 ABI

沿用 [M03 容器/镜像](../m03/README.md)：`dynamo-metax-m03-nfs`、`dynamo-ascend-m03-nfs`、`dynamo-cuda-m03-nfs`。MetaX 源码为本仓库；华为为 `/home/huawei/CJ_workspaces/dynamo`；NVIDIA 为 `/home/inspur/CJ_workspace/dynamo`。

| 平台 | 测试 SDK | 原生库与特别约束 |
| --- | --- | --- |
| MetaX C500 | MACA 3.8.2.208 / vLLM-metax 0.26.0 | `libmcruntime.so`；mcMallocHost 有第三个 flags 参数；NotReady=600 |
| Ascend 910B3 | CANN 9.1 / vLLM-Ascend 0.23.0 | `libascendcl.so`；HostRegisterV2 使用 `ACL_HOST_REG_PINNED=0x10000000`；事件 COMPLETE=1，stream COMPLETE=0 |
| NVIDIA H800 | CUDA 13 / vLLM 0.24.0 | `libcudart.so.13` + `libcuda.so.1`；不是任意 CUDA major 兼容承诺 |

ABI 与镜像内 MACA `include/mcr/mc_runtime_api.h`、`mc_runtime_types.h`、CANN `include/acl/acl_rt.h`、`acl_base_rt.h` 核对，随后执行真实原语测试。NVIDIA 没有额外安装 Rust：使用 MetaX 容器构建的相同 x86_64 SDK 动态加载二进制，在 NVIDIA 容器执行；ARM64 在华为容器本地编译。source.json/binary.sha256 记录对应源码和产物，远端旧 Git HEAD 不能代替文件哈希。

## 手动复现

在有 Rust 1.96.1、clippy 和对应 SDK 的容器内，仓库根目录运行；输出目录必须不存在：

```bash
bash adapt/m04/run.sh metax /var/cache/dynamo-metax/m04-manual
# 华为容器：脚本自动设置 generic ARM CPU，避免 neoverse-n1 指令不兼容。
bash adapt/m04/run.sh ascend /var/cache/dynamo-ascend/m04-manual
```

如果 NVIDIA 容器装有同版本 Rust，也可直接 `run.sh cuda ...`。本次使用跨容器运行：

```bash
# MetaX 容器内构建（不链接 CUDA/MACA/CANN）
CARGO_TARGET_DIR=/var/cache/dynamo-metax/m04-target \
  /root/.cargo/bin/cargo build --locked -p dynamo-platform-runtime --example device_smoke
# 把 debug/examples/device_smoke 复制到 NVIDIA 容器后运行
/tmp/m04-device-smoke cuda
```

CUDA 旧 API 硬件测试使用 `CUDARC_CUDA_VERSION=13000 cargo test -p dynamo-memory --lib --features testing-cuda --no-run` 构建，在 NVIDIA 容器运行产物并加 `--test-threads=1`。不在 MetaX 上执行 cudarc GPU 测试。

## 后续边界

跨设备 P2P 当前明确拒绝；没有验证 IPC、跨进程事件导入/导出、引擎 KV 张量事件桥接、NUMA 性能优化或长时间压力测试。CUDA 专用 pool/NUMA 优化仍保留原路径。M05 负责厂商传输注册与 PD，M06 负责 KVBM 调用、layout 身份序列化及块回收事件，M07 负责 GMS/VMM。M01 中这些高层能力不会因低层复制成功就自动标记为 available。
