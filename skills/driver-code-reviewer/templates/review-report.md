# 飞书报告模板

本模板用于生成飞书云文档审查报告。使用 `feishu-mcp` 的 `create-doc` 或 `feishu` skill 创建。

## 飞书文档标题格式

```
NuttX 驱动质量审查：{驱动名称} [{结论}] {日期}
```

## Markdown 内容模板

```markdown
## 基本信息

| 项目 | 内容 |
|------|------|
| 驱动名称 | {driver_name} |
| 子系统 | {subsystem} |
| 总线类型 | {bus_type} |
| 设计文档 | {design_doc_status} |
| 审查时间 | {review_time} |
| 审查方式 | 双轮交叉验证（59 Pattern 全面 + 深层安全） |
| L1-7 激活状态 | {l17_status} |
| 路径 | {source_path} |

## 质量评分总览

{conclusion_callout}

| 维度 | 满分 | 得分 | Critical | High | Medium | Low |
|------|------|------|----------|------|--------|-----|
| L1-1 内存安全 | 20 | {score} | {n} | {n} | {n} | {n} |
| L1-2 并发安全 | 20 | {score} | {n} | {n} | {n} | {n} |
| L1-3 资源管理 | 10 | {score} | {n} | {n} | {n} | {n} |
| L1-4 错误处理 | 10 | {score} | {n} | {n} | {n} | {n} |
| L1-5 类型与数值 | 10 | {score} | {n} | {n} | {n} | {n} |
| L1-6 输入与边界 | 15 | {score} | {n} | {n} | {n} | {n} |
| L1-7 嵌入式专项 | 15 | {score} | {n} | {n} | {n} | {n} |
| **总计** | **100** | **{total}** | **{n}** | **{n}** | **{n}** | **{n}** |

## 设计健康度：{dp_score}/10 ({grade})

{dp_findings}

## 审查结论：{conclusion}

{conclusion_summary}

## Critical 问题（必须修复）

{critical_section}

## High 问题（应该修复）

{high_section}

## Medium 问题（建议修复）

{medium_section}

## Low 问题（可选优化）

{low_section}

## WARNING（低置信度，仅供参考）

{warning_section}

## 交叉验证摘要

| 最终 ID | 来源 | R1 ID | R2 ID | Pattern | 置信度 | 备注 |
|---------|------|-------|-------|---------|--------|------|
{cross_validation_rows}

## 需求一致性报告

{requirements_section}

## 修复优先级建议

{fix_priority_section}
```

## 结论 Callout 样式

- **PASS**：`<callout emoji="✅" background-color="light-green">质量评分 {score}/100，设计健康度 {dp}/10 ({grade})。代码质量良好，可以提交。</callout>`
- **NEEDS_FIX**：`<callout emoji="⚠️" background-color="light-yellow">质量评分 {score}/100，需要修复 {n} 个 Critical 和 {m} 个 High 问题后方可提交。</callout>`
- **REJECT**：`<callout emoji="❌" background-color="light-red">质量评分 {score}/100，存在严重质量问题，建议修复后重新提交审查。</callout>`

## 单个问题格式

```markdown
### DR-F{NNN}: {title}

| 属性 | 内容 |
|------|------|
| 维度 | {L1-x} |
| Pattern | {P-xx} ({pattern_name}) |
| CWE | {CWE-xxx} |
| 文件 | `{file_path}:{line}` |
| 置信度 | {confidence} (来源：{source}) |

**问题描述**

{description}

**修复方向**

{fix_direction}
```

## 使用说明

1. 裁判输出完整报告后，按本模板格式化为飞书 Markdown
2. 仅在用户确认后创建飞书文档
3. 如果 feishu skill 不可用，跳过飞书输出
