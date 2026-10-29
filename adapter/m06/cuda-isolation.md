<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# NVIDIA 异常卡隔离复测记录

2026-10-19，GPU 2 的利用率返回 N/A，NVIDIA container prestart hook 返回
`nvml error: gpu requires reset`。即使 `--gpus` 只指定 GPU 1、3 的 UUID，
该 hook 仍然失败。GPU 0 有其他任务，本轮未使用，也未重置驱动或重启主机。

经用户要求避开异常卡，使用 runc 显式挂载 GPU 1、3 的设备节点和当前宿主机
驱动库，成功启动 `dynamo-cuda-m06-manual`。这是本机故障隔离环境，不能替代
正常的 NVIDIA Container Toolkit 部署验收。

环境保留自已有容器：

```bash
docker commit dynamo-cuda-m03-nfs dynamo-cuda-m06-env:20261019-isolated
```

以下是本次成功启动所用配置。驱动库版本对应本机 580.126.20，不能直接套用于
其他驱动；运行前确认 GPU UUID、镜像、目录和 volume。现有同名容器不应删除重建。

```python
import pathlib
import subprocess

args = [
    "docker", "run", "--pull=never", "-d", "--name", "dynamo-cuda-m06-manual",
    "--runtime=runc", "--init", "--shm-size=8g", "--ulimit", "memlock=-1",
    "-e", "NVIDIA_VISIBLE_DEVICES=void",
    "-e", "CUDA_VISIBLE_DEVICES=GPU-04c3b089-d08a-fe49-d12f-9a4ee8bcd962,GPU-15c73724-41ec-42aa-bab5-b58d5b7163d3",
]
for node in ("nvidia1", "nvidia3", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools"):
    args += ["--device", "/dev/" + node]
for lib in (
    "libcuda.so.1", "libnvidia-ml.so.1", "libnvidia-ptxjitcompiler.so.1",
    "libnvidia-nvvm.so.4", "libnvidia-gpucomp.so.580.126.20",
):
    source = pathlib.Path("/lib/x86_64-linux-gnu", lib).resolve(strict=True)
    args += ["-v", f"{source}:/usr/local/nvidia/lib64/{lib}:ro"]
args += [
    "-v", "/usr/bin/nvidia-smi:/usr/local/nvidia/bin/nvidia-smi:ro",
    "-v", "/home/inspur/CJ_workspace/dynamo:/workspace/dynamo",
    "-v", "/NFS_62:/NFS_62:ro",
    "-v", "dynamo-cuda-m03-cache:/var/cache/dynamo-cuda",
    "--entrypoint", "/bin/bash", "dynamo-cuda-m06-env:20261019-isolated",
    "-lc", "exec sleep infinity",
]
subprocess.run(args, check=True)
```

容器内 torch 实际返回两张 H800，UUID 分别对应宿主机 GPU 1、3；两张卡上的
`torch.ones(4, device=...).sum().item()` 都返回 4.0。测试使用完整 UUID，避免
容器逻辑序号与宿主机编号混淆；M05 harness 已允许 CUDA 完整 GPU UUID。

复测使用 `/opt/dynamo/m06-venv/bin/python`、vLLM 0.24.0 和 NFS 模型：

- `m06-safe-baseline-tp2`：无 KVBM、TP2、关闭 custom all-reduce，通过。
- `m06-safe-unit.txt`：546 passed / 1 skipped，已在终端核实。
- `m06-safe-tp2`：相同配置启用 KVBM、取消和基线对比；运行中 SSH 断开，
  随后端口 22 拒绝连接/超时。结果未知，不能计为通过，也不能归因为 KVBM。

上述原始输出位于 `/var/cache/dynamo-cuda/` 的持久 volume。SSH 中断时尚未收集
到仓库，需要恢复后读取 summary 和日志；TP2、PD、聚合和小池最终验收仍待完成。
本记录不把管理查询能返回等同于驱动完全恢复，也不据此判定 custom all-reduce 是根因。

## 2026-10-20 恢复后最终复测

用户恢复主机后，正常 NVIDIA runtime 的 `dynamo-cuda-m03-nfs` 容器重新可用。
在 GPU 1、3 顺序运行七项 suite，全部通过；再运行通用 SDK TP2（36 次实际 KV
字节回读）、prefill KVBM + NIXL PD（8 次回读及取消恢复）、双 worker G2 路由
（10 次回读）及 G3 路由（10 个磁盘独有块、10 次回读），均通过。
证据见 `results/cuda/recovered/`。
TP2 基线和两种 KVBM 路径均关闭 custom all-reduce；此结果不证明原故障根因。

原生 SDK 独立测试为 18 种布局 × 4 轮，定向 Python 单测为 546 passed / 1 skipped，
已收集到 `results/cuda/native.txt` 和 `unit.txt`。主机重启再次丢失 NFS 挂载时，
本轮使用之前从 NFS 复制的同一 Qwen3-0.6B 模型，未从外网下载。用户随后恢复挂载；
全部测试完成后已重启空闲开发容器，确认 NFS 模型可见、四卡显存均为 0 MiB，
见 `results/cuda/recovered/runtime.txt`。
此前被中断的 `m06-safe-tp2` 没有最终 summary，仍不作为通过证据。
