#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# 在宿主机分步执行；默认仅显示帮助，不会重建或停止现有容器。
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTAINER_NAME="${CONTAINER_NAME:-dynamo-metax-dev}"
IMAGE="${IMAGE:-cr.metax-tech.com/public-ai-release/maca/vllm-metax@sha256:e7e59c3713ad4ef1290cc0d5029759f2844b2695f36de0bca2f4630c671add95}"
CACHE_VOLUME="${CACHE_VOLUME:-dynamo-metax-cache}"
HTTP_PORT="${HTTP_PORT:-18000}"
NATIVE_PORT="${NATIVE_PORT:-18001}"
USE_MIRROR="${USE_MIRROR:-1}"

case "${1:-help}" in
    create)
        # 对应官方 tag：0.26.0-maca.ai3.8.2.208-torch2.10-py312-ubuntu22.04-amd64
        # 同名容器存在时 docker run 会报错，不会删除或覆盖它。
        docker run -d --name "$CONTAINER_NAME" --init \
            --device=/dev/dri --device=/dev/mxcd --shm-size=8g --ulimit memlock=-1 \
            -p "127.0.0.1:${HTTP_PORT}:8000" -p "127.0.0.1:${NATIVE_PORT}:8001" \
            --mount "type=bind,src=${REPO_DIR},dst=/workspace/dynamo" \
            --mount "type=volume,src=${CACHE_VOLUME},dst=/var/cache/dynamo-metax" \
            -w /workspace/dynamo --entrypoint /bin/bash "$IMAGE" \
            -lc 'exec sleep infinity'
        ;;
    setup)
        docker exec -i -e USE_MIRROR="$USE_MIRROR" "$CONTAINER_NAME" bash -s <<'CONTAINER'
set -euo pipefail
apt-get update
apt-get install -y --no-install-recommends \
    build-essential pkg-config cmake protobuf-compiler libprotobuf-dev \
    libssl-dev libclang-dev libudev-dev libnuma-dev libzmq3-dev git curl ca-certificates

mkdir -p /var/cache/dynamo-metax/logs
if [[ ! -x /opt/dynamo/venv/bin/python ]]; then
    /opt/conda/bin/python -m venv --system-site-packages /opt/dynamo/venv
fi
source /opt/dynamo/venv/bin/activate
# 继承厂商 torch/vllm/nixl；不要安装 .[vllm]，不要升级厂商栈。
python -m pip install uv==0.13.1 maturin==1.15.0 patchelf==0.19.1.0 \
    kubernetes==32.0.1 blake3==1.0.9 'pydantic==2.13.0' uvloop==0.23.0
python -m pip install pre-commit==4.7.0 pytest-benchmark==5.3.0 \
    pytest-timeout==2.4.0 pytest-asyncio==1.4.0 pytest-mock==3.16.0

# lint 单独放一个 venv，保持与仓库 pre-commit 固定版本一致。
if [[ ! -x /opt/dynamo/lint/bin/python ]]; then
    python -m venv --system-site-packages /opt/dynamo/lint
fi
/opt/dynamo/lint/bin/python -m pip install \
    isort==5.12.0 black==23.1.0 flake8==7.3.0 ruff==0.5.2 codespell==2.2.4

export PATH=/root/.cargo/bin:$PATH
if [[ "$USE_MIRROR" == 1 ]]; then
    export RUSTUP_DIST_SERVER=https://rsproxy.cn
    export RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup
    mkdir -p /root/.cargo
    if [[ ! -e /root/.cargo/config.toml && ! -e /root/.cargo/config ]]; then
        cat > /root/.cargo/config.toml <<'CARGO'
[source.crates-io]
replace-with = "rsproxy"
[source.rsproxy]
registry = "sparse+https://rsproxy.cn/index/"
[http]
timeout = 60
CARGO
    else
        echo '保留已有 Cargo 配置；需要换源时请手动编辑 /root/.cargo/config.toml。'
    fi
fi
if ! command -v rustup >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 --fail --show-error --location \
        https://sh.rustup.rs -o /tmp/dynamo-rustup-init.sh
    sh /tmp/dynamo-rustup-init.sh -y --profile minimal --default-toolchain none
fi
RUST_VERSION=$(python -c 'import tomllib; from pathlib import Path; print(tomllib.loads(Path("/workspace/dynamo/rust-toolchain.toml").read_text())["toolchain"]["channel"])')
rustup toolchain install "$RUST_VERSION" --profile minimal
echo '环境和工具已准备好。下一步在宿主机执行：bash adapt/metax/metax-dev.sh build'
CONTAINER
        ;;
    build)
        docker exec -i "$CONTAINER_NAME" bash -s <<'CONTAINER'
set -euo pipefail
source /opt/dynamo/venv/bin/activate
unset CONDA_PREFIX  # maturin 不能同时检测到 Conda 和 virtualenv
export PATH=/root/.cargo/bin:$PATH
export CARGO_TARGET_DIR=/var/cache/dynamo-metax/target
export CARGO_BUILD_JOBS=16 CARGO_PROFILE_DEV_DEBUG=0
cd /workspace/dynamo
python -m pip install --no-deps -e .
cd lib/bindings/python
maturin develop --locked --uv
cd /workspace/dynamo
python -c 'import dynamo._core; print("本地运行时：", dynamo._core.__file__)'
echo 'Python editable 安装完成；Python 修改后重启服务，Rust 修改后重跑 build。'
CONTAINER
        ;;
    model)
        docker exec -i -e USE_MIRROR="$USE_MIRROR" "$CONTAINER_NAME" bash -s <<'CONTAINER'
set -euo pipefail
source /opt/dynamo/venv/bin/activate
if [[ "$USE_MIRROR" == 1 ]]; then
    export HF_ENDPOINT=https://hf-mirror.com
fi
export HF_HUB_DISABLE_XET=1
python - <<'PY'
from huggingface_hub import snapshot_download

print(snapshot_download(
    "Qwen/Qwen3-0.6B",
    revision="c1899de289a04d12100db370d81485cdf75e47ca",
    cache_dir="/var/cache/dynamo-metax/huggingface",
    allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja"],
    max_workers=4,
))
PY
CONTAINER
        ;;
    shell)
        docker exec -it "$CONTAINER_NAME" bash --rcfile /opt/dynamo/venv/bin/activate
        ;;
    help|--help|-h)
        cat <<'HELP'
在宿主机按需分步执行：
  bash adapt/metax/metax-dev.sh create  # 创建容器，挂载当前仓库；不存在的镜像自动拉取
  bash adapt/metax/metax-dev.sh setup   # apt、Python venv、Rust、开发/测试/lint 工具
  bash adapt/metax/metax-dev.sh build   # Python editable 安装 + Rust/PyO3 本地编译
  bash adapt/metax/metax-dev.sh model   # 下载固定 revision 的 Qwen3-0.6B（可选）
  bash adapt/metax/metax-dev.sh shell   # 进入已激活开发 venv 的交互终端

已有容器停止时先 docker start dynamo-metax-dev，无需重新 create。
可覆盖：CONTAINER_NAME、IMAGE、CACHE_VOLUME、HTTP_PORT、NATIVE_PORT。
USE_MIRROR=0 可关闭本脚本的新镜像源配置；不会移除已有 Cargo 源配置。
本脚本仅重现基础推理开发环境；未安装 aisimulate，也不表示完整 pip check 通过。
启动和验收命令见 adapt/metax/metax-bringup.md。
HELP
        ;;
    *) echo "未知步骤：$1。请运行 bash adapt/metax/metax-dev.sh --help" >&2; exit 2 ;;
esac
