<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# CUDA 开发环境启动

[cuda-dev.sh](cuda-dev.sh) 是开发调试入口：`up` 启动挂载源码与模型的容器，`bootstrap` 在容器中准备 Python 环境和源码导入。其默认容器名、镜像和挂载位置来自既有开发现场，使用前按实际环境核对脚本配置。

容器内的 `/opt/dynamo/venv` 与宿主机环境不同；挂载或 editable 导入源码适合调试，不能当作最终安装镜像验证。正式镜像构建走 [M02](../../../modules/m02-build-packaging/README.md)，最终镜像认证走 [M10](../../../modules/m10-validation/README.md)。

本目录不保存运行结果；新证据写到 [results](../../../results/README.md)。平台配置见 [上一级说明](../README.md)。
