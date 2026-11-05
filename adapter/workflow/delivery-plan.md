<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M02/M10 交付配方

`deliver` 不维护第二套 Dockerfile、镜像锁或归档程序。配方固定七步，复用这些入口：

| 顺序 | phase | 入口 | 必须留存的输出 |
| --- | --- | --- | --- |
| 1 | build | `container/platform-build.py`；M10 `common/images/build-derived.sh` | 构建日志、实际镜像 ID/架构/版本 |
| 2 | dependencies | M10 `common/offline/dependency-inventory.py` | 依赖镜像及 Helm/chart/脚本清单、摘要 |
| 3 | export | `export-images.py` | 完整 images.tar.gz 和 image-export.json |
| 4 | verify_archive | `verify-images.py` | 所有 descriptor/blob 完整性检查 |
| 5 | import | `import.sh` | 目标机导入日志、实际镜像 ID |
| 6 | smoke | 现场已冻结的 M10 回归入口 | 请求/输出对照、运行包版本、四类镜像 ID |
| 7 | seal | `bundle.py --prepared-export` | tar、SHA256SUMS、校验摘要 |

所有路径在 `adapter/modules/m10-validation/common/` 内解析。先用各脚本 `--help` 获取当前参数，不凭本文猜测 CLI 参数。现场指定构建节点、导入节点及传输方式；SSH 命令可通过显式节点 executor 执行。不同架构必须在对应机器构建，不能把导出成功解释为异构镜像可运行。

配方文件字段：`directory` 是 campaign 内交付子目录；`phases` 按上述顺序包含命令列表；`outputs` 对应每个 phase 的相对文件路径列表；`smoke_report` 是 smoke 结构化观测文件；`archive` 是 seal 最终产物在 directory 内的相对路径。每条命令支持 `{repo}`、`{delivery}`、`{identity}`。远端任务需要显式同步/回收 outputs，校验在协调器实际持有的字节上进行。

每一步开始前落盘 running，结束后记录输出 SHA256。恢复时已完成步骤只检查摘要，不重做构建/导入。遇到 running/failed 必须先观察该步骤留下的资源及文件，保留旧记录，另建交付目录；禁止把不明状态直接改成 completed。

Smoke JSON 包含 `identity`、`stack_sha256`、`versions`（实际 importlib.metadata 值）、`images`（runtime/dev/tools/operator 的 role、expected_id、observed_id）和 `requests`（http_status、output_tokens、actual、expected）。actual/expected 必须来自真实部署与原生对照；fixture 的观测只验证工作流本身。不能填固定字符串骗过检查。封存后的 `delivery-state.json` 供 M10 用例收集；平台验收仍由 M10 功能门禁与 release 判定。

对于新增平台，bundle.py 接受明确的平台标识和 architecture；既有三平台历史报告保持兼容。不硬编码只能 CUDA/Ascend/MetaX，但新平台仍需其 canonical container/platforms 版本配置。
