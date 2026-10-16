<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M01 平台契约与能力注册

状态：第一版已实现，MetaX C500 / vLLM-metax 0.26.0 与 Ascend 910B3 / vLLM Ascend 0.23.0 的聚合文本及 KV 路由路径已验收。依赖：无；向 M02–M10 提供平台身份、设备描述和能力语义。CUDA 旧协议兼容已做软件测试，CUDA H800 / vLLM 0.24.0 的显式身份与 KV 路由已在 M03 回归；Ascend provider 的验收进度见 [华为记录](../../ascend/ascend-bringup.md)，不能据此宣称全平台硬件认证。

## 已交付的调用链

```text
DYN_PLATFORM + 引擎配置
  → Python PlatformRegistry（只加载所选 provider）
  → SDK 设备/软件栈探测 + 必需能力校验
  → DistributedRuntime.configure_platform(descriptor)
  → 原有引擎初始化和 readiness 流程
  → Endpoint → DiscoverySpec → Instance.platform
  → Python Client.instances() / Rust 设备分类
```

通用 Backend SDK 也可通过 Python/Rust `WorkerConfig.platform` 传入探测结果，由 Rust Worker 在启动引擎前配置 DistributedRuntime。当前 vLLM 启动入口直接调用预检查及 `configure_platform`，不迁移其已有生命周期。平台描述只保留一份，不在 `EngineConfig` 再复制配置。

## 契约与实现位置

| 实现 | 内容 |
| --- | --- |
| [lib/platform](../../../lib/platform/src/lib.rs) | 无厂商 SDK 依赖的 `dynamo-platform` crate；serde/serde_json/thiserror 公共依赖 |
| `PlatformDescriptor` | `schema_version=1`、字符串 `platform_id`、CPU/Accelerator 分类、`provider_version`、软件版本、设备列表、能力报告、可选认证引用 |
| `DeviceDescriptor` | 可见 ordinal、稳定设备身份（UUID 或厂商芯片标识）、架构/型号、显存字节数、可选 PCI/NUMA；ordinal 不作为跨进程身份 |
| `CapabilityReport` | `available/unavailable/unknown`、provider、原因、必须匹配的配置条件 |
| `PlatformError` | `code/provider/message/retryable`；Python 保留底层异常链，Rust 在反序列化和配置时验证契约 |
| [Python 平台包](../../../components/src/dynamo/common/platform/) | registry、CPU/CUDA/MetaX/Ascend provider、vLLM preflight；公共 import 不加载 torch/厂商 SDK |
| [runtime](../../../lib/runtime/src/distributed.rs) | 平台描述首次配置或首次端点注册后冻结；相同配置幂等，不允许切换平台或补写旧注册的身份 |
| [endpoint](../../../lib/runtime/src/component/endpoint.rs)、[discovery](../../../lib/runtime/src/discovery/mod.rs) | 注册可选平台描述、转换及兼容读取 |
| [push_router](../../../lib/runtime/src/pipeline/network/egress/push_router.rs) | 使用新描述的 CPU/Accelerator 分类；旧 CPU/CUDA 仍可识别；未知设备不作为设备感知路由候选 |
| [Python 绑定](../../../lib/bindings/python/rust/lib.rs) | `DistributedRuntime.configure_platform()`、`runtime.platform`、`Instance.platform` |

第一版把 Rust 类型集中在 `lib.rs`，未为每个结构拆空文件。后续 M04 扩展设备/内存接口时再按规模拆分。

## 平台选择与探测

- `DYN_PLATFORM=cpu`：Rust 直接配置 CPU 控制面描述；不依赖加速器 SDK。
- `DYN_PLATFORM=metax`：vLLM 入口选 MetaX provider。使用沐曦 torch 的 `torch.cuda` 兼容接口读取设备 UUID/型号/显存/PCI；使用 `pymxsml.mxSmlInit`、`mxSmlGetMacaVersion`、`mxSmlGetDeviceVersion(..., MXSML_VERSION_DRIVER)` 读取 MACA/驱动版本。这里使用 torch 的接口名称不再决定发现协议的平台身份。
- `DYN_PLATFORM=ascend`：vLLM Ascend 0.23.0 provider，延迟加载 torch-npu、ACL 与 DCMI。ACL 把可见逻辑编号映射为物理编号，DCMI 读取芯片 die ID 与 PCI；torch-npu 查询型号/显存。当前 910B3 SDK UUID 全零，使用 `ascend-die:<芯片标识>` 作为稳定身份。实现见 [ascend.py](../../../components/src/dynamo/common/platform/ascend.py)。
- `DYN_PLATFORM=cuda`：显式 NVIDIA provider，拒绝 MetaX torch 或 ROCm 栈；H800 / vLLM 0.24.0 硬件回归见 [M03 验收](../../m03/README.md)。
- 未设置 `DYN_PLATFORM`：保留旧启动行为，端点注册时给出 legacy CUDA/CPU 推断警告。MetaX 启动脚本已经显式设置平台。
- 未注册平台、重复 provider、配置冲突、缺失设备身份或不支持的软件栈均报错。外部 provider 可通过 `dynamo.platforms` Python entry point 注册，入口加载发生在选择该平台时。

当前 MetaX 软件组合锁定 vLLM 与 vLLM-metax 0.26.0，Ascend 锁定 vLLM 与 vllm-ascend 0.23.0。显式 vLLM 适配已扩展到聚合 eager、本地 multiprocessing TP/DP 及独立高级能力条件；实测模型和配置以 [M03 验收](../../m03/README.md) 为准。PP、跨节点、Ray/external/hybrid DP、PD 仍在预检查拒绝。设备描述的 NUMA 尚未探测，保持缺省，不使用虚构默认值。

## 能力与 readiness

`device.compute` 表示选定设备可通过后端查询；`engine.text` 与 `kv.events` 是软件栈和配置预检查结果，仍须原有引擎初始化成功后才能发布可服务端点，不代表所有模型通过测试。

能力条件包含 engine、task、TP/DP、eager、model 和 KV events 配置。必需能力必须为 `available` 且条件匹配。可用 `DYN_REQUIRED_CAPABILITIES=pd.transfer` 增加要求；当前会在引擎初始化和发现注册之前明确失败。

`pd.transfer`、`device.rdma`、`gms.vmm`、`power.control`、`kvbm.offload` 均保持 `unknown`。安装了 NIXL/Mooncake、torch 暴露 CUDA 名称、SDK 能查询设备，都不会自动提高这些能力状态。认证引用缺省，不由一次基础推理自动填入。

## 协议兼容边界

1. `Instance.platform` 是带 schema 的可选字段。旧记录没有该字段仍可读取；新描述允许未知可选字段，但未知 schema、无设备身份、重复 UUID/ordinal 等必须报错。
2. 旧 `DeviceType` 仍只有 CPU/CUDA，没有加入会破坏旧枚举解析的 `metax` 值。CPU/CUDA 新记录可以同时提供兼容字段；MetaX/Ascend 新记录省略旧 `device_type`。
3. 新消费者优先读取 `platform.device_kind`。两个字段都缺失时分类为未知；不再推断成加速器。普通 KV 路由仍以缓存事件与 Worker ID 选路。
4. MetaX/Ascend 必须同时部署本分支支持 schema 1 的 frontend/runtime。旧控制面即使忽略新字段，也不能保证正确分类，不能作为支持组合。
5. 不修改 ModelDeploymentCard 校验和或模型准入规则，不增加跨厂商调度。

## 验收与后续边界

- [Rust 契约测试](../../../lib/platform/tests/descriptor_compat.rs)：序列化、schema/身份检查、能力状态及条件匹配。
- runtime 测试：旧 CPU/CUDA/未知描述、新 MetaX 发现往返、冻结和配置冲突。
- [Python 测试](../../../components/src/dynamo/common/platform/tests/)：CPU 无 SDK import、延迟加载、重复/未知 provider、CUDA 与 MetaX 栈隔离、能力拒绝及 vLLM 入口配置。
- [Ascend 真机验收](../../ascend/ascend-bringup.md)：真实芯片身份、发现往返与启动拒绝，以及基础推理和 KV 路由。
- [MetaX 真机验收](../../metax/metax-platform.md)：两个 Worker 的实际身份、Python/Rust 双向传递、注册前能力拒绝，以及基础推理和 KV 路由回归。

M01 当前交付的是平台公共契约、MetaX 与 Ascend provider。设备内存/流/事件由 M04 实现，PD/通信由 M05/M06 验证，Kubernetes 注入由 M08 接入，动态健康/功耗由 M09 提供。更多 TP/DP、模型与硬件组合需要独立扩展与验收；Ascend 的已验收范围以其记录为准。
