<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M04 设备运行时与内存

状态：基础原语第一版完成（MetaX/Ascend/CUDA，2026-10-16）。依赖 M01/M02；提供 M05/M06 使用的内存和完成语义。KVBM/PD 调用接入及 GMS/VMM 不在本轮启用范围。

## 已交付与证据

完整实现、版本、所有权规则、测试命令和支持边界见 [M04 三平台验收](../../m04/README.md)。

- `lib/platform/src/device.rs`、`memory.rs`：设备/context 身份、拥有/借用内存与流、host 注册、真实完成事件、有限在途回收、重复关闭。
- `lib/platform-runtime`：CUDA 13 / MACA 3.8.2 / CANN 9.1 原生 ABI，显式选择后动态加载 SDK。共用契约，不共用厂商句柄。
- `lib/memory/src/platform.rs`：PlatformStorage 接入 Buffer/OffsetBuffer/ArenaAllocator；包装保留 device_identity，旧 CUDA API 保留。
- `dynamo-memory --no-default-features`：System/Disk 与公共内存类型不引入 cudarc/NIXL；默认 features 保持旧 CUDA/NIXL 兼容。NIXL descriptor 方法仅在 nixl feature 启用时存在。
- 三平台验证 allocation、H2D/D2H/D2D、64 轮回收/注册、借用 owner、跨线程、context 恢复、取消、边界和关闭；设备错误使用 fake provider 注入。CUDA 旧 API 的 165 项硬件回归通过。

`StorageKind` 保留空间类别和旧序列化形式，平台/context 身份由 MemoryDescriptor 单独提供。不得单凭旧 `StorageKind::is_cuda()` 分派平台；应使用 descriptor 身份或新的 descriptor.cuda_device_index()。M06 负责将身份贯穿物理 layout 与 transfer executor。M05 的厂商 NIXL 注册未交付，当前兼容入口明确拒绝非 CUDA provider 内存。

## 当前调用边界

新调用链为 DeviceBackend → DeviceRuntime/Allocation/CompletionEvent → PlatformStorage → MemoryDescriptor/Buffer。已有 DeviceStorage/PinnedStorage/CudaMemPool 仍属于 CUDA 兼容入口；本轮没有把所有旧构造函数改写为新的 provider。下列原设计中未由“已交付”明确覆盖的项仍是后续计划，特别是 KVBM executor、外部引擎事件桥接、P2P 和 NUMA 优化。

## 目标接口与所有权

在 M01 的 `lib/platform` 中增加设备契约；它不能依赖 `lib/memory`，避免形成循环依赖。`lib/memory` 用契约提供的 allocation/event 抽象实现 Storage，CUDA/Ascend/MetaX provider 封装实际 SDK。

| 拟议接口 | 操作语义 |
| --- | --- |
| `DeviceRuntime` | 枚举/选择设备，创建或借用上下文；声明线程与进程使用限制 |
| `Allocation` / `ExternalAllocation` | owned 与 borrowed 分开；保存设备身份、字节范围、对齐及 owner 保活引用 |
| `HostRegistration` | 注册主机内存并持有租约；是否可供特定设备 DMA 由 provider 确认 |
| `ExecutionStream` | opaque 句柄，只由创建/导入它的 provider 操作；明确 borrowed stream 不得销毁 |
| `CompletionEvent` | record/query/wait 和操作完成；禁止将提交成功当作完成 |
| `copy_async` | 校验方向、范围、设备和 stream；返回持有源/目的及注册租约的完成对象 |

同厂商不等于同一 context 或同一地址空间。错误的设备、越界复制、已注销主机内存、未支持 P2P 在提交前拒绝。对需要绑定线程的 SDK，provider 在实际执行线程处理 context，而不是启动时绑定一次。

资源释放顺序：等待或确认设备操作终止 → 释放传输注册 → 释放内存 → 释放必要上下文。Rust Drop 无法可靠表达异步等待的场景，应提供显式关闭/回收队列；不得在任意 Tokio 线程无界阻塞，也不得遇到取消就立即 free。

普通 CPU System/Disk 存储不初始化设备 SDK。公共 MemoryDescriptor 不强制返回 NIXL 专有类型；可保留临时兼容扩展 trait，由 M05 负责传输注册适配。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [memory/src/lib.rs](../../../lib/memory/src/lib.rs) | 分离 StorageKind 的空间类别与平台身份，规范化错误，隔离 NIXL descriptor 接口 |
| [device.rs](../../../lib/memory/src/device.rs) | 将 CUDA 分配实现委托给 provider；DeviceStorage 保留通用所有权包装 |
| [pinned.rs](../../../lib/memory/src/pinned.rs) | 平台化主机 allocation/registration；CUDA write-combined 优化留在 CUDA provider |
| [external.rs](../../../lib/memory/src/external.rs)、[tensor.rs](../../../lib/memory/src/tensor.rs) | 验证借用张量的平台、布局和 owner；不隐式获得裸地址的所有权 |
| [pool/cuda.rs](../../../lib/memory/src/pool/cuda.rs) | 提取公共 pool 需求，CUDA pool 作为特定实现；保留现有 API 的兼容入口 |
| [numa/nvml.rs](../../../lib/memory/src/numa/nvml.rs) | NVML 拓扑查询限制在 CUDA 实现；公共 NUMA 层消费规范化结果 |
| [nixl.rs](../../../lib/memory/src/nixl.rs) | 与 M05 协调，将 NIXL registration 生命周期绑定到通用内存 owner |
| [transfer/context.rs](../../../lib/kvbm-physical/src/transfer/context.rs) | 持有抽象 execution context，不让所有平台必须创建 CudaStream |
| [executor/cuda.rs](../../../lib/kvbm-physical/src/transfer/executor/cuda.rs)、[notifications/cuda_event.rs](../../../lib/kvbm-physical/src/transfer/notifications/cuda_event.rs) | CUDA 路径作为 provider 实现保留，M06 将调用处改为公共接口 |

## 原文件规划与实际落点

| 拟新增路径 | 内容 |
| --- | --- |
| `lib/platform/src/device.rs` | DeviceRuntime/context/stream 契约；使用 M01 身份和错误 |
| `lib/platform/src/memory.rs` | allocation、borrowed allocation、host registration 的边界与所有权 |
| `lib/platform/src/event.rs`（原拟议） | 已合并到 memory.rs，避免拆分同一生命周期状态 |
| `lib/platform-runtime`（实际） | CUDA/MACA/ACL provider 共用加载和 context guard，ABI 分支分别处理；没有新增三个重复 crate |
| `lib/memory/src/pool/device.rs` | 与平台运行时组合的通用池包装，按实际调用需求建立 |
| `lib/platform/src/memory_tests.rs`（实际） | fake provider 检查生命周期、边界和错误传播 |
| `lib/platform-runtime/examples/device_smoke.rs`、`adapt/m04/run.sh`（实际） | 直接调用 Rust 原语做三平台硬件验收，不以 torch 可用代替 |

## 平台支持与实施关卡

CUDA 原语有现有实现，可作为契约回归。Ascend/MetaX 必须提供分配、复制、host registration、stream/event、线程 context 和必要 P2P 的实测映射；未提供的能力不得从“有 PyTorch tensor”推导出来。

先迁移 CUDA allocation/event，再让公共 memory crate 去掉无条件 CUDA 引用；使用 mock 校验资源顺序，随后验证其他 provider，最后交给 M06 接入 KVBM。原有构造函数可用兼容 façade 保留，待调用者迁移后再删除。

验收覆盖零/异常长度、对齐、借用 owner、跨线程、重复关闭、DMA 在途取消、设备错误和循环分配回收；测试完成后比较设备/注册内存占用，确认没有早释放或持续增长。
