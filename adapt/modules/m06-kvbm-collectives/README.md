<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M06 KVBM 与集合通信

状态：待实现（MetaX 当前阶段）。依赖 M04 的内存/事件和 M05 的实际传输路径；M03 提供引擎 KV 布局与容量。

## 实施进度（2026-10-15）

当前基础推理及 KV 路由复用引擎本地 KV cache，没有实现 Dynamo KVBM 分层缓存或 MetaX 集合通信。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

## 当前功能与真实调用链

KVBM 在设备、主机、磁盘和远端存储之间管理 KV 块，涉及逻辑分配、物理布局、传输、完成通知、预取/卸载和多 rank 协同。`kvbm-physical` 的 executor 除复制外，还处理 NHD/HND 与 universal 布局转换。

当前 Python vLLM connector 的入口在 `lib/bindings/kvbm/python/kvbm/vllm_integration`。绑定 Cargo 默认启用 `dynamo-llm/block-manager`，Rust worker 直接引用 `dynamo_llm::block_manager::distributed::KvbmWorker`。因此其实际链路不能用独立 `lib/kvbm-engine` 的实现代替推断。

```text
vLLM connector → connector_worker.py → PyO3 KVBM worker
  → dynamo-llm/block_manager/distributed + storage + block/transfer

独立 kvbm-engine / kvbm-physical / kvbm-kernels
  → 各自调用者和构建入口；需分别迁移与验证
```

Python 创建 `torch.cuda.Event` 并传原始句柄；Rust `event_sync_blocking` 将其作为 CUevent 同步。`storage/torch.rs` 的 TorchDevice 区分 Cuda/Other；这不是可以直接支持任意 Other 设备的实现。

独立 `kvbm-engine` 已有 `CollectiveOps` 和 NCCL 实现，可复用其广播契约。该通信用于 KVBM 块协同，不等同于引擎 TP/EP 的全部通信。

## 目标设计

保留逻辑块策略，将平台差异收敛到物理分配、布局内核、传输、事件桥接和集合通信。先迁移当前生产入口，再处理独立 crate 的一致性；是否合并新旧实现单独评估，不能边移植边无依据删除旧路径。

M04 提供 Allocation/Stream/Event，M05 提供注册与传输，M06 负责把它们绑定到块生命周期。块被 engine 或传输占用时不可回收；取消 consumer 不代表该块可以立刻重新分配。

| 适配面 | 设计要求 |
| --- | --- |
| 张量注册 | 记录 dtype、shape/stride、block size、层与分片、owner；未知布局拒绝而不是按连续内存解释 |
| 批量复制 | provider 返回真实完成对象；无 batch API 时可以使用明确的多次异步复制实现 |
| 布局变换 | 按真实引擎格式实现 pack/unpack；不要求跨厂商 KV 通用格式，但同平台 offload/reload 必须可逆 |
| 事件桥接 | Python event 通过受控 provider 导入；保留 Python owner；Rust 不再把所有 u64 转成 CUevent |
| 集合通信 | 复用 `CollectiveOps`；明确 communicator 所有权、参与 rank、stream 和本地完成语义 |
| 快路径 | host staging、device RDMA、direct storage 独立探测；关闭快路径不伪装等价性能 |

外部引擎缓存和 Dynamo KVBM 必须有唯一块所有权/回收责任。不能同时让两个 offload manager 管理同一物理块而不定义互斥和完成协议。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [connector_worker.py](../../../lib/bindings/kvbm/python/kvbm/vllm_integration/connector_worker.py) | 改为平台事件工厂及导入协议，保持事件 owner 到最后一次使用完成 |
| [kv_cache_utils.py](../../../lib/bindings/kvbm/python/kvbm/vllm_integration/kv_cache_utils.py) | 从实际张量导出平台/布局，检查 shape/stride/dtype |
| [Rust connector worker](../../../lib/bindings/kvbm/src/block_manager/vllm/connector/worker.rs) | 替换 CUevent 同步入口，接入 M04 完成对象；保持清理顺序 |
| [distributed.rs](../../../lib/bindings/kvbm/src/block_manager/distributed.rs) | 张量设备与布局跨语言转换，保留 owner 和错误信息 |
| [storage/torch.rs](../../../lib/llm/src/block_manager/storage/torch.rs)、[storage/cuda.rs](../../../lib/llm/src/block_manager/storage/cuda.rs) | 使用通用设备身份与分配契约，CUDA 实现作为特例保留 |
| [block/transfer/cuda.rs](../../../lib/llm/src/block_manager/block/transfer/cuda.rs)、[nccl.rs](../../../lib/llm/src/block_manager/block/transfer/nccl.rs) | 迁移当前调用路径中的复制和集合通信 |
| [v2 executor/cuda.rs](../../../lib/llm/src/block_manager/v2/physical/transfer/executor/cuda.rs)、[v2 kernels/mod.rs](../../../lib/llm/src/block_manager/v2/kernels/mod.rs) | 审计实际引用后复用公共 provider；避免遗留第二套 CUDA-only 路径 |
| [独立 executor/mod.rs](../../../lib/kvbm-physical/src/transfer/executor/mod.rs)、[strategy.rs](../../../lib/kvbm-physical/src/transfer/strategy.rs) | 选择 provider 和转换内核；保持边界及布局校验 |
| [tensor_kernels.rs](../../../lib/kvbm-kernels/src/tensor_kernels.rs)、[tensor_kernels.cu](../../../lib/kvbm-kernels/cuda/tensor_kernels.cu)、[build.rs](../../../lib/kvbm-kernels/build.rs) | 将 CUDA 内核封装为实现；按平台构建经验证的 copy/pack/unpack |
| [collectives/mod.rs](../../../lib/kvbm-engine/src/collectives/mod.rs)、[nccl.rs](../../../lib/kvbm-engine/src/collectives/nccl.rs) | 保留 CollectiveOps；从 CUDA event 返回规范化通知；支持实际所需的厂商实现 |
| [kvbm-engine worker/physical.rs](../../../lib/kvbm-engine/src/worker/physical.rs) | 将物理 Worker 与所选设备/通信 provider 绑定，防止初始化后切换 |

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `lib/bindings/kvbm/python/kvbm/platform_events.py` | event 创建、导入、owner 与关闭的 Python 边界 |
| `lib/bindings/kvbm/src/platform_event.rs` | 校验 provider/设备与事件类型的 PyO3 桥接 |
| `lib/kvbm-kernels/src/backend.rs` | copy/pack/unpack 内核契约及能力表 |
| `lib/kvbm-kernels/src/backends/{cuda,ascend,metax}.rs` | 平台 kernel 分派；厂商内核实现由所选 SDK 工具链确定 |
| `lib/kvbm-physical/src/transfer/executor/device.rs` | 使用 M04/M05 的通用设备执行入口 |
| `lib/kvbm-engine/src/collectives/{provider,ascend,metax}.rs` | provider 选择与经验证通信实现，按实际需求添加 |
| `tests/kvbm_integration/test_platform_roundtrip.py` | 不同平台分别执行 offload/reload、布局和多 rank 验证 |
| `lib/kvbm-kernels/tests/platform_roundtrip.rs` | 内核输入输出与边界；stub 只验证不可用行为 |

## 迁移与验收

按“活动调用链记录 → CUDA 事件桥接 → CUDA 内存/复制迁移 → 厂商原语 → host offload → SSD/远端 → 多 rank → PD 组合”推进。每步保留独立可运行的 CUDA 回归，避免一次性更换所有存储策略。

验收分别覆盖字节回读、布局可逆性、不同 block size/stride、内存不足、块被重复引用、在途取消、缓存淘汰、层间流水、多 rank 广播和长稳回收。模型输出另按精度容差验收，不能只比较一次生成字符串。

Ascend/MetaX 的 kernel 工具链、事件句柄导入和集合通信原语尚待验证。若某平台只有 host staging，矩阵记录其有限支持范围；不能据此声明全部 KVBM 快路径对等。
