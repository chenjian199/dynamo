<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 已封存交付索引

这里保存已交付版本的索引，供人查找离线包、核对 SHA256 和了解认证限制，不存放镜像本体。

| 文件 | 作用 |
| --- | --- |
| [1.5.1-heyi-m10/index.json](1.5.1-heyi-m10/index.json) | CUDA、Ascend、MetaX 既有归档的机器、绝对路径、大小、SHA256、包修订、验收摘要和限制 |
| [1.5.1-heyi-m10/index.json.license](1.5.1-heyi-m10/index.json.license) | 索引 JSON 的版权和许可证信息 |

使用离线包时，先选择平台与架构，再从索引指定的机器获取完整 archive，校验大小和 SHA256。`sealed_with_explicit_limitations` 表示带明确限制的交付，不能理解为全部功能、所有拓扑都已通过。

外部既有归档保持原字节，不为目录调整而重写。新交付的构建、导出、导入验证和封存记录保存在所属 [campaign](../results/README.md) 中；操作见 [交付配方](../workflow/delivery-plan.md) 和 [M10 离线工具](../modules/m10-validation/common/offline/README.md)。
