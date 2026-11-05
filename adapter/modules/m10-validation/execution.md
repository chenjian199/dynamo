<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M10 最终镜像与离线交付验收

状态：本轮约定范围的最终镜像回归已完成，按明确限制进行阶段性交付。CUDA/MetaX 使用 FPM 修复后的 `-r1`；Ascend 使用补齐 Hybrid Connector 的 `-r3`。原候选与修复后证据分别保留。包是否封存以统一索引、包内 `validation.json` 和外层 SHA256 为准，存在 `IN_PROGRESS` 时仍不可按最终包交付。基线提交 `4b1efe773c9af518dfe2050ff753e270a7e84b7b`。

本轮锁定 CUDA vLLM 0.28.0、MetaX 0.26.0、Ascend 0.23.0，构建 runtime、其派生 dev 和 Profiler/Planner 工具镜像，并重建 amd64/arm64 Operator。复用厂商基础镜像 digest；不使用 docker commit，不以开发容器源码覆盖代替最终运行镜像。

主索引目录：`/home/metax/CJ_workspace/dynamo-m10/1.5.1-heyi-m10-4b1efe773c-r1/`。用户已确认 CUDA、MetaX 在本机归档，Ascend 在 52 主机 `/root/CJ_workspace/dynamo-m10/1.5.1-heyi-m10-4b1efe773c-r3/` 原生归档，本机保存统一索引和校验清单。每个平台单独保存镜像归档、部署清单、Helm/依赖包、版本及 SHA256、验收结果和限制说明。模型不放入离线镜像包。

CUDA 最新归档位于同级 `1.5.1-heyi-m10-4b1efe773c-r1-config1/`，它是配置元数据修订包，镜像仍为原先验收的 r1。大模型离线输入的 `dgdr.hardware.totalGpus` 从小模型继承值 2 修正为 8，与实际 TP4 两侧和 Planner 预算一致；同一 tools 镜像已验证 Worker、画像身份及生成的 Planner 配置不变。原归档未修改，统一索引保留新旧包的路径和 SHA256。Ascend r3 的对应元数据修正为 16，原始采样输入另行保留。

开发主机链路中断后，CUDA/MetaX 在本机原生 amd64 构建，Ascend 在 52 主机的隔离 Docker daemon 原生 arm64 构建；CUDA H800、Ascend 910B3 集群用于实际回归。MetaX 本机功能可以复测，缺失的 Kubernetes 环境继续明确记为待验证。既有暂缓能力与上游已复现问题不自动变为通过。

所有实际构建修正、源文件哈希、依赖变化、失败尝试与复测结果均需记录。镜像构建成功只证明产物生成，不等于硬件和集群验收通过。

## 产物约定

每个平台包包含以下四个镜像角色，架构为 CUDA/MetaX `amd64`、Ascend `arm64`。Operator 的 amd64 产物由 CUDA 和 MetaX 共用。

| 角色 | 仓库名 | 用途 |
| --- | --- | --- |
| runtime | `heyi/dynamo-vllm-<platform>-runtime` | 已安装 wheel、Rust 绑定、KVBM native 工具和厂商补丁 |
| dev | `heyi/dynamo-vllm-<platform>-dev` | 从该 runtime 派生，增加 Rust、编译调试工具与 editable 安装入口 |
| tools | `heyi/dynamo-tools-<platform>` | 从该 runtime 派生，封装 CPU Profiler/Planner、AIPerf 和部署辅助工具 |
| operator | `heyi/dynamo-operator` | 同一源码生成的控制器、CRD 安装及 DGD 参数合并工具 |

CUDA/MetaX runtime/dev/tools 标签为 `1.5.1-heyi-m10-4b1efe773c-r1`。Ascend 为补齐 Hybrid PD，使用经本轮回归的 `1.5.1-heyi-m10-4b1efe773c-r3`；Operator 源码未变，仍为 `1.5.1-heyi-m10-4b1efe773c-amd64` 或 `-arm64`。候选是否通过以包内逐项验收记录为准。所有依赖保持厂商版本保护；已存在的厂商 pip 冲突与新增冲突分开记录。

## 执行入口

- `prepare-tools.py` 固定工具镜像上下文，记录依赖文件和两个外部二进制的 SHA256。`build-derived.sh` 在 runtime 构建成功且 image ID 一致后生成 dev/tools。
- `render.py` 复用已验证的引擎参数，生成两平台聚合、PD、跨节点 PD、Grove、小/大模型 Planner 与设备测试 Job。去除历史应用源码和依赖覆盖；模型继续使用节点 hostPath。
- `prepare-harness.py` 只复制测试驱动和测试辅助文件，不复制 `dynamo` 实现。`check-installed.py` 检查 Dynamo 导入来自 wheel 安装、不是 editable，并记录绑定哈希及补丁身份。官方 Ascend 内嵌的 vLLM editable 安装保留厂商原状。
- `run-hardware.sh` 在隔离的双卡容器执行 CPU 隔离、独立 KVBM、推理/KV 路由、TP2 缓存卸载及多模态/GMS；失败保留日志，独立用例继续执行。Ascend GMS 和 NIXL 媒体路径不冒充已支持。
- `offline-profile.py` 调用镜像内标准 Profiler 的采样、插值、持久化及配置组装接口；`--reuse-profile` 验证画像身份后重用文件。它不改写测量数据或拟合保护。
- `upgrade-operator.sh` 用于本轮已存在的 `heyi-m08` release 升级，保留旧 values/历史/CRD 记录；不是可直接用于任意新集群的安装脚本。

## 离线包与导入

包内包含 `images.tar.gz`、Chart/CLI、部署文件、测试驱动、构建及验收证据、`validation.json`、限制说明和 `SHA256SUMS`。镜像导出合并共享层；不打包模型、SSH 密钥、kubeconfig 或业务数据。当前部署采用 Kubernetes 服务发现、TCP 请求面、ZMQ 事件面，etcd/NATS 不作为必需运行依赖。Grove/KAI 等可选功能必须配套其锁定依赖和启用配置。

`bundle.py` 只接受有实际证据哈希且所有必需检查通过的记录；非必需限制项必须注明原因。封包前再核对本机 image ID 和架构，防止标签变化。包内的通过项不包括旧源码覆盖测试。

完成交付后，在每个目标节点解压并执行：

```bash
sha256sum -c <平台离线包>.tar.sha256
tar -xf <平台离线包>.tar
cd <平台离线包目录>
bash import.sh containerd  # 普通 containerd；K3s 使用 k3s，Docker 使用 docker
```

`import.sh` 校验文件后仅导入镜像，不安装驱动或修改集群工作负载。部署前需按现场节点、模型路径和设备资源调整清单，逐个场景执行，不能一次 apply 整个目录。新集群的具体安装配置与本轮已验证清单一起归档。

## 本轮发现并修复的问题

- MetaX DP2/LoRA 启动连续三次停在 `FpmEventRelay::new` 的 Rust 等待。Python 构造器现释放 GIL 后等待 Rust 初始化，避免阻塞其他需要 Python 的运行时任务。诊断绑定通过后，`-r1` 最终镜像已独立通过 DP2/LoRA、ngram 和 INT8；诊断覆盖测试未混入最终通过记录。补丁文件 SHA256 为 `f410d8306325923c81c1d6010b2b802660faf0a5f68207e766e60b8689488206`。
- 华为测试脚本曾覆盖厂商 `PYTHONPATH`，丢失 ACL 路径；已改为追加测试插件路径，之后六组硬件测试通过。
- CUDA 离线 Cargo 缓存初次复制缺少源码；完整 registry 包恢复后，断网全量 editable 构建通过。
- DGDR 原生 `runAsNonRoot` 默认值与本轮 hostPath 输出的 root 配置冲突；清单明确设置匹配的 securityContext 后可运行，不修改集群安全策略。
- CUDA 厂商 OpenCV 不带 CPU VP9 解码。独立 `dynamo-vllm-cuda-media` 可选镜像安装校验过的同版本 OpenCV wheel；`-r1` 可选镜像的 15 项 media/GMS 测试通过。基础 runtime 的 codec 限制仍记录。
- 代理下载曾返回截断 wheel；始终保留 SHA256 校验，通过断点续传完整官方 wheel 解决，不绕过校验。
- M10 监控清单原先沿用 M09 部署名称，漏采带 `-r1` 的离线画像服务；`render-services.py` 补齐实际名称。原先画像加载/重启成功但在线 `model_not_ready` 的记录保留，修正抓取后单独补测，不将旧记录算作规划通过。

## 华为 DeepSeek V4 在线 PD 新发现

2026-11-04，最终镜像的 DeepSeek V4 Flash w8a8 / TP8 / EP8 离线采样、6 个 prefill 点和 9 个 decode 点的静态画像检查通过。后续 1P1D 在线首请求返回 500，decode Worker 因厂商普通 Mooncake Connector 的 `remote_block_size(8) not divisible by kernel_size(128)` 断言退出。这不能标为在线画像规划通过，也不能仅凭该异常归因于宿主机驱动。

r1 的 `AscendMooncakeConnectorProtocol` 原样透传厂商返回的 KV 元数据。当时镜像中已有 `mooncake_hybrid_connector.py`，但适配未接入该协议；[上游当前 DeepSeek V4 指南](https://docs.vllm.ai/projects/ascend/en/main/tutorials/models/DeepSeek-V4-Flash.html) 使用 Hybrid Connector，不能据此推定固定的 0.23.0 镜像、现有 TP/EP 配置已通过认证。异常日志、厂商源码哈希和诊断保存在华为包的 `evidence/r1/deepseek-online-crash-20261104/`。普通 Connector 的独立原生服务端到端复现尚未完成；没有绕过断言或强改块大小。

用户已确认本轮补齐 Hybrid Connector、重建华为镜像并补测后再归档。新增协议支持厂商注册名 `MooncakeHybridConnector`，也支持 `MooncakeConnector` 加 `vllm_ascend.distributed.kv_transfer.kv_p2p.mooncake_hybrid_connector` 显式模块路径。按缓存组原样传递 block IDs、prompt 块数、rank 映射及完成确认标识，不合成统一块大小，不将普通 Connector 的 `heyi_tp_split` 补丁用于 Hybrid。

`rebuild-app-image.py` 校验原镜像上下文、预期源文件变化及 SHA256，替换 Python 应用 wheel；Rust/KVBM、依赖和引擎版本继续沿用 r1。r3 通过 `--refresh-vendor-patches` 同时安装下述哈希保护的 Hybrid 加载顺序补丁。`hybrid-pd-client.py` 覆盖不同块边界的输入、并发流、取消与后续恢复。r1 的静态画像和失败 PD 证据保留；不能将它们改名算作新候选的实际运行结果。

`rebuild-binding-image.py` 只允许 FPM 源文件变化，并校验所有原构建产物。它重新构建 runtime binding wheel，沿用未变的 native KVBM、依赖 wheel、厂商补丁和版本锁；重新封装 runtime 后再派生 dev/tools。镜像内 `SOURCE.json`、`REBUILD.json` 和 SHA256 记录该增量来源。Operator 源码不受此补丁影响，继续使用原始标签的已测构建。

## 分析器环境隔离

Ascend 厂商 Triton 要求 NumPy 1.26.4、SciPy 1.13.1、psutil 6.0，而 AISimulate/AIPerf 的依赖不同。`tools-ascend.Dockerfile` 保留 `/opt/dynamo/venv` 原厂推理环境，在 `/opt/dynamo/tools-venv` 安装独立 CPU 工具环境。构建清除厂商 `PYTHONPATH` 对工具安装的干扰，要求工具环境 `pip check` 完全通过，并比较原厂环境安装前后的完整包清单。Planner 清单使用工具解释器；普通 runtime/dev 的解释器和引擎版本不变。

CPU 工具环境不安装 vLLM/torch；实际推理仍由所选平台的 runtime Worker 执行。CUDA/MetaX 的工具环境没有新增依赖冲突，保持原有派生方式。

## 严格验收与中断恢复

`audit-profile.py` 核对原始 AIPerf 导出中的阶段、预期/实际完成数、取消和错误；为离线插值挑选同一 sweep 的原始单引擎元数据。它不计算生产 SLA 或跨平台性能优劣。`run-offline-case.sh` 在静态容量预测通过后才执行文件复用、在线规划和重启；原生 `insufficient_data` 不强制改为可用。DGDR 四候选功能检查与独立离线画像检查分开。

2026-11-04 恢复检查发现原候选 CUDA DeepSeek 的第二副本初始化约 418 秒，超过 300 秒负载窗口，扩容检查未通过；新的运行采用预先冻结的 1200 秒负载窗口。华为原小模型排队脚本先于离线画像完成而超时，后续用独立目录重跑，不覆盖旧失败。CUDA 小模型初次采样完整，但严格静态/在线画像检查发现 prefill 原生拟合不可用，需重复采样并保留该失败记录。

三平台 runtime 硬件六组检查、dev 断网全量 editable 编译、CPU 工具契约 221 项与 Rust 契约已有通过记录。MetaX 最终镜像 30 分钟 PD 长稳完成 3936 个请求、123 次取消和 3 次故障注入；这只认证所记录的有界场景。CUDA 与 MetaX 包已封存，其 SHA256 记录在主索引；Ascend 包在自身封存前保留 `IN_PROGRESS` 标记，镜像导出成功不等于完整验收完成。

Hybrid 原生模块的独立导入在 r2 中以 133 退出，日志为 `corrupted size vs. prev_size`；先调用已有的 `prepare_mooncake_libraries()` 后正常退出。r3 将同一 CANN 加载顺序修复通过版本/源码哈希保护补丁应用到 Hybrid 模块。r3 独立导入退出码为 0；r2 保留为失败候选，不覆盖原镜像标签。

Ascend r3 CPU 工具镜像的 256 项平台/PD/画像契约和 29 项协议测试通过；独立 dev 容器在 `--network none` 下完成全部 editable 编译安装。需要真实设备的上游 Mooncake 导入用例不计入 CPU 契约数，Ascend Hybrid 的原生导入单独实测。六组硬件检查和五卡普通 PD 长稳也已通过；画像验证结果见下文；不将这些单项结果外推为全部设计能力已认证。

r3 已通过两轮 DeepSeek V4 Flash w8a8 跨节点 1P1D、两侧 TP8/EP8 的 Hybrid PD 测试。每轮包含 1、127、128、129、512、1024、2048 token 输入、两组并发 2 请求、4 次首 token 后取消及恢复。保存实际 completion token IDs 供聚合路径对照；仅收到完整 token 流不能代替输出一致性检查，也没有认证 Hybrid 在途传输强杀恢复。聚合对照的数值一致性限制和标准 PD 长稳结果见下文；新画像完整流程的最终结果见下文。

`run-ascend-hybrid-tail.sh` 保存最初的 r3 队列：等待原画像全流程成功，再运行聚合对照、DeepSeek 有界扩缩容和硬件/标准 PD/高级功能 Job。原画像拟合失败后，该队列在前置检查处退出，不能继续当作当前的一键入口。后续独立原生对照、有界扩缩容和扩展画像的实际控制脚本保存在包内 `tests/commands/`，分别对应自己的结果目录。`compare-hybrid-outputs.py` 逐条比较原始 token IDs；不清洗或改写差异，也不把诊断完成改为一致性通过。

r3 标准 PD 补测的初始夹具把五个 Worker 放在两张 NPU 上，ADXL 返回 `not support connect with self device`，首个同卡 P/D 配对失败。该记录保存在 `evidence/r3/pd/`，不计为通过。后续使用独立的五卡 Job（`0,1,2,3,4`）在 `evidence/r3/pd-distinct/` 重跑聚合、2P3D、取消及长稳；镜像和引擎代码未变。原 Job 中独立通过的六组硬件检查和高级功能仍按各自结果记录，不把整个失败 Job 改写为成功。

五卡补测已通过：1809 秒、1952 个请求、61 次取消、3 次故障注入，普通 PD 输出对照一致；各进程 FD/线程增长为零，最大 RSS 增长 222756864 字节，低于预设 256 MiB 阈值。该结论只覆盖本轮有界负载。

DeepSeek Hybrid 与聚合的严格 token 对照有 2/12 条不一致，分别为 1-token 和 128-token 输入，原始失败保存在 `results/ascend/hybrid-parity-initial.json`。同一最终镜像中的独立原生 vLLM 服务（无 Dynamo、无 KV Connector）三轮重复测试也在这两条输入上产生变化；原生调用成功不等于输出一致性通过。两节点全部 70 个权重分片及配置/tokenizer 共 77 文件、约 300 GB 的 SHA256 一致。`native-parity-client.py` 保存原生重复性数据，`native-hybrid-pd-client.py` 用于进一步在同一 Decode 引擎上配对本地生成与原生 Hybrid PD。原生 Hybrid 对照已完成两轮共 24 组：相同两个输入在本地前后与 PD 路径出现差异，其余 10 个输入的 token 在全部四类对照中一致。`results/ascend/hybrid-parity-review.json` 记录原始证据 SHA256；诊断完成不等于严格 token 一致性通过，也未定位到原生引擎内部的具体数值原因。新画像完整流程的最终结果见下文；数值一致性限制继续保留。

r3 首轮 DeepSeek 离线采样的 72 份导出全部完整，包含 6 个 prefill 点和 9 个 decode 点。但静态文件初始化中 Decode 仍为 `insufficient_data`，去掉 SLA 约束也无法获得容量预测；原始失败见 `results/ascend/offline-deepseek-initial-bootstrap.json`。独立 OLS 诊断显示这组约 423–459 ms 的平坦测量具有负斜率，该诊断不是原生模型参数，也不替代其预测。原在线流程因此未启动，依赖它的尾部队列退出，不能记为画像全流程通过。

`expand-decode-profile.py` 准备一次独立的完整采样，将 `max-num-seqs` 从 16 提高到 64，拓宽短上下文的批量覆盖；镜像、TP8/EP8、模型与引擎版本不变。它同步更新 Worker 参数和画像身份，保存源输入 SHA256 与预先固定的采样约定。原配置及全部失败数据保留，新曲线不与旧曲线混合，不按拟合结果挑选点或修改就绪保护；新配置已完成下述独立画像流程，结论仅适用于其记录的身份和配置。原生 Hybrid 对照及独立有界扩缩容已完成。后者使用 C16、ISL1024、OSL128、900 秒负载和 120 秒恢复，Planner 自主完成 1→2→1，API 执行成功，最终一个 Worker 就绪且没有终止中的 Worker；测试脚本未修改副本数。负载阶段完成 249 请求、恢复阶段完成 3 请求，API 错误为零；负载 TTFT p95 为 2596.74 ms，超过 1000 ms 目标，另有一个请求返回完整 token 流但没有可见文本，因此不宣称生产 SLA 或文本质量通过。独立扩缩容与离线画像初始化、复用分别验收，不互相替代。

扩展配置的 72 份原始导出已全部通过完整性检查，覆盖 6 个 Prefill 点和 9 个 Decode 点。文件独立初始化在两次新建预测模型中均可得到有效容量，见 `results/ascend/offline-deepseek-expanded-bootstrap.json`；实测采样峰值为 51 个同时运行请求；完整指标回看发现一个采样时刻有 1 个请求排队，已采集的抢占计数均为零。端点退出附近的指标读取失败也保留原始记录，不将指标观测表述为无间隙监控。同身份文件复用、跨节点 Hybrid PD 在线请求与正容量预测、Planner 重启后的文件复用均已通过。原失败画像及其退出码保留，所有新曲线来自完整的新 sweep。结果见 `results/ascend/offline-deepseek-expanded-{bootstrap,online,planning,restart}.json`，实际命令、原始日志和全部测量导出随华为离线包保存。

扩展画像的在线 C2、ISL512、OSL128 负载完成 10 个请求，API 错误为零；TTFT p95 为 17618.31 ms，高于校准目标 1425.36 ms，TPOT p95 为 543.45 ms。负载请求均有可见文本，但 16 条校准请求中有一条空可见文本。该结果认证所记录的接口与规划流程，不认证延迟 SLA 或文本质量；恢复阶段与 Planner 重启的完整结果随包保存。

在线预测连续可用性仍有限制：负载窗口为 5 次有效决策、0 次 `model_not_ready`，恢复窗口为 1 次有效决策、10 次 Decode `model_not_ready`。此时 Planner 维持当前副本及端点下限，API 请求正常，但不能声明所有周期均可预测；具体原因未单独定位。文件初始化、重启复用和有界接口流程通过不消除此限制。
