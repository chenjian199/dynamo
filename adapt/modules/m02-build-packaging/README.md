<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# M02 构建与依赖隔离

状态：当前三平台 vLLM 增量 runtime / dev 镜像范围完成并通过真机回归；全量厂商软件栈重建留待后续。依赖：M01 平台命名与注册契约；支撑后续部署模块。

## 实施进度（2026-10-26）

在各平台官方固定 digest 镜像中，分别编译并打包 Runtime、Dynamo、KVBM、GMS 和独立 KVBM 诊断程序，生成不依赖源码挂载或 editable 安装的运行镜像。CUDA / MetaX / Ascend 的 vLLM 分别锁定 0.28.0 / 0.26.0 / 0.23.0。构建入口、镜像清单、复现命令及逐平台验收见 [M02 交付记录](../../m02/README.md)。此前的挂载源码开发脚本继续保留。

本轮实际交付采用 `container/platform-build.py`、`container/platform-run.py`、`container/platforms/*.json` 及独立 Dockerfile，不修改原有 renderer 的参数含义。构建保存 Cargo 锁文件、features、OS/Python/编译器清单、源码与 wheel 哈希；最终镜像离线安装并核对厂商核心包版本、非 editable 导入位置和新增依赖冲突。AISimulate 改为显式 `simulation` extra，KVBM 的 CUDA NIXL 依赖保留在 CUDA extras 中。

依赖验收口径为“不新增官方基础镜像已有的冲突”，不是宣称官方镜像全量 `pip check` 无告警。新增依赖的完整解析结果和下载哈希随每次构建归档，支持重放该次最终打包；尚不承诺跨日期冷构建字节一致。

下方目标设计及“拟新增”清单是完整方案，不能等同于本轮全部交付。厂商 SDK 全量源码构建、独立平台 distribution、SGLang 镜像、其他硬件架构及镜像仓库发布留待后续。Ascend GMS、独立 Omni stage 与 CUDA GDS 的限制见交付记录，不因镜像构建成功而提升支持状态。

### dev / runtime 两阶段交付

新增 `platform-build.py --target dev --runtime-image ...`：直接继承已构建的 runtime image ID，在同一厂商版本配置下安装 Rust、编译与调试工具，冻结已有 Python 包版本。`platform-run.py --source ...` 只允许 dev 镜像挂载源码；容器内 `dev-editable.sh python|all` 分别安装纯 Python editable，或连同 Rust Runtime/KVBM 绑定和独立 KVBM 程序一起编译。镜像本身不包含源码，原有开发脚本仍可使用。实际验证与命令见 [M02 dev 流程](../../m02/README.md#从-runtime-派生-dev-镜像)。

## 当前功能与约束

根 `pyproject.toml` 的 vLLM extra 固定 vLLM 0.28.0 和 `nixl[cu13]`；SGLang extra 也包含 CUDA 变体依赖。平台复用不能直接沿用这些 extras，再额外安装厂商插件。

`container/render.py` 已区分 framework、device 和主机架构。`--device` 当前接受 cuda/xpu/cpu；`--platform` 表示 Docker 的 linux/amd64 等主机架构，不能将其改作芯片平台参数。`context.yaml` 及 Jinja 模板是容器配置源。

主 Python 扩展位于独立 Cargo workspace；`lib/bindings/python/Cargo.toml` 的 Linux 分支启用 dynamo-llm 默认特性，非 Linux 分支才使用 `default-features=false`。KVBM 绑定是另一构建入口，默认 block-manager。`lib/memory`、`lib/kvbm-physical` 的 cudarc 依赖仍需隔离。内核 stub 构建通过仅表示编译可行。

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

当前产物及硬件验证以 [M02 交付记录](../../m02/README.md) 为准。更换基础镜像、引擎版本、硬件架构或能力组合后需重新验收；厂商 SDK 分发条件与外部镜像发布仍需单独处理。
