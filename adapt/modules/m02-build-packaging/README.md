<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M02 构建与依赖隔离

状态：部分完成（MetaX 当前阶段）。依赖：M01 平台命名与注册契约；支撑所有模块的产物构建。

## 实施进度（2026-10-15）

已复用官方 MetaX 0.26.0 镜像，提供挂载源码、独立 venv、Rust 绑定及 editable 安装脚本。M01 新 crate 及相关 Cargo workspace 锁文件已接入；正式 MetaX 镜像模板、完整依赖隔离和可发布产物仍待实现。

证据见 [平台验收](../../metax/metax-platform.md)、[基础推理](../../metax/metax-bringup.md)、[KV 路由](../../metax/metax-kv-routing.md)。以下目标设计和文件计划中未由本节明确列为已交付的部分，仍是后续工作。

## 当前功能与约束

根 `pyproject.toml` 的 vLLM extra 固定 vLLM 0.28.0 和 `nixl[cu13]`；SGLang extra 也包含 CUDA 变体依赖。平台复用不能直接沿用这些 extras，再额外安装厂商插件。

`container/render.py` 已区分 framework、device 和主机架构。`--device` 当前接受 cuda/xpu/cpu；`--platform` 表示 Docker 的 linux/amd64 等主机架构，不能将其改作芯片平台参数。`context.yaml` 及 Jinja 模板是容器配置源。

主 Python 扩展位于独立 Cargo workspace，`lib/bindings/python/Cargo.toml` 对 dynamo-llm 使用 `default-features=false`；KVBM 绑定是另一构建入口，默认 block-manager。`lib/memory`、`lib/kvbm-physical` 的 cudarc 依赖仍需隔离。内核 stub 构建通过仅表示编译可行。

## 目标设计

产物分为公共控制面、引擎集成、平台运行时、可选 KVBM/GMS。每个 Worker 镜像只装一个经过验证的生态栈；不同平台之间共享源码与公共包，不共享所有二进制依赖。

构建选择至少包含：引擎、加速平台、主机架构、功能集合、软件栈锁定记录。沿用 `--device` 的概念扩展厂商选项，保留 `--platform` 的现有含义。

Python 的平台注册入口拟为 `dynamo.platforms`。CUDA、Ascend、MetaX 可以是单独 distribution；公共 registry 只按所选 ID 加载，重复 ID 明确失败。版本约束由各平台配置锁定，不以 `--no-deps` 长期掩盖依赖冲突。

Rust 契约 crate 不依赖 CUDA/NIXL。厂商实现 crate 封装 FFI，选定 feature 才进入依赖图。NIXL 与 CUDA 是两种不同依赖维度；禁止把“需要 NIXL”写成“必需 CUDA”。公共错误类型也必须移除对 cudarc 类型的无条件引用。

## 现有文件修改清单

| 文件 | 具体修改 |
| --- | --- |
| [pyproject.toml](../../../pyproject.toml) | 拆分引擎公共依赖与 CUDA 特有 extras；保留旧安装入口的迁移说明；配置插件分发策略 |
| [Cargo.toml](../../../Cargo.toml) | 注册契约和 provider crate，明确 workspace feature 传播 |
| [lib/llm/Cargo.toml](../../../lib/llm/Cargo.toml) | 审计 block-manager/cuda/nccl feature 与调用链，避免隐式启用不相关 SDK |
| [lib/memory/Cargo.toml](../../../lib/memory/Cargo.toml) | 配合 M04 隔离 cudarc/nixl-sys；不能只改 Cargo 而保留公共类型引用 |
| [kvbm-physical Cargo.toml](../../../lib/kvbm-physical/Cargo.toml)、[kvbm-engine Cargo.toml](../../../lib/kvbm-engine/Cargo.toml) | 分离公共算法、设备实现和集合通信依赖 |
| [kvbm-kernels build.rs](../../../lib/kvbm-kernels/build.rs)、[Cargo.toml](../../../lib/kvbm-kernels/Cargo.toml) | 按平台选择工具链；不把缺少 nvcc 自动视为其他平台已实现 |
| [Python 扩展 Cargo.toml](../../../lib/bindings/python/Cargo.toml)、[KVBM Cargo.toml](../../../lib/bindings/kvbm/Cargo.toml) | 两个入口分别验证 features；绑定编译与运行镜像一致 |
| [container/render.py](../../../container/render.py) | 平台配置驱动参数校验和模板选择，保持现有命令兼容 |
| [container/context.yaml](../../../container/context.yaml) | 引入平台配置引用/合并规则，CUDA/XPU 现有参数保持可复现 |
| [wheel_builder.Dockerfile](../../../container/templates/wheel_builder.Dockerfile)、[dynamo_base.Dockerfile](../../../container/templates/dynamo_base.Dockerfile) | 按平台装 SDK/构建工具，公共镜像不无条件携带 CUDA |
| [vllm_runtime.Dockerfile](../../../container/templates/vllm_runtime.Dockerfile)、[sglang_runtime.Dockerfile](../../../container/templates/sglang_runtime.Dockerfile) | 使用平台镜像及依赖锁；后端参数保持独立 |

## 拟新增文件清单

| 拟新增路径 | 内容 |
| --- | --- |
| `container/platforms/{cuda,ascend,metax}.yaml` | 平台工具链、引擎兼容组合、SDK 挂载和镜像信息；通过 renderer 明确读取 |
| `container/requirements/{cuda,ascend,metax}-vllm.lock` | 对首批 vLLM 认证组合生成的依赖锁；不是提前填写未验证版本 |
| `plugins/platforms/cuda/pyproject.toml`、`plugins/platforms/cuda/src/dynamo_platform_cuda/__init__.py` | CUDA 平台 distribution 和注册入口 |
| `plugins/platforms/ascend/pyproject.toml`、`plugins/platforms/ascend/src/dynamo_platform_ascend/__init__.py` | Ascend 平台包及入口，在选定 SDK 后实现 |
| `plugins/platforms/metax/pyproject.toml`、`plugins/platforms/metax/src/dynamo_platform_metax/__init__.py` | MetaX 平台包及入口，在选定 SDK 后实现 |
| `container/tests/test_platform_render.py` | 参数组合、旧命令兼容、模板输出与未选 SDK 隔离 |
| `scripts/validate_platform_stack.py` | 检查已安装版本与锁定记录，输出可归档报告；硬件探测调用 M01 |

插件内部的设备、引擎 hook、遥测文件由对应模块设计；本模块只负责打包及加载元数据。新增 crate 的文件由 M01/M04 等列明，不在这里重复创建。

## 实施与验收

先用 `cargo tree -e features` 和 Python import/依赖报告建立真实依赖图；然后让 CUDA 从新配置产生等价镜像；再逐一构建其他平台。锁文件和生成 Dockerfile 随源配置再生成。

验收：CPU 控制面不加载厂商 SDK；选择某平台不拉入其他平台；pip 依赖一致性通过；Python 扩展和 KVBM 扩展分别构建；各镜像记录 digest、主机架构和工具链；未支持的 engine/platform 组合明确拒绝。

Ascend/MetaX 的版本、SDK 获取/分发条件以及首批主机架构仍待验证。构建成功不能将支持矩阵标为硬件已验证。
