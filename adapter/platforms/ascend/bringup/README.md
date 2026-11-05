<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ascend 开发与基础服务

| 文件 | 作用 |
| --- | --- |
| [ascend-bringup.md](ascend-bringup.md) | 开发环境准备和启动步骤，包含既有现场配置 |
| [ascend-dev.sh](ascend-dev.sh) | 启动容器、安装 Python / Rust 工具、构建与 editable 安装、进入开发终端 |
| [ascend-serve.sh](ascend-serve.sh) | 在准备好的容器中启动基础推理服务 |
| [ascend-kv-serve.sh](ascend-kv-serve.sh) | 启动用于 KV 路由回归的服务 |
| [ascend-smoke.sh](ascend-smoke.sh) | 按 basic、kv、platform 模式执行相应基础检查 |

先阅读 bringup 文档并核对镜像、设备、源码和模型挂载，再准备容器、安装环境、启动服务、收集 smoke 输出。`/opt/dynamo/venv` 及 lint 环境由容器内 setup 阶段建立；它们不是宿主机 Python。

新测试使用独立输出目录并归入 [results](../../../results/README.md)。这里是开发路径，正式镜像与验收范围见 [平台说明](../README.md)。
