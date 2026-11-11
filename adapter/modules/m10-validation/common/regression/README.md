<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 已安装镜像回归

| 文件 / 分组 | 作用 |
| --- | --- |
| [prepare-harness.py](prepare-harness.py) | 准备只包含测试驱动的 harness，不把产品实现覆盖进待测 Pod |
| [check-installed.py](check-installed.py) | 拒绝 editable 或源码遮蔽导入，并记录实际二进制身份 |
| [run-engine.py](run-engine.py) | M03 引擎驱动的已安装镜像版本 |
| [run-hardware.sh](run-hardware.sh)、[run-advanced.sh](run-advanced.sh)、[run-tools.sh](run-tools.sh) | 设备、引擎高级能力与工具镜像检查 |
| [run-pd.sh](run-pd.sh) | 明确设备分配下的 PD 回归 |
| [run-rust-contracts.sh](run-rust-contracts.sh) | 使用发布源码与 dev 镜像执行离线 Rust 契约检查 |
| [hybrid-pd-client.py](hybrid-pd-client.py) | Hybrid PD 块边界、并发流和取消检查 |
| [native-parity-client.py](native-parity-client.py)、[native-hybrid-pd-client.py](native-hybrid-pd-client.py) | 原生引擎重复性与原生 Hybrid PD 对照 |
| [compare-hybrid-outputs.py](compare-hybrid-outputs.py)、[review-hybrid-parity.py](review-hybrid-parity.py) | 比较原始 token 流并复核原生对照，不把差异转换成通过 |
| [fpm-concurrency.py](fpm-concurrency.py) | Rust 指标回调与 Python 初始化的并发检查，需外层 timeout |
| [run-ascend-hybrid-tail.sh](run-ascend-hybrid-tail.sh) | 既有 Ascend r3 活动的后续回归脚本，依赖已准备的工作目录 |

使用 [rendering](../rendering/README.md) 创建隔离测试环境，先核对安装身份，再运行所选 [M10 case](../../README.md)。shell 驱动可能含既有模型、路径和活动编号，应在新配方中确认；测试结果写入新的 campaign。原生引擎问题保留可比对照，不静默修改验收标准。

`check-installed.py --role runtime` 验证推理镜像的版本锁和核心模块。默认 `--role full` 额外检查同一解释器中的 Planner/Profiler；Ascend 的 tools 使用独立解释器，先对 `/opt/dynamo/venv/bin/python` 执行 runtime 检查，再由 `run-tools.sh` 对 tools 解释器执行 Planner/Profiler 检查。不能把缺少可选工具依赖误报为推理引擎失败，也不能省略 tools 的独立验收。
