<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M07 GMS 与多模态高级能力

状态：部分交付 / 有限支持。MetaX GMS V1 与 CUDA 回归通过，三平台单机独立编码器及 LOCAL 媒体路径通过；Ascend GMS 被当前驱动组合阻塞。依赖 M01/M02/M03；设备原语与传输分别引用 M04/M05。

2026-10-26：按用户决定，Ascend GMS 自研暂缓，保留
[候选设计](../../m07/ascend-gms-design.md)，优先推进其它 M07 遗留项；
现有 GMS 能力声明及拒绝逻辑保持不变。

2026-10-26 后续决定：华为相关未完成项统一记录后暂缓，包括 Ascend GMS、
NIXL READ/WRITE 环境补齐、Omni 用户态退出 SIGABRT。保留证据与已通过能力，
不将暂缓项标为完成。独立 Omni full-payload 通道也按用户决定暂缓；
原生聚合模式的通过结果不覆盖独立 stage 服务。

## 阶段性收口（2026-10-26）

CUDA/MetaX 原生聚合 Omni 并发度 2、各 30 分钟持续负载及取消恢复通过。正常请求
1099/499 个全部成功，取消和后端 abort 为 110/50 次；资源增量满足本轮有界条件。
[详细记录](../../m07/omni-regression.md)。当前约定范围收口，模块总体仍为有限支持，
独立 stage 和华为未完成项继续暂缓，其余未认证组合沿用原边界。

## 后续进度（2026-10-26）

- LOCAL lease/TTL 与进程死亡后的回收、后台视频解码并发配额已实现。
- 三平台不同物理卡的编码器/LLM 回归、6000 帧 VP9 抽帧压力和取消恢复通过。
- WRITE v2 的 CUDA/MetaX 真机正常与超时晚到回收通过；Ascend 缺 NIXL Python，永久失联仍采取有界隔离。
- Omni 基础 factory/设备/取消与内存预算已适配，CUDA/MetaX 44 项单元及原生聚合三卡音频生成通过；独立 worker 的 full-payload 通道和 Ascend 用户态退出 SIGABRT 未闭环。
- 通用单元最新为 CUDA 141、MetaX 141、Ascend 117。详见 [四项后续验收及范围](../../m07/followup.md)，本模块仍标记部分交付。

## 实施进度（2026-10-21）

实现、复现命令、分平台原始证据及未完成项见 [M07 分项验收](../../m07/README.md)。

- 固定基线：CUDA vLLM 0.28.0 / 官方 Dynamo 1.5.1；MetaX 0.26.0；Ascend 0.23.0。已撤销为 CUDA 0.24 添加的 GMS 兼容分支，恢复原生 SleepModeBackend；详见[版本锁定](../../version-baselines.md)。
- GMS：复用 VMM 工厂新增 `MetaXVMM`，使用 MACA POSIX FD、稳定 UUID 和真实权限查询。RW→RO 原地切换不生效，提交前保持原地址重新映射 RO；失败禁止发布。CUDA/MetaX 各 4 项真机测试和 6 项旧 tensor/module 测试通过，包括客户端退出后的 allocation 回读、MemPool 恢复和 vLLM 两轮 sleep/wake。
- Ascend：2026-10-26 已复测 CANN 9.1/HDK 26.1.1，权限接口符号、RW 设置及查询可用，但切换/新建/重映射三种 READ 设置均失败，POSIX 分配仍失败。GMS 工厂与预检查明确拒绝；[新驱动证据及原语限制](../../m07/ascend-hdk2611.md)。旧 25.2.0 缺失符号的结论仅保留为历史记录。
- 多模态：新增 `tensor_contract.py`，严格校验描述，厂商设备显式 CPU staging；三平台 LOCAL、CUDA/MetaX NIXL READ 和三平台 CPU VP9 解码通过。三平台独立编码器图文、并发/流式/取消/非法输入恢复通过，测试临时文件残留为零；华为使用完整模型初始化兼容 0.23，仍需预留完整模型显存。
- 最新单元回归：CUDA 132、MetaX 132、Ascend 108 项通过。M01 `gms.vmm` 在选择 GMS 时检查已安装 provider，实际 SDK/权限仍在使用时验证；不能把可配置能力当作所有设备已验收。
- 未完成：Ascend GMS、完整 checkpoint/CRIU/旧引擎 loader、Omni stage、NIXL WRITE 故障协议、发布后进程崩溃的媒体 lease/GC、长视频及硬件加速解码。没有跨节点验收。

以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。实现复用了现有工厂与 `v1/device.py`，未另外增加 registry/device_identity；文件计划不代表每个拟新增文件均已创建。

## GMS 的当前功能与扩展基础

GMS 管理设备物理内存、跨进程共享句柄和虚拟地址映射，使内存生命周期与具体模型执行进程分离。客户端映射、服务端 allocation、权重加载和恢复必须共同维护所有权。

当前已存在 `common/vmm/device.py::VMMDevice` 抽象，提供 create/release、地址预留、map/unmap、访问权限、FD 导入导出、host register 和 stream/copy。`common/vmm/__init__.py` 的工厂选择进程级不可变单例：CUDA 对应 `CudaVMM`，MetaX 对应 `MetaXVMM`；Ascend/XPU 保留明确不可用错误。继续扩展已有抽象，而非重建第二套 GMS VMM 接口。

`v1/device.py` 已按平台获取 UUID 并延迟加载 CUDA；框架 allocator、checkpoint 和 integration 仍要逐条验证。存在 VMM ABC 不代表整个 GMS 已厂商无关。

## GMS 目标设计

通过 M01 的平台选择初始化 VMM provider，保持进程运行期间不可切换。先验证厂商能否满足现有 FD/SCM_RIGHTS 共享句柄协议及虚拟映射语义；不能用一个整数 token 冒充可传递 FD。

若厂商仅提供其他共享机制，必须独立设计版本化句柄 envelope 与两端协议迁移，并评估是否能维持既有访问权限、锁和恢复语义；在此之前 `gms.vmm` 不可用。普通模型重新加载不属于 GMS 等价恢复。

服务端持有物理 allocation，客户端持有自己的映射和导入句柄；客户端失败不能错误释放服务端仍供其他客户端使用的 allocation。恢复须校验设备稳定身份、模型/权重版本、映射大小及权限。释放顺序和故障路径与现有锁状态机一起验证。

## 多模态的当前功能与设计

公共媒体层负责 embedding/媒体描述、传输和视频解码；vLLM Omni 还涉及 stage 间数据交接。本轮 `media_nixl.py` 拒绝未知空间/平台并验证字节数，`nvdec_decoder.py` 限制 CUDA，embedding 对厂商设备使用 CPU staging。独立编码器路径不代表 Omni stage 生命周期已适配。

扩展媒体描述的版本、空间类型、平台/设备、shape/stride/dtype 与 provider 元数据；M05 负责传输，M07 负责输入/输出契约及媒体缓存生命周期。数据描述中的设备 ordinal 只能在对应进程上下文解释。

解码器使用可选 provider：CPU 实现可提供功能路径，NVDEC 保留 CUDA 快路径，其他厂商加速需单独实现和认证。同厂商部署的编码器/LLM/Omni stage 也要核对 embedding dtype、布局、模型版本、缓存键和完成条件。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [VMM 工厂](../../../lib/gpu_memory_service/common/vmm/__init__.py) | 与 M01 平台选择对齐，注册 provider，保留单例及初始化冲突保护 |
| [VMMDevice](../../../lib/gpu_memory_service/common/vmm/device.py) | 复用当前方法；补能力和错误契约，仅在必要语义差异出现时版本化扩展 |
| [cuda_utils.py](../../../lib/gpu_memory_service/common/vmm/cuda_utils.py) | 保留 CudaVMM 实现并约束 CUDA import 范围 |
| [v1/device.py](../../../lib/gpu_memory_service/v1/device.py) | 用稳定平台设备身份替换 CUDA 专用 UUID 查询；保持 socket 身份一致 |
| [server/allocations.py](../../../lib/gpu_memory_service/server/allocations.py)、[v1/client/mapping.py](../../../lib/gpu_memory_service/v1/client/mapping.py) | 验证平台 allocation、映射粒度、权限及销毁顺序 |
| [v1/checkpoint.py](../../../lib/gpu_memory_service/v1/checkpoint.py)、[v1/protocol.py](../../../lib/gpu_memory_service/v1/protocol.py) | 在需要时增加平台/句柄版本检查，拒绝不兼容恢复；不能直接丢弃未知字段 |
| [v1/client/mempool.py](../../../lib/gpu_memory_service/v1/client/mempool.py)、[vLLM backend](../../../lib/gpu_memory_service/v1/integrations/vllm/backend.py) | 对应厂商框架 allocator 和内存池接口适配，验证实际分配归属 |
| [media_descriptor.py](../../../components/src/dynamo/common/multimodal/media_descriptor.py)、[media_nixl.py](../../../components/src/dynamo/common/utils/media_nixl.py) | 规范化平台/空间/张量元数据与接收端校验 |
| [embedding_transfer.py](../../../components/src/dynamo/common/multimodal/embedding_transfer.py) | provider 分派、真实完成通知和缓存引用释放 |
| [nvdec_decoder.py](../../../components/src/dynamo/common/multimodal/nvdec_decoder.py)、[video_loader.py](../../../components/src/dynamo/common/multimodal/video_loader.py) | 注册解码能力和显式 fallback，不让非 CUDA 路径加载 NVDEC |
| [stage_worker.py](../../../components/src/dynamo/vllm/omni/stage_worker.py)、[Omni NIXL connector](../../../components/src/dynamo/vllm/omni/connectors/nixl_connector.py) | 平台化 stage 设备可见性与 connector 选择，核对异步清理 |

GMS v1 与旧 integration 入口需要分别建立测试，不以完成 v1 代替所有入口兼容。CUDA IPC 专用后端可继续限制为 CUDA，不要求为其他平台保留相同 API 名称。

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `lib/gpu_memory_service/common/vmm/registry.py` | 扩展已有工厂的 provider 注册和能力校验 |
| `lib/gpu_memory_service/common/vmm/{ascend,metax}.py` | 只有原语验证通过后实现 VMMDevice；可按包边界委托厂商 plugin |
| `lib/gpu_memory_service/common/device_identity.py` | GMS 设备稳定身份与命名，对接 M01 |
| `components/src/dynamo/common/multimodal/decoder_registry.py` | 解码器选择与能力/降级条件 |
| `components/src/dynamo/common/multimodal/device_tensor.py` | 媒体张量描述和 owner 校验，调用 M04/M05，不重复内存管理 |
| `lib/gpu_memory_service/tests/test_vmm_provider_contract.py` | 初始化、FD 成功/失败关闭、映射/权限/部分失败验证 |
| `tests/platforms/test_gms_recovery.py` | 真机共享、客户端退出、映射恢复和错误设备拒绝 |
| `tests/platforms/test_multimodal_contract.py` | 编解码、embedding、缓存、stage 取消和同平台分离式推理 |

## 验收与支持边界

GMS 先完成原语 PoC，再验证共享映射，最后验证引擎 allocator 与恢复；缺少 VMM/共享句柄的厂商保持待调查或有依据的不支持。fake VMM 测试只能证明状态机。

多模态先验证 CPU 媒体预处理与平台 tensor，再加入设备传输和加速解码。验收错误 shape/dtype、输入大小、长视频资源回收、编码器失败、stage 取消、缓存淘汰及并发消费。CPU 解码的功能通过不代表加速解码已支持。
