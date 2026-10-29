<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M08 跨节点 RDMA 实测

2026-10-29，在已有 Operator 测试 namespace `dynamo-m08-1029` 执行。此前 [扩展测试](../advanced/README.md) 的 CUDA TCP 证据保留；本轮单独认证指定 RoCE 链路和持久设备缓冲区路径。

## 结果与边界

| 验证项 | CUDA H800：4.6 → 4.14 | Ascend 910B3：7.7 → 7.5 |
| --- | --- | --- |
| Worker 引擎 | 原 M02 runtime / vLLM 0.28.0 | 原 M02 runtime / vLLM 0.23.0 |
| KV 数据传输 | NIXL / UCX / `rc_mlx5`，GPUDirect RDMA | Mooncake / AscendDirectTransport / ADXL，NPU RoCE |
| Operator 2P3D | 六种真实交接组合通过 | 六种真实交接组合通过 |
| API/SSE/取消/恢复 | 两轮通过，8 次取消，Decode 删除重建恢复 | 两轮通过，8 次取消，Decode 删除重建恢复 |
| 持久设备缓冲区 | READ/WRITE、5 种大小、2 轮，20 组，两端逐字节一致 | 同样 20 组，两端逐字节一致 |
| 动态注册/注销压力 | 本轮探针通过 | **原生 ADXL/HCCL 报错，未通过** |

测试模型为 Qwen3-0.6B。设备内存大小为 4 B、4 KiB、512 KiB、4 MiB、16 MiB；校验使用随地址位置变化的 int32 序列，而非只检查首字节。TCP 仅交换探针的指针/元数据和同步消息，不传递张量数据。

[汇总](results/summary.json) 由 `python3 adapter/m08/rdma/summarize.py` 读取原始证据生成。它分别检查 API、六组合、重建、两端内存校验、实际传输协议、NIC/NPU 计数器与资源回收；不会把动态注册压力失败改成通过。

这次不认证全网卡带宽、跨节点 NCCL/HCCL 集体通信性能、生产 SLA、长时间网络故障恢复或 KVBM G4。CUDA 只认证本轮选取的 `mlx5_10:1` / `h3cnic0` RoCE rail。MetaX 无集群，仍暂缓。

## CUDA 的必要 Pod 配置

集群已部署 Network Operator、RDMA shared device plugin、Multus 与 `macvlan-roce0`。本轮只引用现有网络与资源，没有安装新的 CNI、调整宿主机网络或升级驱动。

```yaml
metadata:
  annotations:
    k8s.v1.cni.cncf.io/networks: nvidia-network-operator/macvlan-roce0
spec:
  containers:
    - name: main
      securityContext:
        capabilities:
          add: [IPC_LOCK]
      resources:
        requests:
          nvidia.com/gpu: 1
          nvidia.com/roce_h3cnic0: 1
        limits:
          nvidia.com/gpu: 1
          nvidia.com/roce_h3cnic0: 1
      env:
        - {name: UCX_TLS, value: 'rc,cuda_copy,cuda_ipc,sm,self'}
        - {name: UCX_NET_DEVICES, value: 'mlx5_10:1'}
        - {name: UCX_IB_GPU_DIRECT_RDMA, value: 'yes'}
        - {name: UCX_LOG_LEVEL, value: 'info'}
        - {name: UCX_PROTO_INFO, value: 'y'}
```

这里禁用 UCX 的 TCP 数据传输候选；Dynamo 请求平面的 TCP 和 NIXL 元数据握手仍可使用 TCP。UCX 自动选择 Pod 的 RoCE GID，不能把宿主机 GID index 3 照搬到 macvlan Pod。

首轮失败有两个明确原因，原始日志保留：

1. 未加 `IPC_LOCK` 时，Pod 的 64 KiB memlock 限制导致 `ibv_create_cq` 失败。
2. 仅映射 RDMA 设备、没有附加 RoCE 网络时，`ibv_create_ah` 报 `No such device`。加入现有 Multus/macvlan 网络后通过。

成功日志显示 `inter-node`、`rc_mlx5/mlx5_10:1`，以及 CUDA→CUDA 的 `ucp_get` / `ucp_put` zero-copy；实际 VRAM 字节校验与 HCA 计数增长相互印证。最小 4 B READ 可使用同一 RDMA 通道的软件协议，因此不把每种大小都笼统描述为 zero-copy。

参考 [NVIDIA MacVLAN + RDMA shared device 部署](https://docs.nvidia.com/networking/display/kubernetes2670/quick-start/macvlan-rdma-shared.html)。网络名、HCA 名和扩展资源名属于本集群配置，换集群须重新发现。

## Ascend 的路径与新发现

沿用已验证的原生 Mooncake connector。NPU 的 `hccn_tool -i <device> -stat -g` 中 `roce_tx_rc_pkt_num` / `roce_rx_rc_pkt_num` 随跨节点请求增加；本轮所采集 RoCE 收发错误计数增量为零。独立探针日志还确认注册的是 `mem type:device`，安装 `AscendDirectTransport`，且两端张量全量一致。

这与 [Mooncake Ascend Direct 设计](https://github.com/kvcache-ai/Mooncake/blob/main/docs/source/design/transfer-engine/ascend_direct_transport.md)及 [vLLM-Ascend 多节点 PD 说明](https://docs.vllm.ai/projects/ascend/en/main/tutorials/features/pd_disaggregation_mooncake_multi_node.html)一致；不把 CPU 网卡 `/sys/class/infiniband` 的存在本身当作 NPU RDMA 证据。

另有两个独立压力/工具限制，不能隐去：

- 在 vLLM Worker 中额外启动同卡通信探针，会碰到 NPU 16666 端口占用；仅给探针设置 `HCCL_NPU_SOCKET_PORT_RANGE=auto` 在当前 ADXL 路径仍未消除冲突。最终使用单独 Device Plugin 分配 NPU 的两个测试 Pod，避免同卡多进程干扰。未改动服务进程或驱动。
- `transfer-churn.py` 持续注册、注销并改变地址/大小，实测在完成部分 READ/WRITE 后出现 `HcclDeregisterGlobalMem` / `HcclRegisterGlobalMem` 错误。保留 `byte-registration-churn-failed/`。**这是当前原生库动态注册生命周期的待定位问题，本轮没有修复。**持久注册一个 16 MiB 缓冲区后执行同样大小的读写均通过，末尾注销/引擎释放无上述错误；这与实际 vLLM KV cache 的注册方式相符。

共享进程端口的通用规则见 [华为 HCCL_NPU_SOCKET_PORT_RANGE](https://www.hiascend.com/doc_center/source/en/CANNCommunityEdition/900/maintenref/envvar/envref_07_0144.html)；不能用通用规则代替当前 ADXL 组合实测。

## 复现

在仓库生成清单，核对其节点、网络与模型路径：

```bash
python3 adapter/m08/rdma/render.py --platform cuda --output /tmp/pd-rdma.json
# Ascend 改用 --platform ascend。
kubectl apply --dry-run=server -f /tmp/pd-rdma.json
kubectl apply -f /tmp/pd-rdma.json
python3 adapter/m08/cluster/regress.py --name m08-pd-rdma --mode pd \
  --client adapter/m02/cluster/client.py --output /tmp/rdma-regression
python3 adapter/m08/cluster/pairs.py --name m08-pd-rdma \
  --client adapter/m02/cluster/topology.py --output /tmp/rdma-pairs
```

在四台设备节点分别于负载前后运行 `counters.py --platform cuda|ascend`，保存 JSON；不清零计数器。使用 skill 的 `check_interconnect.py node/nixl` 仅作只读先决条件检查；缺少 in-Pod `ibstat` 或 nixlbench 会报告警告，实际传输由本目录探针验证。

CUDA 可在已就绪的跨节点 Worker 中执行独立探针。Ascend 先创建 [两个独占 NPU 测试 Pod](results/ascend/byte-pod-manifest.json)，再指定名称前缀：

```bash
python3 adapter/m08/rdma/run-transfer.py --platform cuda --output /tmp/cuda-byte
kubectl apply -f adapter/m08/rdma/results/ascend/byte-pod-manifest.json
kubectl -n dynamo-m08-1029 wait --for=condition=Ready \
  pod/m08-rdma-byte-prefill-0 pod/m08-rdma-byte-decode-0 --timeout=90s
python3 adapter/m08/rdma/run-transfer.py --platform ascend \
  --name m08-rdma-byte --output /tmp/ascend-byte
```

从跳板外运行时可给 `run-transfer.py` 加 `--ssh-host <alias> --ssh-config <path>`。探针以 `timeout 240` 约束，独立 Pod 有 600 秒截止时间。默认测试持久缓冲区；`--churn` 明确选择另一个动态注册压力用例，当前 Ascend 预计失败。

保存 `collect.py` 输出与完整传输日志后回收：

```bash
python3 adapter/m08/cluster/cleanup.py --names m08-pd-rdma --output /tmp/rdma-cleanup.json
kubectl -n dynamo-m08-1029 delete pod \
  m08-rdma-byte-prefill-0 m08-rdma-byte-decode-0 --ignore-not-found
```

远端本次 PD 清单为各管理节点 `m08-1029/pd-rdma.json`，证据为 `m08-1029/evidence-rdma/`。没有重新打包或修改原 M02 Worker 镜像；Operator/Grove/KAI 安装继续保留，测试 DGD、探针 Pod 与发现记录已回收。
