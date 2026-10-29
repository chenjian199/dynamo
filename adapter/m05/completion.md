<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M05 单机补充验收：不同 TP、传输中故障与有界长稳

本轮限定 vLLM 引擎原生 PD。CUDA 的参照能力是已测配置下的 KV 分片转换、失败报告和必要时重启恢复，不承诺任意故障后原进程继续服务。跨节点仍未验证；Dynamo 自有 TransferProvider/KVBM 留给 M06 接入，SGLang 暂缓。

## 不同 P/D TP

Qwen3-0.6B、eager BF16、DP1/PP1，P=TP2/D=TP1 与 P=TP1/D=TP2 分别测试。三个独立物理设备承担三个 TP rank。不同 TP 不同于先前的 2P3D 副本数量测试。

契约 schema 2 只允许受限 MHA/GQA 的 TP1/TP2 互转；模型配置、引擎/插件版本、dtype、block size 和 connector 仍须一致。CUDA NIXL、MetaX Mooncake 与 MetaX 可选 NIXL 均已验证两种方向，使用引擎原有分片转换。Ascend 0.23.0 原本拒绝 P TP 小于 D TP，新补丁只开放 Qwen3、BF16、连续 NHD 缓存的 P1→D2：每个 D rank 逐 token 拉取自己的一半 KV heads，P 收到两个不同 D rank 的完成确认才交回源块。没有放开 MLA、KV head 复制、任意比例、PD 内部 DP 或高级能力组合。

Ascend 安装顺序（在没有推理 worker 的容器中执行）：

```bash
python adapter/m05/patch_vendor.py --platform ascend --apply
python adapter/m05/patch_ascend_tp.py --apply
```

两个脚本均按版本与完整源码 SHA256 校验，未知源码拒绝修改。撤回时先运行 `patch_ascend_tp.py --restore`，再撤回基础补丁。

## 传输中故障的实际语义

`--inflight-fault` 在独立测试进程中记录 native pending 状态，然后终止脚本自己的提交方引擎；注入发生在首 token 之前。测试用 observer 对 Mooncake 同步调用改用同一种 READ/WRITE 的异步 API，以确定 pending 时刻；此插桩只用于验收，不是生产 connector 实现。NIXL 故障探针强制 TCP 路径，使 pending 可重复观察，不能用于证明跨节点 RDMA 已验证。

- CUDA：独立 NIXL 探针观察到 `PROC → nixlRemoteDisconnectError`，随后安全释放句柄并注销内存。真实引擎故障中，UCX 也可能使对端引擎退出；请求明确报错，需要重新启动 worker。
- MetaX Mooncake：原版本在提交方退出后，D 可能永久等待。新增显式 `heyi_transfer_failure_timeout`（默认 30 秒，允许 5–300 秒）：完成状态不确定时使引擎失败退出，不向调度器伪报完成、不把可能仍被写入的 KV 块归还复用。基础 bootstrap 失败仍走之前的可回收错误路径。随后通过新 worker 恢复服务；这不是透明重试在途请求。
- Ascend：独立 Mooncake pending 探针在源进程退出后仍可能完成，因为本机映射的设备内存仍可访问；只有完整设备字节校验正确才算完成，随后注销并要求消费进程正常退出。
- MetaX NIXL 的独立 peer-exit 探针仍触发镜像内 UCX 1.14 `ucp_proto_request_abort` 的 `Function not implemented`，消费进程 SIGABRT。该路径不能声称具有 CUDA NIXL 的可捕获错误/原进程 drain 能力。旧协议 GPU GET 崩溃修复和当前正常读写能力仍有效。需要匹配 MACA 的 UCX 错误路径修复及重编译，不能通过忽略 abort 或提前释放缓冲区解决。

## Ascend native 退出修复

原镜像仅执行 `from mooncake.engine import TransferEngine` 也会在进程析构时以 134 退出。GDB 定位到 `libascend_trace.so` 析构调用 libc free；单独加载 CANN 库正常。先用 `RTLD_GLOBAL` 加载 CANN 9.1 的 `libllm_datadist.so`，再加载 Mooncake 后，最小复现、64 轮真实设备注册/读取/注销及 pending peer-exit 探针均干净退出。

实现位于 `common/platform/ascend_mooncake.py`，由受版本/哈希保护的厂商补丁调用。若入口会先导入上游 Mooncake（例如完整 pytest 集合），应使用 `bash adapter/m05/ascend-python.sh -m pytest ...`，在 Python 启动前设置库加载顺序；普通 Python 入口仍可重现析构错误，不能声称任意导入顺序均已修复。这是当前镜像验证过的库加载顺序修复，没有证明具体哪个符号冲突，也不将问题归因于宿主驱动。该 helper 不在 CUDA/MetaX 普通路径加载 CANN。

首轮长稳另发现线程增长。新增线程的 GDB 顶层地址位于 `mooncake/libetcd_wrapper.so`；Python 栈没有对应数量的新增线程。这个 Go 辅助库在使用 P2PHANDSHAKE 时也会加载。只设置 `GOMAXPROCS=2` 仍不足：vLLM 设置进程名称时覆盖原始环境区域，随后加载的 Go 共享库不能读到原来的限制。最小对照中默认改名后线程 10→15，而 `SPT_NOENV=1` 下保持 9。当前启动脚本同时保留该环境区域并设置 `GOMAXPROCS=2`，按原阈值完成 30 分钟复验；另设 `MALLOC_ARENA_MAX=2` 限制多线程 glibc arena 的内存保留。这些都是当前开发镜像的验收配置，不是吞吐优化结论。GDB 的 libc 展开存在版本不匹配警告，因此只依据库映射定位，不声称已经得到每个 Go 函数的可靠调用链。

## 30 分钟长稳的验收含义

每平台至少 1800 秒、至少 1000 个正常请求，并发度 4；每 32 请求断开一次 SSE，累计第 320/640/960 请求注入不可达交接，再核对恢复输出。空闲时检查所有 worker 的 running/waiting/KV 占用归零，记录每个进程 RSS、文件描述符、线程数与 native 活跃传输/预备句柄/注册状态。

预热后末次样本相对第 3 次样本：RSS 增长不超过 256 MiB、FD 不超过 8、线程不超过 4，native 活跃对象数一致。独立探针补充反复注册/注销、实际字节校验、设备分配归零和干净进程退出。这里验证的是规定时长和阈值内稳定，不是数学意义的无限时长无泄漏，也不把预分配的 KV 显存池当作泄漏。

长稳与破坏性引擎故障分开执行，避免通过替换进程掩盖资源增长。首轮长稳尚未安装最新 MetaX fail-stop 和 Ascend 反向 TP/加载顺序补丁；保留这些历史结果，另用最终补丁进行完整 30 分钟复验。运行结果的 `vendor_connector.sha256` 记录实际版本，不能把首轮结果追认为最终补丁的验收。

## 手动复现

在对应开发容器 `/workspace/dynamo`、`/opt/dynamo/venv` 环境执行；Ascend 将以下 `python` 替换为 `bash adapter/m05/ascend-python.sh`；每次使用新输出目录，同一设备不要同时运行其他验收。模型沿用 NFS 权重。

```bash
# PLATFORM 为 cuda / metax / ascend；三张不同物理卡。
python adapter/m05/run.py --platform "$PLATFORM" --devices 0,1,2 \
  --prefill-tp 2 --decode-tp 1 --cancel --soak-seconds 1800 \
  --soak-requests 1000 --output /tmp/m05-soak
python adapter/m05/run.py --platform "$PLATFORM" --devices 0,1,2 \
  --prefill-tp 1 --decode-tp 2 --cancel --output /tmp/m05-reverse-tp

# 会终止本测试自己的引擎；必须随后执行独立重启回归。
python adapter/m05/run.py --platform "$PLATFORM" --devices 0,1 \
  --inflight-fault --output /tmp/m05-inflight
python adapter/m05/run.py --platform "$PLATFORM" --devices 0,1 \
  --cancel --output /tmp/m05-restart

# MetaX/Ascend 原生 Mooncake 资源循环与 pending 故障。
python adapter/m05/mooncake_probe.py --platform "$PLATFORM" --devices 0,1 \
  --iterations 64 --output /tmp/m05-mooncake-cycles
python adapter/m05/mooncake_probe.py --platform "$PLATFORM" --devices 0,1 \
  --fault --output /tmp/m05-mooncake-fault

# CUDA 或 MetaX 原生 NIXL：MetaX 先执行
# export UCX_PROTO_ENABLE=y UCX_TLS=maca_ipc,maca_copy,tcp
python adapter/m05/nixl_probe.py --devices 0,1 --iterations 64 \
  --output /tmp/m05-nixl-cycles
python adapter/m05/inflight_probe.py --platform "$PLATFORM" --devices 0,1 \
  --output /tmp/m05-nixl-fault
```

NVIDIA 活跃容器已重建，挂载 `/home/inspur/CJ_workspace/dynamo` 到 `/workspace/dynamo`。旧容器保留为 `dynamo-cuda-m03-nfs-before-path-20261019` 并停止；`dynamo.bak` 不参与本轮测试。原环境保存在本机镜像 `dynamo-cuda-m05-env:20261019`。

## 本轮结果

| 检查 | CUDA | MetaX Mooncake | Ascend Mooncake |
| --- | --- | --- | --- |
| P1→D2、取消恢复 | 通过 | 通过 | 受限补丁后通过 |
| P2→D1 正常输出 | 通过 | 通过 | 通过 |
| 真正 pending 时终止引擎 | 明确错误，可能需重启对端 | 约 32 秒失败退出 | 最终入口约 0.70 秒错误，存活 P 回收 |
| 重建 worker 后输出与聚合一致 | 通过 | 通过 | 通过，包含最终 P1→D2 补丁和运行时入口 |
| native 注册/传输/注销循环 | NIXL 512 次通过 | Mooncake 64 轮通过 | Mooncake 64 轮通过，干净退出 |
| 首轮 30 分钟 | 4480 请求通过 | 3712 请求通过 | 1824 请求；线程增长阈值失败 |
| 最终配置 30 分钟 | 同上，1804.96 秒 | 3680 请求、1803.38 秒通过；115 次取消、3 次故障 | 1728 请求、1813.87 秒通过；54 次取消、3 次故障 |
| 完整定向单测 | 319 passed / 6 skipped | 320 passed / 5 skipped | 统一启动脚本下 319 passed / 6 skipped，exit 0 |

三平台不同 TP 和重启后的正常输出均与各自聚合结果一致，见 [逐平台对照](results/completion/aggregate-parity.json)。跳过测试不计为通过。华为首轮长稳中 RSS、FD、KV 和 native 活跃对象稳定，但部分进程线程数增长 9–10，超过预设 4 的阈值。后续只设置 `GOMAXPROCS` 的完整对照轮次还出现约 330 MiB RSS 增长；原始失败全部保留，没有提高阈值；最终环境保护和 allocator 配置下完整复验通过。

首轮 Ascend 的旧 harness 在异常时未更新 `soak.json` 的 running 状态，该轮最终判定以 `summary.json` 的 failed 为准；后续 harness 已补上失败状态写入，不修改历史原始报告。

最终长稳的最大进程 RSS 增幅分别为 CUDA 71.81 MiB、MetaX 7.79 MiB、Ascend 42.41 MiB；FD 增幅均为 0，线程最大增幅分别为 0、3、1。native 活跃对象计数与预热后基线一致，空闲请求与 KV 占用归零。各轮独立测试，不将多轮时间相加充当 30 分钟。

原始报告见 [completion 结果](results/completion)，包括失败证据。每个 case 的 `collection.json` 指向完整容器日志目录；`commands.json` 保存具体启动参数。采集使用共享缓存卷，采集容器不一定是执行容器。

## Ascend 运行时诊断复现

[最小程序](ascend_runtime_probe.py) 不执行 NPU 推理。在 Ascend 镜像里分别运行以下命令，观察原始环境计数与线程数：

```bash
env -u SPT_NOENV GOMAXPROCS=2 GODEBUG=gctrace=1 \
  /opt/dynamo/venv/bin/python adapter/m05/ascend_runtime_probe.py
SPT_NOENV=1 GOMAXPROCS=2 GODEBUG=gctrace=1 \
  /opt/dynamo/venv/bin/python adapter/m05/ascend_runtime_probe.py
```

[默认改名](results/completion/ascend/setproctitle-0.txt) 和 [保留环境](results/completion/ascend/setproctitle-1.txt) 是原始输出。

[setproctitle 文档](https://github.com/dvarrazzo/py-setproctitle) 说明默认行为会覆盖原始环境区域；[Go 1.25.9 初始化源码](https://github.com/golang/go/blob/go1.25.9/src/runtime/runtime1.go#L78) 从启动参数之后的环境数组读取配置。二者与本机对照结果一致。这里没有修改 Go/UCX 库，也没有关闭 GC 或提高长稳阈值。单独限制 `GOMAXPROCS` 的完整失败轮次保留为 `soak-bounded-go`；库加载和异步抢占的短诊断不作为通过的长稳结果。最终配置只使用脚本中显式列出的环境设置，没有启用诊断用 `GODEBUG=asyncpreemptoff=1`。
