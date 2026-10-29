<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ascend 910B3 开发环境与验收

本次使用官方 `quay.io/ascend/vllm-ascend:v0.23.0`，在华为机器独立部署，不混用其他厂商设备。2026-10-16 完成基础文本推理、双 Worker KV 路由及 M01 平台身份/能力/发现验收，标记为有限支持。

## 环境与路径

| 项目 | 本次配置 |
| --- | --- |
| 主机 | `huawei@192.168.10.95`，Ubuntu 22.04，鲲鹏 920 / ARM64 |
| 代码 | `/home/huawei/CJ_workspaces/dynamo`，挂载至 `/workspace/dynamo` |
| 容器 | `dynamo-ascend-dev` |
| 镜像 digest | `sha256:471744200c5d0c768c1f6c6a6816dcae0fd551de2487d4d129b6fb750024ce6a` |
| 设备 | Ascend 910B3；基础推理用卡 0，KV Worker 用卡 1、2，探测用卡 3 |
| 厂商栈 | vLLM `0.23.0+empty`、vllm-ascend `0.23.0`、torch `2.10.0+cpu`、torch-npu `2.10.0.post4` |
| 系统栈 | 驱动 `25.2.0`、固件 `7.5.0.3.220`、CANN `9.1.0`、Python `3.12.13` |
| 开发环境 | `/opt/dynamo/venv`，继承镜像 system site-packages；工具单独安装至 `/opt/dynamo/lint` |
| 缓存 | 命名卷 `dynamo-ascend-cache` → `/var/cache/dynamo-ascend` |
| 模型 | `/var/cache/dynamo-ascend/models/Qwen3-0.6B` |
| 模型 revision | `c1899de289a04d12100db370d81485cdf75e47ca`；本次从沐曦已有缓存复制相同权重 |
| 端口 | 宿主 `127.0.0.1:18000/18001` → 容器 `8000/8001` |

`torch` 的 `+cpu` 是该镜像的实际包版本；NPU 由 `torch-npu` 提供，不能据包名判断没有加速器。保留镜像 entrypoint 和 ACL 所需的 `PYTHONPATH`，不要用源码路径覆盖整个变量。

## 分步重建

在华为宿主机执行；同名容器已经存在时不要重跑 `create`，停止的容器用 `docker start dynamo-ascend-dev` 恢复。

```bash
cd /home/huawei/CJ_workspaces/dynamo
bash adapter/platforms/ascend/ascend-dev.sh create
bash adapter/platforms/ascend/ascend-dev.sh setup
bash adapter/platforms/ascend/ascend-dev.sh build
bash adapter/platforms/ascend/ascend-dev.sh model  # 已有模型时跳过
bash adapter/platforms/ascend/ascend-dev.sh shell
```

[开发脚本](ascend-dev.sh) 明确包含系统依赖、`python3 -m venv --system-site-packages /opt/dynamo/venv`、Rust 工具链、Python editable 安装及 `maturin develop --locked --uv`。不要安装仓库的 `.[vllm]` extra，它不是本次厂商栈。Rust 的 ARM 编译选项在华为脚本内覆盖为 `target-cpu=generic`，避免仓库 Neoverse N1 默认生成鲲鹏 920 无法执行的指令；不改变其他平台的构建配置。

Python 修改后重启服务即可生效；Rust 修改后停止服务、重跑 `build`，再启动。venv 和 Rust 工具链在容器可写层，模型和构建缓存在命名卷，删除容器后需要重建开发环境。

## 启动与验收

以下在容器内执行。每个启动脚本各占一个终端；`setsid` 使停止脚本时能清理同组 Worker 和引擎进程。

```bash
cd /workspace/dynamo
export MODEL_PATH=/var/cache/dynamo-ascend/models/Qwen3-0.6B
setsid --wait bash adapter/platforms/ascend/ascend-serve.sh
```

另一个终端启动双 Worker KV 路由：

```bash
cd /workspace/dynamo
export MODEL_PATH=/var/cache/dynamo-ascend/models/Qwen3-0.6B
setsid --wait bash adapter/platforms/ascend/ascend-kv-serve.sh
```

另一个终端运行相同测试断言：

```bash
cd /workspace/dynamo
export MODEL_PATH=/var/cache/dynamo-ascend/models/Qwen3-0.6B
bash adapter/platforms/ascend/ascend-smoke.sh basic --output /tmp/ascend-basic.json
bash adapter/platforms/ascend/ascend-smoke.sh kv --output /tmp/ascend-kv.json
bash adapter/platforms/ascend/ascend-smoke.sh platform --output /tmp/ascend-platform.json
```

基础推理使用 round-robin；KV 验收使用 `kv` 路由、真实 ZMQ 事件、两个 TP=1/DP=1 Worker，Ascend 的 block size 显式设置为 128。均为 eager、4096 上下文、最多 4 个序列、每 Worker 1 GiB KV cache；使用 file discovery / TCP / ZMQ，无需 etcd/NATS。测试复用 `adapter/platforms/metax/` 下的断言脚本，通过 `--platform ascend` 及独立命名空间区分；文件所在目录不代表测试加载 MetaX SDK。

停止时向对应 `ascend-serve.sh` 或 `ascend-kv-serve.sh` 启动进程发送 SIGTERM，等待其进程组退出；不要按通用 Python 进程名批量终止主机任务。

## 平台身份与能力边界

`DYN_PLATFORM=cpu` 用于 frontend，`DYN_PLATFORM=ascend` 用于 Worker。Ascend provider 延迟加载 torch-npu、ACL 和 DCMI。ACL 将可见逻辑编号映射到物理设备；DCMI 查询 silicon die ID 和 PCI 地址；torch-npu 查询型号与显存。910B 当前 SDK 的 UUID 全零，因此契约 `uuid` 字段使用 `ascend-die:<真实芯片标识>`，它是稳定的不透明身份，不是伪造的标准 UUID。NUMA 未探测，保留缺省。

该路径通过现有 Rust schema 1 传递 `platform_id=ascend`，不新增旧 `DeviceType` 枚举，也不标成 CUDA。前后端都须使用支持本契约的运行时。必需能力不满足时，在引擎加载与服务发现注册之前失败；`pd.transfer`、RDMA、KVBM、GMS、功耗控制仍为 unknown。

vLLM 0.23.0 没有 `VLLMUnprocessableEntityError`，Dynamo 的异常适配因此按 API 是否存在选择具体异常类型。0.23 保留 400/404，0.26 保留 400/404/422，新版共同基类路径保留；普通 `ValueError` 和运行错误不转换为客户端请求错误。这是同一分支内的局部接口兼容，不修改厂商 vLLM 或 torch 安装包。

## 验收记录

- [原生引擎参考](ascend-native-results.json) 与 [Dynamo 基础推理](ascend-basic-results.json)：模型注册、非流式、流式、四路并发通过；取消计数从 0 到 1，取消后请求成功。固定提示词的输出文本相同，prompt/completion/total tokens 均为 5/16/21；这不是完整精度评测。
- [KV 路由](ascend-kv-results.json)：两个 Worker，各 73 个 KV block，block size=128、DP rank=0；真实数组格式事件经过 Rust 解码完成分叉前缀选择、淘汰、清空和清空后重建。两个 Worker 均观察到两次 AllBlocksCleared，填满的 Worker 观察到 37 次 BlockRemoved。
- [M01 平台契约](../../m01/results/ascend/ascend-platform-results.json)：两个设备有不同的真实 die ID，`platform_id=ascend`，旧 `device_type=null`；重复配置幂等，未知 schema/冲突配置拒绝；要求未验证的 `pd.transfer` 时退出且不发布发现记录。
- 沐曦加载本次共享代码后重新验收：[基础推理](metax-regression-basic-results.json)、[KV 路由](metax-regression-kv-results.json)、[平台契约](../../m01/results/ascend/metax-regression-platform-results.json) 均通过；原有历史证据未覆盖。
- 单元测试覆盖 platform、WorkerFactory、异常映射、InstrumentedScheduler、KV reset 与事件 API：华为 314 项通过，沐曦 315 项通过。vLLM 0.23 缺少 422 异常类，测试只实例化实际存在的类型，同时用隔离的假模块验证三代异常 API。

0.23 的 KV 事件使用 positional array，0.26 使用 tagged map；现有 Rust `zmq_wire` 的 `visit_seq`/`visit_map` 已支持两者。更新测试以覆盖两个已验证版本，不改写厂商事件，也不为此次适配增加 Rust 协议补丁。

镜像、绑定、源码及模型 SHA256 见 [验收汇总](ascend-validation.json)，已核对两台机器的模型文件完全一致、本地与华为的适配源码完全一致；本次基线为 `cac974691d` 上的未提交增量。构建与服务日志位于华为容器 `/var/cache/dynamo-ascend/logs/`，测试命令可按上节复现。

## 限制

- 这是指定镜像、910B3、Qwen3-0.6B 的功能冒烟环境，不是正式发布镜像或性能认证。尚未验证多卡 TP/DP、多节点、P/D、NIXL、KVBM、GMS、多模态、LoRA 或量化。
- 该主机 `npu-smi` board 检查报告 Compatibility NOK，torch-npu 提示 CANN/HDK 组合限制。记录实际测试结果不等于厂商认可该驱动/固件组合；本次没有升级宿主驱动。正式环境需要另外核对驱动/固件兼容性。
- 开发安装使用 `--no-deps` 保留厂商栈，未安装仿真模块 aisimulate，不声称完整 `pip check` 通过。正式依赖拆分和发布产物属于 M02 后续工作。
- CUDA 真机回归仍待单独执行，不能把 CPU mock 测试当作 NVIDIA 硬件验收。
