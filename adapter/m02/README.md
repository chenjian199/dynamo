<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M02 三平台 runtime / dev 镜像

本轮采用官方推理镜像作为固定基础，构建当前分支的 Runtime、KVBM、Dynamo 和 GMS wheel 及独立 KVBM 诊断程序，再生成不依赖源码挂载及 editable 安装的 runtime 镜像，并直接在其基础上派生带工具链的 dev 镜像。厂商完整软件栈从源码重建、独立平台 distribution、SGLang 镜像不属于本轮交付范围。

## 固定软件栈

| 平台 | 架构 | vLLM | 官方基础镜像 | 本地运行镜像标签 |
| --- | --- | --- | --- | --- |
| CUDA | amd64 | 0.28.0 | `nvcr.io/nvidia/ai-dynamo/vllm-runtime`，固定 digest | `heyi/dynamo-vllm-cuda:1.5.1-m02` |
| MetaX | amd64 | 0.26.0 | `cr.metax-tech.com/public-ai-release/maca/vllm-metax`，固定 digest | `heyi/dynamo-vllm-metax:1.5.1-m02` |
| Ascend | arm64 | 0.23.0+empty | `quay.io/ascend/vllm-ascend`，固定 digest | `heyi/dynamo-vllm-ascend:1.5.1-m02` |

精确 digest、torch、厂商插件及保护包列表在 [平台配置](../../container/platforms)。镜像标签只是本地名字，归档和部署以 `image.json` 内的 image ID 为准。尚未推送外部镜像仓库。

## 构建

在各平台对应主机的仓库根目录执行；构建输出必须在源码目录之外。构建不需要 GPU，不会停止其他推理容器。

```bash
# 沐曦；NVIDIA / 华为分别把 device 与 tag 改成 cuda / ascend。
python3 container/platform-build.py \
  --device metax \
  --tag heyi/dynamo-vllm-metax:1.5.1-m02 \
  --output /var/tmp/dynamo-m02-metax-build \
  --jobs 16 --rust-mirror rsproxy
```

`--rust-mirror rsproxy` 是可选 Rust 工具链下载镜像，不改变 `rust-toolchain.toml` 的版本。Cargo 使用锁文件构建。ARM64 使用 generic CPU，避免 Kunpeng 执行 neoverse-n1 指令；本轮 CUDA 内核目标是 SM90，其他 GPU 架构需要单独构建与验证。

构建入口将 Git 管理的文件和未忽略的新文件形成源码快照，明确排除 `reference/`。修改中的源码也会进入快照，`SOURCE.json` 为每个文件保存 SHA256，因此提交号不能单独代表脏工作区产物。构建容器不挂载源码。失败后保留构建容器及缓存，使用相同参数加 `--resume` 重试；修改平台配置时需使用新输出目录。

最终镜像基于官方镜像重新构建，不使用 `docker commit`。输出目录包含 wheel、原生库、源码清单、基础镜像依赖报告、Python/OS/编译器版本及 `image-context/`。新增依赖的完整版本和 wheel 哈希随该次构建归档；跨日期从零解析并不保证字节一致。可直接重放最终打包阶段：

```bash
# BASE_IMAGE 必须取该次 image-context/artifacts/profile.json 中的 base_image。
DOCKER_BUILDKIT=0 docker build \
  --build-arg BASE_IMAGE="$BASE_IMAGE" --build-arg ACCELERATOR=metax \
  -f /var/tmp/dynamo-m02-metax-build/image-context/runtime.Dockerfile \
  -t heyi/dynamo-vllm-metax:1.5.1-m02 \
  /var/tmp/dynamo-m02-metax-build/image-context
```

## 依赖与能力边界

- 不安装根目录的 CUDA `.[vllm]` extra 来覆盖厂商栈。关键厂商包版本必须与基础镜像一致。
- `ai-dynamo[simulation]` 显式安装 AISimulate；纯推理 Worker/Frontend 不再强制安装模拟器。已有模拟器与 Planner 镜像仍按原有固定版本安装。
- KVBM 的 CUDA NIXL 通过 `kvbm[cu12]` / `kvbm[cu13]` 声明。MetaX/Ascend 的本地主机暂存路径不引入 CUDA NIXL wheel。
- 官方三平台镜像自身存在 `pip check` 告警。`base.json` 保留原始结果，`installed.json` 要求不引入新的依赖冲突，并检查包版本和非 editable 导入位置；这不等于整个厂商镜像全量 `pip check` 通过。
- Runtime 编译启用多模态路由与请求跟踪 S3；Python KVBM 和独立 `native_replicated` 诊断入口分别编译，原生 C ABI 库从 Cargo JSON 输出精确提取。CUDA 使用真实 SM90 内核及 NIXL 运行时 C API；MetaX/Ascend 使用已验收的厂商 SDK 主机暂存路径，CUDA stub 不代表厂商融合内核已实现。
- M05 的 Mooncake 补丁按原始文件哈希校验后应用，Ascend 额外应用 TP 补丁；MetaX 保留已验收的 UCX 参数。MetaX 可选 NIXL 在传输中断时的 UCX abort 限制仍然存在。
- GMS 包可在三平台导入，但 Ascend GMS 硬件能力仍暂缓。CUDA 原镜像附带的 Omni 保留；MetaX Omni 是单独的可选 overlay，基础文本镜像不会自动安装。独立 Dynamo Omni stage 的完整 hidden-state 通道仍暂缓。
- 原有 `container/render.py --device cuda/xpu/cpu --platform linux/...` 接口保持不变；本轮增量路径由 `platform-build.py` 提供。

## 运行与开发

```bash
python3 container/platform-run.py \
  --device metax --image heyi/dynamo-vllm-metax:1.5.1-m02 \
  --name dynamo-metax-runtime --devices 1,2 --detach \
  --models /NFS_62/Models -- sleep infinity

docker exec -it dynamo-metax-runtime /opt/dynamo/bin/entrypoint.sh bash
# 模型路径：/models/Qwen_Series/Qwen3-0.6B
# Python：/opt/dynamo/venv/bin/python
```

运行脚本仅挂载模型和所需设备/宿主机驱动，不挂载仓库。Ascend 仍需要兼容的宿主机驱动；容器不能代替驱动升级。CUDA/Ascend 的 `--devices` 是宿主机设备 ID；容器内可见设备序号按暴露列表重新编号。例如 Ascend 暴露宿主机 4、5 卡，Worker 参数使用容器内 0、1。当前 Ascend 驱动会拒绝多个容器重复占用同一设备，运行脚本会检查已运行容器的显式设备映射；应选择未被占用的卡。

开发推荐使用下一节的 dev 镜像；原有 [MetaX 开发脚本](../platforms/metax/metax-dev.sh)、[Ascend 开发脚本](../platforms/ascend/ascend-dev.sh) 的源码挂载和 editable 流程仍保留。生产运行镜像中的 wheel 不随宿主机源码修改，修改后需重建镜像。

## 从 runtime 派生 dev 镜像

三平台使用相同的两阶段流程。已为前面构建的运行镜像增加 `heyi/dynamo-vllm-{cuda,metax,ascend}-runtime:1.5.1-m02` 别名，与原标签指向同一 image ID；开发镜像使用对应的 `-dev` 标签。第一阶段由 `platform-build.py --target runtime`（默认值）产生运行镜像；第二阶段直接 `FROM` 该镜像的实际 image ID，添加 Rust 1.96.1、rustfmt/clippy、C/C++ 编译工具、maturin、gdb/strace、debugpy 和 pytest-timeout。不会重新选择 vLLM/torch/NIXL 版本；安装开发依赖前冻结全部现有 Python 包，构建后再次验证版本及新增依赖冲突。

```bash
# 先用前面的构建命令生成 runtime，再生成 dev。
# 新构建也可以直接把 --tag 设为这个 -runtime 名称。
docker tag heyi/dynamo-vllm-metax:1.5.1-m02 heyi/dynamo-vllm-metax-runtime:1.5.1-m02
python3 container/platform-build.py \
  --device metax --target dev \
  --runtime-image heyi/dynamo-vllm-metax-runtime:1.5.1-m02 \
  --tag heyi/dynamo-vllm-metax-dev:1.5.1-m02 \
  --output /var/tmp/dynamo-m02-metax-dev-build --rust-mirror rsproxy

python3 container/platform-run.py \
  --device metax --image heyi/dynamo-vllm-metax-dev:1.5.1-m02 \
  --name dynamo-metax-dev-m02 --devices 1,2 --detach \
  --source "$PWD" --cache-volume dynamo-metax-dev-m02-cache \
  --models /NFS_62/Models -- sleep infinity

# 首次接入源码：编译 Runtime/KVBM Rust 绑定及独立 KVBM 程序，安装 Python editable。
docker exec dynamo-metax-dev-m02 /opt/dynamo/bin/entrypoint.sh \
  /opt/dynamo/bin/dev-editable.sh all

# 只调试 Python 时可使用快速模式，沿用镜像里的 Rust 扩展。
docker exec dynamo-metax-dev-m02 /opt/dynamo/bin/entrypoint.sh \
  /opt/dynamo/bin/dev-editable.sh python

docker exec -it dynamo-metax-dev-m02 /opt/dynamo/bin/entrypoint.sh bash
```

CUDA / Ascend 将上述 `metax` 换成对应平台；设备编号仍遵守前面的宿主机映射规则。源码在 `/workspace/dynamo`，编译输出放在 `/build-cache`，不在宿主机仓库创建 Cargo target 目录。Runtime 和 KVBM 使用独立 Cargo target 目录，避免两个 `_core` 扩展在缓存重建时互相覆盖；正式 wheel 构建采用同样隔离。旧版共用 `_core` 的缓存可能已有错误产物，迁移时使用新的 cache volume，不直接复制旧 target 目录。当前开发容器以 root 工作，maturin 会在挂载源码的 Python 包目录生成扩展文件；源码目录需可写。若 Git 因挂载目录所有者不同而拒绝访问，可在容器内执行 `git config --global --add safe.directory /workspace/dynamo`，只信任这个开发目录。只有 dev 镜像允许 `--source` 和 `--cache-volume`，runtime 的无源码挂载流程保持不变。

dev 镜像将挂载源码中的 `lib` 放在 Python 搜索路径前部，避免 setuptools 的 editable finder 被基础镜像自带的 GMS 包遮蔽。安装脚本会检查实际导入位置。

`dev-editable.sh` 不会在每次启动时自动重编译。源码中的平台配置与 Rust 工具链必须匹配镜像；变更厂商版本后需要重建 runtime 和 dev。Python 改动重启对应服务即可，Rust 改动重跑 `all`。`python` 模式仅把 Dynamo Python 与 GMS 接到源码，不表示 Rust/KVBM 扩展已从这份源码重建。

dev 构建会继承宿主机已设置的 HTTP(S)/NO_PROXY 标准代理变量，作为 Docker 预定义构建参数传递；参数值不打印到命令日志，也不设为镜像 ENV。MetaX 的开发工具使用 Ubuntu 官方 HTTPS apt 源，保留原有发行版与签名校验；厂商推理包仍继承 runtime。

dev 构建输出包含 `dev-inputs.json`、冻结的 `dev-context/` 和 `image.json`，记录 runtime 父镜像 ID 与开发构建脚本哈希。使用 `--resume` 复用该上下文；修改脚本后应使用新输出目录。dev 镜像不内置仓库和模型，Rust 工具链已预装，但首次构建所需 Cargo crates 仍可能从网络下载。

### dev 镜像验收记录

开发镜像的验收与 runtime 分开记录：核对父镜像层、全部继承包版本、工具链、四个实际源码导入位置及 Rust API，再运行 Qwen3-0.6B 基础推理与双 Worker KV 路由。验收使用独立源码快照目录；编译缓存复用只用于加速，源码和模型均没有写入镜像。

| 平台 | dev image ID | editable / 工具 / 源码导入 | 基础推理 / KV 路由 |
| --- | --- | --- | --- |
| CUDA | `916f5a013818` | [通过](results/cuda/dev.json) | [通过](results/cuda/dev-smoke.json) |
| Ascend | `e81bcd77e2a4` | [通过](results/ascend/dev.json) | [通过](results/ascend/dev-smoke.json) |
| MetaX | `f82504063c9c` | [通过](results/metax/dev.json) | [通过](results/metax/dev-smoke.json) |

dev 构建目录位于各自主机：CUDA `/var/tmp/dynamo-m02-cuda-dev-4`、Ascend `/var/tmp/dynamo-m02-ascend-dev-4`、MetaX `/var/tmp/dynamo-m02-metax-dev-6`。验收容器已停止，原有开发容器未改动。

每个平台的 `dev-installed.json` 保存 editable 安装后的依赖清单。`dev.json` 保存 runtime 父镜像 ID、构建输入哈希、编译后的扩展 SHA256、editable 元数据和工具版本。可以在完成 `dev-editable.sh all` 后重新执行：

```bash
python3 adapter/m02/dev-validate.py \
  --container dynamo-metax-dev-m02 \
  --build /var/tmp/dynamo-m02-metax-dev-build \
  --output /var/tmp/dynamo-metax-dev-validation.json
```

## 验收

[smoke.py](../../container/platforms/smoke.py) 仅包含测试逻辑，可通过 `docker cp` 放入新运行容器，不需要复制 Dynamo 源码。使用两个空闲设备运行：

```bash
docker cp container/platforms/smoke.py dynamo-metax-runtime:/tmp/m02-smoke.py
docker exec dynamo-metax-runtime /opt/dynamo/bin/entrypoint.sh \
  python /tmp/m02-smoke.py --platform metax --devices 1,2

docker cp dynamo-metax-runtime:/tmp/m02-smoke ./m02-smoke-results
```

检查两 Worker 就绪、基础生成、三次重复前缀的 Worker 选择、引擎 cached tokens 和路由 KV 命中。

完整的本轮测试入口如下，使用新创建且没有 `/test` 目录的容器。只复制测试脚本，不复制或安装生产源码。CUDA / Ascend 的测试设备参数用容器内 `0,1`，MetaX 使用选择的物理卡号。

```bash
bash adapter/m02/run.sh metax dynamo-metax-runtime 1,2 \
  /var/tmp/dynamo-m02-metax-build /var/tmp/dynamo-m02-metax-results
```

入口依次执行 CPU 平台无 SDK 导入、基础推理/KV 路由、独立 KVBM 六组双 rank 数据校验、无缓存基线、G2/G3 缓存恢复与输出一致性、1P1D、CUDA/MetaX GMS 四项测试。CUDA 另测原有 NIXL 主机缓存路径。输出目录必须不存在；失败日志保留在容器 `/tmp/m02-*`，不要把不完整报告记为通过。测试脚本固定 Qwen3-0.6B，并非全模型、性能或长稳认证。

## 本轮结果与边界

证据分别位于 [CUDA](results/cuda/summary.json)、[MetaX](results/metax/summary.json)、[Ascend](results/ascend/summary.json)。每份报告关联实际 image ID、构建源码快照哈希、测试脚本哈希、wheel 清单和容器挂载。编译后的生产代码来自同一冻结快照；随后修正的测试端口、可见设备参数以及文档不改变镜像中的生产代码。构建提交号单独不能代表这些未提交修改。

| 检查 | CUDA 0.28.0 | MetaX 0.26.0 | Ascend 0.23.0 |
| --- | --- | --- | --- |
| wheel 安装、版本保护、零新增依赖冲突 | 通过 | 通过 | 通过 |
| CPU 平台导入不加载加速器 SDK | 通过 | 通过 | 通过 |
| 基础生成、两 Worker 真实 KV 路由 | 通过 | 通过 | 通过 |
| 独立 KVBM 双 rank，六组数据布局 | 6/6 | 6/6 | 6/6 |
| 原生 G2/G3 恢复、逐字节校验、输出一致性 | 通过 | 通过 | 通过 |
| 原有 CUDA NIXL G2 主机缓存 | 通过 | 不适用 | 不适用 |
| 1P1D | 通过 | 通过 | 通过 |
| GMS 跨进程所有权、释放、内存池及引擎 sleep/wake | 4/4 | 4/4 | 按既定范围暂缓 |

CUDA 原有 NIXL/GDS 磁盘路径在当前配置报 `GDS_MT: file register error: error=5027`，随后发生写回失败和输出不一致。未改动的官方基础镜像使用同一模型、测试配置也复现；[打包镜像结果](results/cuda/m02-gds-packaged.json)、[官方镜像结果](results/cuda/m02-gds-official.json) 与对应 `.txt` 错误摘录均保留。它属于本环境仍未通过的 GDS 路径，不能标成已支持；本轮磁盘验收使用 M06 已有的 `--native-cuda` 路径，CUDA 默认后端保持原样。

本轮没有重复 M03–M07 的全部模型、TP/DP、2P3D、故障和长稳矩阵；这些历史证据仍按原版本解读，不能把 CUDA 0.24.0 的全部结论移到 0.28.0。这里新增的是固定镜像下上述配置的验收。外部发布、离线全量 SDK 构建、跨节点、Ascend GMS 和完整独立 Omni stage 均未宣称完成。


本轮本地镜像 ID：CUDA `b59de8ff7d75`、MetaX `5de6fdd7ddea`、Ascend `a0d44e6ad9e0`。构建目录分别位于对应主机 `/var/tmp/dynamo-m02-{cuda,metax,ascend}-1`。验收容器已停止，原有开发容器保留；模型仍由外部 `/NFS_62/Models` 提供。

代码检查：打包完整性及 dev/runtime 保护单元测试 10 项通过；AISimulate 声明一致性 4 项通过，未安装可选模拟器的 1 项跳过；Black、Ruff、shell 语法和 `git diff --check` 通过。GMS 在隔离测试配置中有未注册 marker 告警，执行由外层 `timeout 900` 限时，四项均实际运行且未跳过。新增 M02 路径已声明 CODEOWNERS 并重新生成；全仓严格归属检查仍报告此前存在的 2378 个 catch-all 路径，本轮未扩大为全仓归属整理，也未宣称全量 CI 通过。
