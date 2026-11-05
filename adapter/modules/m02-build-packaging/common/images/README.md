<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 镜像验证与证据采集

| 文件 | 作用 |
| --- | --- |
| [run.sh](run.sh) | 在目标平台已创建的容器上执行镜像检查，接收平台、容器、设备、构建记录和输出目录 |
| [dev-validate.py](dev-validate.py) | 检查 dev 对 runtime 的继承、开发工具及 editable 导入 |
| [collect.py](collect.py) | 汇集已完成的镜像检查，拒绝不完整或身份不一致的材料 |
| [cluster-regression-plan.md](cluster-regression-plan.md) | 已有集群回归的范围与现场安排参考，不是通用环境默认配置 |

顺序为构建镜像 → 创建隔离测试容器 → 执行 runtime/dev 检查 → 汇集证据。运行 shell 脚本前查看其参数声明，Python 工具可通过 `--help` 查看选项。设备编号需区分容器内编号与宿主机编号。

`run.sh` 将 M05 驱动、补丁安装器及补丁目录放入测试包的 `adapter/modules/m05-pd-transfer/common/transfer/`，将 M06 驱动和源码清单放入 `adapter/modules/m06-kvbm-collectives/common/cache/`，与容器内 `/test/` 下的调用路径一致。[打包回归](../../../../workflow/tests/test_packaging_paths.py) 检查实际文件复制与依赖完整性，不调用 Docker 或 GPU，不代替镜像硬件验收。

镜像构建入口见 [container/platforms](../../../../../container/platforms)，用例要求见 [M02](../../README.md)，新证据放在 [results](../../../../results/README.md)。
