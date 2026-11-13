<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 升级镜像准备检查：2026-11-10

目标配置：[Dynamo 1.6.2 升级计划](../../../../upgrades/targets/dynamo-1.6.2.yaml)。
本次仅确认现场镜像与实际安装的 Python 包版本，不进行代码合并、驱动升级、镜像下载或推理验收。

| 平台 / 机器 | 目标 | 检查结果 |
| --- | --- | --- |
| MetaX / 本机 63metax | vLLM-MetaX 0.28.0，MACA 3.8.2.401，amd64 | Docker 中 py310、py312 均已存在；两者实际 vLLM 和插件基础版本均为 0.28.0。升级计划仍选择 py310 |
| Ascend / Huawei 52，master-02 | `quay.io/ascend/vllm-ascend:v0.30.0`，arm64 | Docker 中存在；实际 vLLM 为 `0.30.0+empty`，插件为 `0.30.0`。当前 CRI 镜像列表未找到该目标标签 |
| CUDA / H800 4.6，worker06 | `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.6.2`，amd64，计划 vLLM 0.31.0 | 检查时 Docker、CRI 和已发现的 containerd 命名空间均未找到目标镜像；常用目录有界搜索也未找到明显对应 1.6.2 的归档，等待用户提供实际镜像名或路径 |

## 检查方法与证据

- [MetaX py310 回执](images/metax.json)、[py312 备选回执](images/metax-py312-alternative.json)、[Ascend 回执](images/ascend.json)：包含实际镜像 ID、官方仓库 RepoDigest、架构、Python 和包版本。
- 镜像检查先 inspect，再按不可变镜像 ID 启动临时容器，关闭网络、无设备或宿主机目录挂载，仅使用 `importlib.metadata` 读取已安装包信息；未导入推理引擎，也未使用加速卡。
- MetaX 使用工作流 `probe-image`。华为旧 Docker CLI 不接受 `--pull=never`，初次失败保存在 `observations/ascend-probe.*`。随后通过 Docker Engine API v1.24 的 create/start/wait/logs/delete 完成同等检查；create API 不负责拉取镜像。API 调用和删除成功状态保存在 [原始记录](observations/ascend-engine-api.stdout.txt)。这属于探测工具的 CLI 兼容问题，不是镜像版本不匹配。
- 两份计划内回执均通过现有 `validate_receipt` 校验。py312 仅作备选检查，未修改计划或正式平台版本锁。
- CUDA [镜像清单](observations/cuda-inventory.stdout.txt)覆盖 Docker、CRI，以及 `k8s.io`、`moby`、`moby_history`、`sglang-repro-verify-20260804-1327` 命名空间。[归档搜索](observations/cuda-archives.stdout.txt)覆盖 `/root`、`/home`、`/data`、`/mnt`、`/opt`，限制深度 4、预算 25 秒，仅按文件名筛选，不代表遍历整个文件系统或检查全部归档内部内容。

## 结论与后续

沐曦、华为的目标基础镜像已准备好；CUDA 镜像位置尚待确认，因此不能将三平台镜像准备全部标记通过。Docker 与 Kubernetes 的镜像存储需要分别核对，华为后续集群部署前还需将目标镜像导入相应 CRI 存储。

镜像元数据通过不等于设备初始化、驱动兼容、推理、PD 或集群回归通过。后续升级应使用这些现场回执固定镜像内容，再运行相应功能验收；本次未推进任何功能认证状态。

`summary.json` 提供机器可读结论和输入哈希；`sha256sums.txt` 记录此检查目录内的文件哈希，不包含校验清单自身。
