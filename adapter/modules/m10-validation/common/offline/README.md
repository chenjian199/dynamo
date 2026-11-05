<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 离线镜像与交付包

| 文件 | 作用 |
| --- | --- |
| [offline-usage.md](offline-usage.md) | 离线包组成、获取、导入与使用步骤 |
| [export-images.py](export-images.py) | 导出不可变候选镜像；此时尚未封存发布 |
| [verify-images.py](verify-images.py) | 检查 OCI / Docker 导出的完整性，不依赖目标机器已有缓存层 |
| [image_helpers.py](image_helpers.py) | 选择单个平台及实际 image ID，避免导出本机没有的多架构描述符 |
| [dependency-inventory.py](dependency-inventory.py) | 检查可选依赖镜像归档的完整性和架构 |
| [import.sh](import.sh) | 在完整离线包目录中校验 SHA256SUMS，并导入 Docker、containerd 或 K3s；不自动安装控制器 |
| [bundle.py](bundle.py) | 校验交付目录并封存归档，支持只验证模式 |
| [thin-oci.py](thin-oci.py) | 向已知测试节点传输缺少的 OCI 层，属于临时传输优化，不是完整交付包 |
| [test_delivery.py](test_delivery.py) | 无 Docker daemon 的归档完整性测试 |

先验证镜像导出完整，再进行导入和真实 smoke，最后封存。`import.sh` 依赖包内的镜像归档和 SHA256SUMS，不能在此源码目录直接当成完整安装器使用。完整交付不能依赖接收机器已有镜像层。

新的构建、验证和归档记录保存在 [results](../../../../results/README.md) 的对应 campaign；已有外部封存包见 [releases](../../../../releases/README.md)。统一编排方式见 [交付配方](../../../../workflow/delivery-plan.md)，产物封存与功能认证分别报告。
