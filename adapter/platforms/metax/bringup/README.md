<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MetaX 开发与基础服务

| 文件 | 作用 |
| --- | --- |
| [metax-bringup.md](metax-bringup.md) | 厂商镜像、挂载、开发依赖、构建和基础检查说明 |
| [metax-dev.sh](metax-dev.sh) | 开发容器创建、Python / Rust 工具安装、editable 安装和进入终端 |
| [metax-serve.sh](metax-serve.sh) | 基础推理服务启动 |
| [metax-smoke.py](metax-smoke.py) | 基础 API 请求与输出检查 |
| [metax-kv-routing.md](metax-kv-routing.md) | KV 路由场景、步骤及证据解释 |
| [metax-kv-serve.sh](metax-kv-serve.sh) | KV 路由测试服务启动 |
| [metax-kv-smoke.py](metax-kv-smoke.py) | KV 路由相关请求和检查 |

先按 bringup 文档核对当前镜像、设备和模型挂载，再依次准备容器、安装环境、启动服务、收集测试结果。setup 在容器内建立 `/opt/dynamo/venv` 和 lint 环境，不是安装到宿主机同名目录。

脚本是可复用的开发入口，默认值仍需按现场绑定；每次测试输出写到 [results](../../../results/README.md)。正式镜像与集群入口见 [平台说明](../README.md)。
