<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 厂商 Connector 补丁材料

| 文件 | 作用 |
| --- | --- |
| [manifest.json](manifest.json) | 基础厂商补丁的文件与摘要清单，由上一级补丁工具读取 |
| [ascend.patch](ascend.patch)、[metax.patch](metax.patch) | 对应厂商 Mooncake Connector 的基础修复 |
| [metax-bootstrap.patch](metax-bootstrap.patch) | MetaX bootstrap 相关修复 |
| [metax-nixl-endpoint.patch](metax-nixl-endpoint.patch) | 独立 NIXL 构建使用的 endpoint 清理实验补丁 |
| [ascend-tp-manifest.json](ascend-tp-manifest.json) | Ascend TP / Hybrid 修复的摘要清单 |
| [ascend-tp-config.patch](ascend-tp-config.patch)、[ascend-tp-connector.patch](ascend-tp-connector.patch) | Ascend 配置和 Connector 的异宽 TP 相关改动 |
| [ascend-hybrid-load-order.patch](ascend-hybrid-load-order.patch) | Hybrid Connector 的库加载顺序修复 |
| [ascend-0.30.0/](ascend-0.30.0/README.md) | 0.30.0 独立补丁及摘要，按已安装版本选取 |
| `*.license` | 对应补丁或 JSON 文件的版权信息 |

使用 [patch_vendor.py](../patch_vendor.py)、[patch_ascend_tp.py](../patch_ascend_tp.py) 或 [独立构建入口](../build-metax-nixl.sh)，不要忽略源码摘要直接批量套用。补丁文件中的 diff 上下文属于补丁语义，不应为排版随意改写。修复适用范围与尚存限制见 [传输工具说明](../README.md)。
