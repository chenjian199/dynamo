<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M05 PD 协调与数据传输

状态：待实现/待验证（MetaX 当前阶段）。依赖 M01/M02/M03；Dynamo 自有数据面使用 M04，厂商引擎原生 PD 不强制经过 Dynamo KVBM。

## 实施进度（2026-10-15）

官方镜像内有厂商 NIXL/Mooncake 包，不等于 Dynamo PD 或设备 RDMA 路径已经可用。M01 中 pd.transfer/device.rdma 仍为 unknown。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

## 当前功能与边界

Prefill 与 Decode 分离后，Dynamo 要选择相应 Worker、传递协调信息，并确保 KV 对 Decode 可用。实际 KV 数据传输通常由引擎 connector 或特定数据面完成，请求控制面和 KV 数据面不是同一条链路。

vLLM 的 `KvConnectorProtocol` 已提供 `prefill_request_kv_transfer_params()` 与 `decode_request_kv_transfer_params()`。当前 registry 包含 NixlConnector、NeuronNixlConnector、MooncakeConnector，并处理 MultiConnector/PdConnector 的单一 PD 子 connector。未知名称会报错，不能将新厂商 connector 静默回退到 NIXL。

SGLang 的 `_disagg.py` 负责 bootstrap 等辅助逻辑。Dynamo 自有 NIXL 内存接口则位于 `lib/memory/src/nixl.rs`，与 `kvbm-physical` 中的传输执行器配合。两种接入路线需要分别说明，不能把它们视为同一个全局替换点。

## 目标设计

保留协议策略边界，增加可注册的厂商策略和版本约束。引擎适配报告实际 connector 标识及其协议版本，协议工厂按经验证组合解析；可扩展到分层传输，但不依赖名称相似性。

协议契约至少覆盖：两端角色、先分配方、bootstrap/transfer ID、KV 布局、提交方、完成通知、源块释放条件、超时与取消。现有两个参数转换方法不足以表达的新协议，可以补充策略 hook；不能将额外语义硬编码在公共 handler。

对 Dynamo 自有传输引入窄 `TransferProvider`：register/unregister、export/import metadata、submit、poll/wait、cancel/drain。NIXL 是其实现之一；若目标平台的 NIXL backend 已满足所有语义，优先复用，不再新写厂商网络栈。

### 状态与资源

```text
Prepared → Registered → Submitted → InFlight → Completed → Released
                            │          │
                            └── CancelRequested / Failed → Drain → Released
```

此为适配层状态模型，具体 connector 可合并状态。应用层超时只触发取消策略；只有 provider 确认操作终止或完成后才能释放注册内存。错误报告必须区分请求失败与底层传输仍在清理。

PD 两端校验模型配置指纹、dtype、block size、布局及并行分片。即使相同厂商，也不假定不同引擎/版本天然兼容。第一批认证固定一种可互通组合，再扩大范围。

重算/重试由明确策略控制：部分输出已返回时不能无条件重放请求；源块需要保持到 transfer 的释放条件满足。主机中转只能在显式允许并有结果证据时启用。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [kv_connector_protocols.py](../../../components/src/dynamo/vllm/kv_connector_protocols.py) | 扩展注册入口、协议版本和组合校验；保持未知 connector 失败语义 |
| [vllm/handlers.py](../../../components/src/dynamo/vllm/handlers.py) | 通过策略传递 PD 元数据和取消；不增加厂商专用分支 |
| [vllm/args.py](../../../components/src/dynamo/vllm/args.py) | 检查 PD connector 与 KVBM/PdConnector 组合及依赖，不默认要求 NIXL |
| [vllm/worker_factory.py](../../../components/src/dynamo/vllm/worker_factory.py) | 启动时验证协议，shutdown 时调用正确的 drain hook |
| [sglang/_disagg.py](../../../components/src/dynamo/sglang/_disagg.py)、[init_llm.py](../../../components/src/dynamo/sglang/init_llm.py) | 校验实际 bootstrap 与协议需求，区分非 PD 路径和缺少必需信息 |
| [common/backend/disagg.py](../../../components/src/dynamo/common/backend/disagg.py) | 如公共流程需要新增策略 hook，在此维护，不重复编排流程 |
| [memory/nixl.rs](../../../lib/memory/src/nixl.rs)、[nixl/agent.rs](../../../lib/memory/src/nixl/agent.rs)、[nixl/config.rs](../../../lib/memory/src/nixl/config.rs) | 让 NIXL 注册和 backend 配置适配通用内存描述及生命周期 |
| [executor/nixl.rs](../../../lib/kvbm-physical/src/transfer/executor/nixl.rs)、[notifications/nixl_status.rs](../../../lib/kvbm-physical/src/transfer/notifications/nixl_status.rs) | 将完成/失败映射到统一语义，避免调用层依赖 NIXL 状态码 |
| [transfer/capabilities.rs](../../../lib/kvbm-physical/src/transfer/capabilities.rs) | 能力来自已验证 provider；direct 与 host staging 分别声明 |

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `components/src/dynamo/common/backend/transfer_protocol.py` | PD 协议描述、布局兼容及策略 hook；只提取真正共享的语义 |
| `plugins/platforms/ascend/src/dynamo_platform_ascend/kv_protocols.py` | 指定引擎版本的 Ascend connector 协议映射 |
| `plugins/platforms/metax/src/dynamo_platform_metax/kv_protocols.py` | 在确认可用 connector 后增加协议映射 |
| `lib/platform/src/transfer.rs` | Dynamo 自有传输的注册、传输句柄与完成契约 |
| `lib/kvbm-physical/src/transfer/provider.rs` | provider 选择、通用错误与生命周期适配 |
| `lib/kvbm-physical/src/transfer/providers/nixl.rs` | 包装现有 NIXL executor；仅确有需要时再新增其他 provider |
| `tests/platforms/test_pd_contract.py` | 单机/跨节点、完整与部分失败、取消、KV 释放的集成验收 |

## 实施与验收

先为选定生态记录 connector 的实际请求/响应及释放时序，再写策略与模拟对端测试；之后运行单机 PD，最后跨节点与故障测试。PD 测试不能只看 HTTP 成功，还需确认远端 KV 被采用、源块被正确释放。

最低验收：P/D 输出与同平台聚合部署在约定容差内一致；不同并行/布局不兼容时提前失败；consumer 退出、producer 退出、网络超时与客户端取消均有确定行为；流量停止后注册与块占用回落。

CUDA 存在当前 NIXL/Mooncake 协议路径；Ascend 的生态 connector 需要逐版本核对；MetaX 的实际 connector 和注册内存能力待调查。是否采用 NIXL、厂商原生传输或中转，必须由结果决定。
