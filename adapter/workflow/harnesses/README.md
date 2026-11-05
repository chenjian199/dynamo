<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# AI 接入与源码修改范围

本包连接 Codex、Claude CLI 和 OpenAI Responses 接口，让不同 AI 使用同一份冻结任务、工作副本与结构化输出。AI 提交实施建议，实际执行和认证仍由工作流负责。

| 文件 | 职责 |
| --- | --- |
| [agent.py](agent.py) | 构造 handoff / envelope，调用指定 harness，解析结构化实施及配方建议，记录调用结果 |
| [workspace_tools.py](workspace_tools.py) | 为 API harness 提供受路径范围及原文件 SHA 约束的文件读取、搜索、修改工具 |
| [isolation.py](isolation.py) | 创建源码工作副本，检查保护路径与补丁范围，在原文件未被并发修改时应用允许的变更 |

`agent` 默认只输出交接材料，`agent --invoke` 才调用模型；`automate` 进一步组织有次数和墙钟时间上限的循环。调用时明确模型，凭据由现有 CLI 登录或环境配置提供，不写入结果文件。

源码副本和补丁门禁不等于操作系统安全沙箱，执行权限取决于所在环境。契约、验证器、版本锁、技能和指令属于保护输入，不能通过修改验收门槛获得通过。

真实 AI 账号调用目前按用户决定暂缓，接口替身测试不能算作真实服务接入通过。当前验证范围见 [validation.md](../validation.md)，使用方式见 [操作手册](../README.md)。
