<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M07 GMS 与多模态分项验收

2026-10-26 阶段性收口：CUDA/MetaX 原生 Omni 并发、取消恢复及各 30 分钟有界稳定性
回归通过，见 [验收记录](omni-regression.md)。当前约定范围完成，总体仍为有限支持；
独立 Omni stage 与华为相关未完成项记录后暂缓，不标记为通过。

2026-10-26 后续：LOCAL lease/TTL 回收、WRITE v2 与视频取消保护已实现；
三平台长视频解码和同机跨卡编码器回归通过。WRITE 真机认证限 CUDA/MetaX，
CUDA/MetaX 原生聚合 Omni 三卡音频生成通过；Omni 独立阶段与华为依赖仍有阻塞，见 [四项后续验收](followup.md)。

2026-10-26 范围调整：按用户决定，Ascend GMS 自研暂缓，保留
[token 共享与权重保活设计](ascend-gms-design.md)，先推进 M07 其它工作。
这不是 GMS 验收通过，也不解除 Ascend GMS 的预检查限制。

2026-10-26：华为升级 HDK 26.1.1 后完成四项原语复测。RW 设置与查询恢复，
但 READ 权限和 POSIX 分配仍不支持，Ascend GMS 继续阻塞。
参数级官方文档、复测证据及后续条件见 [26.1.1 复测记录](ascend-hdk2611.md)。
新驱动下 108 项单元、4 项媒体及独立编码器端到端回归通过，见
[升级后回归证据](results/ascend-hdk2611/summary.json)。这些通过项不包含 GMS。
下方 25.2.0 结果为历史记录。

2026-10-21：完成 MetaX GMS V1、CUDA 回归和三平台单机编码器分离的本轮适配。按用户要求将 NVIDIA 基线纠正为官方 Dynamo 1.5.1 / vLLM 0.28.0，移除为旧 0.24 添加的 GMS 兼容分支；版本锁定见[三平台固定基线](../version-baselines.md)。**M07 整体仍为有限支持**：Ascend GMS 被当前驱动组合阻塞；Omni stage、完整进程 checkpoint/CRIU 恢复及媒体传输的部分故障路径尚未验收。

## 环境与结果

沿用已安装依赖的开发容器，代码在容器内 `/workspace/dynamo`，Python 为 `/opt/dynamo/venv/bin/python`；未下载模型。

| 平台 | 开发容器 / 当前镜像 | 引擎与设备 | 单元回归 | 真机 GMS / 旧 tensor-module 接口 | 媒体原语 | 独立编码器图文 |
| --- | --- | --- | --- | --- | --- | --- |
| NVIDIA | `dynamo-cuda-151-dev` / `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.1` | vLLM 0.28.0、torch 2.13.0+cu130、H800，物理卡 1 | 132 passed | 4 / 6 passed | 7 passed | passed |
| 沐曦 | `dynamo-metax-m03-nfs` / `dynamo-metax-m03-env:20261016` | vLLM-metax 0.26.0、MACA 3.8.2.208、C500，物理卡 2 | 132 passed | 4 / 6 passed | 7 passed | passed |
| 华为 | `dynamo-ascend-m03-nfs` / `dynamo-ascend-m03-env:20261016` | vLLM-Ascend 0.23.0、CANN 9.1、driver 25.2、910B3，物理卡 2 | 108 passed | 阻塞；未执行 | 4 passed，3 项 NIXL READ 未执行 | passed |

当前证据入口：[CUDA 0.28](results/cuda-vllm028/summary.json)、[MetaX 固定版本复测](results/metax-pinned/summary.json)、[Ascend 固定版本复测](results/ascend-pinned/summary.json)。CUDA 完整重跑本表各项；MetaX 重跑 unit/GMS，Ascend 重跑 unit，其他厂商媒体用例复用本轮先前结果，未重复宣称为重新执行。每个 suite 保存命令、包版本和测试时源码 SHA256，summary 如实记录与当前源码的差异。CUDA 0.24 的 [旧记录](results/cuda/summary.json) 仅为历史证据，不代表当前基线。

宿主机 Git HEAD 不完全一致，以文件 SHA256 判断代码同步状态。CUDA 新容器采用官方 Rust 扩展和工作区 Python/GMS 源码，没有重新构建本分支 Rust M04/M06；这些模块在 0.28 上需要另行完整回归。官方镜像的 OpenCV 缺少视频后端，CPU VP9 需要 bootstrap 中的额外 wheel，不能把此依赖缺失当作 vLLM 降级理由。

## GMS 已交付能力

复用 `VMMDevice` 和现有 FD/SCM_RIGHTS 协议，新增 `MetaXVMM`。平台选择优先于 PyTorch 的 CUDA 兼容 API；MACA SDK 延迟加载，设备身份使用真实 UUID，实例不允许切换设备或继承 fork 后继续调用。

MACA 原语验证发现：64 MiB 分配粒度，POSIX FD 可导入导出，但同一映射从 RW 改为 RO 时 SDK 返回成功、实际权限仍为 RW。新建 RO 映射的权限查询正确。因此提交权重前先同步，再在原虚拟地址解除映射并重新映射为 RO，查询权限成功后才发布；失败锁存并阻止发布。证据：[原地切换失败](results/metax/probe/mode-1.txt)、[新映射 RO 成功](results/metax/probe/mode-2.txt)。

CUDA/MetaX 真机测试分别覆盖：

1. 写客户端提交后直接退出，新进程两次导入服务端 allocation，逐字节回读一致。
2. 未提交客户端直接退出，后续 writer 可以接管，提交和读取正常。
3. PyTorch MemPool 三轮 suspend/resume 后权重地址和计算结果一致，KV 区域可重新使用。
4. Qwen3-0.6B 实际 vLLM GMS V1 两轮 sleep/wake 后生成 token 一致；CUDA 0.28 和 MetaX 0.26 均使用上游原生 SleepModeBackend，未保留 CUDA 0.24 allocator 兼容分支。

旧接口额外执行了 6 项 tensor/module 测试，不能由此推导旧 vLLM loader、SGLang、TensorRT-LLM 或完整进程 checkpoint 恢复已支持。实际引擎测试为单卡 eager、同进程 sleep/wake；跨进程内存存活由独立 GMS 客户端测试证明。

### Ascend GMS 阻塞原因（25.2.0 历史环境）

当前组合可以物理分配、预留地址、映射和释放，默认共享 token 也可导出，但不满足现有 GMS 的两个必要契约：

- 权限设置/查询返回 `207000`，运行时明确报告 `halMemSetAccess does not exist` 和 `halMemGetAccess does not exist`。
- POSIX 共享类型的物理分配返回 `100000`；默认共享值是 token，不能作为 Unix FD 交给 SCM_RIGHTS。

证据：[默认共享类型与权限](results/ascend/probe/mode-0.txt)、[POSIX 类型](results/ascend/probe/mode-1.txt)。工厂和平台预检查明确拒绝当前 Ascend GMS，未用空实现绕过。下一步需要可提供权限接口的兼容驱动栈；如果仍只有 token 共享，还需要设计带版本的 token/PID 协议及恢复验证。这是当前认证组合的结论，不代表所有 Ascend 设备都没有 VMM。

已进一步核实缺失符号来自宿主机只读挂入容器的 `libascend_hal.so`，两侧哈希一致；[挂载与符号证据](results/ascend/driver-origin.json)。这是宿主机驱动栈与 CANN 的配套能力问题，不是容器内 vLLM 的版本问题；仅更换 CANN 不会增加该驱动库的导出接口。

## 多模态已交付能力

- 媒体描述校验 schema、shape、dtype、字节数和空间；未知 dtype 不再默认为 float32。张量先 detach/contiguous，非 CUDA 设备走显式 CPU staging，避免把 MACA/NPU 指针当作 CUDA 指针。
- 三平台 LOCAL FP32/FP16/BF16 设备往返数据一致；CUDA、MetaX 的 NIXL READ 同样通过，MetaX 走 CPU staging。当前 Ascend 镜像没有 NIXL Python 包，认证路径为 LOCAL。
- LOCAL 文件接收后及时释放；保存/读取取消及批量接收部分失败有清理测试。NIXL READ 的已提交读取在取消后继续等待终态，再回收缓冲区。
- 三平台 CPU VP9 解码均重复执行五轮。非 CUDA 平台拒绝 NVDEC；没有认证厂商加速解码。MetaX 镜像的 AV1 解码未通过，不能扩大为全部视频格式支持。
- 三平台 Qwen3-VL-2B 独立编码器 → LOCAL embedding → 聚合 LLM → frontend，通过红/蓝图像识别、4 请求并发、流式响应、取消、非法图像拒绝及后续请求恢复。每轮使用独立 TMPDIR，结束后 `encoder_cache.*.safetensors` 残留为零。

本轮编码器和 LLM 是同一主机、同一物理卡上的独立进程，TP1/DP1/eager；没有验证跨节点或 E/P/D 三阶段组合。华为 0.23 的 `mm_encoder_only` 会触发语言模型 meta tensor 预热失败，因此使用完整模型初始化后取出视觉模块；部署预算仍按完整模型显存计算，未认证语言权重释放。CUDA/MetaX 保留 encoder-only 初始化路径。

## 手动复现

先按 M03/M06 的脚本准备开发容器并挂载源码、`/NFS_62`。已有环境只需对 CUDA/MetaX 增加 GMS 可编辑安装，不重装厂商 torch/vLLM：

```bash
cd /workspace/dynamo
source /opt/dynamo/venv/bin/activate
python -m pip install --no-deps --no-build-isolation -e lib/gpu_memory_service
```

以沐曦为例，在容器内执行；输出目录必须尚不存在，重复运行请更换 `run_root`：

```bash
cd /workspace/dynamo
source /opt/dynamo/venv/bin/activate
run_root=/var/cache/dynamo-metax/m07/manual-20261021
for suite in unit media gms legacy encoder; do
    python adapter/m07/run.py --platform metax --device 2 \
        --suite "$suite" --output "$run_root/$suite" || break
done
```

NVIDIA 先按 [cuda-dev.sh](../platforms/cuda/cuda-dev.sh) 创建官方 1.5.1 开发容器，再使用 `--platform cuda --device 1`。华为先 `source /usr/local/Ascend/ascend-toolkit/set_env.sh`，使用 `--platform ascend --device 2`，只运行 `unit media encoder`；不要为绕过 GMS 阻塞安装 CUDA wheel。脚本把物理卡映射为逻辑卡 0；测试端口使用既有 M03 端口以及 encoder 8091/20096，不应在同一容器并发启动多组服务。

模型路径固定为 `/NFS_62/Models/Qwen_Series/Qwen3-0.6B` 和 `/NFS_62/Models/Qwen_Series/Qwen3-VL-2B-Instruct`。GMS 模型可以通过 `--model` 覆盖。媒体 VP9 fixture 已在本目录；需要重建时使用带 `libvpx-vp9` 的 FFmpeg 执行 `bash adapter/m07/make-video.sh`。

原语调查可单独执行；`DEVICE` 是当前可见设备编号，`OUTPUT` 必须新建：

```bash
PLATFORM=metax DEVICE=2 OUTPUT=/tmp/m07-metax-vmm bash adapter/m07/probe.sh
# 华为先加载 CANN 环境：
PLATFORM=ascend DEVICE=2 OUTPUT=/tmp/m07-ascend-vmm bash adapter/m07/probe.sh
```

`probe.sh` 本身完成后返回成功，不表示所有原语通过。检查每个 `mode-*.exit`：0 成功，4 权限契约不满足，1 SDK/导出失败，3 清理失败，124 超时。

本轮完整原始日志在各主机的 `/var/cache/dynamo-{cuda,metax,ascend}/m07/`。`collect.py` 按本轮目录命名收集 `closed-unit`、`closed-encoder`、`verified-media/gms/legacy/probe`，保留历史哈希并生成 license 元数据；不是自动修改测试结论的工具。仓库中的文本日志仅清理行尾空白，完整原始日志的 SHA256 保存在 summary 中。

## 剩余工作

| 项目 | 当前边界 / 下一步 |
| --- | --- |
| Ascend GMS（暂缓） | 保留 [候选设计](ascend-gms-design.md)；后续再决定是否实施 token 共享与权重保活，完整模式仍需解决只读契约 |
| 完整 GMS 恢复 | vLLM 新进程重建、checkpoint/CRIU、完整旧 loader 和更多引擎尚未认证 |
| Omni stage（暂缓） | CUDA/MetaX 原生聚合三阶段已通过；独立三阶段完整 payload 通道与华为原生库退出异常记录后暂缓；[后续证据](followup.md) |
| NIXL WRITE 故障协议 | WRITE v2 有界隔离及晚到完成后回收已在 CUDA/MetaX 通过；Ascend 缺 NIXL Python，永久失联/原生 peer abort 仍未闭环 |
| 崩溃后的媒体回收 | LOCAL lease/TTL/GC 已完成；所有 worker 退出后下一 worker 启动才恢复 GC；READ/WRITE 未知 DMA 状态保留内存，不认证无限故障回收 |
| 视频/部署组合 | 三平台十分钟 VP9 抽帧压力、取消恢复及不同卡图文编码器通过；其他 codec/硬件加速/跨节点/E-P-D 组合待验收 |

以上边界同时适用于解释 CUDA 基线；存在上游入口不等于这次已经验证。
