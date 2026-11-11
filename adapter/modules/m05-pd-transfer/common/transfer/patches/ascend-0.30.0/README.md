<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ascend 0.30.0 Connector 补丁

适用镜像摘要：`sha256:28351d337b7f9367073344240fa4e28d99794d549d577eaf317689950064150a`。
由上级 `patch_vendor.py` 和 `patch_ascend_tp.py` 按已安装版本选取；不覆盖 0.23.0 的历史补丁。
每个修改前后文件都由 manifest 的 SHA256 校验，未知源码拒绝应用。

| 文件 | 作用 |
| --- | --- |
| `manifest.json`、`ascend.patch` | REQ socket 设置 linger=0，避免连接失败时无限等待关闭 |
| `ascend-tp-manifest.json` | 三个 TP/加载顺序修复文件的原始及输出摘要 |
| `ascend-tp-config.patch` | 显式 `heyi_tp_split` 开关下允许 Qwen3 TP1→TP2；将三个 Dynamo 元数据键排除在 Ascend 专属字段校验之外 |
| `ascend-tp-connector.patch` | 逐 token/head 拉取、双 decode rank ACK 后回收、NHD BF16 布局检查（连续独立页或共享存储的交错 K/V 页） |
| `ascend-hybrid-load-order.patch` | Hybrid Connector 导入 Mooncake 前加载当前 CANN 库 |

迁移依据是镜像内 0.30.0 源码。新版的 group/rank/registration 方法已重排，因此重新生成补丁，未直接修改旧补丁的版本号。
厂商原生配置保留原行为；扩展 TP 拆分仍仅作用于显式开启的已定义布局。
源码迁移不等于硬件认证；以本次升级 campaign 的实际回归结果为准。

Ascend 0.30.0 用严格 dataclass 校验 `additional_config`，会拒绝 Dynamo 写入的 `consolidator_endpoints`（即使值为 null）。补丁只从 Ascend 构造参数排除 `consolidator_endpoints`、`benchmark`、`dynamo_kv_event_block_size`，仍在原始 `VllmConfig.additional_config` 中保留它们供 KVBM、Profiler 和 KV 发布读取。其它未知参数仍按厂商规则报错，不放宽厂商参数校验。

0.30.0 的普通注意力缓存采用交错 K/V 页，单个 K 或 V 视图的 block stride 是有效页长度的两倍。TP 拆分补丁保留该步长并检查内部 NHD 排列、BF16 和 ND 格式；搬运继续按双方公布的 block stride 定位，不复制或假定每个视图连续。
