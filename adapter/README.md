<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 芯片适配工作流

使用同一套功能契约，将新芯片从环境准备推进到 M01–M10 验收与离线交付。当前工作流支持本地、SSH、已有容器和指定 Kubernetes context 的任务执行；Codex/Claude 可接收源码实施任务，GPT Responses 接口通过受限文件工具实施源码修改并生成结构化计划。AI 返回成功不直接更新认证状态。

**工作流 1.0.0 定版：四组补齐工作已落地；真实 AI 账号调用按用户决定暂缓，未计为实测通过。** 见 [完整方案定版审计](workflow/finalization.md) 和 [逐项检查清单](contracts/workflow-audit.yaml)。

[本次改造验证](workflow/validation.md) · [总体设计](design.md) · [操作手册](workflow/README.md) · [模块功能目录](modules.md) · [证据矩阵](support-matrix.md) · [版本基线](version-baselines.md) · [遗留事项](contracts/issues.yaml) · [已封存交付](releases/1.5.1-heyi-m10/index.json)

## 快速开始

跟随新的 Dynamo 稳定版时，使用 [上游升级工作流](upgrades/README.md)：固定旧上游 / HEYI / 新上游三方身份，在隔离 worktree 中迁移，再复用本目录的 M01–M10 验收。当前待准备镜像见 [1.6.2 升级说明](upgrades/images-1.6.2.md)；升级意向不会改写现有已验证基线。

```bash
# 在仓库根目录；可使用现有开发环境或独立 CPU 虚拟环境。
python3 -m pip install -r adapter/workflow/requirements.txt
python3 -m adapter.workflow --help

# 按目标机器填写环境文件；SSH 密钥和 kubeconfig 留在操作系统凭据配置中。
cp adapter/environments/examples/cluster.yaml /tmp/chip-environment.yaml
# 编辑 /tmp/chip-environment.yaml，先保持 execution_enabled: false。
python3 -m adapter.workflow init \
  --platform metax --chip C500 \
  --environment /tmp/chip-environment.yaml \
  --campaign adapter/results/metax/C500/onboarding \
  --features M01-F01 M01-F02
python3 -m adapter.workflow doctor --campaign adapter/results/metax/C500/onboarding
python3 -m adapter.workflow plan --campaign adapter/results/metax/C500/onboarding
python3 -m adapter.workflow status --campaign adapter/results/metax/C500/onboarding
```

上述是操作示例，不代表目标环境已经预检通过。未配置完整的模板会明确失败。新增平台先创建其真实 `container/platforms/<id>.json` 版本锁，再执行 `onboard --platform <id> --stack-profile container/platforms/<id>.json --resource-key <官方资源名>`；45 个共享功能自动可用，不复制厂商模块目录。

## 目录职责

| 目录 | 内容 |
| --- | --- |
| [contracts](contracts/README.md) | 顶层规则、[数据格式](contracts/schemas/README.md)、结构化遗留项 |
| [upgrades](upgrades/README.md) | 上游升级目标、迁移控制层设计、镜像准备与操作顺序 |
| [modules/<模块>/features/<功能>](modules/README.md) | 稳定编号、范围、依赖、断言与功能入口 |
| [modules/<模块>/common](modules/README.md) | 本模块共享工具；各模块 README → common README → 功能组 README 可逐层查看脚本作用 |
| [workflow](workflow/README.md) | 预检、状态、执行器、AI handoff、证据门禁和报告；regression/cluster 是跨模块回归库 |
| [platforms/<平台>](platforms/README.md) | 规范资源名、版本锁引用、平台 bringup/cluster 入口 |
| [environments](environments/README.md) | 现场配置说明与[无凭据模板](environments/examples/README.md) |
| [results/<平台>/<芯片>/<campaign>](results/README.md) | 编号后的历史索引与结果；新现场结果同样在此目录 |
| [releases](releases/README.md) | 外部已封存离线包索引，不放巨大镜像 |
| [tools](tools/README.md) | 项目统计和开发辅助工具 |

历史执行说明保存在各模块 execution.md；设计中旧的阶段状态不作为当前门禁，权威判断来自 feature 契约与对应 campaign。功能索引已经统一，不再保留兼容目录。
