<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 扩展验收复现说明

本目录清单固定本次节点、模型路径与版本。换集群先替换这些输入。不要把测试 namespace 的授权外推到其他业务 namespace；DRA 隔离步骤尤其不能省略。

## 版本和安装

- Grove Chart/operator/initc：`v0.1.0-alpha.13`。
- KAI Scheduler Chart/镜像：`v0.17.0`，Queue `default-queue`；只选取带 `heyi.io/kai-test=true` 标签的测试 namespace。
- NVIDIA DRA Chart/driver：`v0.5.1`，仅在隔离后的 worker28 启用 GPU 资源；ComputeDomain 关闭。
- Operator：本轮测试标签为 `heyi/dynamo-operator:1.5.1-m08-dgdr-amd64` / `arm64`。保留原 M08 镜像，仅替换重新编译的 `/manager` 与再生成的 DGDR CRD；没有修改运行时依赖。
- 推理 Worker：M02 CUDA/Ascend runtime，vLLM `0.28.0` / `0.23.0+empty`。

完整 Operator 构建仍使用 [原构建脚本](../build-operator.sh) 和 `deploy/operator/Dockerfile`，本轮新增 SKU 已在源类型中定义。重新构建前运行 `make manifests` 和 Pydantic 生成器，不手改生成文件。安装新标签时对已有 `heyi-m08` release 执行 Helm upgrade，不再安装第二个全局 Operator。

Grove/KAI 安装值见 `manifests/<platform>/{grove-values,grove-kai-values,kai-values}.json`。Grove 初装为 default-scheduler，然后启用 KAI，避免把默认调度器启动成功当作 gang 验证。以下是已有依赖安装完成后的回归命令，工作目录需有 `../cluster/` 脚本和 `adapter/m02/cluster/client.py`：

```bash
kubectl apply --dry-run=server -f pd-cross.json
kubectl apply -f pd-cross.json
python3 regress.py --name m08-pd-cross --mode pd --client client.py --output evidence-advanced/pd-cross
python3 pairs.py --name m08-pd-cross --client adapter/m02/cluster/topology.py --output evidence-advanced/pd-cross

kubectl apply -f grove.json
python3 regress.py --name m08-grove --client client.py --output evidence-advanced/grove

python3 gang.py --manifest grove-kai.json --output evidence-advanced/grove-kai/gang
python3 regress.py --name m08-grove-kai --client client.py --output evidence-advanced/grove-kai/regression
```

`gang.py` 占用 7/8 张设备，只剩一张时连续检查两个 gang Worker 均未调度；释放占位 Pod 后再执行正常推理、扩缩容与重建。占位 Pod 有截止时间。原始等待证据和正常回归分别存放在 `grove-kai/gang/` 与 `grove-kai/regression/`。

CUDA DRA 用 [dra-cuda.sh](dra-cuda.sh)，先阅读脚本中的节点、模型和镜像路径。它要求测试节点没有其他占卡工作负载，并临时禁用该节点 Device Plugin，防止 DRA 与插件各自分配同一物理卡。结束后必须确认 Claims 清理及节点标签/taint 恢复。

## 独立 Profiler 镜像

[profiler.Dockerfile](profiler.Dockerfile) 和 [profiler-ascend.Dockerfile](profiler-ascend.Dockerfile) 是测试 Profiler 的构建配方，不是 Worker 的推理环境。Ascend 的 Profiler 使用 CPU，设置 `TORCH_DEVICE_BACKEND_AUTOLOAD=0`；不载入宿主机 NPU 后端。新增工具依赖不会进入已锁定的 Worker 镜像。

构建 context 需要：

- 再生成的 `components/src/dynamo/profiler/utils/dgdr_v1beta1_types.py`；
- `components/src/dynamo/profiler/utils/{dgd_platform,dgd_materialization}.py` 与 `config_modifiers/vllm.py`（复制到 context 根目录）；
- `deploy/utils/`（context 下为 `deploy/utils/`）；
- 对应架构的 `kubectl`，CUDA 配方还复制 `dgd-apply-overrides`；控制器运行时也会通过 init container 提供该工具；
- 本目录 `patch-aiperf.py`；CUDA 的 `wheels/` 中需要 pandas/pyarrow wheel，精确名称与 SHA256 见 `profiler-wheels.json`。

```bash
docker build -f profiler.Dockerfile -t <profiler-test-image> <context>
docker run --rm --entrypoint /opt/dynamo/venv/bin/python \
  <profiler-test-image> -m dynamo.profiler --help
docker save <profiler-test-image> | ctr -n k8s.io images import --platform linux/amd64 -
# K3s/ARM64 使用 k3s ctr 与 linux/arm64。
```

如果使用代理，只给构建传递 `HTTP_PROXY/HTTPS_PROXY` build args，不更改宿主机全局 Docker 配置。保留构建日志和 `pip freeze`，并核对 Worker 的 vLLM 版本仍不变。

现场 CUDA 使用 `heyi/dynamo-profiler-cuda:1.5.1-m08-advanced3`，Ascend 使用 `heyi/dynamo-profiler-ascend:1.5.1-m08-advanced`。测试中通过只读 `m08-profiler-source` ConfigMap 注入最新 `dgd_platform.py`、`dgd_materialization.py` 、`config_modifiers/vllm.py`、`deploy/utils/dynamo_deployment.py` 和修复后的 AIPerf tokenizer；CUDA Worker 另挂 `m08-preflight-source`，使用 HEAD 已修复的预检查。最终 M10 镜像应直接打包这些源代码与经过版本检查的补丁，不能漏掉挂载所覆盖的代码。

`patch-aiperf.py` 只接受 AIPerf 0.13.0 和预期源代码形状。修复离线模式下已有本地 tokenizer 目录被传给 `snapshot_download(repo_id=...)` 的错误；不存在的目录和远程模型仍走原分支。容量 YAML 仅填写 H800 80 GiB 或 910B3 64 GiB，没有复制 H100 的性能数据库。

DGDR 清单见 `manifests/<platform>/dgdr.json`，运行前准备它引用的 ConfigMap，并核对每个只读挂载的源码 SHA256。通过以下检查才算自动部署通过：

```bash
# 清单中的 profiling-output hostPath 在 Profiler 节点预建：
install -d -m 0770 -o 1000 -g 1000 <本次工作目录>/dgdr-output-final
kubectl apply --dry-run=server -f dgdr.json
kubectl apply -f dgdr.json
python3 check-dgdr.py --client client-dgdr.py --output evidence-advanced/dgdr
```

检查要求真实 Job → DGD、控制器关联标签与 status.dgdName、Ready、重复输出一致、SSE、四次取消后恢复。Decode 覆盖项中的 `DYN_HEALTH_CHECK_PAYLOAD` 必须保留：它让无远端 KV 交接的本地健康探针执行一次真实 forward；不改变正常请求的 PD 校验。成功也不等于 SLA 预测模型或 M09 Planner 已认证。`thorough` 的原始 AIPerf 输出要另行保留，不能用 dry-run 或伪造 ConfigMap 代替画像。

## 暂缓范围

Ascend DRA 等待用户提供 Kubernetes ≥1.34 测试集群。接受 HAMi 实验整卡驱动不代表已经完成认证；新集群需重新核对版本对应的 DRA API、feature gates、CDI、驱动兼容与资源回收。当前九节点 K3s 不升级。MetaX 无可用集群，继续保留待验证状态。

完整 Profiler 原始输出在删除 DGDR 前归档（在 Profiler 所在节点执行）：

```bash
python3 collect-dgdr.py --output evidence-advanced/dgdr \
  --archive profiler-m08-dgdr.tar.gz --source <本次工作目录>/dgdr-output-final
```

该脚本只读取此 Job 的持久 output 目录（默认模式也支持仍存在的 emptyDir）。Kubelet 会在容器退出后回收 emptyDir，因此本轮使用独立 hostPath 保留数据；非 root Profiler 的 UID/GID 为 1000，预建目录必须匹配。脚本，归档全部输出并记录 SHA256；将各次 AIPerf 汇总、配置、CSV 和日志另行保存到证据目录。不会收集 service-account token 或 Kubernetes Secret。

清理时先保存 Job 日志与输出，再分别删除 DGDR 和 `status.dgdName` 指向的最终 DGD。最终 DGD 按上游设计不设置 DGDR ownerReference，以便独立管理；删除 DGDR 并不自动删除它。

## 验证工具与源码交付

- `summarize.py`：两平台跨节点 2P3D、恢复与清理。
- `summarize-orchestration.py`：Grove/KAI 的真实等待、扩缩容、重建以及 CUDA DRA 的 Claim 释放和 Device Plugin 恢复。
- `summarize-dgdr.py`：逐项核对实际 AIPerf 完成/错误计数、生成 DGD 的 API 与回收结果。缺少结果会报错，不自动补成通过。
- `package-source.py --output /var/tmp/m08-advanced-source.tar.gz`：导出当前工作树的 M08 源码覆盖包，包含 `SOURCE.json` 逐文件哈希；它应应用到注明的基线仓库，并非独立完整 checkout。排除无关 `reference/`、镜像层及模型文件。

117 项 Python 回归使用独立 CUDA Profiler 容器并挂载本轮修改的 Python 文件，运行 `test_dgd_platform.py`、`test_dgd_materialization.py`、`test_profiler_protocol.py`、`deploy/utils/tests/test_dynamo_deployment.py`。临时测试容器安装 `pytest-asyncio==0.23.8`；`-c /dev/null` 隔离环境导致项目 marker 警告，原始日志保留，未跳过失败测试。

华为现场的 `kubectl` 来自 K3s 多调用程序，需要首次解包。构建配方为 UID/GID 1000 预建 `/.rancher/k3s` 缓存目录；本次已运行的 output-copier 在其自己的容器内补建该目录并调整所有权，然后恢复正常 ConfigMap 回传。没有修改宿主机 K3s 配置、没有修改 NPU 驱动。若换用官方独立 kubectl，仍需以 UID 1000 验证 `kubectl version --client` 和 ServiceAccount API 访问。
