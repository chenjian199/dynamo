<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MetaX NIXL GPU READ 崩溃定位

## 问题位置

当前镜像 NIXL `1.0.0+maca3.8.2.208` 自带的 UCX 1.14 默认关闭新协议。旧协议在 TCP 模拟 GET 中把远端 MACA 显存按主机内存打包，触发 CPU 访问设备地址的 SIGSEGV。启用 `UCX_PROTO_ENABLE=y` 后，同一镜像、NIXL、MACA 运行时和宿主机驱动下的 READ/WRITE 探针通过。

这不是宿主机 NIXL 安装问题，也不是单纯缺失 UCX 插件。探针进程实际加载的 NIXL、UCX、MACA transport 都位于容器内；该容器只挂载工作目录、缓存 volume 和模型目录，没有挂载宿主机 NIXL/UCX。宿主机仍提供内核驱动，但本次修复未更换驱动或 native 库。

加载路径与 SHA256 见 [loaded-libraries.txt](results/metax/nixl-diagnosis/loaded-libraries.txt) 和 [libraries.sha256.txt](results/metax/nixl-diagnosis/libraries.sha256.txt)：

- `.nixl.mesonpy.libs/plugins/libplugin_UCX.so`
- `nixl.libs/libucp-4087c85a.so.0.0.0`
- `nixl.libs/ucx/libuct_maca.so.0.0.0`

## 因果证据

1. 使用 [nixl_probe.py](nixl_probe.py)，独立于 Dynamo/vLLM，以两个进程分别注册两张卡的 VRAM。
2. `UCX_TLS=maca_ipc,maca_copy,tcp`、`UCX_PROTO_ENABLE=n`：源进程 SIGSEGV，退出码 `[-11, 1]`。栈为 `ucp_get_req_handler → ucp_dt_contig_pack → libc`，旧路径选择 `rma_am(tcp/eth0)`。见 [旧协议结果](results/metax/nixl-diagnosis/old-protocol.json) 和 [崩溃日志](results/metax/nixl-diagnosis/old-protocol.log.txt)。
3. 仅将 `UCX_PROTO_ENABLE` 改为 `y`：4 B、4 KiB、512 KiB、4 MiB 的 READ/WRITE，各重复三轮，共 24 次传输。逐元素校验、释放传输句柄、移除 peer、注销内存，两个进程均以 0 退出。见 [结果](results/metax/nixl-diagnosis/fixed.json)、[传输矩阵](results/metax/nixl-diagnosis/transfers.json)。有限循环不等同于长期无泄漏认证。
4. `UCX_PROTO_INFO=y` 明确打印 MACA→MACA 的读写采用 `software emulation | tcp/eth0`。因此当前结果不是 RDMA 或 IPC 零拷贝认证。
5. 排除实验：显式插件目录、关闭 memory-type cache、强制 IPC GET、关闭 peer error handling 均未解决旧协议崩溃。正式修复保留 `ucx_error_handling_mode=peer` 默认值。

[UCX v1.14.0 的上游实现](https://github.com/openucx/ucx/blob/v1.14.0/src/ucp/rma/rma_sw.c#L237) 在 `ucp_get_req_handler` 中，仅当新协议启用时保留请求的 memory type，否则设置为 HOST；旧 GET 接收路径也直接使用 `memcpy`。这与本机栈和 A/B 结果相符。厂商分支的完整源码未提供，未宣称已逐行核对该二进制的全部实现。

## 修复方式

`platform/preflight.py` 仅在显式 `DYN_PLATFORM=metax` 且选择 `NixlConnector` 时设置：

```bash
UCX_PROTO_ENABLE=y
UCX_TLS=maca_ipc,maca_copy,tcp
```

显式关闭新协议会在引擎启动前报错；已配置的 `UCX_TLS` 保留，以便其他已验证部署使用自己的 transport 配置。CUDA、Ascend、MetaX Mooncake 不受这个环境设置影响。MetaX 的默认验收 connector 仍为 Mooncake，使用 `--connector nixl` 可运行 NIXL。

复现修复后的底层探针：

```bash
docker exec -w /workspace/dynamo \
  -e UCX_PROTO_ENABLE=y -e UCX_TLS=maca_ipc,maca_copy,tcp \
  dynamo-metax-m03-nfs /opt/dynamo/venv/bin/python \
  adapt/m05/nixl_probe.py --output /tmp/nixl-probe-manual
```

此修复认证范围限于当前版本、单机和文档中的模型/并行配置。跨节点 RDMA、不同 TP、传输中故障安全释放仍需独立验收。


## 引擎端到端复验

[MetaX NIXL 2P3D 结果](results/metax/nixl-2p3d/summary.json)：Qwen3-0.6B、eager BF16、每引擎 TP1/DP1；四张 C500 上运行五个独立引擎，其中 D0/P1 共享卡 0。六条 P→D 组合全部成功，12 个 HTTP 请求（并发度 6）的输出与聚合推理一致，三个 D 均记录远端 KV 命中，两个 P 均记录传输完成。

不可达握手端口在 5.027 秒后返回明确后端错误；等待租约回收后五端 running/waiting/KV 占用归零，后续请求成功。流式取消、占用回收、再次请求也通过。完整日志位于容器 `/var/cache/dynamo-metax/m05/nixl-2p3d/`；仓库保存摘要、命令、逐对结果、关键指标与 UCX/传输日志摘录。这是单机功能验收，不是性能或跨节点 RDMA 验收。
