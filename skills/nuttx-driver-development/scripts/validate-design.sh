#!/bin/bash
# validate-design.sh — 检查 design.md 可追溯性矩阵完整性
# 契约：成功 exit 0 静默，失败 exit 2 + stderr

DESIGN="${1:-design.md}"

if [ ! -f "$DESIGN" ]; then
  echo "ERROR: $DESIGN not found" >&2
  exit 2
fi

ERRORS=0

# 检测是否为 Mode B2（含改进计划/审查结果关键词）
IS_B2=false
if grep -q "改进计划\|改进方案\|审查结果" "$DESIGN"; then
  IS_B2=true
fi

if $IS_B2; then
  # Mode B2: 可追溯性矩阵必填
  if ! grep -qi "可追溯性矩阵\|traceability.*matrix\|traceability\|req.*编号\|req.*需求" "$DESIGN"; then
    echo "ERROR: Mode B2 design.md 缺少可追溯性矩阵" >&2
    ERRORS=$((ERRORS + 1))
  fi
else
  # Mode A/B1: 建议填写
  if ! grep -qi "可追溯性矩阵\|traceability.*matrix\|traceability\|req.*编号\|req.*需求" "$DESIGN"; then
    echo "WARN: design.md 可能缺少可追溯性矩阵" >&2
  fi
fi

EMPTY_CELLS=$(grep -c "| — |$\|| |$" "$DESIGN" 2>/dev/null || true)
EMPTY_CELLS=${EMPTY_CELLS:-0}
if [ "$EMPTY_CELLS" -gt 0 ]; then
  echo "WARN: design.md 矩阵中有 $EMPTY_CELLS 个可能为空的单元格" >&2
fi

if [ "$ERRORS" -gt 0 ]; then
  exit 2
fi

exit 0
