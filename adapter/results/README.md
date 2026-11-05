<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 执行结果与证据

本目录统一保存历史材料与新现场结果。先通过 [支持矩阵](../support-matrix.md) 找到平台和功能，再进入相应 campaign；目录存在不代表测试通过。

```text
results/
  <平台>/<芯片>/<campaign>/     一次冻结输入的适配活动
  <平台>/unspecified/imported-mNN/  已迁移的历史材料
  workflow/                   工作流自身的控制测试与定版记录
```

新 campaign 由工作流创建，直接放在 `adapter/results/<平台>/<芯片>/<campaign>/`。`unspecified` 表示历史材料没有确定芯片身份，不能据此推定任意芯片支持。

| 文件 / 层级 | 阅读方法 |
| --- | --- |
| `lock.json` | 冻结的平台栈、源码、环境、模型、功能契约和范围，标识本次结果适用的输入 |
| `tasks.json`、`state.json`、`preflight.json` | 分别查看执行计划、各类状态、环境预检；状态还需结合原始证据 |
| `features/<功能编号>/` | 按功能编号定位证据；新执行继续按 attempt 区分每次尝试 |
| attempt 中的日志、`result.json`、执行回执 | 检查实际命令、退出状态、测量、断言和文件 SHA256；失败尝试保留 |
| 历史 campaign 的 `evidence-index.json`、`artifacts/` | 迁移材料索引与原始文件；规范化摘要与原始摘要有区分，不宣称重新执行 |
| [workflow/](workflow/README.md) | CPU 控制、SSH/Job 恢复、离线镜像往返等工作流验证；不代替芯片推理认证 |
| [.gitignore](.gitignore) | 排除部分大型归档和 AI 临时工作副本；在仓库目录内保存不等于所有文件都提交 Git |

新增输出写入独立 campaign / attempt，不能覆盖已有证据或手改其校验值。镜像归档、模型文件很大，是否提交 Git 与证据是否保存在本目录是两回事。登录密钥、API key、密码和 kubeconfig 不属于结果材料。

认证与证据继承规则见 [evidence](../workflow/evidence/README.md)；历史离线包的位置和 SHA256 见 [releases](../releases/README.md)。
