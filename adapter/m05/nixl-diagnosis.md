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
  adapter/m05/nixl_probe.py --output /tmp/nixl-probe-manual
```

此修复认证范围限于当前版本、单机和文档中的模型/并行配置。跨节点 RDMA 仍未验证；不同 TP 的补测及传输中故障的失败结论见下文。


## 引擎端到端复验

[MetaX NIXL 2P3D 结果](results/metax/nixl-2p3d/summary.json)：Qwen3-0.6B、eager BF16、每引擎 TP1/DP1；四张 C500 上运行五个独立引擎，其中 D0/P1 共享卡 0。六条 P→D 组合全部成功，12 个 HTTP 请求（并发度 6）的输出与聚合推理一致，三个 D 均记录远端 KV 命中，两个 P 均记录传输完成。

不可达握手端口在 5.027 秒后返回明确后端错误；等待租约回收后五端 running/waiting/KV 占用归零，后续请求成功。流式取消、占用回收、再次请求也通过。完整日志位于容器 `/var/cache/dynamo-metax/m05/nixl-2p3d/`；仓库保存摘要、命令、逐对结果、关键指标与 UCX/传输日志摘录。这是单机功能验收，不是性能或跨节点 RDMA 验收。

## 2026-10-19：资源循环与传输中故障

扩大到 512 次 READ/WRITE、每次创建/移除 remote agent 后，数据与设备分配仍正确，但原版 NIXL/UCX 的 socket FD 从 53 增至 541。关闭 MACA IPC、禁用 memory type cache、开启 progress thread 均未消除增长。同版本上游 NIXL 源码重新构建也能复现，因此不能归因于宿主驱动或只归因于 MACA IPC。

`ucp_ep_close_nbx(flags=0)` 在活着的对端之间反复握手/失效时保留 transport lanes；上游也记录了这一行为及强制关闭对端连接的影响，见 [NIXL #2336](https://github.com/ai-dynamo/nixl/pull/2336)。本轮隔离构建的 `ucx_ep_close_force=yes` 补丁在 512 次实际设备读写后将 FD 保持在 29→29，进程干净退出。先用卡 3 上的两个进程（`--devices 3,3`）复验，再在长稳释放设备后用卡 0/1 的两个独立进程重复 512 次读写；后者两端 FD 均保持 30→30、设备活跃分配归零且干净退出。它验证单机跨卡的已完成传输回收，不是跨节点或在途安全取消认证。它默认关闭，要求 peer error handling，只用于明确允许两端断开且传输已经结束的测试；不得推广为任意在途取消/drain。强制断开会影响对端已连接的 endpoint，也可能取消尚在途的通知。

构建脚本 [build-metax-nixl.sh](build-metax-nixl.sh) 固定 NIXL v1.0.0 与 Abseil 源码提交，并校验当前镜像 NIXL/UCX 二进制哈希。输出独立 plugin 目录，不替换镜像原库。它不是整套厂商 NIXL 重建，也不使未验收的 ABI/网络组合获得支持。

```bash
# 在 MetaX 开发容器执行；输出目录必须不存在。
bash adapter/m05/build-metax-nixl.sh /tmp/m05-nixl-build
NIXL_PLUGIN_DIR=/tmp/m05-nixl-build/plugins \
UCX_PROTO_ENABLE=y UCX_TLS=maca_ipc,maca_copy,tcp \
python adapter/m05/nixl_probe.py --devices 0,1 --iterations 64 \
  --force-close --output /tmp/m05-nixl-cleanup
```

**仍未修复的独立问题**：64 MiB GPU READ 已处于 `PROC` 后终止对端，UCX 1.14 的 `ucp_proto_request_abort → ucp_ep_reqs_purge` 走到 `ucs_fatal_error_message: Function not implemented`，消费进程 SIGABRT。上述 endpoint 清理补丁没有解决这个 abort。需要匹配 MACA 的 UCX 核心源码与异常路径修复/重编译；本轮未取得该厂商核心源码，不用覆盖 fatal handler、伪造完成或提前注销设备内存来绕过。

另外，镜像原有 NIXL 插件（未加载隔离构建的 endpoint 补丁）在 Qwen3-0.6B 上完成 P TP1→D TP2 和 P TP2→D TP1 两个方向的端到端回归：正常请求、远端 KV 命中（各 1686 tokens）、取消回收和恢复均通过，输出与聚合一致。见 [P1→D2](results/completion/metax/nixl-tp-p1d2/summary.json) 与 [P2→D1](results/completion/metax/nixl-tp-p2d1/summary.json)。这不覆盖上面失败的 pending peer-exit 情形。

当前正常 PD 默认仍选 Mooncake。NIXL 的有限功能验证、连接清理实验和传输中故障失败是三个不同结论；不能据此宣布 NIXL 全部故障语义已与 CUDA 对齐。详见 [本轮完整边界](completion.md)。

### MACA UCX 公开源码核查（2026-10-19）

通过 GitHub API 枚举 [MetaX-MACA 官方组织](https://github.com/MetaX-MACA) 的 65 个公开仓库，没有 UCX/NIXL 仓库；直接查询这两个仓库名返回 404。另检查 vLLM-metax、Mooncake、QuickGuide、maca-samples 的默认分支目录及发布附件，没有找到匹配 MACA 3.8.2.208 的 UCX 源码或错误路径修复包。[vLLM-metax v0.26.0 发布项](https://github.com/MetaX-MACA/vLLM-metax/releases/tag/v0.26.0) 没有附件；[Mooncake](https://github.com/MetaX-MACA/Mooncake) 的源码不能替代 MACA UCX 核心。

结论仅限目前公开可获取的材料；不代表厂商内部不存在源码或修复。核查范围、仓库列表和分支 SHA 见 [查询记录](results/completion/metax/public-source-check.json)。在获得兼容源码或厂商修复前，传输中断触发 UCX abort 继续标记为未解决，不用上游原版 UCX 直接替换具有 MACA 自定义内存类型的二进制。
