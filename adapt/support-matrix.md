<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# 平台支持情况与验收矩阵

本矩阵基于 Dynamo v1.5.1 源码，记录的是功能适配的证据层次。当前已完成总体设计和 [十个模块的详细设计草案](modules/README.md)，并完成 [MetaX C500 单卡文本推理首轮适配与验收](metax/metax-bringup.md)。通用平台运行时框架尚未实施。设计文档完成不会将能力状态自动提升为已支持。每个平台分别部署和测试，不记录跨厂商组合。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| 源码已有 | 当前 Dynamo 中存在实现或集成路径；不是本次硬件测试通过 |
| 生态入口已找到 | 存在可调查的厂商引擎/插件；尚未证明与当前 Dynamo 组合可用 |
| 待调查 | 必须确认 SDK、引擎或硬件是否提供必要能力 |
| 待实现 | 已有足够能力证据和细化设计，尚未完成 Dynamo 适配 |
| 待验证 | 适配代码已具备，尚未通过指定版本与硬件验收 |
| 已验证 | 对明确型号、版本、配置和功能组合有可复现通过证据 |
| 有限支持 | 仅部分语义或配置通过，必须列明边界与降级 |
| 不支持 | 已确认当前认证组合缺少必要能力，并附依据 |

“待调查”不能根据没有测试结果直接改成“不支持”。功能可配置关闭、编译 stub 成功、单次 API 返回或厂商宣称 CUDA 兼容均不足以标记“已验证”。

## 初始功能矩阵

| 功能 | CUDA 基线 | Ascend | MetaX | 核查重点 | 模块 |
| --- | --- | --- | --- | --- | --- |
| 引擎基础推理 | 源码已有 | 生态入口已找到 | 有限支持：C500、vLLM-metax 0.26.0、Qwen3-0.6B、单卡 eager 文本推理；[验收记录](metax/metax-bringup.md) | 锁定引擎/插件组合，验证生命周期及输出 | M02/M03 |
| KV-aware 路由 | 源码已有 | 待调查 | 待调查 | 事件、哈希、block size、容量、DP rank | M03 |
| P/D 分离 | 源码已有 | 生态入口已找到，协议待核查 | 待调查 | connector 协议、KV 布局、完成与取消 | M05 |
| Dynamo KVBM 主机缓存 | 源码已有 | 待调查 | 待调查 | 注册内存、异步复制、引擎布局与事件桥接 | M04/M06 |
| Dynamo KVBM SSD/远端缓存 | 源码已有 | 待调查 | 待调查 | 回读、一致性、中转与直接路径 | M05/M06 |
| 设备直连 RDMA | 源码已有可选路径 | 待调查 | 待调查 | 设备内存注册、NIC/驱动、跨节点验证 | M04/M05 |
| KVBM 集合通信 | 源码已有 NCCL 路径 | 待调查 | 待调查 | 对应 collective 的语义与完成通知 | M06 |
| Kubernetes 基础部署 | 源码已有 | 待调查 | 待调查 | 资源名、镜像、设备插件、调度与计数 | M08 |
| SLO Planner/Profiler | 源码已有 | 待调查 | 待调查 | 指标、资源计数、平台实测画像 | M09 |
| 设备健康与功耗控制 | 源码已有 NVIDIA 路径 | 待调查 | 待调查 | 指标语义、错误码、功耗 API 与权限 | M08/M09 |
| GMS/共享内存恢复 | 已有 VMM 抽象和 CUDA 实现 | 待调查 | 待调查 | 复用 VMMDevice；共享句柄、内存存活与恢复语义仍需实测 | M07 |
| 多模态/编码器分离 | 源码已有集成路径 | 生态入口已找到，组合待核查 | 待调查 | 编解码、embedding 布局、传输与缓存 | M03/M07 |
| LoRA/量化/推测解码/MoE | 依引擎与配置，待建立基线 | 待调查 | 待调查 | 按模型、精度、引擎能力与组合分别记录 | M03/M10 |

“源码已有”不表示所有引擎、型号、模型和功能组合均支持；CUDA 也需要建立实际基线。高级功能应在模块细化时拆为独立行，不能一次测试后整体标绿。

生态调查入口：[vLLM Ascend 版本矩阵](https://docs.vllm.ai/projects/ascend/en/main/community/versioning_policy.html)、[Ascend PD 设计](https://docs.vllm.ai/projects/ascend/en/v0.20.2rc/developer_guide/Design_Documents/disaggregated_prefill.html)、[Ascend EPD 示例](https://docs.vllm.ai/projects/ascend/en/v0.18.0/user_guide/feature_guide/epd_disaggregation.html)、[vLLM-metax](https://github.com/MetaX-MACA/vLLM-metax)。这些入口用于后续选择并锁定版本，不代表直接采用文档对应版本。

## 认证环境记录

每个认证组合建立独立记录，至少包含：

| 项目 | 必填内容 |
| --- | --- |
| 身份 | 平台、型号/架构、设备数量、主机 CPU 架构、节点数量 |
| 系统 | OS、内核、驱动、固件、SDK |
| 引擎 | Dynamo commit、引擎及插件版本、PyTorch/设备扩展版本 |
| 传输 | 通信库、KV connector、传输库、NIC、驱动和网络配置 |
| 产物 | 镜像 digest、依赖锁定文件、构建参数 |
| 负载 | 模型 revision、tokenizer、dtype/量化、并行配置、KV 布局 |
| 功能 | 启用能力及组合、明确关闭项、显式降级路径 |
| 验收 | 测试命令/配置、结果位置、日期、性能口径、已知限制 |

首个实测组合为 MetaX C500 + 官方 vLLM-metax 0.26.0 Ubuntu 22.04 / Python 3.12 镜像，详细版本、镜像 digest、模型 revision、源码改动及限制见 [适配记录](metax/metax-bringup.md)。其他型号与平台仍需单独确定并验收。

## 支持结论的更新规则

1. 从源码或 SDK 确认必要原语，将证据关联至模块细化文件。
2. 细化接口和行为后，将已确认可实现的能力转为“待实现”。
3. 代码完成并通过公共契约测试后，转为“待验证”。
4. 在记录的软件与硬件组合上通过测试后，转为“已验证”或“有限支持”。
5. 新型号、驱动、SDK、引擎、connector 或关键配置变化时重新判定验证范围，不自动继承全部支持状态。

## 每项能力的验收记录模板

```text
能力名称：
关联模块与细化设计：
平台认证组合：
状态：
用户可见语义与适用组合：
实现提供者与源码提交：
功能/故障/性能测试：
执行命令与结果位置：
限制和降级条件：
外部问题或依赖：
最近验证日期：
```
