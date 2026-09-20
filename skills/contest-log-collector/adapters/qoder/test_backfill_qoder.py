#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Tests for adapters/qoder/backfill_qoder.py.
# Fixtures replicate the Qoder transcript shape (docs.qoder.com/cli/
# sessions + community readers): Claude-Code-isomorphic JSONL lines
# with sessionId / cwd / message.content blocks.
# Covers:
#   T1 in-workspace transcript with text+tool_use+tool_result
#      -> user/assistant/tool events, tool fields, ALL OK
#   T2 transcript with cwd outside the workspace     -> filtered
#   T3 sidechain lines                               -> skipped
#   T4 idempotent second run                         -> 0 imported
#   T5 IDE SharedClientCache path layout             -> discovered

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent
SKILL_DIR = ADAPTER_DIR.parent.parent
EXPORT_PY = SKILL_DIR / "tools" / "export-session.py"
VALIDATE_PY = SKILL_DIR / "tools" / "validate-log.py"

LOGIN = "qoder-tester"


def qline(session_id: str, cwd: str, ts: str, rtype: str, role: str,
          content, model=None, sidechain=False) -> str:
    rec = {
        "type": rtype,
        "uuid": f"u-{ts}",
        "sessionId": session_id,
        "timestamp": ts,
        "cwd": cwd,
        "gitBranch": "main",
        "isSidechain": sidechain,
        "message": {"id": f"m-{ts}", "role": role, "content": content},
    }
    if model:
        rec["message"]["model"] = model
    return json.dumps(rec, ensure_ascii=False)


class QoderBackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qoder-test-"))
        self.qoder_root = self.tmp / "qoder-home"
        self.workspace = self.tmp / "openvela-ws"
        (self.workspace / ".repo").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_transcript(self, relpath: str, lines: list[str]):
        p = self.qoder_root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _run_backfill(self):
        import os
        env = {
            **os.environ,
            "QODER_ROOT_OVERRIDE": str(self.qoder_root),
            "TEAM_ID": "contest2026_test_team",
        }
        return subprocess.run(
            [sys.executable, str(EXPORT_PY), "--backfill",
             "--source", "qoder", "--github-login", LOGIN,
             "--dest", str(self.workspace)],
            capture_output=True, text=True, env=env,
        )

    def test_import_with_tool_calls(self):
        ws = str(self.workspace)
        self._write_transcript(
            "projects/my-proj/ses-1.jsonl",
            [
                qline("ses-1", ws, "2026-09-18T08:00:01.000Z",
                      "user", "user", "Port the STM32 board config"),
                qline("ses-1", ws, "2026-09-18T08:00:05.000Z",
                      "assistant", "assistant",
                      [{"type": "text", "text": "Reading the board file."},
                       {"type": "tool_use", "id": "t1",
                        "name": "read_file",
                        "input": {"path": "boards/stm32/config"}}],
                      model="qwen3-coder-plus"),
                qline("ses-1", ws, "2026-09-18T08:00:06.000Z",
                      "user", "user",
                      [{"type": "tool_result", "tool_use_id": "t1",
                        "content": "CONFIG_STM32=y"}]),
                qline("ses-1", ws, "2026-09-18T08:00:10.000Z",
                      "assistant", "assistant",
                      [{"type": "text", "text": "Done, config verified."}],
                      model="qwen3-coder-plus"),
            ])
        r = self._run_backfill()
        self.assertIn("1 session(s) imported", r.stdout, r.stdout)

        jsonl = list(
            (self.workspace / "logs" / LOGIN).rglob("qoder__ses-1.jsonl"))
        self.assertEqual(len(jsonl), 1)
        events = [json.loads(l) for l in jsonl[0].read_text().splitlines()]
        # tool_result lines become role=tool events (same as Claude Code)
        self.assertEqual(
            [e["role"] for e in events],
            ["user", "assistant", "tool", "tool", "assistant"])
        tool_call_ev = events[2]
        self.assertEqual(tool_call_ev["tool_name"], "read_file")
        self.assertEqual(tool_call_ev["tool_call_id"], "t1")
        self.assertEqual(tool_call_ev["input"],
                         {"path": "boards/stm32/config"})
        tool_result_ev = events[3]
        self.assertEqual(tool_result_ev["tool_call_id"], "t1")
        self.assertEqual(tool_result_ev["output"], "CONFIG_STM32=y")
        self.assertEqual(events[1]["model"], "qwen3-coder-plus")
        self.assertEqual(
            [e["seq"] for e in events], list(range(len(events))))

        v = subprocess.run(
            [sys.executable, str(VALIDATE_PY),
             str(self.workspace / "logs")],
            capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    def test_outside_workspace_filtered(self):
        self._write_transcript(
            "projects/other/ses-2.jsonl",
            [qline("ses-2", "/home/someone/personal",
                   "2026-09-18T08:00:01.000Z", "user", "user",
                   "personal chat")])
        r = self._run_backfill()
        self.assertIn("0 session(s) imported", r.stdout, r.stdout)
        self.assertFalse(
            list((self.workspace / "logs").rglob("*.jsonl")))

    def test_sidechain_skipped(self):
        ws = str(self.workspace)
        self._write_transcript(
            "projects/p/ses-3.jsonl",
            [
                qline("ses-3", ws, "2026-09-18T08:00:01.000Z",
                      "user", "user", "main thread prompt"),
                # sidechain (sub-agent) line must not produce events
                qline("ses-3", ws, "2026-09-18T08:00:02.000Z",
                      "assistant", "assistant",
                      [{"type": "text", "text": "subagent internal"}],
                      sidechain=True),
                qline("ses-3", ws, "2026-09-18T08:00:03.000Z",
                      "assistant", "assistant",
                      [{"type": "text", "text": "main thread answer"}]),
            ])
        r = self._run_backfill()
        self.assertIn("1 session(s) imported", r.stdout, r.stdout)
        jsonl = list(
            (self.workspace / "logs" / LOGIN).rglob("qoder__ses-3.jsonl"))
        events = [json.loads(l) for l in jsonl[0].read_text().splitlines()]
        texts = [e["text"] for e in events]
        self.assertIn("main thread prompt", texts)
        self.assertIn("main thread answer", texts)
        self.assertNotIn("subagent internal", texts)

    def test_idempotent(self):
        ws = str(self.workspace)
        self._write_transcript(
            "projects/p/dup.jsonl",
            [qline("dup-ses", ws, "2026-09-18T08:00:01.000Z",
                   "user", "user", "hello")])
        r1 = self._run_backfill()
        self.assertIn("1 session(s) imported", r1.stdout, r1.stdout)
        r2 = self._run_backfill()
        self.assertIn("0 session(s) imported", r2.stdout, r2.stdout)

    def test_ide_shared_client_cache_layout(self):
        # the IDE (newer versions) writes under SharedClientCache;
        # QODER_ROOT_OVERRIDE points at the root, layout inside mimics
        # the real tree
        ws = str(self.workspace)
        self._write_transcript(
            "SharedClientCache/cli/projects/proj-transcript/ses-4.jsonl",
            [qline("ses-4", ws, "2026-09-18T09:00:01.000Z",
                   "user", "user", "ide session prompt")])
        r = self._run_backfill()
        self.assertIn("1 session(s) imported", r.stdout, r.stdout)
        jsonl = list(
            (self.workspace / "logs" / LOGIN).rglob("qoder__ses-4.jsonl"))
        self.assertEqual(len(jsonl), 1)


if __name__ == "__main__":
    unittest.main()
