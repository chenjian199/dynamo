<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 华为下线：备份与清理完成记录

用户要求仅华为机器下线。2026-11-12 已暂停适配，不合并、不删分支；沐曦与 CUDA 运行环境未清理。

## 下载这四组文件

| 位置 | 文件 | 大小与内容 |
| --- | --- | --- |
| 华为 52 `/root/CJ_workspace/dynamo-huawei-backup-20261112/` | `dynamo-huawei-offline-20261112.tar` 和 `.tar.sha256` | 23,773,532,160 字节；1.5.1 r3 交付包、1.6.2 候选镜像/源码/wheel、Grove 0.1.0-alpha.14 镜像、Helm charts、两节点证据、清理前配置 |
| 沐曦 `/home/metax/CJ_workspace/` | `dynamo-huawei-source-20261112.tar` 和 `.tar.sha256` | 2,232,801,280 字节；所有 Git 分支、三个工作区、未提交修改、现场证据，包含恢复说明 |
| 华为 52 同一备份目录 | `cleanup-records.tar.gz` 和 `.tar.gz.sha256` | 约 739 KiB；52 两次清理记录、7.5 清理记录、最终状态 |
| 华为 52 同一备份目录 | `extended-cleanup-20261112.tar` 和 `.tar.sha256` | 7,979,622,400 字节；进一步清理镜像、源码及 `/data` 前保存的两节点剩余材料、文件清单和操作记录 |

华为备份目录是到 `/data/dynamo-huawei-backup-20261112/` 的链接。下载表中的文件即可；`payload/` 打包暂存目录已在重新校验独立归档后删除，不影响下载和恢复。

如果电脑上仍使用原 SSH 别名：

```bash
scp 'bedi-huawei52:/root/CJ_workspace/dynamo-huawei-backup-20261112/dynamo-huawei-offline-20261112.tar*' .
scp 'bedi-huawei52:/root/CJ_workspace/dynamo-huawei-backup-20261112/cleanup-records.tar.gz*' .
scp 'bedi-huawei52:/root/CJ_workspace/dynamo-huawei-backup-20261112/extended-cleanup-20261112.tar*' .
scp 'metax:/home/metax/CJ_workspace/dynamo-huawei-source-20261112.tar*' .
```

下载后在 Linux/WSL 中校验：

```bash
sha256sum -c dynamo-huawei-offline-20261112.tar.sha256
sha256sum -c dynamo-huawei-source-20261112.tar.sha256
sha256sum -c cleanup-records.tar.gz.sha256
sha256sum -c extended-cleanup-20261112.tar.sha256
```

Windows PowerShell 可用 `Get-FileHash -Algorithm SHA256 <文件>` 与对应 `.sha256` 内容比较。

华为大包 SHA256：`40697d9cfb95ba5896b5c7a139ba6c11f6efa222f85abd880c26894513390186`。封包前校验两份原始镜像包；封包后重新读取 138 个内部文件并核对 SHA256。源码快照核对 65,866 个归档文件；Git bundle 通过 `git bundle verify`；外层源码包核对 14 个内部文件。

## 版本边界

- 阶段交付版本：Dynamo 1.5.1 / vLLM-Ascend 0.23.0，`1.5.1-heyi-m10-4b1efe773c-r3`，状态为 `qualified_with_explicit_limitations`，限制随包保留。
- 升级候选：Dynamo 1.6.2 / vLLM-Ascend 0.30.0，源码 `2a239c2b500ea7e48c86b7216b9878158db4802a`，尚未通过全部 38 项要求及最终发布门禁。不能把备份成功当成正式验收通过。
- `heyi` 主分支和升级临时分支均保留。恢复升级需先核查环境、映射工作区路径和冻结身份，并对回传超时的旧尝试 reconcile 后重试。

## 首次清理结果

- 卸载本轮 Helm releases：`heyi-upgrade162`、`heyi-grove`、`heyi-kai`；卸载前确认没有 DGD、DGDR、PodCliqueSet、PodGroup 或使用 KAI 的业务 Pod。
- 删除 52 两套 Docker 中的 27 个明确归属适配的容器；没有按匿名容器或全局 prune 规则清理。
- 停止两个镜像分发 HTTP 服务及独立构建 Docker daemon；原系统 Docker 保持运行。
- 删除 52 的编译/测试缓存、历史镜像传输副本约 222.49 GB；7.5 删除历史镜像传输副本约 139.32 GB，总计 361.82 GB。这是删除文件的逻辑大小，不等于扣除新备份后的净磁盘变化。
- 最终适配 Pod 数为 0。模型、运行时镜像、源代码和证据、驱动、Kubernetes、共享监控/存储、其他业务及未知归属容器保留；未删除共享 CRD。
- 首次清理访问系统旧 Docker 时遇到 API 版本不兼容，失败原记录保留；第二次改用系统 Docker CLI 后完成。见 `cleanup-records.tar.gz` 中 `cleanup/`、`cleanup-r2/` 和 `FINAL-STATUS.json`。

华为机器下线前，请先将上述备份下载并校验；备份本身仍保存在华为机器上。

## 补充清理：适配镜像和数据

用户随后明确要求清理遗留镜像和 `/data` 中的适配数据。以下为最终状态，取代首次清理中“镜像和现场源码保留”的说明：

- 52 和 7.5 均已完成；分别处理 19、10 个明确归属适配的顶层路径，删除历史构建、源码副本、重复镜像归档和测试数据。
- 两节点 `/data/dynamo-m10` 均已删除，包含 52 已停用的独立 Docker 构建存储。52 的 `/data/dynamo-upgrade-1.6.2-20261111` 仅保留 `models/`；7.5 对应目录已删除。
- 清理未被其他业务使用的 HEYI Dynamo、Grove、KAI 镜像。最终复查两节点 containerd 和系统 Docker 中这些适配镜像引用均为零，没有因引用冲突而跳过的目标镜像。未执行全局 prune，也未直接删除共享 containerd 存储。
- 保留模型权重、共享厂商基础镜像、其他业务、驱动、Kubernetes 和备份。未清理沐曦或 CUDA 运行环境。
- 删除前补充保存 52 的 279,990 个文件和 7.5 的 23,125 个文件并验证内容。包含/排除项及哈希位于补充包的 manifest；大型镜像由原有离线包保存，模型不在此次备份范围内。
- 原始 23,773,532,160 字节离线包重新校验后 SHA256 不变。补充包 SHA256：`dc24b009c6685205c2725e721a5e2905f5dd3ec4de085996e9b1a88f93394f07`。
- 52 首次补充校验因 gzip 硬链接随机读取效率问题中断，当时尚未删除数据；第二次以流式校验完成，原中断记录保留。临时传输服务已退出。

最终独立检查见 [52 状态](extended-cleanup/huawei-52-post-cleanup-verification.json)、[7.5 状态](extended-cleanup/huawei-55-post-cleanup-verification.json)；具体删除命令和返回值见 `extended-cleanup/huawei52-final-status.json` 与 `extended-cleanup/huawei55-final-status.json`。备份和清理完成不改变升级候选的未验收状态。
