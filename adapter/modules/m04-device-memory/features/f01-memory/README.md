<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M04-F01 设备内存分配注册和复制

契约见 [feature.yaml](feature.yaml)，revision 2。每个 case 独立绑定节点、镜像、模型、拓扑和观察材料；必需 case 全部通过才可认证声明范围。

| 用例 | 能力 |
| --- | --- |
| M04-F01-C01 | 内存分配与回收 |
| M04-F01-C02 | 主机与设备复制 |
| M04-F01-C03 | 错误设备拒绝 |

命令入口必须来自契约 recipe，验证器重新计算原始观察的断言。原生失败、缺少环境和暂缓分别记录，不转为通过。
