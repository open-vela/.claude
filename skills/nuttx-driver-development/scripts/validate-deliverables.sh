#!/bin/bash
# validate-deliverables.sh — 检查产出物完整性和函数可达性
# 契约：成功 exit 0 静默，失败 exit 2 + stderr
# 用法：validate-deliverables.sh <design.md路径> [项目根目录]
#
# 改进 v2:
#   - 文件存在性检查：只检查 design.md 中标记为 "New" 的文件
#   - 函数可达性检查：只扫描新增的驱动文件，不扫描整个目录

DESIGN="${1:-design.md}"
PROJECT_ROOT="${2:-.}"

if [ ! -f "$DESIGN" ]; then
  echo "ERROR: $DESIGN not found" >&2
  exit 2
fi

ERRORS=0

# 1. 检查 design.md 中标记为 "New" 的文件是否存在
#    匹配模式: | `path/to/file.c` | New | 或 | path/to/file.c | New |
while IFS= read -r filepath; do
  resolved=""
  if [ -f "$PROJECT_ROOT/$filepath" ]; then
    resolved="$PROJECT_ROOT/$filepath"
  elif [ -f "$filepath" ]; then
    resolved="$filepath"
  fi

  if [ -z "$resolved" ]; then
    echo "ERROR: design.md 标记为 New 的文件 $filepath 不存在" >&2
    ERRORS=$((ERRORS + 1))
  fi
done < <(grep -i "| *New *|" "$DESIGN" 2>/dev/null \
  | grep -oP '`[^`]+\.(c|h)`' \
  | tr -d '`' \
  | sort -u)

# 2. 函数可达性检查：只扫描新增的 .c 文件中的 _register/_initialize 函数
NEW_FILES=()
while IFS= read -r filepath; do
  if [ -f "$PROJECT_ROOT/$filepath" ]; then
    NEW_FILES+=("$PROJECT_ROOT/$filepath")
  elif [ -f "$filepath" ]; then
    NEW_FILES+=("$filepath")
  fi
done < <(grep -i "| *New *|" "$DESIGN" 2>/dev/null \
  | grep -oP '`[^`]+\.c`' \
  | tr -d '`' \
  | sort -u)

for src in "${NEW_FILES[@]}"; do
  for func in $(grep -ohP '\w+_register\b|\w+_initialize\b' "$src" 2>/dev/null | sort -u); do
    # 确认是函数定义（非调用）
    defn=$(grep -cP "^[^/]*\b$func\s*\(" "$src" 2>/dev/null || true)
    defn=${defn:-0}
    if [ "$defn" -eq 0 ]; then
      continue
    fi

    # 在 boards 目录和驱动所在目录搜索调用方（避免全项目 grep）
    DRIVER_PARENT="$(dirname "$(dirname "$src")")"
    callers=$(grep -rl "\b$func\b" \
      "$PROJECT_ROOT/nuttx/boards/" \
      "$DRIVER_PARENT" \
      --include="*.c" 2>/dev/null \
      | grep -v "$(basename "$src")" \
      | head -1)

    if [ -z "$callers" ]; then
      echo "WARN: $func (in $(basename "$src")) 没有调用方，可能缺少 board 层代码" >&2
    fi
  done
done

if [ "$ERRORS" -gt 0 ]; then
  exit 2
fi

exit 0
