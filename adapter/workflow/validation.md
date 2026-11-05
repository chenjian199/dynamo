<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 工作流定版验证记录

2026-11-05。本轮验证工作流机制、远端控制路径及离线归档机制，不等同于再次认证 M01–M10 的 GPU 推理功能。真实 AI 账号调用按用户要求暂缓。

| 检查 | 本轮结果与证据 |
| --- | --- |
| CPU 工作流与离线归档单元测试 | **95 项通过**；[pytest.log](../results/workflow/cpu-controls/20261105-finalization/pytest.log) |
| 源码指纹增量复核 | 3 项通过；[记录](../results/workflow/cpu-controls/20261105-finalization/source-fingerprint.log) |
| CUDA / Ascend SSH 与 Kubernetes | 两侧均通过：创建确认、重复启动同一 UID/PID、丢失本地确认后收养原资源、停止与 absence proof、reconcile；[现场摘要](../results/workflow/cpu-controls/20261105-live-r4/summary.json) |
| 双平台源码/输入/结果传输 | 独立受控 workspace，stage、stage-inputs、collect 字节与摘要一致；[传输摘要](../results/workflow/cpu-controls/20261105-staging/summary.json) |
| 实际镜像导出/依赖校验/导入/封存 | 小型 registry 镜像四个测试别名，架构固定，移除别名后重新导入，ID 与命令字节一致，完整 blob 检查和封存通过；[往返摘要](../results/workflow/cpu-controls/20261105-offline-roundtrip-r3/summary.json) |
| 三种 AI 接口 | Codex 本机 CLI 参数核查；Codex/Claude/GPT 文件工具、结构化交接、重新冻结及预算/中断分支为离线 fixture 测试，未调用实际账号 |
| 迁移与契约 | 45 功能、68 用例；34 份原历史索引、4554 份已索引材料的摘要保持一致 |
| Skill | 34 个技能索引一致；六个 adapter skill 已更新到当前 CLI 与恢复规则 |

现场演练只使用本轮标签/UID 所属 namespace、Job 和进程；结束后已释放，不占 GPU。所有失败尝试也保存在 `results/workflow/cpu-controls/`：早期 Job 清理等待问题、多架构导出不完整和索引/平台镜像 ID 差异均留有原始输出，没有用成功记录覆盖失败。最终通过记录分别是 live-r4、staging、offline-roundtrip-r3。

旧 plan-only CLI 示例已归档到 [imported-smoke](../results/workflow/plan-only/imported-smoke/migration.json)，保留迁移前后摘要，未重执行。外部已封存 CUDA/Ascend/MetaX 发布包未修改。

## 复现

```bash
python3 -m pip install -r adapter/workflow/requirements.txt
python3 -m pip install pytest
python3 -m pytest -c adapter/workflow/tests/pytest.ini -q \
  adapter/workflow/tests adapter/modules/m10-validation/common/offline/test_delivery.py
python3 -m adapter.workflow report --catalog --check
python3 adapter/tools/check-workflow.py --require-ready
python3 scripts/validate_skills.py
```

现场 driver.py 是当次执行程序的存档，含当时的 SSH 别名、workspace 和测试镜像，供审计复现参考；换现场应重新绑定环境，不能原样使用旧控制脚本。大归档在 results 本地保存，Git 保存清单和摘要。正式芯片认证必须使用真实模型和模块配方；fixture 镜像不作为发布镜像。

## 前轮验证，不冒充本轮重跑

路径迁移前轮曾通过 34 项迁移/渲染测试，以及 M09 CPU 回归三组 649、119、22 项测试。本轮没有重复声明这些产品回归。完整入口仍为 `adapter/modules/m09-telemetry-planner/common/packaging/run-unit.sh`，需要已安装 Dynamo 的开发环境。

工作流 1.0.0 范围、WF-13 暂缓决定与芯片遗留边界见 [finalization.md](finalization.md)。
