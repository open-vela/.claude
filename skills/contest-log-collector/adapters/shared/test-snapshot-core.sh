#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Smoke test for adapters/shared/snapshot_core.py.
# Constructs a fake Claude Code transcript and stdin payload, invokes the core,
# then runs the format validator against the produced JSONL.

set -eu

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMPDIR_ROOT=$(mktemp -d -t contest-snapshot-test-XXXXXX)
trap 'rm -rf "$TMPDIR_ROOT"' EXIT

WORKSPACE="$TMPDIR_ROOT/demo"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# The collector's privacy gate only collects inside an openvela workspace
# (a directory containing .repo/). Mark the fixture workspace accordingly.
mkdir -p "$WORKSPACE/.repo"

git init -q -b main >/dev/null
git config user.name "test"
git config user.email "test@local"
echo "test" > README.md
git add . >/dev/null
git commit -q -m "init" >/dev/null

TRANSCRIPT="$TMPDIR_ROOT/transcript.jsonl"
SESSION_ID="ses_smoketest_$$"

cat > "$TRANSCRIPT" <<EOF
{"type":"user","sessionId":"$SESSION_ID","uuid":"u1","timestamp":"2026-06-09T00:00:00.000Z","message":{"role":"user","content":"Read README.md and tell me what is inside"}}
{"type":"assistant","sessionId":"$SESSION_ID","uuid":"u2","timestamp":"2026-06-09T00:00:01.000Z","message":{"role":"assistant","model":"claude-opus-4.5","usage":{"input_tokens":42,"output_tokens":18},"content":[{"type":"thinking","thinking":"User wants me to use the Read tool on README.md."},{"type":"text","text":"OK, let me use the Read tool to take a look."},{"type":"tool_use","id":"toolu_test_001","name":"Read","input":{"file_path":"README.md"}}]}}
{"type":"user","sessionId":"$SESSION_ID","uuid":"u3","timestamp":"2026-06-09T00:00:02.000Z","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_test_001","content":"test\n"}]}}
{"type":"assistant","sessionId":"$SESSION_ID","uuid":"u4","timestamp":"2026-06-09T00:00:03.000Z","message":{"role":"assistant","model":"claude-opus-4.5","usage":{"input_tokens":50,"output_tokens":12},"content":[{"type":"text","text":"README.md has only one line: 'test'. My API key sk-1234567890abcdefghij1234567890 is for testing."}]}}
{"type":"progress","sessionId":"$SESSION_ID","uuid":"u5","timestamp":"2026-06-09T00:00:03.500Z","message":{"role":"system","content":"progress event should be dropped"}}
EOF

STDIN_PAYLOAD=$(printf '{"session_id":"%s","cwd":"%s","transcript_path":"%s","hook_event_name":"Stop"}' \
  "$SESSION_ID" "$WORKSPACE" "$TRANSCRIPT")

echo "=== running snapshot_core.py with fake stdin ==="
echo "$STDIN_PAYLOAD" | TEAM_ID=team-099-test LOG_PUSH_INTERVAL=99999 \
  python3 "$REPO_ROOT/adapters/shared/snapshot_core.py" --tool claude-code

echo ""
echo "=== produced files ==="
find "$WORKSPACE/logs" -type f | sort

echo ""
echo "=== JSONL content summary ==="
JSONL=$(find "$WORKSPACE/logs" -name "*.jsonl" | head -1)
python3 - "$JSONL" <<'PYEOF'
import sys, json
path = sys.argv[1]
for line in open(path):
    e = json.loads(line)
    role = e.get('role')
    seq = e.get('seq')
    if role == 'tool':
        name = e.get('tool_name')
        cid = (e.get('tool_call_id') or '')[:20]
        is_err = e.get('is_error', False)
        has_in = e.get('input') is not None
        has_out = e.get('output') is not None
        print(f'  seq={seq} role=tool name={name:10} call_id={cid:20} input?{has_in} output?{has_out} err?{is_err}')
    else:
        text = e.get('text', '') or e.get('thinking', '')
        rd = e.get('redacted_count', 0)
        rd_str = f' REDACTED×{rd}' if rd else ''
        print(f'  seq={seq} role={role:9} len={len(text):4}c{rd_str}')
PYEOF

echo ""
echo "=== running validator ==="
python3 "$REPO_ROOT/tools/validate-log.py" "$WORKSPACE/logs/"

echo ""
echo "✅ snapshot_core test passed"
