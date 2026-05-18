---
name: driver-code-reviewer
description: >-
  NuttX/Vela 驱动代码质量审查（59 Pattern + 双轮交叉验证 + 量化评分）。
  Use when: driver review, driver code review, 驱动审查, 驱动代码审查,
  review driver, 审查驱动代码, NuttX driver review, 驱动质量检查,
  review nuttx/drivers, review arch/ driver, 驱动提交前检查.
  即使用户只说"帮我审查下这个驱动"也应触发。
  驱动开发/实现(use nuttx-driver-development).
---

# NuttX Driver Code Reviewer

基于 59 Pattern（52 质量 + 7 设计）的驱动代码质量审查，双轮交叉验证 + 量化评分。
**不修改被审查代码**，Critical 问题仅给出修复建议。

完整 Pattern 定义和维度规格见 `references/layer1-code-quality-spec.md`。

## 外部依赖

| 依赖 | 用途 | 缺失时降级方案 |
|------|------|---------------|
| `nuttx-driver-development` skill | 子系统 pattern 参考 | 仅用本 skill 内置规则 |
| `feishu` skill 或 `feishu-mcp` | 飞书报告输出（可选） | 跳过飞书，仅本地报告 |

## 触发格式

```
review <路径>
review <路径>，对比 <分支名>
```

---

## 维度体系

### 质量维度（L1-1 ~ L1-7，参与评分，100 分制）

| 维度 | 分值 | Pattern 数 | 审查内容 |
|------|:----:|:----------:|----------|
| **L1-1 内存安全** | 20 | 8 | 空指针、UAF、double-free、未初始化、堆泄漏 |
| **L1-2 并发安全** | 20 | 7 | 数据竞争、死锁、锁误用、原子性违反 |
| **L1-3 资源管理** | 10 | 7 | fd/sem/timer/work_queue 申请释放配对 |
| **L1-4 错误处理** | 10 | 4 | 返回值检查、错误传播、状态回滚 |
| **L1-5 类型与数值安全** | 10 | 5 | 整数溢出、符号混用、截断、移位 |
| **L1-6 输入与边界校验** | 15 | 12 | 缓冲区边界、ioctl 参数、格式串注入 |
| **L1-7 嵌入式专项** | 15 | 9 | DMA/cache、ISR、RPMSG、W1C 寄存器、PM |

权重分层依据：
- T0 致命（40%）：L1-1 + L1-2 — 直接导致 crash/数据损坏
- T1 严重（30%）：L1-6 + L1-7 — 攻击入口 + RTOS 独有陷阱
- T2 重要（30%）：L1-3 + L1-4 + L1-5 — 长期影响但很少立即致命

### 维度适用性判定

如果驱动不涉及某维度的检查场景，该维度标记为 **N/A**，其分值按比例重分配到其余适用维度。

| 场景 | N/A 维度 | 理由 |
|------|----------|------|
| 纯 I2C 线程驱动，无 ISR/中断 | L1-7 中 P-47/P-49 跳过 | 无中断上下文 |
| 无 DMA/RPMSG/PM 关键词 | L1-7 整体 N/A | 嵌入式专项不适用 |
| 无并发访问（单线程 chardev） | L1-2 中 P-09~P-14 部分跳过 | 无共享状态 |
| 无外部输入（纯硬件寄存器驱动） | L1-6 部分 N/A | 无用户空间输入 |

**权重重分配公式**：N/A 维度的分值按剩余维度原始权重比例分配。例如 L1-7(15分) N/A 时，15 分按 20:20:10:10:10:15 比例分配到 L1-1~L1-6。

### 独立信号（不参与质量评分）

| 维度 | 扣分上限 | Pattern 数 | 审查内容 |
|------|:--------:|:----------:|----------|
| **L1-8 设计哲学** | -10 | 7 | 抽象层绕过、命名空间污染、影子状态、重复造轮子 |
| **需求/设计一致性** | 独立报告 | — | 对照 requirement.md/design.md 检查功能完整性 |

---

## 评分规则

### 严重度与扣分（L1-1 ~ L1-7）

| 严重度 | 扣分 | 定义 |
|--------|:----:|------|
| Critical | -15 | 导致 crash、数据损坏或安全漏洞 |
| High | -8 | 显著影响代码质量，但不一定立即致命 |
| Medium | -3 | 值得修改但不阻塞 |
| Low | -1 | 改进建议 |

### L1-8 设计哲学扣分（独立）

| 严重度 | 扣分 | 适用 Pattern |
|--------|:----:|-------------|
| High | -4 | DP-01 绕过抽象层, DP-02 跨层依赖, DP-05 板级侵入, DP-06 影子状态 |
| Medium | -2 | DP-03 头文件泄漏, DP-04 缺少 static, DP-07 重复造轮子 |

### 置信度体系

| Pattern Confidence | 评分置信度 | 处理方式 |
|:------------------:|:---------:|----------|
| HIGH | 0.8 | 直接扣分 |
| MEDIUM | 0.6 | 扣分 + 标注需人工确认 |
| LOW | 0.4 | 仅 WARNING，不扣分 |

### 计算

```
维度得分 = 满分(含重分配) - Σ(每个 issue 的扣分)，最低 0 分
总分 = Σ(各适用维度得分)（满分 100）
一票否决：任一维度出现 Critical → 总分封顶 60
```

### 结论判定

| 总分 | 条件 | 结论 |
|------|------|------|
| ≥ 80 | 且无 Critical | **PASS** |
| 60-79 | 或有可修复 Critical | **NEEDS_FIX** |
| < 60 | — | **REJECT** |

### 设计健康度（L1-8，独立输出）

```
设计健康度 = 10 - Σ(DP-pattern 扣分)，最低 0
等级：A(8-10) / B(5-7) / C(0-4)
不影响质量总分和结论判定
```

---

## 执行流程

### Step 0: 收集元信息

从本地收集审查目标的元信息（文件列表、对比分支）。
**主流程不获取文件内容** — 文件内容由每个审查 agent 自行获取，保证独立性。


#### 文件获取策略

1. `get_change` 获取元信息（status, project, branch, patchset）— 401/403 中断并提示检查凭证
2. `get_files` 获取变更文件列表
3. 根据 change status 选择获取方式：

| Status | 获取方式 | branch 参数 |
|--------|---------|-------------|
| **MERGED** | `get_file_content(branch=<target_branch>)` | 目标分支名（如 `trunk`） |
| **NEW/DRAFT** | `get_file_content(branch="refs/changes/XX/<change>/<patchset>")` | XX = change 号末两位，patchset 从 get_files 返回 |

4. **Fallback 链**（任一步骤失败时依次尝试）：
   - `get_file_content` 失败 → `get_diff` 提取新文件内容
   - `get_diff` 失败 → `get_file_lines` 分段读取（每段 500 行）
   - 全部失败 → 跳过该文件，在报告中标注"未获取"

5. .md 文件与 .c/.h 文件同等处理

#### API 限流策略

- 按文件列表顺序逐个读取
- 每个请求间隔 2 秒
- 遇到 409/429：等待 5 秒后重试，最多 3 次
- **获取优先级**：.c/.h 源码文件优先，.md 文档最后获取（丢失可降级，不阻塞审查）

#### 本地路径输入

按优先级检测 diff 范围：
1. 用户显式指定基准：`review <路径>，对比 <分支名>`
2. 未提交改动：`git -C <路径> diff`
3. 已暂存改动：`git -C <路径> diff --cached`
4. 最近 commit：`git -C <路径> diff HEAD~1`

**diff 大小保护**：总变更超过 5000 行时提示用户缩小范围。

### Step 1: 预处理

1. 提取修改文件列表和关键词
2. **维度适用性判定**：扫描代码关键词，标记 N/A 维度并重分配权重
3. **L1-7 动态激活判定**（详见 spec §10.2）：

| 代码区域 | 激活关键词 | 激活的 Pattern |
|----------|-----------|---------------|
| DMA | `dma_`, `DMA`, `up_clean_dcache`, `up_invalidate_dcache` | P-44, P-45 |
| RPMSG/跨核 | `rpmsg_`, `RPMSG`, `shared_mem`, `openamp` | P-46, P-50, P-52 |
| ISR/中断 | `irq_attach`, `_isr`, `_interrupt`, `enter_critical` | P-47, P-49 |
| 硬件寄存器 | `getreg32`, `putreg32`, `modifyreg32`, `REG_` | P-48 |
| 电源管理 | `pm_stay`, `pm_relax`, `pm_resume`, `pm_notify` | P-51 |
| 无匹配 | — | L1-7 标记 N/A，权重重分配 |

4. 识别驱动子系统（sensor/can/fb/lcd/usb/input/charger/其他）

### Step 2: 加载审查规则

1. **Pattern 规格**：`references/layer1-code-quality-spec.md`（59 Pattern 完整定义）
2. **驱动通用规则**：`.claude/skills/nuttx-driver-development/references/coding_rules.md`
3. **子系统规则**（按需）：`.claude/skills/nuttx-driver-development/references/<subsystem>_pattern.md`
4. **设计文档**（如果存在）：搜索变更文件中或同目录下的 requirement.md / design.md

### Step 3+4: 双轮审查

**Round 1**：按 `agents/code-review/prompt.md` 执行 — 52 Pattern 逐项检查（L1-1~L1-7）+ 7 DP-Pattern（L1-8）+ 需求一致性。
**Round 2**：按 `agents/deep-review/prompt.md` 执行 — 侧重内存安全、并发竞态、资源生命周期的深层逻辑分析。

#### 执行模式

**默认模式（并行 subagent，各自获取源码）**：

每个 subagent 在独立上下文中自行读取源码文件并审查。主流程只传递文件路径列表。

```
主流程 Step 0 收集元信息
    ├── Round 1 subagent: 读取源码 → 7 维 Pattern 审查 → 输出 A
    ├── Round 1 subagent: 读取源码 → 7 维 Pattern 审查 → 输出 A
                                                                    （并行执行）
主流程收集 A + B → Step 5 裁判评分
```

**优势**：
- 两个 subagent 完全独立（各自全新上下文，互不可见）
- 不消耗主流程 context 传递大文件
- 两个 subagent 完全独立，互不可见

**Subagent prompt 中必须包含**：
- 变更文件路径列表
- project 名称
- branch 或 patchset ref（`refs/changes/XX/<change>/<patchset>`）
- 变更文件路径列表
- 文件获取 fallback 链（get_file_content → get_diff → get_file_lines）

**降级模式（主流程串行）**：

当 subagent 环境不可用时，在主流程中串行执行：

1. 主流程自行获取所有源码文件
2. 执行 Round 1，输出问题列表 A
3. 插入**视角切换提示**：

```
--- 视角切换 ---
以下进入 Round 2 独立安全审查。
忽略上方 Round 1 的所有结论和问题列表。
以全新的安全审查视角重新审视源码，仅关注：
内存安全深层逻辑、并发竞态时序、资源生命周期对称性、边界条件、错误传播链。
不要重复 Round 1 已报告的表面问题，专注于跨函数/跨状态的深层逻辑。
---
```

4. 执行 Round 2，输出问题列表 B

**关键约束**：无论哪种模式，Round 2 不得基于 Round 1 的结论做判断。

### Step 5: 裁判 + 评分

按 `agents/judge/prompt.md` 在主流程中直接执行：
1. 交叉验证 Round 1 和 Round 2，标注置信度
2. 按 L1-1~L1-7 评分（考虑 N/A 维度权重重分配）+ L1-8 独立信号 + 需求一致性独立报告
3. 生成结构化报告

### Step 6: 输出报告

**默认**：在对话中输出完整结构化报告。

**可选（需用户确认）**：创建飞书云文档。
- 仅在用户明确要求时执行，创建前提示确认
- 如果 `feishu` skill 不可用，跳过并告知用户

---

## 问题记录格式

```markdown
### DR-001: [标题] [Critical]
- **维度**：L1-2 并发安全
- **Pattern**：P-10 (ISR 中使用 mutex/sleep)
- **CWE**：CWE-764
- **文件**：drivers/sensors/bmi270.c:142
- **描述**：ISR handler 中调用 nxmutex_lock()，会导致死锁
- **置信度**：HIGH (0.8)
- **修复建议**：ISR 中仅设置标志位，通过 work_queue 延迟处理
```

---

## 关键文件路径

| 文件 | 用途 |
|------|------|
| `references/layer1-code-quality-spec.md` | 59 Pattern 完整规格（权威参考） |
| `agents/code-review/prompt.md` | Round 1: Pattern 驱动的全面审查 |
| `agents/deep-review/prompt.md` | Round 2: 深层逻辑安全分析 |
| `agents/judge/prompt.md` | 裁判：交叉验证 + 评分 + 报告生成 |
| `templates/review-report.md` | 飞书报告模板 |

---

## 与其他 Skill 的关系

| Skill | 关系 |
|-------|------|
| `nuttx-driver-development` | 子系统 pattern 参考（引用其 references/） |
| `feishu` | 可选依赖：飞书报告输出，缺失时降级为本地输出 |
