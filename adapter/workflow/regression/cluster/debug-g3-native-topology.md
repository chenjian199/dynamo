<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# G3 路由与原生引擎拓扑预检排查

日期：2026-10-29。来源：用户反馈及 2026-10-28 集群回归。

## 范围

- CUDA G3：Qwen3-0.6B、两个 TP1 worker、H800、冻结 CUDA runtime / vLLM 0.28.0。
- 原生 TP/PP/DP、headless 和 eager 参数不应被平台适配层的历史验收白名单拦截。
- Ascend Qwen3-32B TP4、CUDA DeepSeek 并发确定性均在原生引擎可复现，按用户要求记录、暂缓。

## G3 复现

原始用例为 `render-cache-system.py --platform cuda --case routing-disk --repeat 3 --full-logs`。
目标前缀有 640 token / 64 = 10 个完整块；GPU/CPU/disk 池分别为 32/24/128 块。
先验证 G1=10，再发送四组不同前缀制造逐出，等待 G1=G2=0、G3>=10，最后验证选路与回载。

2026-10-29 `cache-disk-debug-1029` 在未改变生产代码的镜像中再次失败：G3=9/10，
与前一日的 8/10 不同。不是固定阈值写错；暂未证明是块未落盘还是事件链缺失。
首次诊断的 RUST_LOG 未生效：Dynamo 使用 DYN_LOG，且测试 harness 原来覆盖该变量。
已让 harness 保留调用者 DYN_LOG，另建 `cache-disk-trace-1029` 获取具体事件。
原始日志及清单位于 `/var/tmp/dynamo-regression-1028/`，旧结果保持不变。

## 原生参数限制

已发现并移除普通引擎路径的三个重复限制：

1. provider 将 TP/DP 的 1/2/4 和 eager 模式作为引擎是否可用的条件。
2. preflight 拒绝 PP、headless、embedding、非本地 multiprocessing DP。
3. 将全局 TP×DP 与本进程设备数量比较，以及有效引擎配置生成后再次拒绝 PP。

引擎能力现在表示厂商后端已经安装，具体模型与拓扑合法性仍由 vLLM 初始化验证。
新增 PP、节点数、headless 和本地 DP 配置记录；保留设备探测、版本校验、GMS 已知缺失能力、
KV 事件 scheduler 契约及 Dynamo PD/KVBM 专属连接器约束。
普通参数获准进入原生引擎不代表 TP8/PP 大模型实机验收已经通过。

## 验证记录

- 平台单元测试：68 passed，涵盖 CUDA/MetaX/Ascend 下 TP8、跨节点 TP16、PP2、headless、
  本地/全局 DP 不同、external/hybrid/Ray DP、非 eager 参数透传。
- 有效引擎配置的 PP2 检查：通过。
- CUDA 0.28.0 容器中的平台、生命周期、scheduler、PD 契约测试：102 passed。
- CUDA 两节点 TP2×PP2 + headless：Qwen3-0.6B，重复请求、长前缀 KV 命中、SSE、4 次取消及恢复通过。
- CUDA 单机 TP8：Qwen3-0.6B，同一 API 检查通过。
- 上述拓扑测试只读挂载三个候选 Python 文件，未更改 vLLM、原生库或镜像；属于开发补丁验证。
  不能替代新镜像验收，也不能推广至 Ascend DeepSeek TP8、其它模型或全部连接器组合。

## G3 定位结论

`cache-disk-trace-1029` 的完整事件日志确认：原前缀在 G2 注册 10 块，仅 8 块产生 G3 STORE。
缺失的两块为 `3131473864305231187` 和 `15420825580640916334`：
02:28:16 UTC 注册于 HostPinned，02:28:21 UTC 在 `acquire_free_block` 中被回收，未出现对应磁盘 STORE。
路由器看到的 8 块与已发布的磁盘块一致，不能将这次现象归因为路由器丢了已落盘的两块。

`lib/llm/src/block_manager/offload/request.rs` 明确规定卸载队列持弱引用，允许源块在压力下复用；
`offload.rs` 只能继续卸载仍可从源池找到的块。磁盘空间足够不构成卸载完成保证。
原用例在仅确认 G1 命中后就施加压力，测试前置条件不足。
此外 `get_overlap_scores` 的各层块数包含前面层级已覆盖的前缀：G1=10 时的 disk_blocks=10
不能用来证明磁盘实际已有 10 块。

修正仅涉及测试 harness：在发送逐出请求前，等待该 worker 的 Host→Disk 完成日志累计 10 块。
此前该 worker 只有一个完整目标前缀，chat 和另一 worker 的预热请求均不足一个块。
没有增加池容量、减小期望块数或修改 Rust 路由实现。
`cache-disk-barrier-1029` 连续三轮通过：G1/G2=0、G3=10、credit=2.5、自动选中缓存持有者、
cached_tokens=640、输出一致，并有 Disk→Device 传输和回载字节校验。

这是“已落盘缓存的路由与回载”验收；持续过载时的卸载覆盖率与性能仍是另外的指标，
不承诺所有排队块必然落盘。原失败结果保留。

结果与原始清单/日志归档在 `results/followup-1029/`；`evidence-index.json` 记录 16 份原始文件的 SHA256。
本轮新建的服务 Deployment 及测试 etcd 已缩容至 0，无运行中的回归 Pod；保留完成/失败的 Job 与 ConfigMap。
