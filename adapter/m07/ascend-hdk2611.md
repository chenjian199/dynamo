<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ascend HDK 26.1.1 升级后 GMS 复测

2026-10-26：用户确认继续 M07 GMS，不是 M06 KVBM。
驱动升级消除了缺失符号，但 **Ascend GMS 仍不可用，不能标记完成**。
本轮没有变更引擎版本、驱动或固件，也没有把软件锁当成设备只读保护。

## 环境与结果

910B3、AArch64，宿主机 HDK 从 25.2.0 升到 26.1.1。
重新启动原开发容器 `dynamo-ascend-m03-nfs`，确认加载宿主机只读挂载的新驱动。
CANN 保持 9.1.0，vLLM 为 `0.23.0+empty`、vLLM-Ascend 为 0.23.0，
torch-npu 为 2.10.0.post4。物理卡 2 执行探针。

| 检查 | HDK 26.1.1 实测 |
| --- | --- |
| `halMemSetAccess` / `halMemGetAccess` 导出 | 已存在，原缺失符号问题消除 |
| 物理分配、地址预留、映射、默认 token 导出 | 返回 0 |
| 设置 READWRITE、查询权限 | 返回 0，查询值为 3（RW） |
| mode 0：RW → READ | READ 返回 207000，权限仍为 3；退出码 4 |
| mode 1：POSIX 类型分配 | `aclrtMallocPhysical` 返回 100000；退出码 1 |
| mode 2：新映射直接 READ | 返回 207000，权限仍为 3；退出码 4 |
| mode 3：RW → 同步 → unmap/map → READ | 返回 207000，权限仍为 3；退出码 4 |
| unmap、释放地址、释放物理句柄 | 所有已创建资源的清理返回 0 |

驱动日志报告 `devmm_access_desc_check: Not support type (type=1)`，
继而 CANN 返回 `halMemSetAccess does not support`。因此不能应用 MetaX 的
“重新映射后设为只读”办法，也不能把查询到的 RW 解释成 RO。

[原语证据](results/ascend-hdk2611-probe/summary.json)保存各模式退出码与原始日志哈希；
[环境快照](results/ascend-hdk2611-probe/environment.json)保存驱动/CANN、软件版本、
HAL 库哈希以及探针源码和二进制哈希。`probe/` 为日志摘录，完整日志仍位于
证据指定的华为容器缓存目录。旧 25.2.0 记录保留，不覆盖。

## 官方约束与修正结论

CANN 9.1 文档明确写明：

- [aclrtMemAccessDesc](https://www.hiascend.com/doc_center/source/zh/CANNCommunityEdition/910/API/runtimeapi/aclcppdevg_03_2047.html)：目前仅支持 READWRITE。
- [aclrtPhysicalMemProp](https://www.hiascend.com/doc_center/source/zh/CANNCommunityEdition/910/API/runtimeapi/aclcppdevg_03_1401.html)：目前仅支持 NONE 句柄类型。

此前根据接口支持表推荐 26.1.1 作为候选，不构成对 READ/POSIX 子能力的保证。
本次实测和参数级文档表明，仅升级宿主机驱动不能闭环当前 GMS 契约。
不能继续把错误描述成“26.1.1 缺少权限接口”，也不能推断所有华为产品或后续 SDK
均不支持只读映射。

## 继续实现的条件

1. 获得针对 910B3 的真实设备只读映射方案，验证权限及拒绝写入的行为。
   当前仅有 RW 映射时，软件锁或只读 CPU 映射不能防止设备写入共享权重。
2. 若共享仍使用 token，实现带版本、设备身份、进程授权及生命周期管理的协议。
   token 不能通过现有 `SCM_RIGHTS` 文件描述符通道冒充 FD。
3. 原语可满足契约后，实现 Ascend VMM provider、TorchNPU 分配器接入及 vLLM
   sleep/wake，验证发布者退出后权重存活、异常回收和恢复一致性。

当前保留 GMS 工厂与预检查的明确拒绝并更新错误说明，没有加入不可验证的后端。
Omni、CRIU 和跨节点不属于本次驱动复测的交付范围。

## 已有功能回归

新驱动下继续使用 vLLM 0.23.0 和 NFS 中的 Qwen3-VL-2B-Instruct：

- 华为 M07 单元测试：108 passed。
- 华为媒体原语：4 passed、3 deselected；LOCAL FP32/FP16/BF16 往返及 CPU VP9
  解码通过。未执行的三项是当前镜像没有依赖的 NIXL READ。
- 独立编码器：图文、并发、流式、取消及非法输入后恢复通过，编码器临时文件残留为零。
- MetaX 定向 GMS provider/预检查回归：9 passed；本轮未重复完整 MetaX/CUDA GMS 验收。

[升级后回归证据](results/ascend-hdk2611/summary.json)与原语证据分开记录。
编码器引擎已退出；退出日志仍有 Python resource tracker 的一个 semaphore 清理警告，
因此本轮短测不能用于声明长期 IPC 零泄漏。原始日志路径及哈希在证据中保留。
运行后 providers.py 仅将同一错误字符串折行，不改变测试时的行为；原测试源码哈希不改写。

## 复现

在华为开发容器内运行，`OUTPUT` 必须是不存在的新目录：

```bash
cd /workspace/dynamo
source /usr/local/Ascend/ascend-toolkit/set_env.sh
PLATFORM=ascend DEVICE=2 OUTPUT=/var/cache/dynamo-ascend/m07-access-replay \
  bash adapter/m07/probe.sh
/opt/dynamo/venv/bin/python adapter/m07/collect.py --platform ascend --probe-only \
  --raw /var/cache/dynamo-ascend/m07-access-replay \
  --output /var/cache/dynamo-ascend/m07-access-replay-evidence
```

脚本执行完成不等于能力通过：必须检查每个 `mode-*.exit` 和实际 SDK 返回值。
退出码 4 表示权限契约未满足，不能计入 GMS 通过数量。
