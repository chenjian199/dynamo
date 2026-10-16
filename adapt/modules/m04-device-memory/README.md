<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M04 设备运行时与内存

状态：待实现（MetaX 当前阶段）。依赖 M01/M02；提供 M05/M06 使用的内存和完成语义。GMS 的 VMM 抽象归 M07，避免重复实现同一服务层。

## 实施进度（2026-10-15）

M01 已能查询设备身份与容量，但未新增 MetaX 设备分配、注册内存、流或完成事件实现。torch 可用不等于本模块完成。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

## 当前功能与调用链

`lib/memory` 提供 `MemoryDescriptor`、Buffer、设备/主机/磁盘存储、外部内存包装、pool 与 NIXL 注册。`device.rs::DeviceStorage` 直接持有 `CudaContext`，分配与释放调用 cudarc；`pinned.rs` 使用 CUDA host allocation；`pool/cuda.rs` 暴露 CUDA pool。

公共 `StorageError` 直接引用 CUDA/NIXL 错误类型，`StorageKind::Device(u32)` 和 `is_cuda()` 将设备存储等同于 CUDA。`MemoryDescriptor::nixl_descriptor()` 也使公共接口带有特定传输库类型。只替换 allocator 不足以完成依赖隔离。

现有数据关系是：具体 Storage → MemoryDescriptor/Buffer → 物理 layout → transfer executor → CUDA stream/event。借用引擎 KV tensor 时还需要保留引擎分配对象的存活期。

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

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `lib/platform/src/device.rs` | DeviceRuntime/context/stream 契约；使用 M01 身份和错误 |
| `lib/platform/src/memory.rs` | allocation、borrowed allocation、host registration 的边界与所有权 |
| `lib/platform/src/event.rs` | CompletionEvent 和异步等待/回收规则 |
| `lib/platform-cuda/Cargo.toml`、`lib/platform-cuda/src/{lib,device,memory,event}.rs` | 抽离现有 cudarc 实现；M02 负责 features 和构建 |
| `lib/platform-ascend/Cargo.toml`、`lib/platform-ascend/src/{lib,device,memory,event}.rs` | 验证 SDK 原语后实现，不能先用空完成事件占位 |
| `lib/platform-metax/Cargo.toml`、`lib/platform-metax/src/{lib,device,memory,event}.rs` | 验证 MACA API/ABI 后实现，不假定 cudarc 可复用 |
| `lib/memory/src/pool/device.rs` | 与平台运行时组合的通用池包装，按实际调用需求建立 |
| `lib/platform/tests/memory_contract.rs` | mock provider 检查生命周期、边界和错误传播 |
| `tests/platforms/test_device_memory.py` | 各平台真实分配、复制、注册、异步顺序与故障验证 |

## 平台支持与实施关卡

CUDA 原语有现有实现，可作为契约回归。Ascend/MetaX 必须提供分配、复制、host registration、stream/event、线程 context 和必要 P2P 的实测映射；未提供的能力不得从“有 PyTorch tensor”推导出来。

先迁移 CUDA allocation/event，再让公共 memory crate 去掉无条件 CUDA 引用；使用 mock 校验资源顺序，随后验证其他 provider，最后交给 M06 接入 KVBM。原有构造函数可用兼容 façade 保留，待调用者迁移后再删除。

验收覆盖零/异常长度、对齐、借用 owner、跨线程、重复关闭、DMA 在途取消、设备错误和循环分配回收；测试完成后比较设备/注册内存占用，确认没有早释放或持续增长。
