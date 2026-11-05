<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M05 本轮复核

按 `graham-code-review` 对本轮 diff 进行了同会话复核，没有使用独立子代理。

## 尚未关闭

- Ascend 定向单测断言通过后 native 退出码为 134，单独导入厂商 connector 也可复现。不能记为干净的完整回归；见 `results/ascend/native-import.txt`。
- MetaX 补丁只处理未提交设备传输的 bootstrap 失败。传输已开始后的超时不能直接释放块，仍需验证厂商 completion/drain 与恢复；本轮不声称此路径通过。
- 模型指纹覆盖配置而非权重内容；各端必须挂载相同权重。跨节点、PD TP/DP/高级组合与自有 KVBM 传输未认证。

## 已修复与检查

- 按 connector 名称和模块解析协议，包装 connector 使用子项模块，不继承父包装模块；不将同名厂商实现静默当成 NIXL。
- MetaX bootstrap 失败发生在 `receive_kv` 之前，failure/finished 状态在同一 receiver loop 内发布；不伪造进行中设备操作的完成。
- Ascend LINGER=0 仅用于握手/完成通知的控制 socket。设备传输和源块租约继续由 connector 管理。
- 验收脚本在进程清理及指标采集成功后才写 passed，异常写入 failed；补丁工具先检查版本和完整源哈希、在临时副本上应用并校验输出后原子替换，撤回与重复应用已验证。
- 删除无关的类型标注升级，保留 NVIDIA 上游版权；标记收集器补齐缺失的依赖 stub 后 pre-commit 通过。新脚本与结果保留 HEYI 版权。

三平台补丁后/基线最终用例的输出均与各自聚合对照一致；正常、不可达和取消后的两端 KV/请求占用为零，恢复请求通过。只证明引擎块归还，不证明全部 native 注册句柄无泄漏。
