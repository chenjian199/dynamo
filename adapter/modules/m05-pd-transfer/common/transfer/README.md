<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# PD 与底层传输工具

| 文件 / 分组 | 作用 |
| --- | --- |
| [run.py](run.py) | 在已有厂商开发容器中运行有界单机 PD smoke |
| [topology.py](topology.py) | 对发现的 producer/consumer 配对逐一执行新的 KV 交接 |
| [soak.py](soak.py) | 长稳、取消和恢复回归，观察预热后的引擎与进程资源 |
| [nixl_probe.py](nixl_probe.py)、[mooncake_probe.py](mooncake_probe.py) | 脱离 vLLM 的真实设备注册、复制及字节一致性探针 |
| [handoff.py](handoff.py)、[inflight_engine.py](inflight_engine.py)、[inflight_probe.py](inflight_probe.py) | 握手错误、原生引擎传输中故障、NIXL pending 传输故障注入 |
| [observer.py](observer.py) | 测试专用 worker 扩展，观察传输生命周期与资源计数，不作为生产导入 |
| [ascend-python.sh](ascend-python.sh)、[ascend_runtime_probe.py](ascend_runtime_probe.py) | Ascend CANN / Mooncake 加载顺序入口，以及 Go 运行时线程行为对照 |
| [patch_vendor.py](patch_vendor.py)、[patch_ascend_tp.py](patch_ascend_tp.py) | 按源码摘要检查、应用或恢复指定厂商版本的 Connector 修复 |
| [build-metax-nixl.sh](build-metax-nixl.sh) | 在独立目录构建实验性 MetaX NIXL 修复，不覆盖镜像自带插件 |
| [patches/](patches/README.md) | 补丁文本、适用文件摘要及加载顺序修复 |
| [completion.md](completion.md)、[review.md](review.md)、[nixl-diagnosis.md](nixl-diagnosis.md) | 既有回归范围、复核和 NIXL 故障定位记录 |

先读取 [M05 契约与执行说明](../../README.md)，在隔离测试容器中明确设备、模型、拓扑和输出目录。补丁工具只接受声明的原始源码，不能当作其他版本的通用安装器；应用前停止对应测试 worker。

探针和日志保存在 [results](../../../../results/README.md)。进程超时不证明传输资源释放，底层故障仍需保留原始证据与独立对照。

## 交接探针

`handoff.py` 默认保留本地 file/tcp/zmq 发现路径。使用 `--discovery-backend kubernetes` 或 `etcd` 时，探针连接同一命名空间的 `prefill.generate` 与 `backend.generate`，需要对应服务发现权限及配置。

- `--case valid`：发送正常交接，要求非空 token、正常结束且交接元数据未被探针修改。
- `--case mismatch`：只修改测试副本中的 block size，要求后端明确拒绝不兼容布局。
- `--case unreachable --unreachable-port PORT`：只修改测试副本的 bootstrap 地址和 engine ID，要求显式后端错误；调用方仍需独立验证排空及恢复。
- `--model`、`--token-ids FILE`（JSON 整数数组）和 `--max-tokens`：冻结模型与输入，供混合 KV block 边界及原生配对测试使用。

输出同时保留原始请求、prefill 交接、传给 decode 的交接和响应。正常交接成功不等于跨节点字节一致性认证；节点身份、传输路径和设备字节读回需由独立探针与验证器确认。`tests/` 只验证测试工具的判定逻辑，不替代硬件实测。
