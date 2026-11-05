<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M01-F02 设备枚举与服务发现

契约见 [feature.yaml](feature.yaml)，revision 2。每个 case 独立绑定节点、镜像、模型、拓扑和观察材料；必需 case 全部通过才可认证声明范围。

| 用例 | 能力 |
| --- | --- |
| M01-F02-C01 | 设备枚举与服务发现 |

命令入口必须来自契约 recipe，验证器重新计算原始观察的断言。原生失败、缺少环境和暂缓分别记录，不转为通过。
