<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 离线采样、画像和启动复用

| 文件 / 分组 | 作用 |
| --- | --- |
| [offline-bootstrap.md](offline-bootstrap.md) | 离线画像与在线启动的整体操作说明 |
| [offline_profile.py](offline_profile.py) | 调用产品插值与画像组装路径，验证测量、持久化和消费；不宣称搜索得到最优配置 |
| [capture_sweep.py](capture_sweep.py)、[collect_profiler.py](collect_profiler.py) | 在 Profiler 删除临时部署前保留 worker / 测量材料 |
| [profile_identity.py](profile_identity.py)、[provenance.py](provenance.py) | 从真实 worker 记录画像来源、包、驱动及模型身份 |
| [repeat_prefill.py](repeat_prefill.py)、[sequence_decode.py](sequence_decode.py) | 增补 prefill 采样或串行安排 decode 测试，保留先前失败记录 |
| [prepare_offline_online.py](prepare_offline_online.py) | 为 Profiler 生成结果补充测试设施，保持测得的画像内容 |
| [restart_offline.py](restart_offline.py) | 重启测试 Planner，验证重新加载相同持久化画像 |
| [collect_offline.py](collect_offline.py)、[finish_offline.sh](finish_offline.sh) | 汇集已完成离线活动、画像与 Planner 记录，执行既有收尾步骤 |
| [test_offline_render.py](test_offline_render.py) | 检查聚合配置转换为 PD 部署时 decode 相关参数保持正确 |

先由 [rendering](../rendering/README.md) 准备清单，再采样、组装和保存画像，最后进入 [online](../online/README.md)。画像身份必须与使用它的模型、版本、拓扑和测量配置匹配，不能凭文件存在宣称预测器已成功加载。独立核对工具见 [evidence](../evidence/README.md)。
