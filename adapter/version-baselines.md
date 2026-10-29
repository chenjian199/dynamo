<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 三平台固定验证基线

2026-10-21 按用户指定锁定以下验收版本，后续升级需要明确更新基线和回归证据。按最新要求保留已有的新旧接口兼容逻辑，不再要求 CUDA 路径拒绝旧接口；兼容逻辑必须保持新版行为正确。

| 平台 | vLLM 本体 | 厂商插件 | 镜像 / 环境 |
| --- | --- | --- | --- |
| NVIDIA | 0.28.0 | 无 | `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.1`，torch `2.13.0+cu130` |
| MetaX | 0.26.0 | vllm-metax 0.26.0 | 保留已验证的 MACA 3.8.2.208 环境 |
| Ascend | 0.23.0 | vllm-ascend 0.23.0 | 保留已验证的 CANN 9.1 环境 |

允许版本号附带厂商 `+build` 后缀，禁止静默升级或降级。公共校验见 [versions.py](../components/src/dynamo/common/platform/versions.py)，显式平台预检查与 M07 测试入口均调用它，错误版本以 `unsupported_stack` 拒绝。接口兼容与验收环境版本锁定是两回事：保留兼容逻辑不代表将 NVIDIA 验收镜像降回 0.24，也不代表旧版已重新完成全部回归。

## NVIDIA 开发环境

镜像固定到实测 digest：

```text
nvcr.io/nvidia/ai-dynamo/vllm-runtime@sha256:2b224a823d52a198ec0eeb0e3e4f5cf26cd8a7fc2e9812ee16204b6b1e974870
```

在 inspur 主机 `/home/inspur/CJ_workspace/dynamo` 执行：

```bash
bash adapter/platforms/cuda/cuda-dev.sh up
bash adapter/platforms/cuda/cuda-dev.sh bootstrap
```

容器名 `dynamo-cuda-151-dev`，挂载源码和 `/NFS_62`，新建独立 cache volume。bootstrap 创建继承镜像包的 `/opt/dynamo/venv`，安装测试工具和 Git，GMS 使用 `--no-deps` 可编辑安装；`.pth` 优先加载工作区 Python 源码，Rust 扩展继续使用官方 `ai-dynamo-runtime==1.5.1`。没有安装 `ai-dynamo[vllm]` 或替换 torch/vLLM。

官方镜像中的 OpenCV 不包含视频后端。CPU VP9 验收额外安装指定版本的完整 `opencv-python-headless` wheel，安装来源与哈希在容器 `/var/cache/dynamo-cuda/opencv-install.json`。该可选媒体依赖不改变引擎版本。

GMS worker 中为 0.24 添加的 allocator/sleep 兼容分支已撤销，恢复上游 `SleepModeBackend` 调用。0.26 原生支持该接口，沐曦仍需回归。

## 历史证据如何解释

M01/M03/M04/M05/M06 和早期 M07 中标为 CUDA 0.24 的结果仍是真实历史记录，但**不构成当前 CUDA 0.28 基线的验收**，也不改写其版本字段。本轮优先重新验证正在开发的 M07；其余模块的 0.28 完整回归需逐模块补齐。官方 Rust 扩展下的 M07 结果不能代替本分支 Rust M04/M06 改动的重新构建与验收。

M07 新旧结果和源码哈希见 [M07 验收](m07/README.md)。

## Ascend GMS 的驱动来源

2026-10-26 更新：宿主机已升级 HDK 26.1.1，重新启动开发容器后复测。
原缺失符号已补齐，RW 权限设置/查询成功；READ 权限与 POSIX 分配仍失败。
不能把接口存在当作 GMS 可用，详见 [26.1.1 原语复测](m07/ascend-hdk2611.md)。
以下哈希及缺失符号说明仅对应原 25.2.0 环境。

阻塞点是**宿主机驱动栈提供的用户态 HAL 库**，不是 vLLM 0.23 的接口差异。容器将宿主机 `/usr/local/Ascend/driver` 只读绑定到同一路径。宿主机和容器中 `lib64/driver/libascend_hal.so` 的 SHA256 完全一致：

```text
bbde1dae7c55c8ffebe23595d8febd1762c7cd85314fc599267e44c70a8f4f02
```

该库不导出 `halMemSetAccess/halMemGetAccess`，CANN 调用时返回 207000。挂载、库哈希和符号检查命令见 [driver-origin.json](m07/results/ascend/driver-origin.json)，实际调用见 [原语日志](m07/results/ascend/probe/mode-0.txt)。因此仅换镜像或容器 CANN 不会补齐这两个宿主机驱动接口；需要厂商确认支持这些接口的配套宿主机驱动与 CANN 组合，再安排升级和复测。此证据不能进一步断言内核模块或硬件本身必然不支持。

POSIX 共享类型分配失败是另一项已记录的接口限制，不能承诺升级驱动后自动消失；若升级后仍只有 token 共享，还需迁移 GMS 的共享句柄协议。本轮不修改宿主机驱动。
