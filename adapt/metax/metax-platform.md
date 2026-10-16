<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MetaX M01 平台契约验收

2026-10-15 完成 M01 第一版：将 MetaX 平台身份、实际设备信息与能力报告贯通 Python provider、Rust runtime、服务发现和 Python 客户端。范围限定在 C500 / vLLM-metax 0.26.0 的聚合文本及 KV 路由开发配置；完整设计及兼容边界见 [M01](../modules/m01-platform-contract/README.md)。

## 当前行为

启动脚本已分别设置 frontend 的 `DYN_PLATFORM=cpu` 和 worker 的 `DYN_PLATFORM=metax`。MetaX 的发现记录包含：

```json
{
  "platform": {
    "schema_version": 1,
    "platform_id": "metax",
    "device_kind": "accelerator",
    "provider_version": "1",
    "software": {
      "driver": "3.8.30",
      "maca_runtime": "3.8.2.6",
      "vllm": "0.26.0"
    }
  }
}
```

这里只截取部分字段。完整记录还有 torch/插件/MACA 构建版本、设备 UUID、可见 ordinal、型号、显存、PCI 和能力条件。MetaX 不再发布 `device_type=cuda`；旧字段省略，Python getter 返回 `None`。继续使用沐曦 torch 的 `torch.cuda` 兼容 API 不改变这个平台身份。

CPU/CUDA 旧记录保持可读；未配置平台的旧调用仍保留兼容推断并输出警告。MetaX 必须使用本分支支持 schema 1 的 frontend 和 runtime，不能只更新 Worker 后沿用旧控制面。

`DYN_REQUIRED_CAPABILITIES` 是额外必需能力的逗号分隔列表，例如 `pd.transfer`。本机目前没有验证 PD，报告为 `unknown`，要求该能力时在创建引擎和注册端点之前失败。KV 路由仍依据事件、缓存块和 Worker ID；平台字段不会触发 CUDA 数据面。

## 手动复现

沿用 [开发环境脚本](metax-dev.sh) 和 [环境安装记录](metax-bringup.md)。本次有 Rust 修改，需在容器内重新编译绑定：

```bash
source /opt/dynamo/venv/bin/activate
unset CONDA_PREFIX
export PATH=/root/.cargo/bin:$PATH
export CARGO_TARGET_DIR=/var/cache/dynamo-metax/target
export CARGO_BUILD_JOBS=16 CARGO_PROFILE_DEV_DEBUG=0
cd /workspace/dynamo/lib/bindings/python
maturin develop --locked --uv
cd /workspace/dynamo
```

重新启动使用旧绑定的服务进程。新开终端，启动两个 KV Worker：

```bash
export MODEL_PATH=/var/cache/dynamo-metax/huggingface/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca
setsid --wait bash adapt/metax/metax-kv-serve.sh
```

服务 ready 后，在另一个容器终端验收：

```bash
source /opt/dynamo/venv/bin/activate
python adapt/metax/metax-platform-smoke.py \
  --model-path /var/cache/dynamo-metax/huggingface/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca \
  --output /tmp/metax-platform-results.json
python adapt/metax/metax-kv-smoke.py --output /tmp/metax-platform-kv-results.json
```

平台验收脚本会读取实际发现记录、检查两个 UUID、验证 PyO3 配置入口的幂等/拒绝行为，并额外启动一个要求 `pd.transfer` 的失败用例。失败用例默认探测 GPU 3，不加载模型；可用 `--probe-gpu` 修改。KV 脚本会清空和填充测试 Worker 的缓存，应对专用验收服务运行。基础推理回归沿用 [基础脚本](metax-smoke.py) 和独立 8000 端口服务。

## 验收证据

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 两个 Worker 的身份 | `metax` / Accelerator，UUID 不同，可见 ordinal 均为 0；旧 `device_type` 均为空 | [平台结果](metax-platform-results.json) |
| 软件栈与设备容量 | 驱动 3.8.30、MACA runtime 3.8.2.6、vLLM 0.26.0；单卡 68,283,269,120 字节 | 同上，逐 Worker 完整描述 |
| 配置与拒绝行为 | 重复相同配置成功；未知 schema/冲突拒绝；必需 PD 能力 unknown 时退出码 1，未写任何发现文件 | 同上 |
| 基础推理回归 | 非流式、流式及取消后的恢复通过 | [基础结果](metax-platform-basic-results.json) |
| KV 路由回归 | 真实 Stored/Removed/Cleared 事件、前缀选择、淘汰、清空后索引归零及重新命中通过 | [KV 结果](metax-platform-kv-results.json) |

平台结果记录基线 HEAD 与实际变更源码 SHA256；本次工作未提交时，HEAD 本身不包含这些改动。原有基础/KV 验收 JSON 保留历史结果，本次单独保存回归结果。基础结果来自现有 basic smoke；与本页同次重新启动服务的版本组合对应。

软件测试：Rust 契约 3 项、runtime 平台 3 项、discovery 68 项、设备感知路由相关 3 项通过；Python 平台与既有 vLLM 工厂/调度器测试 290 项通过，Backend SDK 绑定测试 23 项通过。

```bash
cargo test -p dynamo-platform --locked
cargo test -p dynamo-runtime --lib platform_ --locked
cargo test -p dynamo-runtime --lib discovery:: --locked
cargo test -p dynamo-runtime --lib device_ --locked
python -m pytest -q components/src/dynamo/common/platform/tests \
  components/src/dynamo/vllm/tests/test_vllm_worker_factory.py \
  components/src/dynamo/vllm/tests/test_vllm_instrumented_scheduler.py
python -m pytest -q components/src/dynamo/common/backend/tests/test_backend_bindings.py
```

KV 回归首次发现清空端点的异步发现尚未收齐，验收脚本已改为等待两个目标 Worker 都出现再发送 direct 请求。

## 未覆盖内容

- CUDA 只验证软件契约、旧描述和选中 provider 的隔离行为，未进行 NVIDIA 真机推理回归；Ascend provider 尚未实现。
- PD、设备 RDMA、KVBM、GMS、功耗控制均保持 unknown；没有因镜像中存在 NIXL/Mooncake 就声明支持。
- 当前显式引擎接入限 TP=1/DP=1、eager 聚合文本；其他模型、并行配置和高级功能需独立验证。
- 当前基于 Docker 与文件服务发现，没有验收 Kubernetes 平台注入、设备资源映射或多机部署。
