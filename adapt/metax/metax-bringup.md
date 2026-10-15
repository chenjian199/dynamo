<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MetaX C500 基础推理适配记录

状态：2026-10-15 完成首轮基础推理验收，标记为有限支持。已验证本页指定组合上的单机单卡文本推理、模型注册、流式/非流式生成、并发、取消及健康检查。PD、KVBM、KV-aware 路由和完整平台插件框架不在本阶段支持声明内。

## 开发环境

- 基础镜像：`cr.metax-tech.com/public-ai-release/maca/vllm-metax:0.26.0-maca.ai3.8.2.208-torch2.10-py312-ubuntu22.04-amd64`。
- 固定 digest：`sha256:e7e59c3713ad4ef1290cc0d5029759f2844b2695f36de0bca2f4630c671add95`。
- 开发容器：`dynamo-metax-dev`；宿主仓库挂载到 `/workspace/dynamo`；缓存卷 `dynamo-metax-cache` 挂载到 `/var/cache/dynamo-metax`。
- 容器内 Python 开发环境：`/opt/dynamo/venv`，通过镜像的 `/opt/conda/bin/python` 创建并继承 system site-packages。
- 本机调试端口：`127.0.0.1:18000 → 8000`、`127.0.0.1:18001 → 8001`。
- 硬件：4 × MetaX C500，单卡 65536 MiB；本阶段先使用一张卡。宿主驱动报告 `3.8.30`。
- 验收模型：`Qwen/Qwen3-0.6B`，revision `c1899de289a04d12100db370d81485cdf75e47ca`。
- Dynamo 工作树基线：`eb40c4bf3d09ca740e1f539a918eab3f3c005cad`，版本 `1.5.1`；本页与脚本为该基线上的增量。

## 镜像内实测版本

| 包 | 版本 |
| --- | --- |
| Python | 3.12.11 |
| torch | 2.10.0+metax3.8.2.2 |
| vllm | 0.26.0 |
| vllm-metax | 0.26.0+g097358.d20260911.maca3.8.2.208.torch2.10.0 |
| nixl | 1.0.0+maca3.8.2.208 |
| mooncake-transfer-engine | 0.3.12+geb7daa.d20260903.maca3.8.2.104 |
| mcoplib | 0.4.11+ga3b69de.maca3.8.0.25.torch2.10 |

本地插件 `v0.26.0` 标签为 `615ac623bdbb8dcbd337436ab9bbff5f263cc3d0`，不同于镜像中的插件构建提交，不能将两者混记为同一认证组合。运行验收以镜像中的实际安装包为准；参考源码现在位于 `reference/metax/`。

## 已完成检查

- GPU 可见：`torch.cuda.is_available() == True`，检测到 4 张 C500。
- 在 GPU 0 分配张量并执行加法，回读结果为 `[2.0, 2.0, 2.0, 2.0]`。
- 创建继承厂商包的独立开发环境，安装 uv、maturin、patchelf、ruff。
- 原生 vLLM：模型注册、非流式生成、四路并发、流式文本及 `[DONE]` 通过。
- Dynamo：相同项目通过；主动断开长请求后 Worker 取消计数从 1 增加到 2，后续请求成功；引擎日志恢复到 Running/Waiting 均为 0。
- 固定提示词、temperature=0、max_tokens=16 的生成文本与原生服务完全相同，prompt/completion/total tokens 均为 5/16/21。此项不是全模型精度评测。
- Worker 工厂单测 46 项、异常处理单测 6 项通过。
- 不存在的模型路径启动退出码为 1，frontend 随之退出；正常 SIGTERM 停机退出码为 0，无残留 Worker/EngineCore。
- 停机后重新启动，再次通过全部 API 验收；宿主机 `http://127.0.0.1:18000/v1/models` 能查询到模型。调试服务保留运行在 GPU 0。
- 实际 API 返回与安装版本见 [metax-basic-results.json](metax-basic-results.json)。

## 本次源码修改

1. `components/src/dynamo/vllm/worker_factory.py`：仅在创建多模态编码器时导入 `EncodeWorkerHandler`。原先纯文本启动也会间接导入 NIXL/CuPy，镜像内 CuPy 使用已被 NumPy 2 移除的 `np.float_`，导致启动失败。没有降级 NumPy，也没有吞掉多模态启动错误。
2. `components/src/dynamo/vllm/errors.py` 与 `handlers.py`：vLLM 0.26 没有 `VLLMClientError` 共同基类，改为版本兼容的异常类型集合。旧版仅捕获三类明确请求错误，映射为 400/404/422；新版仍使用共同基类，普通 `ValueError`/运行错误保持传播。
3. 增加文本导入隔离、编码器缺依赖失败、生成异常状态和正常生成回归测试。未修改厂商插件、PyTorch、vLLM 或 NIXL 安装包。

`metax-serve.sh` 和 `metax-smoke.py` 分别提供容器内启动与 API 验收入口。

## 重建开发环境

已将下述过程整理为宿主机脚本 [metax-dev.sh](metax-dev.sh)，可逐步手动执行：

```bash
bash adapt/metax/metax-dev.sh create
bash adapt/metax/metax-dev.sh setup
bash adapt/metax/metax-dev.sh build
bash adapt/metax/metax-dev.sh model  # 已有缓存时可跳过
bash adapt/metax/metax-dev.sh shell
```

无参数或 `--help` 只显示帮助。`setup` 包含系统依赖、继承厂商包的 venv、Rust、测试和 lint 工具；`build` 完成两部分 editable 安装。默认采用本次用过的 Rust/Cargo/Hugging Face 镜像源，`USE_MIRROR=0` 可关闭新镜像源配置；保留已有 Cargo 配置，不改宿主机 DNS。已有同名容器时 `create` 会报错，停止的容器用 `docker start dynamo-metax-dev` 恢复。更新环境或重编译前先停止容器中的推理服务。

宿主机创建容器（需要先拉取上述镜像；现有同名容器可直接 `docker start`）：

```bash
docker run -d --name dynamo-metax-dev --init \
  --device=/dev/dri --device=/dev/mxcd --shm-size=8g --ulimit memlock=-1 \
  -p 127.0.0.1:18000:8000 -p 127.0.0.1:18001:8001 \
  --mount type=bind,src=/home/metax/CJ_workspace/dynamo,dst=/workspace/dynamo \
  --mount type=volume,src=dynamo-metax-cache,dst=/var/cache/dynamo-metax \
  -w /workspace/dynamo --entrypoint /bin/bash \
  cr.metax-tech.com/public-ai-release/maca/vllm-metax@sha256:e7e59c3713ad4ef1290cc0d5029759f2844b2695f36de0bca2f4630c671add95 \
  -lc 'exec sleep infinity'
docker exec -it dynamo-metax-dev bash
```

以下在容器中执行。不要安装 `.[vllm]`：主仓库该 extra 固定的是另一套 vLLM/CUDA 依赖。开发环境继承镜像厂商包，只增量安装公共依赖与 Dynamo。

```bash
apt-get update
apt-get install -y --no-install-recommends \
  build-essential pkg-config cmake protobuf-compiler libprotobuf-dev \
  libssl-dev libclang-dev libudev-dev libnuma-dev libzmq3-dev git
/opt/conda/bin/python -m venv --system-site-packages /opt/dynamo/venv
source /opt/dynamo/venv/bin/activate
python -m pip install uv 'maturin[patchelf]' 'kubernetes>=32.0.1,<33' blake3
python -m pip install --no-deps -e /workspace/dynamo
```

使用 rustup 安装仓库 `rust-toolchain.toml` 指定的 Rust `1.96.1`。本次官方源连接缓慢，使用 `rsproxy.cn` 的 Rust 工具链及 Cargo sparse 镜像；该选择只写在容器 `/root/.cargo/config.toml`，未修改仓库源配置。

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup-init.sh
sh /tmp/rustup-init.sh -y --profile minimal --default-toolchain 1.96.1
unset CONDA_PREFIX  # maturin 不允许同时存在 Conda 与 virtualenv 标识
export PATH=/root/.cargo/bin:$PATH
export CARGO_TARGET_DIR=/var/cache/dynamo-metax/target
export CARGO_BUILD_JOBS=16 CARGO_PROFILE_DEV_DEBUG=0
cd /workspace/dynamo/lib/bindings/python
maturin develop --locked --uv
cd /workspace/dynamo
```

仅 Python 文件修改可直接重启服务；Rust 文件修改需重新执行 `maturin develop`。此构建未接入厂商原生 NIXL 库，也未构建 KVBM 扩展，不作为设备内存传输能力的证明。

## 模型与启动

下载权重（本次 Hugging Face 直连不可达，使用 `HF_ENDPOINT=https://hf-mirror.com`）：

```bash
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 python - <<'PY'
from huggingface_hub import snapshot_download

print(snapshot_download(
    "Qwen/Qwen3-0.6B",
    revision="c1899de289a04d12100db370d81485cdf75e47ca",
    cache_dir="/var/cache/dynamo-metax/huggingface",
    allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja"],
    max_workers=4,
))
PY
```

在容器内独立进程组启动，便于统一清理 frontend、worker 及引擎子进程：

```bash
export MODEL_PATH=/var/cache/dynamo-metax/huggingface/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca
setsid --wait bash /workspace/dynamo/adapt/metax/metax-serve.sh
```

默认使用 GPU 0、TP=1、round-robin 路由、file discovery/TCP/ZMQ，无需 etcd/NATS；上下文 4096、最多 4 个序列、KV cache 上限 1 GiB、eager 模式。可通过脚本中的环境变量覆盖模型路径、设备和端口。模型对外名称为 `Qwen3-0.6B`。

要停止服务，向该启动脚本的进程发送 SIGTERM，等待同组进程退出再重启。日志使用独立文件或追加模式，避免前一进程尚未退出时截断其输出文件。

另开一个容器终端，在模型就绪后验收：

```bash
/opt/dynamo/venv/bin/python /workspace/dynamo/adapt/metax/metax-smoke.py \
  --url http://127.0.0.1:8000 --worker-url http://127.0.0.1:8081 \
  --output /var/cache/dynamo-metax/dynamo-smoke.json
```

原生参考服务使用 `python -m vllm.entrypoints.openai.api_server`，参数为同一模型、`--served-model-name Qwen3-0.6B --host 0.0.0.0 --port 8001 --enforce-eager --max-model-len 4096 --max-num-seqs 4 --kv-cache-memory-bytes 1073741824 --gpu-memory-utilization 0.1`。验收脚本改用 `--url http://127.0.0.1:8001` 并省略 `--worker-url`，不检查 Dynamo 取消计数。切换到 Dynamo 前停止原生服务。

取消检查的 `--namespace` 表示 Rust 指标标签，默认 `metax_basic`，对应启动 namespace `metax-basic`。Rust 会将标签中的连字符归一化为下划线。

## 检查与边界

```bash
source /opt/dynamo/venv/bin/activate
python -m pytest components/src/dynamo/vllm/tests/test_vllm_worker_factory.py \
  components/src/dynamo/vllm/tests/test_vllm_errors.py -q
bash -n adapt/metax/metax-serve.sh
```

本次安装了 pytest-benchmark、pytest-timeout、pytest-asyncio、pytest-mock 以运行仓库测试。固定版本的 Black 23.1.0、isort 5.12.0、Ruff 0.5.2、codespell 2.2.4 检查通过；完整 pre-commit 在克隆 GitHub hook 时遇到 early EOF，未完成。Flake8 原配置报告已有代码的 W503 换行规则，与 Black 格式冲突；排除 W503 后通过，未为此改写无关代码。`bash -n`、Python compileall 和 `git diff --check` 通过。

- 开发环境不是最终发布镜像或完整依赖锁。官方镜像自身 `pip check` 就有上游 CUDA 依赖/版本声明不一致；开发环境相对它新增的缺项为 `aisimulate==0.12.0`。本阶段未使用仿真模块，未安装其 JAX 依赖链。正式打包时须完成 M02 的依赖拆分，不以 `--no-deps` 代替依赖验收。
- runtime 构建为调试 profile，未进行吞吐或延迟认证。
- 本次仅实测官方 MetaX vLLM 0.26.0 组合；源码保留新版异常基类路径，但未执行 vLLM 0.28.0 环境回归，不能据此声明两个版本均已验收。
- 尚未验证多卡 TP/DP、多节点、PD、NIXL/Mooncake 传输、KV-aware 路由、KVBM、GMS、多模态、量化及 `--use-vllm-processor` 前端路径。镜像里存在相关包不代表这些路径已经适配。
- CuPy/NumPy 的兼容问题仍需在多模态阶段单独处理。当前修复保证文本 Worker 不加载这套可选依赖。

容器内日志保存在 `/var/cache/dynamo-metax/logs/`：`native-vllm.log`、`runtime-build-mirror.log`、`dynamo-validated.log`、`invalid-model.log`、`worker-factory-tests.log`、`error-tests.log`、`pip-check.txt` 与 `vendor-pip-check.txt`。模型和编译缓存位于命名卷，Python venv 与 Rust 工具链在容器可写层；删除容器后需重建后两者。
