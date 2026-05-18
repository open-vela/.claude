#!/bin/bash
# workflow-state.sh — driver-workflow 状态管理
# 契约：成功 exit 0，失败 exit 2 + stderr
#
# 用法：
#   workflow-state.sh init                        初始化 .driver-workflow/
#   workflow-state.sh complete <step> [validated]   标记步骤完成（validated=true 表示验证脚本已执行）
#   workflow-state.sh check <step>                  检查步骤是否完成 + 验证是否执行
#   workflow-state.sh gate <step>                   门控：上一步必须完成且验证通过才放行
#   workflow-state.sh set-context <step> <json>     保存步骤的关键决策变量（JSON 字符串）
#   workflow-state.sh get-context <step>            读取步骤的关键决策变量（输出 JSON）
#   workflow-state.sh status                        输出当前进度

STATE_DIR=".driver-workflow"
STATE_FILE="$STATE_DIR/progress.json"

cmd="$1"
step="$2"
validated="${3:-false}"

init_state() {
  mkdir -p "$STATE_DIR"
  cat > "$STATE_FILE" << 'INIT'
{
  "workflow": "driver-workflow",
  "started": "",
  "mode": "",
  "steps": {
    "A": {"status": "pending", "validated": false},
    "B": {"status": "pending", "validated": false},
    "C": {"status": "pending", "validated": false},
    "D": {"status": "pending", "validated": false},
    "D2": {"status": "pending", "validated": false},
    "D35": {"status": "pending", "validated": false},
    "E": {"status": "pending", "validated": false}
  }
}
INIT
  INIT_TIME=$(date -Iseconds)
  python3 -c "
import json, sys
with open('$STATE_FILE') as f: d = json.load(f)
d['started'] = '$INIT_TIME'
with open('$STATE_FILE', 'w') as f: json.dump(d, f, indent=2)
"
}

complete_step() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "ERROR: $STATE_FILE not found. Run 'workflow-state.sh init' first." >&2
    exit 2
  fi
  python3 -c "
import json, sys
with open('$STATE_FILE') as f: d = json.load(f)
if '$step' not in d['steps']:
    print(f'ERROR: Unknown step $step', file=sys.stderr)
    sys.exit(2)
d['steps']['$step']['status'] = 'completed'
d['steps']['$step']['validated'] = $( [ "$validated" = "true" ] && echo "True" || echo "False" )
with open('$STATE_FILE', 'w') as f: json.dump(d, f, indent=2)
"
}

check_step() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "ERROR: $STATE_FILE not found" >&2
    exit 2
  fi
  python3 -c "
import json, sys
with open('$STATE_FILE') as f: d = json.load(f)
s = d['steps'].get('$step', {})
status = s.get('status', 'unknown')
validated = s.get('validated', False)
if status != 'completed':
    print(f'Step $step: {status}', file=sys.stderr)
    sys.exit(2)
if not validated:
    print(f'WARN: Step $step completed but validation not executed', file=sys.stderr)
"
}

gate_step() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "ERROR: $STATE_FILE not found" >&2
    exit 2
  fi

  local prev_steps=""
  case "$step" in
    B)   prev_steps="A" ;;
    C)   prev_steps="B" ;;
    D)   prev_steps="C" ;;
    D2)  prev_steps="C" ;;
    D35) prev_steps="D2" ;;
    E)   prev_steps="D" ;;
  esac

  if [ -z "$prev_steps" ]; then
    exit 0
  fi

  for ps in $prev_steps; do
    python3 -c "
import json, sys
with open('$STATE_FILE') as f: d = json.load(f)
s = d['steps'].get('$ps', {})
if s.get('status') != 'completed':
    print(f'ERROR: Step $ps not completed. Cannot proceed to $step.', file=sys.stderr)
    sys.exit(2)
if not s.get('validated', False):
    print(f'WARN: Step $ps completed but validation was skipped. Recommend running validation before proceeding.', file=sys.stderr)
"
    rc=$?
    if [ $rc -eq 2 ]; then
      exit 2
    fi
  done
}

show_status() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "No active workflow" >&2
    exit 0
  fi
  python3 -c "
import json
with open('$STATE_FILE') as f: d = json.load(f)
print(f\"Workflow started: {d.get('started', 'unknown')}\")
print(f\"Mode: {d.get('mode', 'unknown')}\")
for step, info in d['steps'].items():
    v = '✓' if info.get('validated') else '✗'
    ctx = ' +ctx' if info.get('context') else ''
    print(f\"  {step}: {info['status']} (validated: {v}{ctx})\")
"
}

set_context() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "ERROR: $STATE_FILE not found. Run 'workflow-state.sh init' first." >&2
    exit 2
  fi
  local ctx_json="$3"
  if [ -z "$ctx_json" ]; then
    echo "ERROR: Missing JSON argument. Usage: set-context <step> '<json>'" >&2
    exit 2
  fi
  python3 -c "
import json, sys
with open('$STATE_FILE') as f: d = json.load(f)
if '$step' not in d['steps']:
    print(f'ERROR: Unknown step $step', file=sys.stderr)
    sys.exit(2)
try:
    ctx = json.loads('$ctx_json')
except json.JSONDecodeError as e:
    print(f'ERROR: Invalid JSON: {e}', file=sys.stderr)
    sys.exit(2)
existing = d['steps']['$step'].get('context', {})
existing.update(ctx)
d['steps']['$step']['context'] = existing
with open('$STATE_FILE', 'w') as f: json.dump(d, f, indent=2)
"
}

get_context() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "ERROR: $STATE_FILE not found" >&2
    exit 2
  fi
  python3 -c "
import json, sys
with open('$STATE_FILE') as f: d = json.load(f)
if '$step' not in d['steps']:
    print(f'ERROR: Unknown step $step', file=sys.stderr)
    sys.exit(2)
ctx = d['steps']['$step'].get('context', {})
print(json.dumps(ctx, indent=2))
"
}

case "$cmd" in
  init)        init_state ;;
  complete)    complete_step ;;
  check)       check_step ;;
  gate)        gate_step ;;
  set-context) set_context "$@" ;;
  get-context) get_context ;;
  status)      show_status ;;
  *)
    echo "用法: workflow-state.sh {init|complete|check|gate|set-context|get-context|status} [step] [args]" >&2
    exit 1
    ;;
esac
