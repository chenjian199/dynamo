<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M10 公共工具

| 目录 | 负责的工作 |
| --- | --- |
| [images](images/README.md) | 最终镜像的派生构建、tools 上下文和受控重建 |
| [rendering](rendering/README.md) | 最终镜像回归清单与 Operator 升级入口 |
| [regression](regression/README.md) | 已安装镜像的引擎、设备、PD、高级能力与原生对照检查 |
| [profiling](profiling/README.md) | 使用最终镜像验证离线采样、画像加载和在线恢复 |
| [offline](offline/README.md) | 完整镜像导出、依赖检查、离线导入与封存 |

顺序是构建 → 部署与回归 → 验证导出完整性 → 导入与 smoke → 封存。可由统一 [交付配方](../../../workflow/delivery-plan.md) 编排。开发源码挂载结果不能替代已安装镜像验证；产物封存也不等于所有功能认证通过。范围以 [M10](../README.md) 和 campaign 声明为准。
