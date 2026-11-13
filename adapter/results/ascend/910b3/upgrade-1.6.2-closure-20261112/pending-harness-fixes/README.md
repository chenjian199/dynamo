<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 尚未应用到冻结候选的验收脚本修订

当前现场 campaign 的源码仍固定在 `2a239c2b500ea7e48c86b7216b9878158db4802a`。
本目录为待审查修订，不能代表候选镜像已修改或硬件验收已通过。

- M03：新增显式 `parallel` 模式，通过真实服务发现、唯一设备 UUID、逐 DP rank 指定请求和实际返回 token 验证并行服务。默认 `cache` 模式保留原有 BlockStored、重用、清空、容量等检查。DeepSeek 混合 KV 的短前缀不必满足稠密模型的两块缓存假设，M03-F03 缓存验收仍单独执行。
- M08：DGDR 生成的服务名称不固定为 `VllmDecodeWorker`。根据实际组件配置识别 prefill/decode，再读取相应状态；拒绝缺失、歧义、空副本和运行命名空间不一致。保留真正 autoApply、Ready、API/SSE、取消恢复等检查。
- `test_parallel_probe.py`、`test_dgdr_selection.py` 为负例/清理单元验证。`unit-validation.json` 是结果和被测脚本哈希；它不是硬件验收证据。

待集中应用并更新源码身份后，重新冻结受影响的 campaign、审查迁移项、检查交付源码/镜像身份，并完成实际硬件测试。不得将本目录文件静默覆盖到正在运行的冻结 campaign 中。
