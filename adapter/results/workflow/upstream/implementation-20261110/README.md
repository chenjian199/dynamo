<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 升级工作流实现检查与历史材料恢复

这是工作流实现记录，不是 CUDA/Ascend/MetaX 新版本硬件认证，也不是 `upgrade init` 创建的实际升级会话。

- `contract-audit.json`：恢复后完整目录、契约、历史摘要审计；45 个功能、4,554 份历史材料，0 错误。
- `restored-evidence.json`：237 个历史缺失日志的新规范路径、预期/恢复一致的 SHA256、备份根目录。仅按摘要完全一致的字节恢复；没有改变索引或填造测量结果。
- `*.license`：对应 JSON 的 HEYI 版权元数据。

恢复的历史定版 pytest.log 来自原先留存的测试输出，保存在其原证据目录；不是本次测试。工作流新增能力及当前 CPU 检查结果见 [验证说明](../../../../upgrades/validation.md)。
