<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Dynamo 1.6.2 升级镜像准备

2026-11-10 核实的目标如下。本阶段准备厂商基础镜像；最终 HEYI runtime/dev/tools/operator 和离线包要经过后续适配、构建与 M10 验证才产生。

| 平台 | 目标 | 官方入口 |
| --- | --- | --- |
| CUDA / 当前 x86_64 NVIDIA 主机 | Dynamo 1.6.2，配套 vLLM **0.31.0**，amd64 | [NGC 镜像](https://catalog.ngc.nvidia.com/orgs/nvidia/ai-dynamo/containers/vllm-runtime/1.6.2)、[1.6.2 发布依赖表](https://github.com/ai-dynamo/dynamo/releases/tag/v1.6.2) |
| 华为 Ascend 910B3 / ARM64 主机 | vLLM-Ascend **0.30.0**，A2/910B 镜像 | [官方安装说明](https://docs.vllm.ai/projects/ascend/en/v0.30.0/getting_started/installation.html) |
| 沐曦 C500 / x86_64 主机 | vLLM-MetaX **0.28.0**，MACA AI 3.8.2.401、Torch 2.10、Python 3.10、Ubuntu 22.04 | [官方运行示例](https://vllm-metax.readthedocs.io/en/v0.28.0/release/common/index.html)、[下载中心](https://developer.metax-tech.com/softnova/docker?chip_name=%E6%9B%A6%E4%BA%91C500%E7%B3%BB%E5%88%97&package_name=vllm-metax&dimension=docker&deliver_type=%E5%88%86%E5%B1%82%E5%8C%85) |

分别在对应架构主机执行：

```bash
# NVIDIA / amd64
docker pull --platform linux/amd64 nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.6.2

# Ascend 910B3 / arm64；不要选仅面向 A3 的 -a3 标签
docker pull --platform linux/arm64 quay.io/ascend/vllm-ascend:v0.30.0

# MetaX / amd64；按开发者中心说明登录，不把凭据写进脚本或仓库
docker login cr.metax-tech.com
docker pull --platform linux/amd64 cr.metax-tech.com/public-ai-release/maca/vllm-metax:0.28.0-maca.ai3.8.2.401-torch2.10-py310-ubuntu22.04-amd64
```

NVIDIA registry 已通过只读 manifest 请求确认 `1.6.2` 同时含 amd64/arm64。观察到的索引摘要为 `sha256:fd7f975e6da114d745611176c67b2ebedd3f891bf80211dbf611e8d154d313a0`，amd64 子清单为 `sha256:6b14a01c813c0a4ab8a5ac2c13a6403b639ac1ed08e816c16ad9eda0f93af75f`；后续以实际拉取观察和冻结 receipt 为准。另两款地址有官方文档来源，本次没有拉取镜像层；MetaX registry 匿名查询返回 401，未声称其登录后摘要已经实测。

沐曦这里选的是官方文档明确给出的 py310 标签，没有猜测 py312 标签。若开发者中心提供并希望改用 py312，先复制其完整 pull 地址并修改目标意向，再 init；已经冻结的会话不能换镜像冒充同一输入。Dynamo 的 Python/二进制 ABI、CANN/MACA 与宿主机驱动匹配仍由后续 doctor/build 检查，不因镜像可拉取就视为兼容。

## 拉取后生成观察记录

提交/同步工作流代码到相应主机后，使用已经下载的镜像执行轻量探测。例如：

```bash
python3 -m adapter.workflow upgrade probe-image \
  --plan adapter/upgrades/targets/dynamo-1.6.2.yaml \
  --platform ascend --image quay.io/ascend/vllm-ascend:v0.30.0 \
  --output adapter/results/workflow/upstream/image-preparation/images/ascend.json
```

CUDA / MetaX 替换对应 platform 和完整镜像地址，output 分别为 cuda.json / metax.json。探测只运行包元数据读取，不启动推理、不加载模型、不挂载硬件，也不自行拉取。它要求官方仓库 RepoDigest、目标架构、vLLM 和插件版本匹配，并记录镜像 ID、Python、Torch、NIXL/Mooncake 等实际版本。标签中写着某个版本不能代替这个核验。

把三份 JSON 回收到协调器实际 upgrade session 的 `images/` 子目录，使用 `bind-image` 固定，随后再允许 `prepare`。首次 CLI 报错若是 Python 可执行文件路径不同，先 inspect 确认官方环境并修订目标计划，不能通过忽略版本检查继续。

若需离线转移基础镜像，可用 `docker save` 与 `sha256sum`，同时保存 probe-image 的 registry provenance；注意 save/load 可能不保留 RepoDigests。本流程不会把没有 registry 摘要的本地 tag 当作已验证来源。最终跨平台离线交付仍使用 M10，而不是这次基础镜像下载。
