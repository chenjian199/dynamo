<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 最终镜像与工具镜像准备

| 文件 / 分组 | 作用 |
| --- | --- |
| [build-derived.sh](build-derived.sh) | 在平台原生构建机上，从已成功构建的 runtime 派生后续镜像 |
| [prepare-tools.py](prepare-tools.py) | 从明确的发布源码与工具输入冻结 tools 构建上下文 |
| [tools.Dockerfile](tools.Dockerfile)、[tools-ascend.Dockerfile](tools-ascend.Dockerfile) | 常规与 Ascend tools 镜像定义 |
| [media.Dockerfile](media.Dockerfile) | 多模态验证所需的派生镜像定义 |
| [tools-entrypoint.sh](tools-entrypoint.sh)、[capacity-links.py](capacity-links.py) | tools 运行环境与显式容量文件路径处理，不伪造性能曲线 |
| [requirements-tools-constraints.txt](requirements-tools-constraints.txt) | tools 依赖约束 |
| [download-wheel.py](download-wheel.py) | 按记录的 SHA256 和大小下载/续传 wheel |
| [rebuild-app-image.py](rebuild-app-image.py) | 审计 Python 应用 wheel 的替换，保持其他原生产物 |
| [rebuild-binding-image.py](rebuild-binding-image.py) | 审计 Rust binding 修复的镜像重建，保持其余产物 |

这些工具依赖 [M02 构建](../../../m02-build-packaging/README.md) 的真实记录和镜像，不是另一套引擎版本基线。使用后记录最终 image ID / digest，再进入 [回归](../regression/README.md) 与 [离线交付](../offline/README.md)。

`rebuild-app-image.py --refresh-vendor-patches` 从已审计源码的 `adapter/modules/m05-pd-transfer/common/transfer/` 复制两个安装器及 `patches/`，写入新构建上下文的 `artifacts/vendor-patches/` 并重新生成摘要。未指定该选项时保留原上下文中的补丁；原构建目录保持不变。[打包回归](../../../../workflow/tests/test_packaging_paths.py) 覆盖这两种情况，Docker 构建和 inspect 使用替身，不宣称完成真实镜像重建。
