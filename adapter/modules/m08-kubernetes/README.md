<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M08 Kubernetes 与平台部署

MetaX 2026-10-30 补充：跨节点 PD/Grove/DGDR 参数化入口与 C500 CRD 已补齐，本地检查不等于集群认证；见 [交付入口](../../platforms/metax/cluster/README.md)。

状态：2026-10-29 阶段性收尾。基础 Device Plugin 路径已完成 CUDA / Ascend 实际集群回归；MetaX 代码及本地测试完成，硬件集群待验证。依赖 M01 平台契约和 M02 runtime 镜像；基础部署不依赖 GMS。

补充：[Helm/Profiler/DRA/SLA 使用说明](../../m08/helm-profiler-dra.md)；[跨节点 RDMA 实测](../../m08/rdma/README.md)。两平台持久设备缓冲区与 2P3D 通过，Ascend 动态注册压力失败单独保留。

阶段结论、遗留项和 M09 边界见 [收尾记录](../../m08/README.md#阶段性收尾与-m09-交接)。Profiler 主体属于 M09，本轮仅提前完成 DGDR 所需接入与功能验证。

## 实施进度（2026-10-29）

当前交付复用既有 v1beta1 PodTemplate、component 工作负载路径和 Helm 镜像配置，增加 CUDA/MetaX/Ascend 整卡资源识别、华为计数、平台一致性与不支持能力拒绝；提供聚合/KV 路由和同节点 2P3D 清单生成及只读集群预检查。部署与官方来源见 [M08 交付记录](../../m08/README.md)。Operator 的双架构镜像构建、离线包及导入安装步骤见 [镜像交付说明](../../m08/operator-image.md)。

未新增 `spec.platform`；扩展轮为 H800/Ascend 910B3 增加 DGDR SKU 枚举并再生成 CRD/Pydantic。原设计中的 PlatformSpec 仍是后续候选方案，当前使用现有资源声明与 `DYN_PLATFORM`，避免两处配置冲突；已有 alpha/beta 转换通过回归。DGDR 厂商硬件自动发现、拓扑、共享设备/DRA、GMS/failover 和自动规划不包含在本轮基础路径中。

CUDA H800 / Ascend 910B3 的 Operator 安装、整卡调度及计数、Kubernetes 服务发现、KV 路由、手动扩缩容、Worker 重建、同节点跨 Pod 2P3D 与两节点聚合推理已形成现场证据，见 [集群验收](../../m08/cluster/README.md)。固定 vLLM 0.28.0 / 0.23.0，沿用 M02 runtime 镜像；基础验收轮修复清单和回归工具；扩展轮另外更新 SKU 源类型、硬件识别及 Profiler 平台配置生成。

下方为完整目标设计，未在实施进度中列为已完成的内容仍是后续工作。扩展轮已通过 CUDA/Ascend 跨节点 Operator 2P3D、Grove/KAI 实际 gang 等待及生命周期回归、CUDA NVIDIA DRA 整卡分配与回收，见 [扩展验收](../../m08/advanced/README.md)。MetaX 无集群，Ascend HAMi 实验 DRA 等待用户另行提供 ≥1.34 集群。DGDR 另已通过明确硬件配置下的真实画像→自动部署→API/SSE/取消恢复；不代表厂商自动发现、预测精度或共享设备获得认证。

## 当前功能与调用关系

Operator 从 DGD/DGDR 配置生成组件资源，执行准入校验、调度配置、服务就绪与伸缩。`graph.go::applyDynDeploymentResources()` 的 GPU 简写映射 NVIDIA 资源，同时已有 Custom resource 支持。v1beta1 通过 PodTemplate 表达容器资源。

`internal/gpu/discovery.go` 使用 NVIDIA 标签和 DCGM；`internal/dra/dra.go` 有资源名识别和 DRA 处理。它并非只接受 `nvidia.com/gpu`，还采用 `/gpu`、MIG 等启发式规则，但这些规则不足以覆盖所有厂商资源。

GMS/failover 校验及部署有额外 GPU/DRA 假设。Planner、Profiler 在 Operator 之外也读写资源名，因此“Pod 能运行”不等于资源计数和扩缩容正确。

## 目标配置与职责

建议在 DGD/DGDR 的 Go 源类型定义可选 `PlatformSpec`，表达 `id`、设备资源映射/请求模式和认证 profile 引用。具体 JSON 字段建议为 `spec.platform`，需 API 兼容测试后才能成为正式字段；字段语义归本模块，M01 管平台 ID，M02 管镜像目录。

一个 DGD 的所有加速 Worker 使用一个平台；CPU Frontend/Planner 不要求请求设备资源。旧 CUDA DGD 可以保持兼容默认，新增非 CUDA 配置必须显式指定平台。平台配置不覆盖用户显式 PodTemplate 资源：冲突应被校验并报错，不能同时追加两种设备请求。

| 拟议 provider 职责 | 输出 |
| --- | --- |
| 资源解析 | 标量设备/MIG 等切分资源/DRA claim 的设备单位、数量与已知/未知状态 |
| Pod 配置 | 对应镜像、必要环境变量、runtimeClass、挂载、节点约束与容忍项 |
| 硬件发现 | 型号、内存、拓扑及可用性；通过节点标签/厂商 exporter/设备分配信息获取 |
| 功能校验 | GMS、功耗、拓扑、RDMA 是否有对应平台能力；实际运行能力仍由 Worker preflight 确认 |

基础 Device Plugin 与 DRA 分开支持，不强制新平台首先实现 DRA。DRA 设备数不能直接取 ResourceClaim 对象数量；无法解析应保留未知并停止依赖此数值的自动规划。

不修改 `nvidia.com` CRD API group，也不通过全局字符串替换改变注解和兼容接口。设备资源标识由实际平台插件提供，设计中不猜测厂商资源名。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [v1beta1/common.go](../../../deploy/operator/api/v1beta1/common.go) | 定义 PlatformSpec 及复用资源/状态类型，明确缺失值和默认行为 |
| [DGD types](../../../deploy/operator/api/v1beta1/dynamographdeployment_types.go)、[DGDR types](../../../deploy/operator/api/v1beta1/dynamographdeploymentrequest_types.go) | 增加可选平台配置并贯通 profiler 请求 |
| [v1alpha1/shared_spec_conversion.go](../../../deploy/operator/api/v1alpha1/shared_spec_conversion.go) | 保证转换不丢失非 CUDA 语义；无法表达的情况明确拒绝或使用经验证的保留机制 |
| [internal/dynamo/graph.go](../../../deploy/operator/internal/dynamo/graph.go) | 通过平台 provider 生成资源、环境与镜像，保留 PodTemplate 合并优先级 |
| [gpu/discovery.go](../../../deploy/operator/internal/gpu/discovery.go) | 将 NVIDIA/DCGM 查询封装为 CUDA discovery provider |
| [dra/dra.go](../../../deploy/operator/internal/dra/dra.go) | 使用显式资源描述识别设备，保留旧启发式的限定兼容范围 |
| [dynamo/failover.go](../../../deploy/operator/internal/dynamo/failover.go) | 根据平台能力创建共享设备和备用实例，不固定 NVIDIA 资源路径 |
| [shared_v1beta1.go](../../../deploy/operator/internal/webhook/validation/shared_v1beta1.go) | 校验平台一致、资源冲突、GMS/DRA 能力，不只更改错误消息文本 |
| [Makefile](../../../deploy/operator/Makefile) | 若新增 schema/生成输入，接入已有 generate/manifests 流程 |

Helm 的资源、镜像和厂商安装示例在 provider 字段确定后修改对应 chart 源模板；DRA、Grove 和网络拓扑仅对经验证的组合启用。

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `deploy/operator/internal/platform/types.go` | 平台配置、资源解析和 discovery 契约 |
| `deploy/operator/internal/platform/registry.go` | provider 注册、平台唯一选择与错误 |
| `deploy/operator/internal/platform/{cuda,ascend,metax}.go` | 厂商资源/环境配置，按证据填写；共同逻辑留在公共层 |
| `deploy/operator/internal/platform/resources.go` | 规范化设备数量与单位，供控制器/状态统一使用 |
| `deploy/operator/internal/platform/resources_test.go` | Pod 资源、标量/切分/DRA、缺失/冲突解析用例 |
| `deploy/operator/internal/webhook/validation/platform_test.go` | 同平台约束、冲突、不可用 GMS 与旧 CUDA 兼容 |
| `deploy/operator/test/e2e/platform_test.go` | 同厂商部署、扩缩容与重建；按平台 runner 选择 |

## 生成产物与迁移

Go API 是 schema 源；修改后运行 Operator 现有 `generate`、`manifests`，并检查 `generate-pydantic` 结果。`zz_generated.deepcopy.go`、CRD YAML、Profiler Pydantic 类型等属于派生产物，不手工维护一套新字段。

先加入可选字段和 CUDA provider，再迁移所有资源计数消费者，最后增加厂商 provider。升级测试覆盖旧配置读写、alpha/beta 往返、已有 PodTemplate、自定义资源、回滚时不丢失平台配置。

## 验收与待验证事项

最低验收：生成资源正确、准入错误清晰、平台与镜像匹配、设备可见性正确、readiness 与故障一致、扩缩容前后计数一致。CPU 服务不消耗设备配额，未知数量不能被当成零。

Ascend/MetaX 的 Device Plugin 资源名、分配粒度、runtimeClass、DRA 及拓扑能力由目标环境确认。设计不要求跨厂商调度，也不因某平台缺少 DRA 而否定其基础推理部署。
