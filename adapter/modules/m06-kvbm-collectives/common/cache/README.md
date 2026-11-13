<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# KVBM 构建与验证

| 文件 / 分组 | 作用 |
| --- | --- |
| [build.sh](build.sh)、[install-cuda.sh](install-cuda.sh) | 准备 KVBM 开发构建及 CUDA 安装路径 |
| [check-unit.sh](check-unit.sh) | 本模块相关单元检查 |
| [run.py](run.py)、[run-suite.sh](run-suite.sh) | 在已有开发容器运行真实引擎缓存 offload/reload 验收及组合场景 |
| [routing.py](routing.py) | 使用两个专用 KVBM workers 检查驱逐后 G2/G3 缓存感知路由 |
| [native-build.sh](native-build.sh)、[native-run.py](native-run.py) | 构建并运行独立 KVBM 多进程验收，与 vLLM 接入路径分开 |
| [collect.py](collect.py) | 收集有界证据，不复制模型或完整大日志 |
| [sources.txt](sources.txt) | 构建和证据身份需要记录的源码列表 |
| [independent.md](independent.md)、[cuda-isolation.md](cuda-isolation.md) | 独立调用链说明与 CUDA 环境隔离定位记录 |

先按 [M06 执行说明](../../execution.md) 选择调用链，再构建、运行并采集。脚本依赖现有厂商容器和明确设备；输入版本、模型与测试输出须绑定到 [campaign](../../../../results/README.md)。

独立验收先执行每个 rank 数据不同的分片往返，再执行 G2/G3 replicated 广播；两者分开保存字节文件。每个 case 产出 `rank-N.measurements.json`、分片及 G2/G3 实际读回文件和参考文件。`native-run.py` 重新比较字节、布局、设备/rank 身份及 SDK 创建/释放计数，不再仅凭日志 `passed=true` 判定成功。该证据覆盖底层调用链，不等同于完整 MLA 模型或 G4 验收。
