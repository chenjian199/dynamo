<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 华为 DeepSeek 补充回归（2026-10-29）

当前状态：基础 TP8/EP API 功能检查通过；严格确定性失败且原生引擎复现。MTP 功能及 30 分钟 API 长稳通过，严格确定性保留为原生复现暂缓项。

使用 7.5 / Kubernetes `master` 的 8 张 910B3，宿主机驱动 25.2.0；
7.7 运行本轮专用 etcd、frontend 和客户端。模型为本地只读
`/data/DeepSeek-V4-Flash-w8a8-mtp`，70 分片、300013759966 bytes，
全部索引分片存在；仅对配置/tokenizer/量化描述/索引计算 SHA256，未全量校验权重内容。

镜像保持 `docker.io/heyi/dynamo-vllm-ascend-runtime:1.5.1-m02`，
vLLM `0.23.0+empty` / vLLM-Ascend `0.23.0` / torch-npu `2.10.0.post4`。
本轮三个候选 Python 文件通过 immutable ConfigMap 只读覆盖安装路径：
`common/platform/{preflight,providers}.py`、`vllm/platform.py`。
这是取消普通引擎拓扑白名单后的源码验证，不是新镜像验收。

基础配置：TP8 / DP1 / PP1、EP、Ascend W8A8、eager、DSA-CP、FlashComm1、
4096 上下文、并发上限 4、prefix cache 关闭、block size 128、显存利用率 0.9。
先不开 MTP，基础通过后再开启单 token MTP。CPU/主存上限 32 / 256 GiB。
前端 round-robin，发现 etcd，请求 TCP，事件 ZMQ。

[官方 v0.23.0 教程](https://docs.vllm.ai/projects/ascend/en/v0.23.0/tutorials/models/DeepSeek-V4-Flash.html)
提供 A2 8 卡 W8A8 配置。网页当前镜像例子已更新到 post1；本轮不升级引擎，
以锁定镜像实际支持的配置为准。TP8 预检已通过并进入 EngineCore 初始化，尚待请求验证。

复现（先确认所列节点有空闲 8 卡，并已有 regression-tests ConfigMap）：

```bash
python3 adapter/m02/cluster/render-ascend-deepseek.py > /tmp/ascend-deepseek.json
kubectl apply -f /tmp/ascend-deepseek.json
python3 adapter/m02/cluster/render-client.py --platform ascend \
  --name ascend-deepseek-api-base --prefix ascend-deepseek --uncached \
  > /tmp/ascend-deepseek-api.json
kubectl apply -f /tmp/ascend-deepseek-api.json
```

本轮先完成华为 DeepSeek，再推进 M08/M09，最后构建新镜像执行 M10。
原生引擎已复现的输出确定性问题和既有明确暂缓项保持单列，不能算作通过。


## 基础阶段已取得的证据

- 7.5 的基础模型加载完成，每 rank 权重约 38.1255 GiB，KV 预算约 15.89 GiB。
- `ascend-deepseek-api-base`：6 次 greedy 文本续写有 3 种输出，严格用例失败。
- `ascend-deepseek-functions-base`：SSE 结束、4 次取消及后续请求、32/2048 token 的并发 1/4/8（共 32 个阶梯请求）及 chat 算术回答通过。
  阶梯使用人工 token IDs，验证长度、排队和传输，不是语义质量基准；chat 正确输出 `4`。
  SSE 与非流式内容不一致。该早期报告的 `deterministic` 字段仅汇总阶梯，不能推广到 SSE/文本续写；后续脚本已将字段明确为 `ladder_deterministic`。
- 7.7 的 `native-ascend-deepseek.py` 直接调用冻结 vLLM LLM，未加载 Dynamo。
  6 个串行单请求出现 2 种 token 序列；4 组 batch=4 后共 5 种。表明不经过 Dynamo 也能复现确定性问题。
  7.7 驱动为 24.1.rc3，不能据跨节点对照证明两条路径的数值误差完全相同，更不能归因为某个驱动版本。
- 短负载预检（与最后的阶梯测试有重叠）：16 个计时请求约 42.05 秒，并发 4。
  在正式运行前固定 1800 秒且至少 512 个成功请求，保守低于预检外推约 685 个；不是性能 SLO。
  `--observe-output-parity` 仅分离 API 稳定性与确定性，输出变体完整记录；默认脚本仍严格检查。
- 最初 4 个资源快照未找到旧 ConfigMap 中的 resources.py；补充该测试脚本后恢复采样。
  采样缺口保留，不能声称从负载第一秒开始连续观测。

MTP 使用 7.7 的同路径既有 NFS 权重（源 7.5），配置保持 TP8/EP，增加 1-token `mtp`。
两节点不同驱动/存储路径，不据两者时间差判断 MTP 加速比。


## MTP 功能与自然语言补测

7.7 上单 token MTP 已运行，SSE 正常结束、4 次取消后恢复、6 组长度/并发阶梯及 chat 算术测试通过。
32 个阶梯请求的文本与无 MTP 基线一致；原始自然语言 completion 的 SSE/非流式仍有确定性差异。
采样指标 `spec_decode_num_draft_tokens_total=1052`、`spec_decode_num_accepted_tokens_total=1044`，
证明 MTP 真正参与生成。该统计包含大量人工重复 token 输入，不能作为真实业务接受率或加速比。

额外对基础/MTP 分别发送 4 类自然语言问题，各重复 3 次、并发 4；
两边均准确返回 `42`、`Paris`、`北京`、`1, 2, 3`，共 24 个请求通过。
只是简单语义 smoke，不是模型质量评测。结果见 `ascend-deepseek-chat.json`。

MTP 结束时 `num_requests_running`、`num_requests_waiting`、`kv_cache_usage_perc` 均为零；
已缩容其 worker/frontend 并释放设备，保留测试结果。基础配置随后完成长稳。

本轮未测试 DeepSeek 混合注意力专用 PD/KVBM connector、跨节点 TP16 或 1M 上下文。
此前 Qwen 的跨节点 PD/KVBM 结果不能自动推广为这些 DeepSeek 组合也通过。


## 其余复现命令

基础实例 ready 后：

```bash
python3 adapter/m02/cluster/render-client.py --platform ascend \
  --name ascend-deepseek-functions-base --prefix ascend-deepseek --kind deepseek \
  > /tmp/ascend-deepseek-functions.json
kubectl apply -f /tmp/ascend-deepseek-functions.json
python3 adapter/m02/cluster/render-client.py --platform ascend \
  --name ascend-deepseek-load-30m --prefix ascend-deepseek --kind load \
  --seconds 1800 --minimum-requests 512 --observe-output-parity \
  > /tmp/ascend-deepseek-load.json
kubectl apply -f /tmp/ascend-deepseek-load.json
```

确认 7.7 上原生对照 Job 已结束并释放 8 张卡后：

```bash
python3 adapter/m02/cluster/render-ascend-deepseek.py \
  --prefix ascend-deepseek-mtp --mtp --node-index 0 > /tmp/ascend-deepseek-mtp.json
kubectl apply -f /tmp/ascend-deepseek-mtp.json
# 等待模型注册 ready，再提交功能客户端：
python3 adapter/m02/cluster/render-client.py --platform ascend \
  --name ascend-deepseek-functions-mtp --prefix ascend-deepseek-mtp --kind deepseek \
  > /tmp/ascend-deepseek-functions-mtp.json
kubectl apply -f /tmp/ascend-deepseek-functions-mtp.json
```

每轮使用新的 prefix / Job 名称，或者归档后清理旧同名资源；候选源码 ConfigMap 是 immutable，
不能覆盖不同候选内容。原生对照、自然语言检查的本轮精确执行清单保存在证据归档内。
所有模型均只读使用现有权重；没有下载权重、升级引擎或修改宿主机驱动。


## 最终结果与阶段边界

30 分钟负载完成：1803.570 秒、864 个计时请求（不含预热）、并发 4，HTTP 请求无错误。
输出存在多种变体，`deterministic=false`，不把该项记为确定性通过。
worker 全程未重启；结束后运行请求、等待请求和 KV 占用全部为零。

资源观察包含 33 个有效快照、4 个开头缺失快照，有效跨度约 1668 秒；
18 个进程身份稳定，文件描述符总数峰值 2001、结束空闲时 1997。
总 RSS 增量约 16.1 MiB，线程数无增长；此为有界、部分窗口的进程观察，
不是完整 30 分钟原生句柄无泄漏认证，也不等于物理总内存占用（RSS 可能重复计共享页）。

基础/MTP 的本轮 worker/frontend 与专用 etcd 已缩容到零，完成/失败 Job 和配置保留。
本轮未修改生产引擎代码、镜像或驱动，也未提交 Git commit。
M01–M07 可在既有明确暂缓项下阶段性收口，不声明全部组合全通过；
下一步按用户顺序进入 M08 集群验收、M09 遥测/Planner，最后统一构建镜像并完成 M10。

结果入口：[`results/ascend-deepseek-1029/summary.json`](results/ascend-deepseek-1029/summary.json)。
同目录保留各客户端、原生引擎结果与 provenance；`evidence.tar.gz` 和 `evidence-index.json`
归档本轮精确清单、ConfigMap 源码、日志、指标、候选差异与资源快照及 SHA256。
