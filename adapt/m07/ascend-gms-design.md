<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ascend GMS 设计备忘

2026-10-26：按用户决定保留设计，暂缓实施，先推进 M07 其它工作。
本文为候选方案，不代表实现或验收通过；现有 Ascend GMS 预检查继续拒绝。

## 目标与已知限制

独立服务持有 NPU 物理权重内存，客户端退出后，新客户端导入同一分配。
只覆盖服务和设备仍正常时的 worker 退出恢复，不承诺服务死亡、设备复位或
驱动故障后的设备内存存活。已有原语结果见 [驱动复测](ascend-hdk2611.md)。

CANN 9.1 提供物理内存 token 导出、PID 白名单、跨进程导入和映射接口，
但当前 910B3 组合无法满足设备只读保护；不能由此否定权重保活的可行性，
也不能将保活原型视为完整 CUDA GMS 契约。
参考：[官方共享流程](https://www.hiascend.com/doc_center/source/zh/CANNCommunityEdition/910/API/runtimeapi/aclcppdevg_03_0118.html)、
[访问权限约束](https://www.hiascend.com/doc_center/source/zh/CANNCommunityEdition/910/API/runtimeapi/aclcppdevg_03_2047.html)。

## 候选实现

1. 每设备独立服务申请并持有物理内存，通过 CANN token 授权客户端导入。
   不把模型 worker 作为唯一内存所有者。
2. 将共享句柄协议区分为 POSIX FD 和 Ascend token，保留 CUDA/MetaX 的
   SCM_RIGHTS 路径。加入协议版本、设备身份、分配 ID、服务代次和客户端授权；
   使用 CANN 设备侧进程身份，不直接假定容器 PID 等于授权 PID。
3. 区分未完成加载和已发布权重；同步后才发布。客户端断开不得自动删除已发布
   权重，未提交写入失败不得复用；实现客户端异常退出、过期 token 与分配回收。
4. 接入 TorchNPU 分配器或权重加载路径，恢复张量布局、dtype 和共享关系。
   初期仅管理权重，KV Cache 和临时缓冲仍由引擎管理。新进程重建图和通信组。
5. 完整模式必须有真实设备只读保护。若未来选择受限模式，需要显式能力声明，
   不得将软件锁或校验等同于硬件保护，不得将 set_access(RO) 实现为空操作。
   当前没有授权启用这种降级模式。

## 恢复工作时的验证顺序

先用小内存完成服务端分配、A 导入写入并同步、终止 A、B 导入逐字节回读。
重复异常退出并检查内存/句柄回收，同时验证错误设备、未授权客户端和过期
token 拒绝。原型通过后才接入固定的 vLLM-Ascend 0.23.0，使用已有 NFS 模型
验证输出一致性、恢复耗时和分配归属。只读保护单独验收，不伪装为已通过。
