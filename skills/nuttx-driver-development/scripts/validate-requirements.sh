#!/bin/bash
# validate-requirements.sh
# 检查 requirements.md 是否包含必要章节
# 契约：成功 exit 0 静默，失败 exit 2 + stderr

REQUIREMENTS="${1:-requirements.md}"

if [ ! -f "$REQUIREMENTS" ]; then
  echo "ERROR: $REQUIREMENTS not found" >&2
  exit 2
fi

ERRORS=0

# Mode B2 必须包含系统集成分析
if grep -q "改进计划\|改进方案\|FAIL.*项\|审查结果" "$REQUIREMENTS"; then
  # 这是 Mode B2 文档
  if ! grep -q "系统集成分析\|调用链\|call chain\|System Integration" "$REQUIREMENTS"; then
    echo "ERROR: Mode B2 requirements.md 缺少「系统集成分析」章节" >&2
    ERRORS=$((ERRORS + 1))
  fi

  if ! grep -q "替换边界\|replacement boundary" "$REQUIREMENTS"; then
    echo "WARN: requirements.md 可能缺少「替换边界」标注" >&2
  fi

  if ! grep -q "BSP.*对接\|BSP.*操作\|原始函数" "$REQUIREMENTS"; then
    echo "ERROR: Mode B2 requirements.md 缺少「BSP 对接表」" >&2
    ERRORS=$((ERRORS + 1))
  fi

  if ! grep -q "启动链\|不可.*filter" "$REQUIREMENTS"; then
    echo "WARN: requirements.md 可能缺少「启动链节点识别」" >&2
  fi
fi

# 所有模式都应该有功能 Checklist
if ! grep -q "\- \[.\]" "$REQUIREMENTS"; then
  echo "WARN: requirements.md 可能缺少功能 Checklist" >&2
fi

# Mode A / B1: 检查必填章节（非 B2 时）
if ! grep -q "改进计划\|改进方案\|FAIL.*项\|审查结果" "$REQUIREMENTS"; then
  for section in \
    "工作原理\|Datasheet\|寄存器介绍" \
    "架构.*接口\|Architecture\|接口规格" \
    "实现约束\|Constraint\|全局约束" \
    "性能指标\|Performance\|采样率"; do
    if ! grep -qi "$section" "$REQUIREMENTS"; then
      echo "WARN: Mode A/B1 requirements.md 可能缺少章节匹配: $section" >&2
    fi
  done
fi

# 检查 TODO 占位符
TODO_COUNT=$(grep -c "TODO\|待补充\|占位" "$REQUIREMENTS" 2>/dev/null || true)
TODO_COUNT=${TODO_COUNT:-0}
if [ "$TODO_COUNT" -gt 0 ]; then
  echo "WARN: requirements.md 包含 $TODO_COUNT 个 TODO 占位符" >&2
fi

if [ "$ERRORS" -gt 0 ]; then
  exit 2
fi

exit 0
