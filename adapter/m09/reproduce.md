<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 回归复现

本轮复用 M08 已安装的 Operator、CRD、RBAC、平台 ConfigMap 和工具镜像。所有对象位于 `dynamo-m08-1029`，仅创建 `m09-*` 对象及本轮 DGDR 所有的临时候选；不升级驱动，不修改推理镜像中的 vLLM。运行需要对应 namespace 的创建/删除权限。先完成 [M08 安装](../m08/cluster/README.md)。

## 节点与依赖

| 平台 | 控制/工具节点和目录 | 大模型 worker |
| --- | --- | --- |
| CUDA | worker06 / 4.6，`/home/bedicloud/CJ_workspace/m09-1029` | Qwen：worker28 / 4.14；DeepSeek：worker06 |
| Ascend | master-02-45d4a763 / 7.7，`/root/CJ_workspace/m09-1029` | Qwen：master / 7.5；DeepSeek：两节点各一个 TP8 副本 |

各 worker 节点须具有相同的源码路径和渲染后的模型 hostPath。渲染默认使用 `workdir/source`；本次正式 Ascend DeepSeek 为保留运行中的旧快照改用 `workdir/source-v7`，以保存的 manifest 为准。不要将 CUDA amd64 依赖复制到 Ascend aarch64。`provenance-*.json` 记录实际镜像 ID、引擎/插件、驱动、SDK 和模型配置哈希；Ascend 两节点驱动不同，按用户要求保留实际配置。

M08 Profiler 工具镜像尚缺 Planner 的 sklearn/prophet 等依赖。本轮通过独立 `/m09-deps` 目录补齐，未向 inference site-packages 安装包。复现时在**对应架构、相同工具镜像**内执行：

```bash
# platform 为 cuda 或 ascend；requirements 文件须先复制进工具容器。
/opt/dynamo/venv/bin/python -m pip install --no-deps \
  --target /tmp/m09-python -r requirements-planner-${platform}.txt
```

将 `/tmp/m09-python` 内容复制到该平台工具节点的 `${workdir}/deps`。挂载后的 PYTHONPATH 顺序是 `/m09-deps:/m09-source/components/src:/opt/dynamo/profiler-support`。两个 requirements 文件记录了本次实际使用的新增依赖版本，基础 numpy/scipy/pandas 等来自固定工具镜像。CUDA 本轮下载较慢，实际从同架构已装依赖的 CPU 测试容器提取这些包；M10 应将它们安装进最终工具镜像并检查依赖闭包。

## 渲染与启动

以下在代码仓库根目录渲染；随后把 JSON 和运行脚本复制到目标控制节点。`workdir` 是目标节点路径，不是本地源码路径。镜像必须已导入该节点的 Kubernetes containerd。

```bash
platform=cuda
workdir=/home/bedicloud/CJ_workspace/m09-1029
python3 adapter/m09/package.py --output /tmp/m09-source.tar.gz
python3 adapter/m09/render.py --platform "$platform" --workdir "$workdir" --tools > /tmp/tools.json
python3 adapter/m09/render.py --platform "$platform" --workdir "$workdir" > /tmp/small.json
python3 adapter/m09/render.py --platform "$platform" --workdir "$workdir" --sla > /tmp/sla.json
python3 adapter/m09/render_metrics.py --platform "$platform" > /tmp/metrics.json
```

在目标节点解压源码到 `${workdir}/source`；同一平台第二节点也要同步。Profiler 使用独立 `${workdir}/profiler-source`，避免正在运行的 worker 看到中途修改。准备好 deps 后：

```bash
kubectl apply --dry-run=server -f tools.json
kubectl apply -f tools.json
kubectl apply -f metrics.json
kubectl apply --dry-run=server -f small.json
kubectl apply -f small.json
python3 campaign.py --name m09-small --load-script load.py --output small-final
python3 provenance.py --name m09-small --output provenance-small.json
python3 collect.py --workdir "$workdir" --run small-final
```

`campaign.py` 等待 DGD 当前 generation 和全部组件 ready，启动负载并观察 Planner，**不设置副本数**。每 5 秒保存副本/Pod 状态，并跟随每个 worker 日志，保留被缩容删除的实例证据。负载完成后等待回到 1 个 ready worker、无 terminating Pod；全量退出释放在最后删除 DGD 后另查。

SLA 检查使用 `sla.json` 和 `--name m09-sla --minimum-peak-ready 1 --osl 128 --duration 240 --recovery 60`。这里允许 HOLD；必须结合 Planner 非零 ITL 预测和客户端延迟检查，不能仅凭副本不变宣称 SLA 生效。CUDA 使用本轮专用 Prometheus；Ascend 使用现有 Prometheus 和范围限定的 PodMonitor。命名、端口和 URL 都在渲染脚本中。

## 大模型

在仓库渲染 Qwen3-32B：

```bash
python3 adapter/m09/render_large.py --platform cuda \
  --workdir /home/bedicloud/CJ_workspace/m09-1029 --model qwen32 \
  --worker-node worker28 --worker-model-path /root/Qwen3-32B > /tmp/cuda-qwen32.json
python3 adapter/m09/render_large.py --platform ascend \
  --workdir /root/CJ_workspace/m09-1029 --model qwen32 \
  --worker-node master --worker-model-path /data/Qwen3-32B > /tmp/ascend-qwen32.json
python3 adapter/m09/render_large.py --platform "$platform" \
  --workdir "$workdir" --model deepseek > /tmp/deepseek.json
```

Qwen 两平台 TP4。DeepSeek CUDA TP4、FP8 KV；Ascend TP8/EP、W8A8、DSA-CP、FlashComm1，使用已验证原生参数。模型路径和完整参数在 JSON 中，不下载权重。先释放上一个测试占用的设备，再 apply 并运行：

```bash
python3 campaign.py --name m09-qwen32 --load-script load.py \
  --output qwen32-final --isl 1024 --osl 128 --duration 600 --recovery 90
# DeepSeek 将 name/output 改为 m09-deepseek/deepseek-final，追加 --token-id-metrics。
# CUDA DeepSeek 使用 --duration 900，为第二个副本的首次 JIT 预热留出时间。
```

小模型最多 3 卡；Qwen 最多 8 卡；CUDA DeepSeek 最多 8 卡；Ascend DeepSeek 最多 16 卡。正式回归 KV 扩容阈值均为 12%，缩容均为 2%；用于功能触发，不是生产最优参数。扩容后的第二副本须真正 ready；仅 desired=2 不算扩容通过。

脚本使用合成 token 输入，验证调度和完整响应，不验证回答质量；此前原生引擎确定性问题继续保留。CUDA DeepSeek 首轮出现完成 128 token 却无可见文本的情况，原始 SSE 和失败记录保留。DeepSeek 正式运行请求 `nvext.extra_fields=["completion_token_ids"]`，按实际 token 到达时间计时，并要求流中的 token ID 数量与 usage 相等；单独统计空文本次数。该指标不能冒充可见文本 TTFT，也不能证明回答质量。小模型/Qwen 保留原可见文本计时口径。

## Profiler 与画像

先通过 `provenance.py` 从小模型 worker 取证，然后：

```bash
python3 profile_identity.py --provenance provenance-small.json \
  --device-model 'NVIDIA H800' --output profile-identity.json
# Ascend 使用 Ascend910B3。
mkdir -p "$workdir/profile-output"
chown 1000:1000 "$workdir/profile-output"
```

在仓库运行 `render_profiler.py --platform "$platform" --workdir "$workdir"`，将产物复制到目标节点 apply。Ascend output-copier 显式设置 `K3S_DATA_DIR=/tmp/m09-k3s`，让旧工具镜像中的 K3s kubectl 可按 UID 1000 解压；M08 配方已有目录修复，但本次复用的旧镜像尚未包含它。现场这次通过在该 copier 容器内补建并授权默认目录恢复；没有改宿主机 K3s 或 NPU 驱动。后续直接使用渲染器中的环境变量配置。

使用 `thorough`，枚举 prefill/decode × TP1/TP2 共 4 个实测候选；`autoApply=false`，不会部署未经审阅的最终推荐。身份文件是显式 provenance，模型配置 SHA 不是整个权重目录的哈希。容量 YAML 只用于搜索可行性，不冒充硬件性能数据库。

等待 `profile-m09-profile` Job 终止后，在该节点收集：

```bash
python3 collect_profiler.py --source "$workdir/profile-output" \
  --output "$workdir/export/profile" --archive "$workdir/archives/profile.tar.gz"
python3 check_profile_exports.py --directory "$workdir/export/profile/profiler" \
  --output "$workdir/export/profile/verification.json"
```

检查每个候选配置与结果的 SHA256、AIPerf 完成/取消/错误状态。完整原始请求与 CSV 位于 archive；仓库保留 JSON、配置和日志。`benchmark-results` 类型不能传给 Planner 的 FPM bootstrap 读取器。版本不匹配、过期、文件被修改的旧画像必须拒绝。

## MetaX 本机容量兼容检查

本轮使用已有 Docker volume 中的模型，没有下载权重。以下是本次启动参数；复跑前确认同名容器未占用，停止的原容器可保留为证据并换一个名称。

```bash
docker run -d --init --name dynamo-m09-metax-capacity \
  --device=/dev/dri --device=/dev/mxcd --shm-size=8g --ulimit memlock=-1 \
  -p 127.0.0.1:18090:8000 \
  --mount type=bind,src=/home/metax/CJ_workspace/dynamo,dst=/workspace/dynamo,readonly \
  --mount type=volume,src=dynamo-metax-cache,dst=/cache,readonly \
  -e PYTHONPATH=/workspace/dynamo/components/src -e CUDA_VISIBLE_DEVICES=3 \
  -e MODEL_PATH=/cache/huggingface/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca \
  -e DYN_NAMESPACE=m09-metax-capacity \
  -e _PROFILE_OVERRIDE_VLLM_KV_CACHE_BYTES=536870912 \
  --entrypoint /bin/bash heyi/dynamo-vllm-metax-dev:1.5.1-m02 \
  /workspace/dynamo/adapter/platforms/metax/metax-serve.sh
curl --fail --silent --show-error http://127.0.0.1:18090/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-0.6B","prompt":"The capital of France is","max_tokens":32,"temperature":0}'
docker logs dynamo-m09-metax-capacity
docker stop --time 40 dynamo-m09-metax-capacity
mx-smi
```

等 `/v1/models` 就绪后调用 completions。检查非空文本、usage，及 MDC 中 `runtime_config.runtime_data.kv_cache_capacity` 的原生容量与引擎日志一致。本次为 4672 token；实际 API、MDC、版本和源文件哈希在 `results/metax/capacity/`，不是集群 Planner 认证。

## 结果与清理

每个运行用 `collect.py` 保存全量归档及 SHA256，再同步 `export/` 到本地 `results/<platform>/`。`summarize.py --results adapter/m09/results` 生成总表。不要删除已有失败尝试或重写其测量数据。

完成取证后逐个删除本轮 `m09-*` DGD、DGDR、tools、metrics 对象；不得删除整个 namespace 或已有 M08 Operator。检查本轮 worker Pod 已消失、GPU/NPU 进程和设备内存已释放。保留宿主机 source、deps、archives 便于复核。源码覆盖的验收不能替代 M10 新镜像验收。


仅清理本轮对象（在各集群控制节点执行，先完成结果归档）：

```bash
kubectl -n dynamo-m08-1029 delete dgd m09-small m09-sla m09-qwen32 m09-deepseek --ignore-not-found
kubectl -n dynamo-m08-1029 delete dgdr m09-profile --ignore-not-found
kubectl -n dynamo-m08-1029 delete pod m09-tools --ignore-not-found
# 使用本平台 render_metrics.py 生成的文件；Ascend 只删除本轮 PodMonitor。
kubectl delete -f metrics.json --ignore-not-found
kubectl -n dynamo-m08-1029 get pods -o wide
# CUDA 各 worker 节点检查：
nvidia-smi --query-gpu=index,uuid,memory.used,memory.total --format=csv
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv
# Ascend 各 worker 节点检查：
npu-smi info
```

如仍有其他业务进程，不得为达到“空设备”结果而停止它们；确认本轮 Pod/worker 已消失并记录实际剩余占用。
