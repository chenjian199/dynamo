<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 引擎功能测试工具

| 文件 | 作用 |
| --- | --- |
| [serve.sh](serve.sh) | 使用明确的平台、模型、设备和 TP/DP 参数启动专用测试服务 |
| [run-case.py](run-case.py) | 有界执行单个硬件用例，保存日志并停止自身进程 |
| [rank-smoke.py](rank-smoke.py) | 检查各 rank 的 KV 路由和缓存失效行为 |
| [advanced-smoke.py](advanced-smoke.py) | 对已启动的服务检查 LoRA、视觉或推测解码能力 |
| [create-lora.py](create-lora.py) | 从指定模型创建非零、确定性的 LoRA 测试权重 |
| [create-quantized.py](create-quantized.py) | 创建 W8A8 动态量化加载测试权重，不做校准或效果认证 |

按选中的 [M03 case](../../README.md) 准备资产、启动服务并运行相应 smoke。Python 工具可查看 `--help`；`serve.sh` 读取的环境变量应由现场配方明确填写。输出归入 [results](../../../../results/README.md)，TP/DP、模型和引擎版本与结果一起冻结。
