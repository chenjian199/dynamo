<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 设备内存检查入口

[run.sh](run.sh) 接收平台名和新的输出目录，运行 M04 的 Rust 契约与平台硬件检查，保留测试日志和源码信息。它依赖已有 Cargo 工具链、编译缓存及厂商运行环境；可通过脚本声明的 `CARGO_BIN`、`CARGO_TARGET_DIR` 等变量指定位置。

使用前先阅读 [M04 执行说明](../../execution.md)，检查脚本参数和当前设备分配，再将输出目录设置在所属 [campaign](../../../../results/README.md)。CPU 契约通过不能代替真实设备内存测试，也不能推定尚未认证的跨卡能力。
