<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->
# Dynamo 1.6.2 华为升级迁移审查

本记录是适配边界的源码审查，不是上游全部新增代码的逐行安全审计，也不是硬件验收或发布许可。

- B：`2093662a4d4e5fd318732935213dde08dc7be818`（v1.5.1）。
- H：`bd801c6234aae2827042c8abf268395d4582e5eb`（原 HEYI 冻结基线）。
- T：`28f9765d98ded77149a94d0defee6d54e97f6741`（v1.6.2）。
- 当前验证控制器：`9ee6c9fa4f19dfc987511dd77c3da2e3b5933c05`。
- 当前镜像构建源：`64815280cf5ec3db7a7c0570197f585ea004d323`。后续提交只涉及环境身份预检、测试及文档；完整路径差异见 `image-workflow-source-equivalence.json`。镜像内编译来源保持原值，不改标为控制器提交。

## 迁移处置

| 边界 | 上游变化与处置 | 尚需实测 |
| --- | --- | --- |
| 版本与打包 | 上游 vLLM extra 0.31.0、AISimulate 0.13；保留华为官方 vLLM/vLLM-Ascend 0.30.0，不安装 CUDA extra 覆盖厂商栈。上游 CUDA/SGLang NIXL/Mooncake 要求与华为 Mooncake 0.3.13 分开锁定。 | runtime/dev/tools 依赖快照、离线导入 |
| 引擎生命周期 | 保留上游统一 WorkerLifecycle、显式 EngineCore shutdown、benchmark GC 恢复及有限时 RPC。保留厂商旧 API 兼容；不回退上游新接口。修复 Python 回调与健康监控 shutdown 的锁等待，验收脚本拒绝隐藏 FATAL/非零退出。 | 推理、取消、重启、PD 退出 |
| KV 事件与传输 | 保留上游 scheduler/发布器演进，同时保留平台描述与 Hybrid Connector 接入；普通/混合布局和设备可见性分开。无未经实测的传输通过声明。 | 定向2P3D、异宽TP、边界、真实字节与长稳 |
| 内存与 KVBM | 保留平台运行时和 CPU/CUDA/Ascend 分离；原生测量新增实际 G2/G3/分片读回和 SDK 创建/释放计数。修复跨源码快照复用 Cargo 产物的问题。 | 冻结版本独立调用及本地缓存路径 |
| Profiler / Planner | 随上游迁移 aic→ais；保留测量画像、厂商容量和原生并行参数。DGD shape 使用当前 generation 的 Operator status，不恢复旧参数推断。非 CUDA 功耗写入仍拒绝。离线到在线转换必须显式指定1.6.2。 | 真实采样、插值边界、复用拒绝错配、扩缩容/重启 |
| Operator | 保留上游 provider/ownership 冲突保护、feature gate 和当前 status 投影；保留标准厂商资源名校验。新构建 controller 与旧的现场控制器分开记录，未自动替换其它 release。 | admission、跨节点PD、Grove/KAI、DGDR UID链 |
| 历史材料 | 946项 H 适配差异处置见 `semantic-review-r20.json`。旧版本脚本/清单/结果的保留属于历史可复现性决定，不能直接当作1.6.2环境配置或通过证据。 | 新现场必须重新绑定，不继承旧 passed |
| 环境身份 | 52与7.5来自同一OS模板，machine-id相同；显式冻结并现场读取DMI UUID，保留原OS ID；重复/缺失/错配/混合来源拒绝。 | 两节点只读现场预检已通过，完整资产/镜像doctor待最终镜像 |

## 范围及门禁

按用户确认，38项必需功能仍须通过；G4、华为GMS、完整多模态/独立VL/Omni、厂商DRA及功耗写入共7项保留明确暂缓。旧45项会话及其失败记录不改写。

迁移审查和CPU检查通过不能代替以下门禁：冻结campaign逐case验收、原HEYI/新HEYI/原生配对对照、七阶段离线交付及最终发布复核。上述门禁未全部通过前，不合并、不删除临时分支、不称为正式发布。
