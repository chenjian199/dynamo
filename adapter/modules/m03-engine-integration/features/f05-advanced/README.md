<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M03-F05 LoRA 量化推测解码与 MoE

契约见 [feature.yaml](feature.yaml)，revision 2。每个 case 独立绑定节点、镜像、模型、拓扑和观察材料；必需 case 全部通过才可认证声明范围。

| 用例 | 能力 |
| --- | --- |
| M03-F05-C01 | LoRA 加载与隔离 |
| M03-F05-C02 | 量化模型 |
| M03-F05-C03 | 推测解码 |
| M03-F05-C04 | MoE 与专家路由 |

命令入口必须来自契约 recipe，验证器重新计算原始观察的断言。原生失败、缺少环境和暂缓分别记录，不转为通过。
