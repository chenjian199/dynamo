<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 工作流单元与契约测试

这里使用 CPU 测试夹具、模拟执行器和模拟 AI 响应验证工作流规则。它们不代替真实芯片推理或真实 AI 账号调用。

| 文件 | 覆盖内容 |
| --- | --- |
| [test_workflow.py](test_workflow.py) | catalog、冻结身份、预检、执行与重试、清理、证据篡改、恢复和发布门禁 |
| [test_contracts.py](test_contracts.py) | 范围依赖、精确配方、独立测量、输入时效、补丁保护、资源归属和证据继承 |
| [test_loop.py](test_loop.py) | 有界 AI 循环、重新冻结、中断处理和原生故障对照；使用 harness 替身 |
| [test_api_tools.py](test_api_tools.py) | API 文件工具的范围与 SHA 检查，模拟 Responses 多轮工具调用 |
| [test_delivery_pipeline.py](test_delivery_pipeline.py) | 构建至封存的交付顺序、篡改检测和中断后不重复导入 |
| [test_image_helpers.py](test_image_helpers.py) | 多架构镜像与单平台 ID 处理；不依赖 Docker daemon |
| [test_native_measurements.py](test_native_measurements.py) | 拒绝只有 pass 文本、字节损坏、资源未回收或设备不匹配的独立 KVBM 证据；使用 CPU 夹具 |
| [test_platform_smoke.py](test_platform_smoke.py) | M01 发现脚本按版本锁精确比对，以及导出源码摘要漂移拒绝 |
| [test_packaging_paths.py](test_packaging_paths.py) | 实际 M02 测试包复制、M10 补丁刷新与摘要完整性；替代 Docker 调用，不构建镜像 |
| [pytest.ini](pytest.ini) | 此组测试独立的 pytest 配置 |

从仓库根目录、在已有 pytest 与工作流依赖的 Python 环境运行：

```bash
python3 -m pytest -c adapter/workflow/tests/pytest.ini -q adapter/workflow/tests
```

离线包完整性另有 [M10 test_delivery.py](../../modules/m10-validation/common/offline/test_delivery.py)。真实执行器控制与镜像往返材料在 [results/workflow](../../results/workflow/README.md)，当前验收范围见 [validation.md](../validation.md)。

`test_upgrade.py` 覆盖上游自动升级：临时真实 Git 分支/冲突、镜像就绪门禁、候选隔离、审查与检查失效、配对条件/阈值/哈希，以及缺失硬件证据时禁止生成发布提案。测试不会拉镜像或访问 GPU。
