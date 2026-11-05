<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Dynamo 项目代码规模

统计提交：`b24f6dbe071be0d047e421568288755a85f041de`；受 Git 管理文件 **11,945** 个；工具 cloc 2.06。

## 统计口径

- 统计指定提交的完整源码，包含上游和 HEYI 代码；不是适配增量，也不是人日估算。
- 有效代码行使用 cloc 的 code 字段，排除注释和空行；Python 文档字符串按 cloc 默认规则计入注释。物理行包含代码、注释和空行。
- 主表包含测试、示例、基准、适配工具、网站组件、协议定义、Dockerfile/Makefile/CMake 等构建代码。YAML/JSON/TOML 等配置与数据单列。
- 不纳入未跟踪 reference/、模型、缓存和编译目录；符号链接不跟随。独立路径即使内容相同也分别计数。
- vendor/third_party 单列；results 目录、结果文件、日志、CSV、快照、补丁不计作源码。没有标记的生成文件不能自动可靠识别，不能把主表等同于全部手写代码。
- 明确生成标记（前 40 行）及生成文件命名的源码单列；bpftrace 使用 C 注释规则解析并单独命名。
- 非源码类别只统计文件数与文本物理行，不把 JSON 数据或文档内容称为有效代码；二进制文件记文件数不记行数。
- 本次新增统计工具与报告不在被统计提交中。复现命令会在输出目录生成逐文件 project-statistics.csv 和 metadata.json；仓库仅保留此汇总报告。

## 源码总数

| 分类 | 文件 | 有效代码行 | 注释行 | 空行 | 物理行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 全部源码 | 3,628 | 1,029,547 | 162,283 | 156,163 | 1,347,993 |

包含明确生成源码时的总数：

| 分类 | 文件 | 有效代码行 | 注释行 | 空行 | 物理行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 源码 + 生成源码 | 3,638 | 1,034,282 | 163,152 | 156,803 | 1,354,237 |

## 按语言

| 分类 | 文件 | 有效代码行 | 注释行 | 空行 | 物理行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rust | 1,233 | 529,974 | 70,125 | 68,667 | 668,766 |
| Python | 1,698 | 340,233 | 67,817 | 69,447 | 477,497 |
| Go | 341 | 114,534 | 12,808 | 10,996 | 138,338 |
| Bourne Shell | 226 | 17,241 | 4,047 | 2,862 | 24,150 |
| TypeScript | 40 | 14,579 | 1,574 | 1,774 | 17,927 |
| Dockerfile | 39 | 3,971 | 2,319 | 682 | 6,972 |
| Protocol Buffers | 11 | 1,741 | 2,678 | 628 | 5,047 |
| C++ | 7 | 1,207 | 50 | 143 | 1,400 |
| CSS | 1 | 1,047 | 127 | 107 | 1,281 |
| HTML | 2 | 1,000 | 2 | 91 | 1,093 |
| CUDA | 2 | 914 | 216 | 199 | 1,329 |
| make | 7 | 870 | 166 | 192 | 1,228 |
| JavaScript | 4 | 784 | 136 | 88 | 1,008 |
| PowerShell | 2 | 706 | 40 | 148 | 894 |
| C | 2 | 323 | 60 | 44 | 427 |
| bpftrace | 9 | 253 | 100 | 40 | 393 |
| C/C++ Header | 4 | 170 | 18 | 55 | 243 |

## 按一级目录

| 分类 | 文件 | 有效代码行 | 注释行 | 空行 | 物理行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| lib | 1,442 | 552,341 | 77,664 | 75,269 | 705,274 |
| components | 870 | 185,781 | 34,578 | 39,309 | 259,668 |
| deploy | 410 | 133,143 | 19,403 | 14,723 | 167,269 |
| tests | 248 | 63,535 | 12,876 | 11,684 | 88,095 |
| docs | 89 | 24,615 | 4,976 | 3,996 | 33,587 |
| benchmarks | 126 | 15,063 | 2,328 | 2,814 | 20,205 |
| adapter | 142 | 14,237 | 714 | 944 | 15,895 |
| container | 81 | 12,383 | 3,958 | 2,193 | 18,534 |
| examples | 150 | 10,885 | 2,634 | 2,098 | 15,617 |
| .github | 22 | 6,365 | 1,486 | 1,320 | 9,171 |
| dev | 4 | 4,802 | 857 | 796 | 6,455 |
| .agents | 22 | 3,653 | 338 | 475 | 4,466 |
| scripts | 6 | 1,748 | 107 | 307 | 2,162 |
| recipes | 12 | 784 | 276 | 155 | 1,215 |
| .devcontainer | 2 | 109 | 32 | 38 | 179 |
| (root) | 2 | 103 | 56 | 42 | 201 |

## 按大模块展开

| 分类 | 文件 | 有效代码行 | 注释行 | 空行 | 物理行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| lib/llm | 440 | 241,811 | 29,511 | 29,800 | 301,122 |
| deploy/operator | 346 | 115,856 | 13,221 | 11,302 | 140,379 |
| lib/kv-router | 125 | 75,458 | 4,284 | 8,600 | 88,342 |
| lib/runtime | 159 | 57,428 | 12,027 | 9,482 | 78,937 |
| components/src/dynamo/vllm | 144 | 46,903 | 7,494 | 8,893 | 63,290 |
| components/src/dynamo/planner | 206 | 40,747 | 8,943 | 8,792 | 58,482 |
| lib/bindings | 162 | 34,945 | 7,871 | 6,394 | 49,210 |
| docs/fern | 86 | 24,082 | 4,970 | 3,905 | 32,957 |
| lib/mocker | 62 | 23,327 | 793 | 2,026 | 26,146 |
| components/src/dynamo/common | 178 | 20,765 | 5,532 | 5,984 | 32,281 |
| lib/kvbm-engine | 69 | 20,260 | 6,338 | 3,724 | 30,322 |
| components/src/dynamo/profiler | 74 | 17,425 | 2,251 | 3,129 | 22,805 |
| lib/bench | 40 | 17,383 | 372 | 1,427 | 19,182 |
| components/src/dynamo/sglang | 68 | 16,490 | 2,709 | 3,787 | 22,986 |
| lib/kvbm-logical | 59 | 15,760 | 4,230 | 3,259 | 23,249 |
| lib/gpu_memory_service | 113 | 15,015 | 1,903 | 3,336 | 20,254 |
| tests/fault_tolerance | 52 | 13,248 | 3,944 | 3,014 | 20,206 |
| components/src/dynamo/trtllm | 75 | 13,085 | 3,477 | 3,179 | 19,741 |
| components/src/dynamo/frontend | 22 | 12,923 | 1,756 | 2,190 | 16,869 |
| lib/sidecar | 52 | 10,682 | 801 | 1,178 | 12,661 |
| lib/kvbm-physical | 40 | 9,562 | 2,719 | 1,703 | 13,984 |
| tests/router | 18 | 9,536 | 1,140 | 1,359 | 12,035 |
| tests/frontend | 34 | 8,728 | 1,248 | 1,452 | 11,428 |
| deploy/inference-gateway | 26 | 8,538 | 1,726 | 1,066 | 11,330 |
| lib/backend-common | 19 | 8,342 | 1,947 | 989 | 11,278 |
| tests/utils | 32 | 8,312 | 1,740 | 1,766 | 11,818 |
| examples/backends | 113 | 8,095 | 1,969 | 1,548 | 11,612 |
| container/compliance | 40 | 6,711 | 1,947 | 1,458 | 10,116 |
| deploy/power-agent | 22 | 6,483 | 3,913 | 1,868 | 12,264 |
| tests/serve | 24 | 6,434 | 1,385 | 580 | 8,399 |
| benchmarks/frontend | 41 | 5,287 | 1,144 | 1,056 | 7,487 |
| adapter/modules/m02-build-packaging | 57 | 5,181 | 277 | 320 | 5,778 |
| lib/memory | 24 | 4,877 | 1,608 | 1,010 | 7,495 |
| tests/deploy | 11 | 4,364 | 857 | 798 | 6,019 |
| .agents/skills | 22 | 3,653 | 338 | 475 | 4,466 |
| lib/kvbm-consolidator | 22 | 3,590 | 543 | 506 | 4,639 |
| benchmarks/multimodal | 43 | 3,480 | 316 | 585 | 4,381 |
| tests/kvbm_integration | 12 | 3,302 | 855 | 771 | 4,928 |
| components/src/dynamo/global_planner | 11 | 3,031 | 848 | 580 | 4,459 |
| container/templates | 15 | 2,923 | 1,648 | 405 | 4,976 |
| dev | 1 | 2,851 | 693 | 544 | 4,088 |
| lib/data-gen | 7 | 2,791 | 141 | 281 | 3,213 |
| tests | 11 | 2,715 | 506 | 510 | 3,731 |
| lib/kvbm-kernels | 10 | 2,589 | 465 | 407 | 3,461 |
| components/src/dynamo/triton | 22 | 2,527 | 543 | 620 | 3,690 |
| lib/tokens | 3 | 2,516 | 797 | 461 | 3,774 |
| adapter/modules/m08-kubernetes | 29 | 2,494 | 134 | 135 | 2,763 |
| .github/codeowners | 5 | 2,404 | 651 | 509 | 3,564 |
| components/src/dynamo/thunderagent_router | 12 | 2,375 | 158 | 425 | 2,958 |
| components/src/dynamo/replay | 11 | 2,169 | 93 | 325 | 2,587 |
| components/src/dynamo/global_router | 9 | 2,159 | 295 | 413 | 2,867 |
| adapter/modules/m05-pd-transfer | 14 | 2,151 | 103 | 164 | 2,418 |
| .github/workflows | 4 | 2,112 | 375 | 466 | 2,953 |
| dev/observability | 3 | 1,951 | 164 | 252 | 2,367 |
| components/src/dynamo/mocker | 11 | 1,914 | 184 | 357 | 2,455 |
| .github/scripts | 13 | 1,849 | 460 | 345 | 2,654 |
| scripts | 6 | 1,748 | 107 | 307 | 2,162 |
| tests/mm_router | 5 | 1,639 | 205 | 278 | 2,122 |
| components/src/dynamo/router | 8 | 1,565 | 81 | 224 | 1,870 |
| lib/kvbm-config | 11 | 1,486 | 612 | 301 | 2,399 |
| tests/gpu_memory_service | 10 | 1,457 | 127 | 242 | 1,826 |
| deploy/utils | 8 | 1,430 | 358 | 302 | 2,090 |
| benchmarks/request_trace | 3 | 1,401 | 14 | 155 | 1,570 |
| container | 8 | 1,355 | 211 | 162 | 1,728 |
| adapter/modules/m07-gms-multimodal | 14 | 1,330 | 62 | 83 | 1,475 |
| benchmarks/router | 7 | 1,287 | 130 | 231 | 1,648 |
| lib/platform-runtime | 5 | 1,218 | 64 | 44 | 1,326 |
| benchmarks/prefix_data_generator | 13 | 1,208 | 372 | 322 | 1,902 |
| benchmarks/cuda_custom_storage | 10 | 1,202 | 40 | 174 | 1,416 |
| lib/platform | 5 | 1,103 | 90 | 49 | 1,242 |
| adapter/platforms | 10 | 1,010 | 55 | 93 | 1,158 |
| lib/rl | 1 | 989 | 123 | 100 | 1,212 |
| examples/diffusers | 3 | 964 | 63 | 119 | 1,146 |
| components/src/dynamo/squeeze_evolve | 8 | 927 | 170 | 216 | 1,313 |
| adapter/modules/m06-kvbm-collectives | 9 | 911 | 45 | 60 | 1,016 |
| tests/dependencies | 9 | 911 | 294 | 261 | 1,466 |
| adapter/modules/m03-engine-integration | 6 | 832 | 26 | 68 | 926 |
| container/platforms | 11 | 718 | 45 | 38 | 801 |
| tests/lmcache | 9 | 647 | 184 | 192 | 1,023 |
| lib/kv-hashing | 8 | 602 | 249 | 109 | 960 |
| tests/rl | 5 | 600 | 26 | 106 | 732 |
| examples/router | 15 | 571 | 75 | 110 | 756 |
| deploy/pre-deployment | 2 | 559 | 74 | 120 | 753 |
| components/src/dynamo/tokenspeed | 6 | 541 | 27 | 135 | 703 |
| docs/tests | 3 | 533 | 6 | 91 | 630 |
| benchmarks/nat_trace | 3 | 487 | 98 | 138 | 723 |
| container/deps | 5 | 474 | 93 | 91 | 658 |
| tests/vllm_self_benchmark | 2 | 421 | 111 | 62 | 594 |
| examples/common | 3 | 413 | 231 | 84 | 728 |
| tests/benchmarks | 8 | 393 | 37 | 76 | 506 |
| tests/wheels | 2 | 381 | 123 | 112 | 616 |
| lib/router-plugins | 3 | 350 | 85 | 50 | 485 |
| examples/custom_encoder | 6 | 343 | 153 | 90 | 586 |
| examples/deployments | 1 | 283 | 32 | 44 | 359 |
| tests/basic | 2 | 276 | 88 | 68 | 432 |
| benchmarks/mocker | 2 | 273 | 92 | 66 | 431 |
| benchmarks/burstgpt_loadgen | 1 | 246 | 71 | 67 | 384 |
| lib/truthy | 2 | 246 | 78 | 29 | 353 |
| recipes/qwen3.6-35b | 2 | 206 | 62 | 25 | 293 |
| adapter/modules/m01-platform-contract | 1 | 197 | 3 | 10 | 210 |
| deploy/observability | 2 | 189 | 33 | 36 | 258 |
| container/tests | 1 | 173 | 4 | 33 | 210 |
| tests/runtime | 2 | 171 | 6 | 37 | 214 |
| components/src/dynamo/kv_dc_relay | 3 | 168 | 10 | 42 | 220 |
| recipes/glm-5-nvfp4 | 1 | 165 | 58 | 26 | 249 |
| examples/custom_backend | 5 | 153 | 66 | 79 | 298 |
| benchmarks/sin_load_generator | 1 | 143 | 26 | 15 | 184 |
| .devcontainer | 2 | 109 | 32 | 38 | 179 |
| (root) | 2 | 103 | 56 | 42 | 201 |
| adapter/tools | 1 | 101 | 7 | 11 | 119 |
| deploy/helm | 2 | 78 | 21 | 22 | 121 |
| recipes/kimi-k2.5 | 1 | 77 | 48 | 7 | 132 |
| recipes/nemotron-3.5-lightning | 1 | 76 | 2 | 16 | 94 |
| components/src/dynamo/kv_state_agent | 2 | 67 | 7 | 18 | 92 |
| examples/rl | 3 | 63 | 45 | 24 | 132 |
| recipes/deepseek-v4 | 2 | 59 | 8 | 30 | 97 |
| recipes/qwen3-vl-32b-fp8 | 1 | 53 | 16 | 10 | 79 |
| recipes | 1 | 50 | 20 | 12 | 82 |
| benchmarks/omni | 1 | 49 | 25 | 5 | 79 |
| recipes/inkling | 1 | 47 | 17 | 12 | 76 |
| recipes/qwen3-vl-30b | 1 | 33 | 10 | 10 | 53 |
| adapter/modules/m04-device-memory | 1 | 30 | 2 | 0 | 32 |
| container/dev | 1 | 29 | 10 | 6 | 45 |
| recipes/nemotron-3-nano-omni | 1 | 18 | 35 | 7 | 60 |
| lib/kvbm-common | 1 | 11 | 13 | 4 | 28 |
| deploy/checkpoint-placeholder | 1 | 10 | 42 | 6 | 58 |
| benchmarks | 1 | 0 | 0 | 0 | 0 |
| deploy | 1 | 0 | 15 | 1 | 16 |
| examples | 1 | 0 | 0 | 0 | 0 |

## 生成源码（不在上述主表内）

| 分类 | 文件 | 有效代码行 | 注释行 | 空行 | 物理行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Go | 3 | 3,104 | 394 | 346 | 3,844 |
| Python | 5 | 1,495 | 454 | 289 | 2,238 |
| TypeScript | 2 | 136 | 21 | 5 | 162 |

## 全部文件分类

| 类别 | 文件数 | 文本物理行 |
| --- | ---: | ---: |
| 源码、测试及构建脚本 | 3,628 | 1,347,993 |
| 明确标记的生成源码 | 10 | 6,244 |
| 文档 | 815 | 137,815 |
| 配置与结构化数据 | 1,006 | 929,383 |
| 其它文本/资源 | 329 | 26,000 |
| 第三方 vendor 文件 | 6 | 4,296 |
| 符号链接 | 9 | 0 |
| 依赖锁定文件 | 6 | 37,783 |
| 版权元数据 | 3,107 | 7,052 |
| 结果、日志、快照及补丁 | 3,029 | 2,093,500 |

## 复现

```bash
curl -fL https://raw.githubusercontent.com/AlDanial/cloc/v2.06/cloc -o /tmp/cloc-2.06.pl
python3 adapter/tools/project-stats.py --revision b24f6dbe071be0d047e421568288755a85f041de \
  --cloc /tmp/cloc-2.06.pl --output /tmp/dynamo-project-statistics
```

cloc SHA256：`ed9fbdd081a2ceb933ea490b3c1cfacc87d3898ae2650d0d6756439695a836c8`。脚本不修改 Git index 或工作树，只向指定输出目录写入统计产物。
