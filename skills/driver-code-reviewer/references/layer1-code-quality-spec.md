# Layer 1 — 代码质量层实现规格

> Bug Fix Patch Review 系统的 Layer 1（代码质量层）完整规格。
> 定义 7 个代码质量维度 (52 Pattern) + 1 个独立设计健康度维度 (7 Pattern)、评分规则、自动化策略。

**日期**: 2026-04-28
**状态**: Draft
**作者**: mage + Claude
**前置依赖**: `2026-04-23-bugfix-review-standard-design.md` (总体架构)
**Pattern 来源**: `nuttx-api-patterns.md` (52 patterns, v1.0)
**行业调研**: `2026-04-27-bugfix-review-d1-d5-implementation-spec.md` §11

---

## 目录

1. [概述](#1-概述)
2. [维度总览](#2-维度总览)
3. [维度划分原则与推导过程](#3-维度划分原则与推导过程)
4. [L1-1: 内存安全](#4-l1-1-内存安全)
5. [L1-2: 并发安全](#5-l1-2-并发安全)
6. [L1-3: 资源管理](#6-l1-3-资源管理)
7. [L1-4: 错误处理](#7-l1-4-错误处理)
8. [L1-5: 类型与数值安全](#8-l1-5-类型与数值安全)
9. [L1-6: 输入与边界校验](#9-l1-6-输入与边界校验)
10. [L1-7: 嵌入式专项](#10-l1-7-嵌入式专项)
11. [L1-8: 设计哲学](#11-l1-8-设计哲学)
12. [评分聚合](#12-评分聚合)
13. [自动化策略](#13-自动化策略)
14. [Layer 1 与 Layer 2 的关系](#14-layer-1-与-layer-2-的关系)
15. [59 Pattern 完整映射表](#15-59-pattern-完整映射表)
16. [调研来源](#16-调研来源)

---

## 1. 概述

### 1.1 Layer 1 在整体架构中的位置

```
┌──────────────────────────────────────┐
│         Bug Fix Review Agent         │
├──────────────────────────────────────┤
│  Layer 1: 代码质量层 (占总分 30%)    │  ← 本文档
│  → 7 个质量维度 (52 Pattern, 100分)  │
│  → "这段代码本身有没有通用质量问题"  │
│  + 设计健康度信号 (7 DP-Pattern)     │
│  → "代码的架构决策对不对" (独立报告) │
├──────────────────────────────────────┤
│  Layer 2: Bug Fix 专项层 (占总分 70%)│
│  → D1~D5 五个维度                    │
│  → "这个 fix 有没有正确解决 bug"     │
├──────────────────────────────────────┤
│  Layer 3: 元分析层                   │
│  → 综合置信度, 分级决策, 审计日志    │
└──────────────────────────────────────┘
```

### 1.2 设计目标

1. **仅需 patch diff 即可工作** — 不依赖 bug context 或 root cause 信息
2. **Tool + LLM + Pattern 三层混合** — 工具提供硬事实，LLM + Pattern 提供语义推理
3. **52 个 Pattern 零重叠映射** — 每个 Pattern 只归入一个质量维度
4. **嵌入式 RTOS 特化** — 专门维度覆盖 DMA/ISR/RPMSG/PM 等通用工具不覆盖的领域
5. **设计哲学独立守护** — 7 个 DP-Pattern 作为独立信号报告架构决策健康度，不混入质量评分

---

## 2. 维度总览

### 2.1 代码质量维度 (L1-1~L1-7, 参与评分)

| 维度 | 分值 | Pattern 数 | 定义 |
|------|:----:|:----------:|------|
| **L1-1: 内存安全** | **20** | 8 | 空指针、UAF、double-free、未初始化、堆泄漏 |
| **L1-2: 并发安全** | **20** | 7 | 数据竞争、死锁、锁误用、原子性违反 |
| **L1-3: 资源管理** | **10** | 7 | 资源申请/释放配对 (fd, sem, timer, work_queue) |
| **L1-4: 错误处理** | **10** | 4 | 返回值检查、错误传播、状态回滚 |
| **L1-5: 类型与数值安全** | **10** | 5 | 整数溢出/下溢、符号混用、截断、移位 |
| **L1-6: 输入与边界校验** | **15** | 12 | 缓冲区边界、外部输入校验、注入防护 |
| **L1-7: 嵌入式专项** | **15** | 9 | DMA/cache、ISR、RPMSG、W1C 寄存器、PM |
| **合计** | **100** | **52** | |

### 2.1.1 设计健康度维度 (L1-8, 独立信号)

| 维度 | 扣分上限 | Pattern 数 | 定义 |
|------|:--------:|:----------:|------|
| **L1-8: 设计哲学** | **-10** | 7 | 抽象层绕过、命名空间污染、影子状态、重复造轮子 |

> L1-8 **不参与** Layer 1 百分制评分和总分计算。作为独立的"设计健康度"信号随 review 报告一并输出，报告架构决策质量。
> **设计理由**: L1-1~L1-7 检测客观缺陷 (CWE-mapped)，L1-8 检测主观架构决策。两者性质不同，混入同一数值会稀释缺陷检测的信号精度。

### 2.2 权重分层 (L1-1~L1-7, 100 分制)

```
T0 (致命, 40%): 内存安全(20) + 并发安全(20) = 40
  → 直接导致 crash/数据损坏/安全漏洞
  → CWE Top 25 中占绝对多数
  → Microsoft/Google 统计: ~70% 的 C/C++ 严重漏洞是内存安全问题

T1 (严重, 30%): 输入与边界校验(15) + 嵌入式专项(15) = 30
  → 输入边界是攻击入口 (CWE-20/CWE-120)
  → 嵌入式专项是 RTOS 独有致命陷阱 (DMA cache/ISR 栈/RPMSG)
  → 通用工具无法覆盖，必须显式检查

T2 (重要, 30%): 资源管理(10) + 错误处理(10) + 类型与数值(10) = 30
  → 长期影响 (泄漏/静默错误/数据精度)，但很少立即致命
  → pattern 复杂度较低，检查模式统一

总分 = 100 (直接用于 Layer 1 → 总分贡献计算，无需归一化)
```

> **L1-8 设计哲学** 不参与权重分层。作为独立信号，其扣分体系见 [§11.4](#114-扣分规则)。

### 2.3 严重度与扣分规则 (L1-1~L1-7)

| 严重度 | 扣分 | 定义 |
|--------|:----:|------|
| Critical | -15 | 该问题会导致 crash、数据损坏或安全漏洞 |
| High | -8 | 该问题显著影响代码质量，但不一定立即致命 |
| Medium | -3 | 值得修改但不阻塞 |
| Low | -1 | 改进建议 |

**一票否决**: 任一 L1-1~L1-7 维度出现 Critical issue → Layer 1 总分封顶 60。

> L1-8 设计哲学使用独立的扣分规则 (High=-4, Medium=-2)，不适用本表。详见 [§11.4](#114-扣分规则)。

---

## 3. 维度划分原则与推导过程

### 3.1 数据来源

维度划分基于两套独立体系的交叉验证：

| 体系 | 来源 | 维度数 | 分类方式 |
|------|------|:------:|----------|
| **行业标准调研** | CWE Top 25、CERT C、MISRA C、Coverity、cppcheck、Clang SA、PC-lint、Polyspace、IEC 61508、ISO 26262、NASA JPL、Barr Group、Linux Kernel | 6 维 | 按缺陷类型 (CWE 分类) |
| **NuttX 实战 Pattern** | `nuttx-api-patterns.md` (52 个 pattern) | 8 维 | 按缺陷场景 (代码出现位置) |
| **架构决策准则** | NuttX 分层架构、SOLID 原则、嵌入式设计规范 | 1 维 | 按设计哲学 (接口/位置/模型) |

### 3.2 合并决策

| nuttx-api-patterns (8 维) | 行业调研 (6 维) | 最终决策 | 理由 |
|---|---|---|---|
| Memory Safety | L1-1 内存安全 | **保留** | 两套完全对应 |
| Concurrency | L1-2 并发安全 | **保留** | 两套完全对应 |
| Resource Management | L1-3 资源管理 | **保留** | 两套完全对应 |
| Error Handling | L1-4 错误处理 | **保留** | 两套完全对应 |
| Integer Safety | L1-5 类型与数值安全 | **保留 (扩展)** | 纳入 MISRA 类型规则 + volatile |
| **Boundary** | ❌ 无 | **合并为 L1-6** | 与 Input Validation 共享"外部数据/边界"审查视角 |
| **Input Validation** | ❌ 无 | **↑ 合并入 L1-6** | 12 个 pattern 统一为一个维度 |
| **Embedded-Specific** | ❌ 无 | **保留为 L1-7** | 9 个 pattern 拆散会被稀释 |
| — | L1-6 API 使用正确性 | **砍掉** | pattern 天然归属场景维度 |
| — | — (新增) | **新增 L1-8** | 架构决策维度，与缺陷检测正交 |

### 3.3 分类原则

| 原则 | 说明 | 示例 |
|------|------|------|
| **场景优先** | 有明确触发场景的 pattern 按场景归类 | ISR 中 malloc → L1-7 嵌入式专项 |
| **类型兜底** | 无明确场景的通用缺陷按 CWE 类型归类 | UAF → L1-1 内存安全 |
| **零重叠** | 59 个 pattern 每个只归入一个维度 | P-21 (work_cancel 缺失) → L1-3 资源管理 |
| **L1/L2 互斥** | 同一 issue 不在 Layer 1 和 Layer 2 重复扣分 | "fix 遗漏的 NULL check" → D4; "新增代码 NULL check 缺失" → L1-1 |

---

## 4. L1-1: 内存安全

**分值**: 20 分 | **Pattern**: P-01 ~ P-08 (8 个) | **严重度分布**: 3 Critical, 4 High, 1 Medium

**定义**: Patch 是否引入内存访问错误，包括空指针、UAF、double-free、未初始化内存、堆泄漏。不含边界溢出（归入 L1-6）。

### 4.1 检查项与 Pattern 映射

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 1 | malloc 返回值未检查 → NULL 解引用 | P-01 | CWE-476, CWE-690 | Tool: cppcheck `nullPointer` / Coverity `FORWARD_NULL` | Critical | HIGH |
| 2 | work_queue 回调中 UAF | P-02 | CWE-416 | LLM (异步生命周期分析) | Critical | MEDIUM |
| 3 | 错误路径 double free | P-03 | CWE-415 | Tool: cppcheck `doubleFree` / Coverity | Critical | HIGH |
| 4 | realloc 覆盖原指针 → 泄漏 | P-04 | CWE-401, CWE-476 | Tool: cppcheck / LLM | High | HIGH |
| 5 | sq_entry 摘除后未释放 | P-05 | CWE-401 | LLM (NuttX 队列语义) | High | MEDIUM |
| 6 | free 后指针未置 NULL → 悬挂指针 | P-06 | CWE-825 | LLM | Medium | HIGH |
| 7 | 未初始化栈变量作为输出 | P-07 | CWE-457 | Tool: cppcheck `uninitvar` / Coverity `UNINIT` | High | HIGH |
| 8 | sizeof(pointer) vs sizeof(array) | P-08 | CWE-131 | Tool: Coverity `SIZEOF_MISMATCH` | High | HIGH |

### 4.2 LLM 补充审查

- 复杂条件路径中的空指针风险（工具路径敏感分析不够深时）
- 跨函数的生命周期分析（指针在 A 分配，B 释放，C 使用）
- `container_of()` 宏——如果外层指针可能为 NULL

### 4.3 扣分规则

| 严重度 | 扣分 | 典型 issue |
|--------|:----:|-----------|
| Critical | -15 | UAF / double-free / NULL 解引用 |
| High | -8 | 泄漏 / 未初始化 / sizeof 错误 |
| Medium | -3 | 悬挂指针 |
| **一票否决** | — | 任何 Critical → Layer 1 总分封顶 60 |

---

## 5. L1-2: 并发安全

**分值**: 20 分 | **Pattern**: P-09 ~ P-15 (7 个) | **严重度分布**: 2 Critical, 4 High, 1 Medium

**定义**: Patch 是否引入并发问题，包括数据竞争、死锁、锁使用不当、原子性违反。不含 ISR 阻塞调用（归入 L1-7）。

### 5.1 检查项与 Pattern 映射

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 1 | 共享全局变量无锁访问 | P-09 | CWE-362 | Tool: Polyspace / Coverity `MISSING_LOCK` / LLM | High | LOW |
| 2 | ISR 中使用 mutex/sleep | P-10 | CWE-764, CWE-662 | LLM (调用链检查) | Critical | HIGH |
| 3 | 锁顺序不一致 → 死锁 | P-11 | CWE-833 | Tool: Coverity `ORDER_REVERSAL` / LLM | High | LOW |
| 4 | nxsem_wait 返回值未检查 | P-12 | CWE-252 | LLM (NuttX API 语义) | High | HIGH |
| 5 | 硬件寄存器 RMW 无保护 | P-13 | CWE-362 | LLM (getreg32/putreg32 模式) | High | MEDIUM |
| 6 | 共享标志缺 memory barrier | P-14 | CWE-362 | LLM (多核/编译器重排) | High | LOW |
| 7 | 二值信号量代替 mutex (无优先级继承) | P-15 | CWE-662 | LLM (nxsem_init vs nxmutex_init) | Medium | MEDIUM |

### 5.2 工具 vs LLM 分工

```
工具可覆盖: ~30% (仅 Polyspace/Coverity 有较好覆盖)
LLM 审查:   ~70% (并发问题需要理解语义和时序)
信任度:     LLM 并发审查置信度上限 0.7 (标注 WARNING 供人工确认)
```

> 注: P-09/P-11/P-14 为 LOW confidence，需要跨文件全局分析，LLM 从 diff 中不易可靠判断。
> 这是并发维度检出率最低 (~50%) 的根因。

### 5.3 扣分规则

| 严重度 | 扣分 | 典型 issue |
|--------|:----:|-----------|
| Critical | -15 | ISR mutex / 数据竞争确认 |
| High | -8 | 锁顺序 / 返回值 / RMW / barrier |
| Medium | -3 | 优先级反转风险 |

---

## 6. L1-3: 资源管理

**分值**: 10 分 | **Pattern**: P-16 ~ P-22 (7 个) | **严重度分布**: 2 Critical, 4 High, 1 Medium

**定义**: Patch 是否正确管理系统资源的申请与释放配对。

### 6.1 检查项与 Pattern 映射

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 1 | fd 泄漏 (错误路径未 close) | P-16 | CWE-775 | Tool: Coverity `RESOURCE_LEAK` / LLM | High | HIGH |
| 2 | mutex/sem 未 destroy | P-17 | CWE-404 | LLM (init/destroy 配对) | Medium | HIGH |
| 3 | pm_stay 无匹配 pm_relax | P-18 | CWE-772 | LLM (NuttX PM API) | High | HIGH |
| 4 | socket 泄漏 | P-19 | CWE-775 | Tool: Coverity / LLM | High | HIGH |
| 5 | 多步 init 失败时回滚不完整 | P-20 | CWE-404 | LLM (逆序清理检查) | High | MEDIUM |
| 6 | free 前未 work_cancel | P-21 | CWE-416 | LLM (异步生命周期) | Critical | HIGH |
| 7 | free 前未 wd_cancel | P-22 | CWE-416 | LLM (定时器生命周期) | Critical | HIGH |

### 6.2 嵌入式特化

- 无 swap/OOM killer，资源泄漏 = 必然 crash（只是时间问题）
- RTOS 资源有硬上限（NuttX: `CONFIG_NFILE_DESCRIPTORS`）

### 6.3 扣分规则

| 严重度 | 扣分 | 典型 issue |
|--------|:----:|-----------|
| Critical | -15 | free 前未取消异步引用 (work_queue/timer) |
| High | -8 | fd/socket/PM 泄漏，回滚不完整 |
| Medium | -3 | sem/mutex 未 destroy |

---

## 7. L1-4: 错误处理

**分值**: 10 分 | **Pattern**: P-30 ~ P-33 (4 个) | **严重度分布**: 0 Critical, 2 High, 2 Medium

**定义**: Patch 是否正确检查和传播错误，确保异常路径不导致静默失败。

### 7.1 检查项与 Pattern 映射

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 1 | errno 被中间调用覆盖 | P-30 | CWE-456 | LLM (syslog/close 覆盖 errno) | Medium | HIGH |
| 2 | DEBUGASSERT 用于运行时错误 | P-31 | CWE-617 | LLM (assert vs graceful error) | High | HIGH |
| 3 | 关键操作后状态未回滚 | P-32 | CWE-755 | LLM (状态机一致性) | High | MEDIUM |
| 4 | nxsem_wait 返回值双重取反 | P-33 | CWE-253 | LLM (NuttX 错误码约定) | Medium | HIGH |

### 7.2 NuttX 特化

- NuttX 内核函数返回负 errno (`-EINVAL`)，POSIX 包装层设 `errno` 返回 `-1`
- `nxsem_wait` 已返回 `-ECANCELED`/`-EINTR`，不需要再次取反
- CERT C ERR30-C: 读 errno 前确保它被设置
- CERT C ERR33-C: 检测和处理标准库错误

### 7.3 扣分规则

| 严重度 | 扣分 | 典型 issue |
|--------|:----:|-----------|
| High | -8 | DEBUGASSERT 误用，状态未回滚 |
| Medium | -3 | errno 覆盖，双重取反 |

---

## 8. L1-5: 类型与数值安全

**分值**: 10 分 | **Pattern**: P-39 ~ P-43 (5 个) | **严重度分布**: 1 Critical, 2 High, 2 Medium

**定义**: Patch 是否存在整数溢出/下溢、有符号/无符号混用、隐式截断、移位越界等数值类问题。

### 8.1 检查项与 Pattern 映射

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 1 | size_t 减法下溢 | P-39 | CWE-191 | LLM (unsigned 减法) | High | HIGH |
| 2 | 有符号/无符号混合比较 | P-40 | CWE-195, CWE-697 | Tool: cppcheck `signConversion` / PC-lint `573` | High | HIGH |
| 3 | 乘法溢出 → 小分配大写入 | P-41 | CWE-190, CWE-122 | Tool: Coverity `OVERFLOW_BEFORE_WIDEN` / LLM | Critical | MEDIUM |
| 4 | 类型窄化截断 | P-42 | CWE-681 | Tool: PC-lint `569` / PVS-Studio | Medium | HIGH |
| 5 | 移位量 ≥ 类型位宽 | P-43 | CWE-682 | Tool: Coverity `BAD_SHIFT` | Medium | HIGH |

### 8.2 补充检查项 (无 Pattern，有行业标准依据)

| 检查项 | CWE | 来源 |
|--------|-----|------|
| 除零 | CWE-369 | CERT C INT33-C |
| volatile 遗漏 (MMIO/ISR 共享) | — | Barr Group, MISRA Rule 13.x |
| 固定宽度类型未使用 | — | Barr Group (uint32_t not unsigned int) |
| 浮点 == 判等 | CWE-1025 | MISRA Rule 13.x |

### 8.3 扣分规则

| 严重度 | 扣分 | 典型 issue |
|--------|:----:|-----------|
| Critical | -15 | 乘法溢出导致越界 |
| High | -8 | size_t 下溢 / 符号混合比较 / volatile 遗漏 |
| Medium | -3 | 截断 / 移位 / 浮点判等 |

---

## 9. L1-6: 输入与边界校验

**分值**: 15 分 | **Pattern**: P-23~P-29 + P-34~P-38 (12 个) | **严重度分布**: 5 Critical, 5 High, 2 Medium

**定义**: Patch 是否正确校验外部输入和边界条件，包括缓冲区边界、ioctl 参数、网络包字段、格式串注入等。

### 9.1 边界校验子类

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 1 | strcpy/sprintf → 栈溢出 | P-23 | CWE-121, CWE-120 | Tool: cppcheck / Clang SA | Critical | HIGH |
| 2 | snprintf 截断未检查 | P-24 | CWE-135 | LLM (返回值 vs sizeof) | Medium | HIGH |
| 3 | ioctl 参数未做范围校验 | P-25 | CWE-20, CWE-787 | LLM (arg cast 后直接使用) | High | MEDIUM |
| 4 | VLA 大小无上限 | P-26 | CWE-770, CWE-121 | LLM (数组大小来源检查) | Critical | HIGH |
| 5 | off-by-one (循环边界) | P-27 | CWE-193, CWE-787 | LLM (`<=` vs `<`) | High | MEDIUM |
| 6 | 环形缓冲区回绕错误 | P-28 | CWE-131, CWE-787 | LLM (取模/掩码逻辑) | High | MEDIUM |
| 7 | 网络包字段做数组索引 | P-29 | CWE-129, CWE-125 | LLM (外部数据 → bounds check) | Critical | HIGH |

### 9.2 输入校验子类

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 8 | 外部字符串做格式串 | P-34 | CWE-134 | Tool: GCC -Wformat-security / LLM | Critical | HIGH |
| 9 | system()/popen() + 未净化输入 | P-35 | CWE-78 | LLM (调用链追踪输入来源) | Critical | HIGH |
| 10 | 路径穿越 (../未过滤) | P-36 | CWE-22 | LLM (文件路径拼接检查) | High | HIGH |
| 11 | ioctl switch 缺 default | P-37 | CWE-478 | LLM (switch 完整性) | Medium | HIGH |
| 12 | strncpy 后未保证 NUL 结尾 | P-38 | CWE-170 | LLM (strlcpy 替代) | High | HIGH |

### 9.3 审查思维

此维度的核心问题是 **"数据从哪来，有没有校验"**：

| 数据来源 | 信任级别 | 校验要求 |
|----------|:--------:|----------|
| 用户空间 (ioctl arg, read buffer) | 不可信 | 必须校验 |
| 网络/BLE/RPMSG | 不可信 | 必须校验 |
| 传感器硬件寄存器 | 半可信 | 应校验范围 |
| 内部代码 | 可信 | 按信任级别判断 |

### 9.4 与 Layer 2 D4 的区分

- **L1-6**: 检查 patch 新增代码是否有 **通用的** 输入/边界校验遗漏
- **D4**: 检查 fix 是否只处理了 happy path 而遗漏了 **与 bug 相关的特定** 边界

### 9.5 扣分规则

| 严重度 | 扣分 | 典型 issue |
|--------|:----:|-----------|
| Critical | -15 | 栈溢出 / VLA / 格式串注入 / 命令注入 / 外部索引无检查 |
| High | -8 | ioctl 无校验 / off-by-one / 路径穿越 / strncpy |
| Medium | -3 | snprintf 截断 / switch 缺 default |

---

## 10. L1-7: 嵌入式专项

**分值**: 15 分 | **Pattern**: P-44 ~ P-52 (9 个) | **严重度分布**: 3 Critical, 5 High, 1 Medium

**定义**: Patch 是否涉及 RTOS/嵌入式硬件特有的质量陷阱，这些问题在通用代码审查中极易遗漏。

### 10.1 检查项与 Pattern 映射

| # | 检查项 | Pattern | CWE | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|-----|----------|:------:|:----------:|
| 1 | DMA buffer 未 cache-line 对齐 | P-44 | CWE-119 | LLM (aligned_data 检查) | High | MEDIUM |
| 2 | DMA 前后缺 cache flush/invalidate | P-45 | CWE-119 | LLM (up_clean_dcache / up_invalidate_dcache) | High | MEDIUM |
| 3 | RPMSG 消息超 MTU | P-46 | CWE-120 | LLM (sizeof vs RPMSG_MTU, _Static_assert) | Critical | HIGH |
| 4 | ISR 中调用 malloc/printf/sleep | P-47 | CWE-662, CWE-833 | LLM (ISR 调用链分析) | Critical | HIGH |
| 5 | W1C 寄存器 read-back-write 清除无关位 | P-48 | CWE-1281 | LLM (getreg32→modify→putreg32 模式) | High | MEDIUM |
| 6 | ISR 栈分配过大 (>256B) | P-49 | CWE-770, CWE-121 | LLM (ISR 函数中局部变量大小) | High | HIGH |
| 7 | 多核共享内存写入后缺 cache flush | P-50 | CWE-362 | LLM (up_clean_dcache + SP_DMB) | High | MEDIUM |
| 8 | PM resume 路径缺寄存器恢复 | P-51 | CWE-665 | LLM (pm_resume callback 完整性) | High | MEDIUM |
| 9 | RPMSG 消息结构仅一侧修改 | P-52 | CWE-188 | LLM (共享头文件检查) | Critical | MEDIUM |

### 10.2 动态激活机制

此维度仅当 patch 修改了相关代码区域时才激活对应检查：

| 代码区域 | 激活条件 (diff 中出现的关键词) | 激活的 Pattern |
|----------|-------------------------------|---------------|
| DMA | `dma_`, `DMA`, `up_clean_dcache`, `up_invalidate_dcache` | P-44, P-45 |
| RPMSG/跨核 | `rpmsg_`, `RPMSG`, `shared_mem`, `openamp` | P-46, P-50, P-52 |
| ISR/中断 | `irq_attach`, `_isr`, `_interrupt`, `enter_critical` | P-47, P-49 |
| 硬件寄存器 | `getreg32`, `putreg32`, `modifyreg32`, `REG_` | P-48 |
| 电源管理 | `pm_stay`, `pm_relax`, `pm_resume`, `pm_notify` | P-51 |
| **无匹配** | 以上关键词均未出现 | **跳过此维度**, 满分 15 分 |

### 10.3 扣分规则

| 严重度 | 扣分 | 典型 issue |
|--------|:----:|-----------|
| Critical | -15 | RPMSG MTU / ISR 阻塞 / RPMSG 结构不一致 |
| High | -8 | DMA cache / W1C / ISR 栈 / 共享内存 / PM resume |

---

## 11. L1-8: 设计哲学

**扣分上限**: -10 | **Pattern**: DP-01 ~ DP-07 (7 个) | **严重度分布**: 0 Critical, 4 High, 3 Medium
**评分体系**: 独立信号，不参与 Layer 1 百分制总分 (详见 [§12.2.1](#1221-设计健康度信号-l1-8-独立))
**激活策略**: **始终激活** — 每次 review 均检测全部 7 个 DP-Pattern，无动态激活/跳过机制

**定义**: Patch 是否违反代码架构决策准则。不检查"代码有没有 bug"，而是检查"代码放在了对不对的地方、用了对不对的方式"。

**与 L1-1~L1-7 的本质区别**:

```
L1-1~L1-7: 代码有没有缺陷？ (correctness)
  → 内存泄漏、竞态条件、整数溢出、边界溢出 ...
  → 检测目标是 CWE 映射的、可触发的 bug

L1-8: 代码的架构决策对不对？ (design integrity)
  → 接口选错了、位置放错了、状态建错了 ...
  → 检测目标是破坏抽象/分层/单一职责的设计违反
  → 不会立刻 crash，但持续积累导致系统不可维护、间接 bug 频发
```

### 11.1 三条核心原则

| 原则 | 一句话 | 检查什么 | 反模式 |
|------|--------|----------|--------|
| **用对的接口** | 别绕过抽象层 | 是否通过正确的 API 层级完成操作 | 应用层直接操作寄存器、绕过 VFS 直调 driver 内部函数 |
| **放对的位置** | 别污染公共空间 | 代码是否在正确的层次/模块/可见性中 | 板级逻辑侵入通用驱动、内部符号暴露到公共头文件、缺少 static |
| **建对的模型** | 别造影子状态 | 是否创建了多余的状态副本或重复机制 | 自行维护 PM 已追踪的状态、自建 timer 循环替代 work_queue |

### 11.2 检查项与 Pattern 映射

| # | 检查项 | Pattern | 原则 | 检查方式 | 严重度 | Confidence |
|:-:|--------|---------|:----:|----------|:------:|:----------:|
| 1 | 绕过抽象层直接操作底层 | DP-01 | 接口 | LLM (调用链层次分析) | High | MEDIUM |
| 2 | 跨层直接依赖 (include 越界) | DP-02 | 接口 | LLM + Tool (#include 路径分析) | High | HIGH |
| 3 | 内部定义泄漏到公共头文件 | DP-03 | 位置 | LLM (public header 符号审查) | Medium | HIGH |
| 4 | 非 static 的模块内部函数/变量 | DP-04 | 位置 | Tool (cppcheck `unusedFunction` / LLM) | Medium | HIGH |
| 5 | 板级/芯片特定逻辑侵入通用代码 | DP-05 | 位置 | LLM (#ifdef CONFIG_ARCH/BOARD 位置分析) | High | HIGH |
| 6 | 影子状态 — 复制已有子系统管理的状态 | DP-06 | 模型 | LLM (状态语义重叠分析) | High | MEDIUM |
| 7 | 重复造轮子 — 自建已有基础设施 | DP-07 | 模型 | LLM (NuttX 基础设施匹配) | Medium | MEDIUM |

### 11.2.1 NuttX 架构层级映射表

DP-01 (绕过抽象层) 和 DP-02 (跨层依赖) 的判定依赖"代码属于哪一层"。以下映射表提供确定性判定基准：

| 路径前缀 | 架构层级 | 允许访问的下层 API | 禁止访问 |
|:---------|:---------|:------------------|:---------|
| `apps/` | 应用层 (L4) | POSIX API, nuttx/include/ 公共头 | driver 内部, arch/, board/ |
| `frameworks/` | 框架层 (L3) | POSIX API, nuttx/include/ 公共头 | driver 内部, arch/, board/ |
| `drivers/` | 驱动层 (L2) | nuttx/include/, arch/ 公共接口 | board/ 内部, 其他 driver 内部 |
| `arch/*/src/` | 芯片 HAL (L1) | 寄存器操作 (getreg32/putreg32) | — (本层允许) |
| `boards/` | 板级 (L0) | arch/ 接口, 板级寄存器 | — (本层允许) |
| `include/nuttx/` | 公共 API 头 | — (声明层，不含实现) | 不应包含内部结构体定义 |

**跨层判定规则**: 如果文件路径属于 Lx 层，但代码中出现了 Ly 层的内部 API/头文件（y < x-1 或 y 是非相邻层），则触发 DP-01 或 DP-02。

### 11.3 Pattern 详细说明

#### DP-01: 绕过抽象层直接操作底层

**触发条件**: Patch 在非 arch/board 层代码中引入寄存器直接操作或底层 API 直调

```c
// BAD: 应用/框架层直接操作 sensor 寄存器
void app_read_temperature(void)
{
  uint32_t raw = getreg32(BMI160_REG_TEMP);  // 绕过 sensor driver
  ...
}

// GOOD: 通过 driver 抽象层
void app_read_temperature(void)
{
  struct sensor_temp temp;
  read(fd, &temp, sizeof(temp));  // VFS → sensor upper-half → lower-half
}
```

**判定逻辑**:
1. Patch 所在文件的层级？(arch/board vs drivers vs apps/frameworks)
2. 引入的操作是否属于更低层的 API？(getreg32 是 arch 层，不应出现在 driver 以上)
3. 是否存在对应的上层 API 可替代？
4. 如果确实没有 API，正确做法是扩展 driver，而非绕过

#### DP-02: 跨层直接依赖

**触发条件**: Patch 新增的 #include 路径跨越了层边界

```c
// BAD: 应用层直接 include 内核内部头文件
#include <nuttx/sched/sched.h>     // 内核调度器内部结构
#include "chip/stm32_gpio.h"       // 芯片内部头文件

// GOOD: 通过公共 API 头文件
#include <nuttx/sched.h>           // 公共 API
#include <nuttx/ioexpander/gpio.h> // 标准 GPIO 接口
```

**判定逻辑**:
1. 新增 #include 是否包含 `sched/`、`chip/`、`arch/xxx_internal` 等内部路径？
2. include 的层级关系是否正确？(app → nuttx/include/ 公共头 → 内部实现)
3. 被 include 的符号是否有公共 API 替代？

#### DP-03: 内部定义泄漏到公共头文件

**触发条件**: Patch 修改了 `include/` 目录下的头文件，新增了非 API 符号

```c
// BAD: 在 include/nuttx/sensors/bmi160.h 中
#define BMI160_INTERNAL_RETRY_MAX  5   // 内部实现细节泄漏到公共 API
struct bmi160_internal_ctx_s { ... };  // 内部数据结构

// GOOD: 内部定义放在 .c 文件或私有头文件
// drivers/sensors/bmi160_internal.h
#define BMI160_INTERNAL_RETRY_MAX  5
```

#### DP-04: 非 static 的模块内部函数/变量

**触发条件**: Patch 新增的函数/全局变量未声明 static，且未在任何 .h 中声明

```c
// BAD: 模块内部 helper 未加 static → 污染全局符号表
int calculate_checksum(const uint8_t *data, size_t len)
{
  ...
}

// GOOD: 限制可见性
static int calculate_checksum(const uint8_t *data, size_t len)
{
  ...
}
```

#### DP-05: 板级/芯片特定逻辑侵入通用代码

**触发条件**: Patch 在通用驱动/框架中引入 `CONFIG_ARCH_*`、`CONFIG_BOARD_*` 或硬编码 pin/地址

```c
// BAD: 通用 sensor driver 中硬编码板级逻辑
int bmi160_initialize(struct spi_dev_s *spi)
{
#ifdef CONFIG_ARCH_BOARD_XIAOMI_VELA
  gpio_write(GPIO_BMI160_CS, 0);      // 板级 pin 定义侵入
#endif
  ...
}

// GOOD: 通过 board config 抽象
int bmi160_initialize(struct spi_dev_s *spi,
                      const struct bmi160_config_s *config)
{
  if (config->cs_gpio >= 0)
    gpio_write(config->cs_gpio, 0);   // 由 board 层传入
  ...
}
```

**判定逻辑**:
1. `#ifdef CONFIG_ARCH_*` 或 `CONFIG_BOARD_*` 是否出现在 `drivers/`、`libs/`、`frameworks/` 下？
2. 硬编码的 GPIO pin 号、寄存器地址是否出现在非 board/arch 文件中？
3. 正确做法：板级差异通过 config 结构体或 Kconfig 选择传入

#### DP-06: 影子状态

**触发条件**: Patch 新增的状态变量与已有子系统管理的状态语义重叠

```c
// BAD: Driver 自行维护电源/引用计数状态
struct my_driver_s
{
  bool is_suspended;   // 影子状态! PM 框架已追踪 (pm_querystate)
  int power_state;     // 影子状态! 与 pm_querystate() 重复
  int open_count;      // 影子状态! VFS 已追踪 filep 引用计数
};

// GOOD: 通过已有 API 查询
int state = pm_querystate(PM_IDLE_DOMAIN);
```

**判定逻辑**:
1. 新增成员变量/全局变量的语义是否与某个现有子系统 API 返回值重叠？
2. 两份状态有无同步机制？没有 → 迟早不一致 → 间接 bug
3. 如果已有 API 性能不足（如查询太慢），应扩展该 API 而非建立影子副本

**常见影子状态映射**:

| 影子状态 | 已有权威来源 | 所属子系统 |
|----------|-------------|:----------:|
| `is_suspended` / `power_state` | `pm_querystate()` | PM |
| `open_count` / `ref_count` | VFS `filep` 引用计数 / `inode->i_crefs` | VFS |
| `is_connected` (蓝牙) | BT stack connection state API | BT |
| `sensor_enabled` | sensor upper-half `enabled` 状态 | Sensor |
| `timer_running` | `work_queue` 本身的 `work_available()` | WorkQueue |
| `battery_level` / `charging` | `battery_monitor` driver / uORB `battery_status` | Power |
| `wifi_connected` / `rssi` | `netdev` 状态 / `wireless` ioctl | Network |
| `audio_playing` / `volume` | `nxaudio` session state / mixer API | Audio |
| `gps_fix` / `lat` / `lon` | uORB `sensor_gnss` | GNSS |
| `screen_on` / `brightness` | `fb_ioctl` / PM activity state | Display |
| `alarm_pending` | `rtc_ops->getalarm()` | RTC |

#### DP-07: 重复造轮子

**触发条件**: Patch 新增的实现模式与已有 NuttX 基础设施功能重叠

```c
// BAD: 自建定时器循环
static void my_poll_timer(union sigval val)
{
  timer_settime(timerid, 0, &its, NULL);
  do_work();
}

// GOOD: 使用 work_queue
static void my_poll_work(void *arg)
{
  do_work();
  work_queue(LPWORK, &priv->work, my_poll_work, arg, MSEC2TICK(100));
}
```

**常见重复模式**:

| 自建方案 | 已有基础设施 | 为什么错 |
|----------|-------------|----------|
| 手动 timer + signal | `work_queue` | work_queue 已处理取消/重入/优先级 |
| 自定义进程间消息 | RPMSG / uORB | 重复实现序列化、流控、生命周期 |
| 自定义配置存储 | `property_get/set` / Kconfig | 绕过统一配置管理，无法被工具发现 |
| 自定义日志格式 | `syslog` / `_info` / `_err` | 绕过日志级别控制和统一采集 |
| 自定义环形缓冲区 | `circbuf` (lib/libc/misc) | 重复造轮子且可能有 bug |
| 自定义互斥方案 | `nxmutex` / `nxsem` | 绕过优先级继承等 RTOS 机制 |

**豁免条件** (满足任一则不触发 DP-07):

1. **代码注释明确说明理由**: 包含 `// intentional:` 或等价的设计说明，解释为什么不使用已有基础设施（如性能、避免循环依赖等）
2. **已有基础设施不可用**: 目标运行环境确实不支持对应设施（如 flat build 下无 RPMSG、无 CONFIG_SCHED_WORKQUEUE 配置）
3. **arch/board 层实现**: arch/ 或 boards/ 目录下的代码，因硬件约束可能需要自建底层机制

### 11.4 扣分规则

| 严重度 | 扣分 | 适用 Pattern | 典型 issue |
|--------|:----:|:------------|-----------|
| High | -4 | DP-01, DP-02, DP-05, DP-06 | 抽象层绕过 / 跨层依赖 / 板级侵入 / 影子状态 |
| Medium | -2 | DP-03, DP-04, DP-07 | 头文件污染 / 缺少 static / 重复造轮子 |

> **为什么没有 Critical**: 设计哲学问题不会直接导致 crash 或安全漏洞，不触发一票否决。但 High 级违反在 bug fix 上下文中尤为重要——为了"快速修复"而绕过架构，是最常见的技术债来源。每一个 High 级设计违反都意味着下一个 fix 会更难写、更容易出错。

### 11.5 与 Layer 2 的去重

| 场景 | 归入 | 理由 |
|------|------|------|
| Bug fix 绕过 driver 抽象层直接改寄存器 | **L1-8** (DP-01) | 架构决策错误，不是 fix 有效性问题 |
| Bug fix 为图省事把板级 workaround 加到通用驱动 | **L1-8** (DP-05) | 放错了位置 |
| Bug fix 新增状态变量追踪 PM 已管理的状态 | **L1-8** (DP-06) | 建了影子模型 |
| Bug fix 遗漏了某个特定边界条件 | **D4** (边界完整性) | fix 有效性问题，不是架构问题 |
| Bug fix 改动范围过大包含无关重构 | **D2** (最小化) | fix 范围控制，不是架构问题 |

### 11.5.1 与 L1-1~L1-7 的关系：双重计分

L1-8 与 L1-1~L1-7 之间**允许双重计分**——同一处代码可以同时在质量维度和设计维度各自扣分。

| 重叠场景 | L1-1~L1-7 计分 | L1-8 计分 | 理由 |
|:---------|:---------------|:----------|:-----|
| 自建 `power_state` 变量 + pm_stay 未配对 | L1-3 P-18 (资源管理) | DP-06 (影子状态) | 前者是"资源泄漏"，后者是"状态建模错误"——不同维度的判断 |
| 绕过 kmm_malloc 直接调 sbrk | L1-1 (内存安全) | DP-01 (绕过抽象层) | 前者是"内存 API 误用"，后者是"接口选择错误" |
| 自建 spinlock 替代 nxmutex | L1-2 (并发安全) | DP-07 (重复造轮子) | 前者是"并发机制风险"，后者是"基础设施重复" |

> **设计理由**: L1-1~L1-7 度量"有没有 bug"（correctness），L1-8 度量"设计对不对"（design integrity）。两套分数体系测量不同维度，不构成重复惩罚。且 L1-8 作为独立信号不影响 Layer 1 总分，双重计分不会导致分数失真。

### 11.6 检测能力说明

L1-8 的 7 个 Pattern **不来源于 nuttx-api-patterns.md**（那 52 个 pattern 全部是 CWE 映射的缺陷检测 pattern）。L1-8 Pattern 是独立定义的架构决策检测 pattern，编号前缀用 `DP-`（Design Philosophy）以示区分。

**检测矩阵**:

| Pattern | 检测方式 | 确定性特征 | Confidence | 工具辅助 |
|:--------|:---------|:-----------|:----------:|:--------:|
| DP-01 绕过抽象层 | LLM (调用链层次分析) | — | MEDIUM | ✗ |
| DP-02 跨层依赖 | LLM + Tool | `#include` 路径模式匹配 | HIGH | ✓ |
| DP-03 头文件泄漏 | LLM (public header 审查) | `include/` 目录下新增非 API 符号 | HIGH | ✗ |
| DP-04 缺少 static | Tool + LLM | cppcheck `unusedFunction` | HIGH | ✓ |
| DP-05 板级侵入 | LLM (#ifdef 位置分析) | `CONFIG_ARCH_*` / `CONFIG_BOARD_*` 在非 board/ 文件 | HIGH | ✗ |
| DP-06 影子状态 | LLM (状态语义重叠分析) | — | MEDIUM | ✗ |
| DP-07 重复造轮子 | LLM (NuttX 基础设施匹配) | — | MEDIUM | ✗ |

**分级总结**:

- **工具可辅助 (2/7)**: DP-02 (`#include` 路径 grep)、DP-04 (cppcheck)。这两个 pattern 可在 LLM 检测前/后用工具交叉验证，达到近 0.9 置信度。
- **确定性特征可检测 (4/7)**: DP-02/03/04/05 具备可 grep 的文本特征（路径、关键字、目录位置），HIGH confidence，接近工具级准确率。
- **纯 LLM 语义推理 (3/7)**: DP-01/06/07 依赖 LLM 对 NuttX 架构的语义理解，MEDIUM confidence，无工具可直接替代。

> **Confidence 来源**: L1-8 的 Confidence 不来自 nuttx-api-patterns.md（那是 L1-1~L1-7 的来源），而是基于各 DP-pattern 的检测信号强度独立评估——有确定性文本特征 → HIGH，纯语义推理 → MEDIUM。

---

## 12. 评分聚合

### 12.1 维度内评分

```
维度得分 = 满分 - Σ(每个 issue 的扣分)
最低 = 0 分 (不会为负)

扣分上限: 每个维度扣分不超过该维度满分
  例: L1-4 错误处理 (10分) 发现 3 个 High issue
      → 扣 min(3×8, 10) = 10 分 → 该维度 0 分
```

### 12.2 Layer 1 总分

```
Layer 1 总分 = L1-1(20) + L1-2(20) + L1-3(10) + L1-4(10)
             + L1-5(10) + L1-6(15) + L1-7(15)
             = 100 分 (天然百分制，无需归一化)

一票否决: 任一 L1-1~L1-7 维度出现 Critical issue → 总分封顶 60

特殊: L1-7 嵌入式专项未激活时 → 该维度默认满分 15，不影响总分
```

### 12.2.1 设计健康度信号 (L1-8, 独立)

```
设计健康度 = 10 - Σ(DP-pattern 扣分)
最低 = 0 (不会为负)

扣分规则: High = -4, Medium = -2 (见 §11.4)
无一票否决 (设计问题不直接导致 crash)

输出格式:
  design_health:
    score: 6          # 0~10, 越高越健康
    grade: B          # A(8-10) / B(5-7) / C(0-4)
    findings:         # 命中的 DP-pattern 列表
      - pattern: DP-01
        severity: High
        location: "drivers/sensors/bmi160.c:42"
        description: "应用层直接调用 getreg32() 绕过 sensor driver"
    summary: "1 High, 0 Medium — 存在抽象层绕过"
```

> **设计健康度不影响 Layer 1 总分和 Review 总分**，仅作为 review 报告的附加信号。
> 其价值在于长期追踪：同一模块的设计健康度持续下降，是架构腐化的早期预警。

### 12.3 Layer 1 对总分的贡献

```
Review 总分 = Layer1权重(30%) × Layer1总分 + Layer2权重(70%) × Layer2分数

Layer 1 满分 100 → 对总分贡献最高 30 分
Layer 2 满分 100 → 对总分贡献最高 70 分
```

---

## 13. 自动化策略

### 13.1 三层架构

```
                    Patch Diff
                       │
                       ▼
              ┌─────────────────┐
              │  预处理          │
              │  1. 提取修改文件 │
              │  2. 提取关键词   │
              │  3. L1-7 激活判定│
              └────────┬────────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
   ┌──────────┐ ┌──────────┐ ┌──────────────┐
   │ 工具层   │ │ 编译器   │ │ LLM + Pattern│
   │ cppcheck │ │ -Wall    │ │ 52+7 pattern │
   │ MISRA    │ │ -Wextra  │ │ 触发→匹配→  │
   │ addon    │ │ -Werror  │ │ 判定三阶段   │
   └────┬─────┘ └────┬─────┘ └────┬─────────┘
        │             │            │
        ▼             ▼            ▼
   ┌─────────────────────────────────────┐
   │         Finding 聚合与去重          │
   │  Tool findings → 高置信度 (0.9+)   │
   │  LLM findings → 按 pattern 置信度  │
   │  Tool + LLM 一致 → 最高置信 (0.95) │
   └───────┬─────────────────┬──────────┘
           │                 │
           ▼                 ▼
   ┌──────────────┐  ┌──────────────────┐
   │ 质量维度评分  │  │ 设计健康度信号   │
   │ L1-1 ~ L1-7  │  │ L1-8 (独立报告)  │
   │ → 100 分制   │  │ → 0~10 + A/B/C  │
   └──────────────┘  └──────────────────┘
```

### 13.2 各维度检出率估算

| 维度 | Pattern 数 | Tool 可覆盖 | LLM + Pattern | 预估检出率 |
|------|:----------:|:-----------:|:-------------:|:---------:|
| L1-1 内存安全 | 8 | 70% | 30% | **85%** |
| L1-2 并发安全 | 7 | 30% | 70% | **50%** |
| L1-3 资源管理 | 7 | 40% | 60% | **70%** |
| L1-4 错误处理 | 4 | 30% | 70% | **65%** |
| L1-5 类型与数值 | 5 | 60% | 40% | **75%** |
| L1-6 输入与边界 | 12 | 35% | 65% | **70%** |
| L1-7 嵌入式专项 | 9 | 10% | 90% | **55%** |
| **加权平均** | **52** | | | **~67%** |

**设计健康度 (独立)**:

| 维度 | Pattern 数 | Tool 可覆盖 | LLM + Pattern | 预估检出率 |
|------|:----------:|:-----------:|:-------------:|:---------:|
| L1-8 设计哲学 | 7 | 15% | 85% | **60%** |

### 13.3 置信度体系

**Pattern Confidence 映射**:

L1-1~L1-7: nuttx-api-patterns.md 中每个 pattern 标注了 Confidence (HIGH/MEDIUM/LOW)，直接映射到评分置信度。
L1-8: DP-pattern 的 Confidence 独立评估（基于检测信号强度，详见 [§11.6](#116-检测能力说明)），同样映射到下表：

| Pattern Confidence | LLM 检测条件 | 评分置信度 | 处理方式 |
|:------------------:|-------------|:---------:|----------|
| HIGH | 仅从 diff 上下文即可可靠检测 | 0.8 | `[PATTERN-HIGH]` 直接扣分 |
| MEDIUM | 需要周围代码/调用链辅助 | 0.6 | `[PATTERN-MEDIUM]` 扣分 + 标注需人工确认 |
| LOW | 需要整个文件/跨文件分析 | 0.4 | `[PATTERN-LOW]` 仅 WARNING，不扣分 |

**与工具结果叠加**:

| 组合 | 最终置信度 | 处理 |
|------|:---------:|------|
| Tool 确认 (cppcheck error / Coverity High) | 0.95 | `[TOOL-CONFIRMED]` 直接扣分 |
| Tool + LLM 一致 | 0.95 | `[TOOL+LLM]` 直接扣分 |
| Tool warning + LLM HIGH | 0.90 | `[VALIDATED]` 直接扣分 |
| 仅 LLM HIGH pattern 命中 | 0.80 | `[PATTERN-HIGH]` 扣分 |
| 仅 LLM MEDIUM pattern 命中 | 0.60 | `[PATTERN-MEDIUM]` 扣分 + 标注 |
| 仅 LLM LOW pattern 命中 | 0.40 | `[PATTERN-LOW]` 仅 WARNING |
| LLM 发现但无 pattern 匹配 | 0.50 | `[LLM-UNMATCHED]` 仅 WARNING |

**规则**: 最终置信度 < 0.6 的发现不参与扣分，仅作为 review 报告的附录信息。

### 13.4 Pattern Confidence 分布

**质量维度 (L1-1~L1-7)**:

| 维度 | HIGH | MEDIUM | LOW | 合计 |
|------|:----:|:------:|:---:|:----:|
| L1-1 内存安全 | 6 | 2 | 0 | 8 |
| L1-2 并发安全 | 2 | 2 | 3 | 7 |
| L1-3 资源管理 | 5 | 2 | 0 | 7 |
| L1-4 错误处理 | 3 | 1 | 0 | 4 |
| L1-5 类型与数值 | 4 | 1 | 0 | 5 |
| L1-6 输入与边界 | 9 | 3 | 0 | 12 |
| L1-7 嵌入式专项 | 3 | 6 | 0 | 9 |
| **小计** | **32** | **17** | **3** | **52** |

**设计健康度 (L1-8, 独立)**:

| 维度 | HIGH | MEDIUM | LOW | 合计 |
|------|:----:|:------:|:---:|:----:|
| L1-8 设计哲学 | 4 | 3 | 0 | 7 |

**全部 Pattern 合计**: 36 HIGH + 20 MEDIUM + 3 LOW = **59**

> L1-2 并发安全有 3 个 LOW confidence (P-09, P-11, P-14)——这解释了并发维度检出率最低的根因。
> L1-7 嵌入式专项有 6 个 MEDIUM——DMA/cache/寄存器/PM 问题需要上下文辅助，但 pattern 提供的触发条件和判定逻辑显著优于纯 LLM 推理。
> L1-8 设计哲学有 4 个 HIGH——DP-02/03/04/05 可通过 #include 路径、static 关键字、#ifdef 位置等确定性特征检测，接近工具级准确率。

---

## 14. Layer 1 与 Layer 2 的关系

### 14.1 核心区分

| 方面 | Layer 1 (代码质量) | Layer 2 (Bug Fix 专项) |
|------|-------------------|----------------------|
| **审什么** | 代码有没有缺陷 (L1-1~L1-7, 计入总分) + 架构决策对不对 (L1-8, 独立信号) | 这个 fix 有没有正确解决 bug |
| **立场** | 作为一段 C 代码，它健康吗？(L1-1~L1-7) 放对地方了吗？(L1-8) | 作为一个 bug fix，它有效吗？ |
| **参考标准** | CWE/MISRA/CERT C + nuttx-api-patterns + NuttX 分层架构 | Bug 描述 / Root Cause / 测试结果 |
| **输入依赖** | 仅需 patch diff | 需要 bug context + root cause |

### 14.2 去重规则

同一个 issue 不在 L1 和 L2 重复扣分（L1-8 为独立信号，其扣分不影响 Layer 1 总分，但仍遵循与 L2 的归属判定）：

| 场景 | 归入 | 理由 |
|------|------|------|
| fix 只处理了 happy path，缺少 NULL check | **D4** (边界完整性) | fix 特有的遗漏 |
| patch 新增代码中 sprintf 应改用 snprintf | **L1-6** (输入与边界) | 通用代码质量问题 (P-23) |
| patch 修改了锁的范围但引入新竞态 | **D3** (回归风险) | fix 引入的新问题 |
| patch 中 DMA buffer 没做 cache flush | **L1-7** (嵌入式专项) | 通用嵌入式陷阱 (P-45) |
| fix 没有处理 RPMSG 对端未更新结构体的情况 | **D4** (边界完整性) | fix 应覆盖的特定场景 |
| patch 绕过 driver 抽象层直接操作寄存器 | **L1-8** (设计哲学) | 架构决策错误 (DP-01) |
| patch 新增状态变量追踪 PM 已管理的状态 | **L1-8** (设计哲学) | 影子状态 (DP-06) |
| patch 自建 timer 循环替代 work_queue | **L1-8** (设计哲学) | 重复造轮子 (DP-07) |

---

## 15. 59 Pattern 完整映射表

**质量维度 (L1-1~L1-7, 参与评分)**:

| 维度 | Pattern 范围 | 数量 | Critical | High | Medium | 主要 CWE |
|------|:------------|:----:|:--------:|:----:|:------:|----------|
| L1-1 内存安全 | P-01 ~ P-08 | 8 | 3 | 4 | 1 | CWE-476/416/415/401/457/131 |
| L1-2 并发安全 | P-09 ~ P-15 | 7 | 2 | 4 | 1 | CWE-362/833/764/662/252 |
| L1-3 资源管理 | P-16 ~ P-22 | 7 | 2 | 4 | 1 | CWE-775/404/772/416 |
| L1-4 错误处理 | P-30 ~ P-33 | 4 | 0 | 2 | 2 | CWE-456/617/755/253 |
| L1-5 类型与数值 | P-39 ~ P-43 | 5 | 1 | 2 | 2 | CWE-191/195/190/681/682 |
| L1-6 输入与边界 | P-23~P-29 + P-34~P-38 | 12 | 5 | 5 | 2 | CWE-121/120/787/134/78/22/20 |
| L1-7 嵌入式专项 | P-44 ~ P-52 | 9 | 3 | 5 | 1 | CWE-119/120/662/833/1281/665/188 |
| **小计** | | **52** | **16** | **26** | **10** | **38 unique CWE-IDs** |

**设计健康度 (L1-8, 独立信号)**:

| 维度 | Pattern 范围 | 数量 | Critical | High | Medium | 主要 CWE |
|------|:------------|:----:|:--------:|:----:|:------:|----------|
| L1-8 设计哲学 | DP-01 ~ DP-07 | 7 | 0 | 4 | 3 | — (架构决策类，非 CWE 缺陷) |

**全部 Pattern 合计**: 52 + 7 = **59** (16 Critical, 30 High, 13 Medium)

CWE Top 25 (2025) 交集: CWE-787, CWE-416, CWE-476, CWE-190, CWE-125, CWE-120, CWE-122, CWE-121, CWE-78, CWE-22, CWE-362, CWE-401, CWE-20, CWE-131 (**14 of 25**)

> L1-8 的 7 个 DP-pattern 不映射到 CWE——它们检测的是架构决策违反而非安全/可靠性缺陷。这是 L1-8 与 L1-1~L1-7 的本质区别。

---

## 16. 调研来源

| 来源 | 贡献 |
|------|------|
| **CWE Top 25 (2025)** | 权重校准: 内存安全占 C 漏洞 ~70%，7 个 CWE 入 Top 25 |
| **CERT C Coding Standard** | 维度分类: 14 个规则类别 + 三轴风险评分 |
| **MISRA C:2012** | 类型安全: Essential Type Model, volatile 规则 |
| **NASA JPL Power of Ten** | 优先级: 可自动检查 = 有效; ≥2 assertions/func |
| **Barr Group** | 嵌入式特化: volatile 四场景, ISR 规则, 固定宽度类型 |
| **IEC 61508 / ISO 26262** | 安全等级: 静态分析 → MC/DC → 形式化验证递进 |
| **DO-178C** | 结构覆盖: 语句 → 分支 → MC/DC 按 DAL 递进 |
| **cppcheck** | 工具: 免费, MISRA addon, 内存/NULL/越界好, 无并发 |
| **Clang Static Analyzer** | 工具: 路径敏感好, 无 MISRA, 嵌入式弱 |
| **PVS-Studio** | 工具: copy-paste 检测独特, CWE 映射, MISRA V8xx |
| **PC-lint** | 工具: 嵌入式金标准, `-sem()` 建模 RTOS API |
| **Polyspace Bug Finder** | 工具: 唯一完整 RTOS 并发分析 + ISR 感知 |
| **Coverity** | 工具: LOCK/ORDER_REVERSAL, 最低误报率 |
| **Linux Kernel** | 多层生态: checkpatch + Sparse + Coccinelle + K*SAN |
| **nuttx-api-patterns.md** | Pattern 库: 52 个 NuttX 实战 pattern, 维度校准 |
