<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M07-F04 Omni 阶段生命周期与通道

契约见 [feature.yaml](feature.yaml)，revision 2。每个 case 独立绑定节点、镜像、模型、拓扑和观察材料；必需 case 全部通过才可认证声明范围。

| 用例 | 能力 |
| --- | --- |
| M07-F04-C01 | 阶段通道 |
| M07-F04-C02 | 取消与长稳回收 |

命令入口必须来自契约 recipe，验证器重新计算原始观察的断言。原生失败、缺少环境和暂缓分别记录，不转为通过。
