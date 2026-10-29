<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M05：引擎原生 PD 接入与验收

> 2026-10-21 基线变更：NVIDIA 后续固定官方 Dynamo 1.5.1 / vLLM 0.28.0。本页 CUDA 0.24 结果为历史证据，不代表 0.28 已通过；详见[版本基线](../version-baselines.md)。

当前补充验收见 [不同 TP、传输中故障与 30 分钟长稳](completion.md)。本目录实现 vLLM 引擎原生 PD；三平台单机 1P1D、2P3D、TP1/TP2 互转已有真实验证。跨节点、物理断卡/网络黑洞和 Dynamo 自有 TransferProvider/KVBM 不在已验证范围，SGLang 暂缓。MetaX NIXL 的传输中对端退出仍存在 native abort 限制，不能认定所有可选传输路径全部完成。

下文保存 2026-10-16 初轮与 2P3D 验收；运行命令已更新为当前入口。2026-10-19 的补丁、资源检查与最新边界以补充验收为准。

## 配置与实现

| 平台 | 引擎版本 | connector | 单卡 block size |
| --- | --- | --- | --- |
| NVIDIA H800 | vLLM 0.24.0 / NIXL 1.2.0 | `NixlConnector`，`kv_both` | 64 |
| MetaX C500 | vLLM-metax 0.26.0 / MACA 3.8.2.208 | 上游协议 `MooncakeConnector`，`mooncake_protocol=maca`；或修复配置后的 `NixlConnector` | 64 |
| Ascend 910B3 | vLLM-ascend 0.23.0 / CANN 9.1 | `vllm_ascend.distributed.kv_transfer.kv_p2p.mooncake_connector.MooncakeConnector` | 128 |

模型使用已有 `/NFS_62/Models/Qwen_Series/Qwen3-0.6B`。每端 TP=1、DP=1，eager BF16、512 MiB KV cache、max-model-len=2048；关闭 prefix caching 以便观察请求结束后的块占用。三平台分别测试，不混用 KV。每平台三个确定性请求的输出均与该平台聚合部署逐字一致；正常三次请求 Decode 远端 KV 命中累计为 1686 tokens。

Dynamo 控制面选择 P/D worker 并转交元数据；设备注册、KV 传输及块释放由引擎 connector 完成，因此本轮未修改 Rust M04/KVBM 数据面。

实现位置：

- `kv_connector_protocols.py` 按名称和模块路径区分协议，支持包装 connector 的单个 PD 子项；未知模块提前报错。Ascend Mooncake 使用 pull 元数据，不能使用上游 Mooncake 的 transfer_id/bootstrap 映射。
- `platform/preflight.py` 在启动前约束角色、connector、并行度和功能组合。初轮显式平台 PD 开放 eager 文本 TP1/DP1（本轮扩展至受限 TP1/TP2），不包含 LoRA、量化、推测解码、EP、多模态。
- `pd_contract.py` 在 P 响应中附加平台、引擎/插件版本、模型路径/revision/配置指纹、精度、block size、并行度与 connector 契约，D 提交引擎前核对并移除此控制字段。指纹覆盖模型配置，不校验权重文件内容；同平台 P/D 必须使用相同权重。实际布局协商仍由 connector 负责。
- 保留原有延迟 abort 与 connector 的回收机制，不用请求超时伪造传输完成，也不在可能存在设备写入时强行归还 KV 块。

`pd.transfer=available` 仅表示所选基础配置能接入；不表示 RDMA、跨节点、传输中故障恢复或所有 PD 组合都已认证。未设置 `DYN_PLATFORM` 时保留既有引擎路径。

## 运行方式

使用前轮已有开发容器和 editable 安装。宿主代码挂载在 `/workspace/dynamo`，模型挂载在 `/NFS_62`。脚本使用文件发现、TCP 请求面、ZMQ 事件面，启动并清理自己创建的 frontend 和所有 P/D worker；不需要额外启动 etcd/NATS。

厂商 connector 的修复补丁保存在 [patches](patches)。安装前停止该容器中的推理 worker。脚本默认只检查，`--apply` 安装，`--restore` 撤回；版本及完整源码 SHA256 必须匹配，未知源码不会被修改。补丁仅影响当前容器，需要在新容器中重新应用，后续应纳入正式镜像构建。

```bash
# 沐曦宿主机
cd /home/metax/CJ_workspace/dynamo
docker exec -w /workspace/dynamo dynamo-metax-m03-nfs \
  /opt/dynamo/venv/bin/python adapter/m05/patch_vendor.py --platform metax --apply
docker exec -w /workspace/dynamo dynamo-metax-m03-nfs \
  /opt/dynamo/venv/bin/python adapter/m05/run.py \
  --platform metax --devices 0,1 --cancel --output /tmp/m05-metax-manual

# 华为宿主机，代码为 ~/CJ_workspaces/dynamo
ssh dynamo-huawei 'docker exec -w /workspace/dynamo dynamo-ascend-m03-nfs \
  /opt/dynamo/venv/bin/python adapter/m05/patch_vendor.py --platform ascend --apply'
ssh dynamo-huawei 'docker exec -w /workspace/dynamo dynamo-ascend-m03-nfs \
  /opt/dynamo/venv/bin/python adapter/m05/patch_ascend_tp.py --apply'
ssh dynamo-huawei 'docker exec -w /workspace/dynamo dynamo-ascend-m03-nfs \
  bash adapter/m05/ascend-python.sh adapter/m05/run.py \
  --platform ascend --devices 0,1 --cancel --output /tmp/m05-ascend-manual'

# NVIDIA 宿主机，代码为 ~/CJ_workspace/dynamo
ssh dynamo-nvidia 'docker exec -w /workspace/dynamo dynamo-cuda-m03-nfs \
  /opt/dynamo/venv/bin/python adapter/m05/run.py \
  --platform cuda --devices 0,1 --cancel --output /tmp/m05-cuda-manual'
```

每次使用新的 `--output` 目录。同一组卡不要并发运行多个测试。其他验收选项：

| 参数 | 检查内容 |
| --- | --- |
| `--prefill-workers 2 --decode-workers 3` | 五个独立引擎实例，逐一检查六条 P→D 组合，再以并发度 6 发送 12 个 HTTP 请求 |
| `--allow-shared-devices` | 显式允许多个实例共享物理卡，仅用于小模型功能验收 |
| `--connector nixl` | MetaX 选择 NixlConnector，平台入口自动配置 MACA UCX 新协议 |
| `--aggregate` | 同平台聚合输出对照，仅需一张卡 |
| `--cancel` | 首个文本事件后断开客户端，检查两端占用归零、后续请求输出一致 |
| `--handoff-case mismatch` | 在测试客户端修改交接契约，验证引擎提交前拒绝、P 租约回收及后续请求 |
| `--handoff-case unreachable` | 在交接元数据中注入未监听的 bootstrap/握手端口，验证明确报错、回收与恢复 |
| `--fault producer-exit` | 首个 SSE 事件后杀死本测试 P 引擎，D 应能继续完成 |
| `--fault consumer-exit` | 首个 SSE 事件后杀死本测试 D 引擎，应返回流错误 |

故障注入仅修改该次测试请求或杀死脚本自己的引擎子进程，不修改宿主机网络规则。交接后的退出测试不能证明正在传输的设备缓冲区已经安全 drain。脚本设置 Mooncake producer 租约为 60 秒，客户端故障等待上限为 90 秒；这些是本次测试配置，不是所有引擎的默认值。

## 结果与未完成项

原始结果及补丁前失败证据见 [results](results)。每个 case 的 JSON 记录请求结果、KV/请求占用、取消或故障结果；同名文本文件摘录引擎完成通知。完整日志保存在各开发容器 `/var/cache/dynamo-{platform}/m05/`，JSON 中记录具体目录；执行命令位于对应目录的 `commands.json`。

三平台正常 PD、输出对照、取消及恢复、交接契约拒绝、交接后的 producer/consumer 退出已通过。空闲 KV 指标归零代表引擎块分配已回收，不代表整个预分配显存池被释放，也不能替代 native 注册句柄泄漏检测。

厂商异常与处理：

1. **MetaX NIXL**：已定位至镜像内 UCX 1.14 的旧协议 GPU GET 路径。开启 `UCX_PROTO_ENABLE=y` 后，24 次独立 VRAM READ/WRITE 校验及进程退出通过，NIXL 2P3D、不可达错误/回收/恢复与取消恢复亦通过；未修改宿主机驱动。证据与限制见 [NIXL 定位记录](nixl-diagnosis.md)。
2. **MetaX Mooncake bootstrap 失败**：原版本只打印错误，没有向调度器交回失败与完成状态，90 秒内没有后端响应。补丁仅对尚未提交任何设备传输的 bootstrap 失败报告 invalid blocks + finished receive；不把传输中的超时当作安全完成。
3. **Ascend Mooncake 清理阻塞**：握手失败后 REQ socket 的默认 LINGER 导致 ZMQ context 销毁阻塞，传输线程无法进入完成通知；Decode 有 5 个块未回收。线程栈确认卡在 `zmq.Context.term`。补丁将该控制 socket 的 LINGER 设为 0，KV 数据面不经过此 socket。
4. **Ascend Python 退出异常**：本轮完整定向单测 308 passed / 6 skipped 后进程以 134 退出，输出 `corrupted size vs. prev_size`；单独导入厂商 connector 的最小探针也出现该异常。该初轮结果不能记作干净回归通过；后续已通过库加载顺序修复，使用当前 Ascend 启动入口完成干净回归，见 [补充验收](completion.md)。

补丁后，对端不可达 → 明确错误 → 两端请求/KV 占用归零 → 正常请求恢复 → 再次取消与恢复，三平台全部通过：

| 平台 | 不可达错误耗时 | 两端空闲占用 | 恢复 | 证据 |
| --- | --- | --- | --- | --- |
| CUDA | 5.028 s | 0 | 通过 | [final.json](results/cuda/final.json) |
| MetaX（补丁后） | 0.036 s | 0 | 通过 | [patched.json](results/metax/patched.json) |
| Ascend（补丁后） | 3.241 s | 0 | 通过 | [patched.json](results/ascend/patched.json) |

以上是无人监听端口的确定性故障，不是丢包或黑洞网络实验。保留补丁前失败结果以便比较。MetaX 单测为 309 passed / 5 skipped，CUDA 为 308 passed / 6 skipped；MetaX 使用仅匹配缺少可选 fla 包的 warning 过滤。跳过项包含既有禁用 handler 测试及旧版本缺少的异常 API，不计为通过。

初轮尚缺的传输途中故障、注册资源压力和不同 TP 测试，本轮进展见 [补充验收](completion.md)。跨节点同平台部署、PD 内部 DP/高级组合和 Dynamo 自有传输/KVBM 路径仍需独立验收。当前没有第二台同厂商节点，三台不同厂商机器不能用来证明同平台跨节点 PD。


## 2P3D 副本数量不对等验收

这里的 2P3D 是 **2 个独立 Prefill worker + 3 个独立 Decode worker**，每个 worker 仍为 TP1/DP1；不代表 P/D 使用不同 TP，也不代表 vLLM 内部 DP=3。

| 平台/传输 | 实际设备分配（D0,D1,D2,P0,P1） | 六条组合 | 并发 HTTP / 取消恢复 | 证据 |
| --- | --- | --- | --- | --- |
| NVIDIA / NIXL | 0,1,2,3,0；4 卡运行 5 实例 | 通过 | 12 次 / 通过 | [summary](results/cuda/topology-2p3d/summary.json) |
| MetaX / Mooncake | 0,1,2,3,0；4 卡运行 5 实例 | 通过 | 12 次 / 通过 | [summary](results/metax/topology-2p3d/summary.json) |
| Ascend / Mooncake | 0,1,2,3,4；5 张独立卡 | 通过 | 12 次 / 通过 | [summary](results/ascend/topology-2p3d-ready/summary.json) |
| MetaX / NIXL（UCX 新协议） | 0,1,2,3,0；4 卡运行 5 实例 | 通过 | 12 次 / 通过 | [summary](results/metax/nixl-2p3d/summary.json) |

通过发现服务确认 2 个 P、3 个 D；测试客户端用 `direct()` 分别执行全部六个组合，每次使用新的 prefill 交接结果，核对生成 token 一致。随后真实 frontend 处理 12 个 HTTP 请求（并发度 6），核对输出一致。三个 D 都有远端 KV 命中，两个 P 都有传输完成日志，五个引擎的 running/waiting/KV 使用率最终归零；取消后亦恢复。共享卡结果不用于比较吞吐性能。

```bash
# MetaX Mooncake；NVIDIA 将 platform 改为 cuda，在对应容器执行。
docker exec -w /workspace/dynamo dynamo-metax-m03-nfs \
  /opt/dynamo/venv/bin/python adapter/m05/run.py \
  --platform metax --prefill-workers 2 --decode-workers 3 \
  --devices 0,1,2,3,0 --allow-shared-devices --cancel \
  --output /tmp/m05-2p3d-manual
```

华为本次使用 `dynamo-ascend-m05-5card`（原环境镜像、相同挂载，增加 `/dev/davinci4`），`--devices 0,1,2,3,4`。测试前停止空闲的旧四卡容器，避免设备初始化冲突；测试结束后恢复旧容器。新容器保留但停止，厂商补丁已应用。完整日志仍在共享 cache volume 中。

本次新增配置检查相关定向回归三平台各 43 passed，均正常退出；这不覆盖或消除前一轮完整测试集合触发的 Ascend native 析构异常。

复用本次保留的华为五卡容器时，先确认两个容器均无推理任务，再执行：

```bash
# 在华为宿主机运行；结束后恢复原四卡开发容器。
docker stop dynamo-ascend-m03-nfs
docker start dynamo-ascend-m05-5card
docker exec -w /workspace/dynamo dynamo-ascend-m05-5card \
  /opt/dynamo/venv/bin/python adapter/m05/run.py \
  --platform ascend --prefill-workers 2 --decode-workers 3 \
  --devices 0,1,2,3,4 --cancel --output /tmp/m05-2p3d-manual
docker stop dynamo-ascend-m05-5card
docker start dynamo-ascend-m03-nfs
```


MetaX NIXL 同一 2P3D 部署还通过不可达握手端口故障：5.027 秒返回 `finish_reason=error`，五个引擎 running/waiting/KV 使用率归零，后续正常请求、取消及恢复均通过。全部正常 HTTP 输出与原 MetaX 聚合结果一致，见 [输出对照记录](results/topology-aggregate-parity.json)。

```bash
docker exec -w /workspace/dynamo dynamo-metax-m03-nfs \
  /opt/dynamo/venv/bin/python adapter/m05/run.py \
  --platform metax --connector nixl --prefill-workers 2 --decode-workers 3 \
  --devices 0,1,2,3,0 --allow-shared-devices --cancel \
  --handoff-case unreachable --output /tmp/m05-nixl-2p3d-manual
```
