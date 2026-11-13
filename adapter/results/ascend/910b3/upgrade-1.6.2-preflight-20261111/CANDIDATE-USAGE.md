<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 华为 1.6.2 / vLLM-Ascend 0.30.0 候选包

本包对应提交 `1aabe435ad56922cf7e12f92f64df2dc39531ccb`，架构 ARM64。
这是待正式工作流门禁审核的候选交付，`release_qualified:false`；不是已晋级发布版。
部署和测试结果以随包 `validation/` 为准，镜像身份以 `image-export.json` 为准。
CUDA 与沐曦多机测试按用户要求暂缓。

## 内容和前置条件

- `images.tar.gz`：runtime、dev、tools（Profiler/Planner）、Operator 四个角色。
- `dependencies/`：Grove alpha.14 和 KAI 0.17 的 ARM64 镜像，清单含逐归档摘要。
- `charts/`：Operator 1.6.2、Grove、KAI 的 Helm 包。
- `source-1aabe435ad.tar.gz`：构建源码快照，含厂商补丁、工作流和 CRD。
- `manifests/`：本现场模型部署与控制器配置。
- `profiling/`：实际离线数据、身份、审计、DGDR 输入及在线 Planner 配置。
- `helpers/`：原工作流辅助脚本及 Helm 3.17.3 ARM64 安装包。
- `validation/`：最终场景原始归档、按功能编号的证据索引、升级 session 快照及控制脚本快照。控制脚本固定本现场路径，包含失败尝试，不能当作通用部署入口批量运行。

目标节点需已有可用的 Ascend 驱动、设备插件、ARM64 容器运行时和模型权重。
本次节点为 910B3，K3s 1.32.2，资源名 `huawei.com/Ascend910`。本包不安装或升级驱动，
不附模型、Kubernetes、设备插件、Prometheus 或宿主机 RDMA 配置。
测试使用 Kubernetes 服务发现，无需 etcd/NATS；如切换发现或事件后端，必须另行提供组件。
在线 Planner 测试复用了已有 Prometheus 及 PodMonitor CRD；离线机器需自行准备同等监控服务。

## 导入

完整复制目录到每个参与节点，保留文件结构。最终归档生成 `SHA256SUMS` 后执行：

```bash
cd /root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate
sha256sum -c SHA256SUMS
sudo bash import-candidate.sh k3s
# 标准 containerd 改为：sudo bash import-candidate.sh containerd
k3s crictl inspecti heyi/dynamo:1.6.2-ascend0.30-1aabe435ad-runtime
```

`import-52.json` 记录已在 52 现有 containerd 存储中导入并核对四个 config ID。
这不能替代空镜像存储环境的完整离线安装验收。不要直接执行 `helpers/import.sh`：
它是原标准交付布局的源码副本，本候选目录使用上面的 `import-candidate.sh`。

## Kubernetes 使用

先查看 `manifests/operator-values.json`、`grove-values.json`、`kai-values.json`。
它们是测试现场配置，含节点名、已有控制器依赖和测试 namespace 选择器。
跨集群复用前应修改节点、namespace、模型 hostPath、网络和 Prometheus 地址，检查是否已有
Operator/Webhook、Grove 或 KAI；不要安装重复的集群级控制器。

在确认目标是新的测试环境后，安装顺序为 Grove CRD/Grove、KAI、Dynamo Operator：

```bash
tar -xzf helpers/helm-v3.17.3-linux-arm64.tar.gz
kubectl apply -f manifests/grove-crds.yaml
./linux-arm64/helm upgrade --install heyi-grove charts/grove-charts-v0.1.0-alpha.14.tgz \
  -n grove-system --create-namespace -f manifests/grove-values.json --wait --timeout 10m
./linux-arm64/helm upgrade --install heyi-kai charts/kai-scheduler-v0.17.0.tgz \
  -n kai-scheduler --create-namespace -f manifests/kai-values.json --wait --timeout 10m
./linux-arm64/helm upgrade --install heyi-upgrade162 charts/dynamo-operator-1.6.2.tgz \
  -n heyi-upgrade162 --create-namespace -f manifests/operator-values.json --wait --timeout 10m
```

本现场清单使用 `master-02-45d4a763`（7.7）及 `master`（7.5），部署前核对映射。
模型从清单内的 hostPath 加载；Qwen3-0.6B、Qwen3-32B、DeepSeek V4 Flash W8A8
不可互换模型路径和量化配置。先运行小模型，再单独运行大模型清单，不要一次 `apply` 整个目录。
DeepSeek Hybrid PD 需要两节点各八张空闲 NPU，TP8 Prefill + TP8 Decode。

`profiling/dgdr-thorough.json` 展示真实测量四种阶段/TP 候选并自动部署的入口。
`online-150ms.yaml` 与 `online-75ms.yaml` 是两份独立功能场景；前者实际未触发扩容，
后者使用默认 80% 缩容阈值，达到三个副本但没有缩容，均未通过完整生命周期断言。
`online-75ms-down95.yaml` 使用 95% 缩容阈值，最终镜像已通过 `1→3→2→1`、资源回收及重启复用。
其负载阶段延迟目标未达成，不能当作生产 SLA 配置直接套用。
保存的插值数据只适用于记录中的硬件、引擎、模型与配置，换现场应重新
执行离线分析，不应沿用这组小模型数据预测大模型 SLA。

清理时只删除本次创建的 DGD/DGDR、Job、Pod 和对应测试发布，记录 UID。
模型资源退出不等于应卸载共享 Grove/KAI/Operator。最终检查 Pod、设备资源占用和残留句柄。
