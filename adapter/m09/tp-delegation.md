<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# PD 的 TP 参数透传（2026-10-30）

核对仓库 `v1.5.1`：vLLM 参数由 `AsyncEngineArgs` 解析；`NixlConnectorProtocol.decode_request_kv_transfer_params` 直接返回引擎 KV 元数据。该路径没有 HEYI 后加的 TP1↔TP2 或 Qwen/Llama 白名单。测试覆盖范围应记录在支持矩阵中，不能自动转为启动限制。

本次修改：

- `pd_contract.py` 不再按 TP 组合、模型类型或 `tp_reshard` 认证标记拒绝交接；双方 TP 必须是正整数，实际分片兼容性由原生 connector 决定。模型配置哈希、平台、软件版本、精度、block size、DP/PP、connector 及 layout 字段仍须匹配。
- 保留旧 schema-2 `tp_reshard` 字段的生成值供旧读取端匹配，但新读取端不再用它决定是否放行。旧读取端仍有旧限制；使用新规则需要同步升级参与交接的 worker 源码或镜像。
- Ascend preflight 不再把原生 TP 组合限定为等宽或 `{1,2}`；保留角色和本地 TP 与 peer 配置的一致性检查。原生 0.23.0 connector 自己的比例/方向约束仍会执行。
- `heyi_tp_split=true` 是独立的 HEYI 两路切分补丁，源码使用两份地址及 rank 0/1 完成确认，所以仍只能用于 P1→D2。这是实现约束，不是“没有测试过”。不开启此补丁时，其他组合直接交给原生 connector。
- 本次不修改 KVBM 的独立 TP 门控、PD 的其它功能组合门控或引擎版本。

## 验证

在无设备挂载的 `dynamo-m09-unit` 容器运行平台、PD 契约和 Profiler 配置生成测试，187 项通过。覆盖 TP1↔TP2、TP2↔TP4、TP4↔TP8、TP1↔TP8，以及由 connector 自行判断的非整除比例；保留非法 TP、版本/模型/精度不匹配拒绝、旧 wire hint 兼容和华为切分补丁边界检查。Profiler 生成的两端 TP 值保持不变，不将其它组合误写为 `heyi_tp_split`。

```bash
python -m pytest -c adapter/m09/pytest.ini -q \
  components/src/dynamo/vllm/tests/test_pd_contract.py \
  components/src/dynamo/profiler/tests/unit/test_dgd_platform.py \
  components/src/dynamo/common/platform/tests
```

本次验证的是适配层放行、拒绝和元数据传递行为，没有新增 GPU 组合传输认证。此前 M09 的画像、性能结果和源码包仍对应各自记录的快照；新规则需在后续部署或 M10 镜像构建时纳入。测试记录见 [receipt](results/tp-delegation/validation.json)。
