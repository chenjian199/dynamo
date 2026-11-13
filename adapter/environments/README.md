<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 现场环境配置

本目录提供“在哪些机器、使用哪些资产执行哪些用例”的配置模板。它不保存驱动安装包、模型权重或登录凭据。

从 [examples](examples/README.md) 选择本地或集群模板，填写实际 SSH 别名、工作区、设备探测命令、镜像 digest、模型 manifest 和 Kubernetes context，再绑定功能 case。CUDA 示例中的资源名和设备命令需要按目标平台替换；平台标准入口见 [platforms](../platforms/README.md)。

环境清单用于 `init --environment`，范围声明用于 `init --scope`；初始化后，输入被复制并冻结到 campaign 的 `lock.json`。修改原环境文件不会更新既有 campaign。第一次填写时保持 `execution_enabled: false`，先通过 `doctor` 检查实际条件，再按确认的执行范围建立可执行 campaign。

免密 SSH、镜像、模型和集群能力是执行前置条件，缺少哪项就记录相应阻塞；模板不会自动安装它们。私钥、密码、API key、完整 kubeconfig 留在系统凭据配置中。现场日志、预检报告和任务结果存入 [results](../results/README.md)，不要写回模板目录。

克隆操作系统可能导致不同物理节点拥有相同 `/etc/machine-id`。先通过 SSH 读取每台节点的 `/sys/class/dmi/id/product_uuid`，并与 Kubernetes `status.nodeInfo.systemUUID` 核对；随后可在各节点声明 `host_identity: {kind: dmi_product_uuid, value: <实际的小写 UUID>}`。预检仍记录原 machine-id，并重新读取、匹配硬件 UUID；跨节点用例必须所有节点都绑定该身份且观测值互不相同。缺失、占位、错配或重复 UUID 均不能通过；不使用主机名或 SSH 别名代替实际身份，也不需要修改宿主机文件。

完整命令顺序见 [执行与恢复手册](../workflow/README.md)。
