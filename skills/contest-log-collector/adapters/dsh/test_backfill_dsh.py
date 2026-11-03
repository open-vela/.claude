#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Tests for adapters/dsh/backfill_dsh.py.
# Fixtures replicate the real session format captured from dsh 0.2.0-rc.2
# on 2026-11-03 (session header + user/message / assistant/message /
# tool/call / tool/result / harness-event shapes). Covers:
#   T1 full session -> user/assistant/tool events, thinking, tokens,
#      model, structured tool input, files_touched, manifest title
#   T2 harness-injected user/message kinds -> excluded
#   T3 session outside the workspace            -> filtered
#   T4 corrupt header / torn tail               -> skipped or truncated
#   T5 already-imported session                 -> idempotent
#   T5b open session whose log grew             -> re-exported
#   T6 subagent session                         -> imported with parent
#   T7 image-only user message                  -> text placeholder
#   T8 zstd artifact                            -> decoded (skips without zstd)
#   T9 end-to-end validate-log.py               -> ALL OK
#
# Most tests call backfill() directly with a patched dsh_home; T9 goes
# through tools/export-session.py exactly as a contestant would.

import json
import os
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

sys.path.insert(0, str(ADAPTER_DIR))
import backfill_dsh  # noqa: E402

LOGIN = "dsh-tester"
TEAM = "contest2026_test_team"
T0 = 1793000000000  # 2026-10-something, epoch milliseconds


def header(session_id, cwd, *, version=4, parent=None, origin=None,
           depth=0, created=T0):
    rec = {
        "type": "session",
        "version": version,
        "id": session_id,
        "createdAt": created,
        "cwd": cwd,
        "isSeeded": False,
        "delegationDepth": depth,
        "agentPreset": "standard",
    }
    if parent:
        rec["parentSession"] = parent
    if origin:
        rec["origin"] = origin
    return rec


def event(seq, etype, **data):
    return {"type": etype, "seq": seq, "time": T0 + seq * 1000,
            "data": data}


def user_message(seq, text, kind="user"):
    return event(seq, "user/message",
                 content=[{"type": "text", "text": text}],
                 source={"kind": kind}, role="user", id=f"u{seq}")


def assistant_message(seq, text=None, thinking=None,
                      model="deepseek-flash", usage=None,
                      tool_calls=(), interrupted=False):
    content = []
    if thinking is not None:
        content.append({"type": "reasoning", "text": thinking})
    if text is not None:
        content.append({"type": "text", "text": text})
    for call_id, name, arguments in tool_calls:
        content.append({"type": "tool-call", "id": call_id, "name": name,
                        "arguments": json.dumps(arguments)})
    data = {
        "turn": 1,
        "step": seq,
        "message": {
            "role": "assistant",
            "content": content,
            "source": {"kind": "model", "provider": "deepseek-official",
                       "model": model},
            "id": f"a{seq}",
        },
        "stream": [{"type": "text-chunks", "index": 1}],
    }
    if interrupted:
        data["interrupted"] = True
    if usage is not None:
        data["usage"] = usage
    return event(seq, "assistant/message", **data)


def tool_call(seq, call_id, name, arguments):
    return event(seq, "tool/call", turn=1, step=seq, callId=call_id,
                 name=name, arguments=json.dumps(arguments))


def tool_result(seq, call_id, text, *, is_error=False, meta=None,
                error=None):
    data = {
        "turn": 1,
        "step": seq,
        "message": {
            "role": "tool",
            "source": {"kind": "tool", "callId": call_id},
            "toolCallId": call_id,
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
            "id": f"r{seq}",
        },
    }
    if meta is not None:
        data["meta"] = meta
    if error is not None:
        data["error"] = error
    return event(seq, "tool/result", **data)


def default_records(session_id, cwd):
    return [
        header(session_id, cwd),
        event(0, "permission/preset", preset="workspace-write"),
        event(1, "turn/start", turn=1),
        event(2, "system/message", turn=1, step=1,
              message={"role": "system",
                       "content": [{"type": "text",
                                    "text": "You are an AI agent."}]}),
        user_message(3, "Fix the build error"),
        user_message(4, "<system-reminder>workspace instructions",
                     kind="agent-instructions"),
        user_message(5, "Current runtime context: workspace-write",
                     kind="runtime-context"),
        event(6, "request/header", reason="step", header={"tools": []}),
        event(7, "step/start", turn=1, step=1),
        assistant_message(8, text="The build failed because of a header.",
                          usage={"inputTokens": 10, "outputTokens": 4}),
        tool_call(9, "call_1", "read", {"file_path": "include/nuttx/fs.h"}),
        tool_result(10, "call_1", "int open(const char *path);",
                    meta={"path": "include/nuttx/fs.h", "offset": 1,
                          "totalLines": 1, "lang": "c"}),
        event(11, "step/end", turn=1, step=1),
        event(12, "turn/end", turn=1, reason={"kind": "completed"}),
        event(13, "session/title", title="Fix the build error",
              messageSeqs=[3], source="llm"),
    ]


class DshBackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh-test-"))
        self.home = self.tmp / "dsh-home"
        self.workspace = self.tmp / "openvela-ws"
        (self.workspace / ".repo").mkdir(parents=True)
        self._real_home = backfill_dsh.dsh_home
        backfill_dsh.dsh_home = lambda: self.home

    def tearDown(self):
        backfill_dsh.dsh_home = self._real_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------
    def write_session(self, session_id, records, *, project="proj",
                      compress=False, suffix=None):
        name = f"session.v4.jsonl{suffix or ''}"
        path = self.home / "sessions" / project / session_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
        if compress:
            plain = path.with_suffix("")
            plain.write_text(text + "\n", encoding="utf-8")
            subprocess.run(["zstd", "-q", "-f", str(plain), "-o",
                            str(path)], check=True)
            plain.unlink()
        else:
            path.write_text(text + "\n", encoding="utf-8")
        return path

    def run_backfill(self):
        return backfill_dsh.backfill(self.workspace, TEAM, LOGIN)

    def read_events(self, session_id):
        found = list((self.workspace / "logs" / LOGIN).rglob(
            f"dsh__{session_id}.jsonl"))
        self.assertEqual(len(found), 1)
        return [json.loads(line)
                for line in found[0].read_text(encoding="utf-8").splitlines()]

    def manifest(self):
        path = self.workspace / "logs" / LOGIN / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # -- tests -----------------------------------------------------
    def test_mapping_and_harness_events_dropped(self):
        self.write_session("s1", default_records("s1", str(self.workspace)))
        self.assertEqual(self.run_backfill(), 1)

        events = self.read_events("s1")
        self.assertEqual([e["seq"] for e in events], [0, 1, 2, 3])
        self.assertEqual([e["role"] for e in events],
                         ["user", "assistant", "tool", "tool"])

        user = events[0]
        self.assertEqual(user["text"], "Fix the build error")
        self.assertNotIn("system-reminder", json.dumps(events))
        self.assertNotIn("runtime context", json.dumps(events))

        assistant = events[1]
        self.assertEqual(assistant["model"], "deepseek-flash")
        self.assertEqual(assistant["tokens_in"], 10)
        self.assertEqual(assistant["tokens_out"], 4)
        self.assertNotIn("stream", assistant)

        call, result = events[2], events[3]
        self.assertEqual(call["tool_name"], "read")
        self.assertEqual(call["input"], {"file_path": "include/nuttx/fs.h"})
        self.assertIsNone(call["output"])
        self.assertEqual(result["tool_name"], "<result>")
        self.assertEqual(result["tool_call_id"], "call_1")
        self.assertIn("int open", result["output"])
        self.assertEqual(result["files_touched"], ["include/nuttx/fs.h"])
        self.assertEqual(result["metadata"]["totalLines"], 1)
        self.assertNotIn("lines", result["metadata"])

        entry = self.manifest()["sessions"][0]
        self.assertEqual(entry["tool"], "dsh")
        self.assertEqual(entry["title"], "Fix the build error")
        self.assertEqual(entry["collection_mode"], backfill_dsh.COLLECTION_MODE)
        self.assertEqual(entry["tokens_total"], 14)
        self.assertEqual(entry["event_count"], 4)
        self.assertEqual(entry["source_integrity"]["format_version"], 4)

    def test_all_injected_user_kinds_excluded(self):
        kinds = ["agent-instructions", "runtime-context", "tool-jobs",
                 "compact-checkpoint", "agent-message", "subagent-settled",
                 "repeat-tool-reminder"]
        records = [header("s2", str(self.workspace)), user_message(0, "hi")]
        for i, kind in enumerate(kinds, start=1):
            records.append(user_message(i, f"injected {kind}", kind=kind))
        self.write_session("s2", records)
        self.run_backfill()
        events = self.read_events("s2")
        self.assertEqual([e["text"] for e in events], ["hi"])

    def test_outside_workspace_filtered(self):
        self.write_session("s3", default_records("s3", "/home/someone/other"))
        self.assertEqual(self.run_backfill(), 0)
        self.assertFalse(list((self.workspace / "logs").rglob("*.jsonl")))

    def test_corrupt_header_skipped_and_torn_tail_kept(self):
        # no header at all -> skipped
        self.write_session("bad", [user_message(0, "no header")])
        # torn tail (writer died mid-line) -> earlier events survive
        path = self.write_session(
            "s4", [header("s4", str(self.workspace)),
                   user_message(0, "kept")])
        with path.open("a", encoding="utf-8") as f:
            f.write('{"type":"assistant/message","seq":1,"time":17930')
        self.assertEqual(self.run_backfill(), 1)
        self.assertEqual(len(list((self.workspace / "logs").rglob(
            "dsh__bad.jsonl"))), 0)
        self.assertEqual([e["text"] for e in self.read_events("s4")],
                         ["kept"])

    def test_idempotent(self):
        self.write_session("s5", default_records("s5", str(self.workspace)))
        self.assertEqual(self.run_backfill(), 1)
        self.assertEqual(self.run_backfill(), 0)
        self.assertEqual(len(self.manifest()["sessions"]), 1)

    def test_open_session_reexported_when_source_grows(self):
        """A session exported mid-flight must pick up its later turns."""
        records = default_records("s5b", str(self.workspace))
        self.write_session("s5b", records)
        self.run_backfill()
        self.assertEqual(len(self.read_events("s5b")), 4)

        records.append(assistant_message(len(records), text="Done."))
        self.write_session("s5b", records)
        self.assertEqual(self.run_backfill(), 1)
        events = self.read_events("s5b")
        self.assertEqual(events[-1]["text"], "Done.")
        self.assertEqual([e["seq"] for e in events],
                         list(range(len(events))))
        entry = self.manifest()["sessions"][0]
        self.assertEqual(entry["event_count"], len(events))
        self.assertEqual(entry["last_event_at"], events[-1]["ts"])
        self.assertEqual(self.run_backfill(), 0)

    def test_subagent_session_imported_with_parent(self):
        parent = default_records("s6", str(self.workspace))
        self.write_session("s6", parent)
        child = [
            header("s7", str(self.workspace), parent="s6",
                   origin="subagent", depth=1),
            user_message(0, "Audit this file"),
            assistant_message(1, text="Audited."),
        ]
        self.write_session("s7", child)
        self.assertEqual(self.run_backfill(), 2)
        self.assertEqual(len(self.read_events("s7")), 2)
        entries = {e["session_id"]: e for e in self.manifest()["sessions"]}
        self.assertEqual(entries["s7"]["parent_session_id"], "s6")
        self.assertEqual(entries["s7"]["origin"], "subagent")
        self.assertEqual(entries["s7"]["delegation_depth"], 1)
        self.assertNotIn("parent_session_id", entries["s6"])

    def test_image_only_user_message_becomes_placeholder(self):
        records = [
            header("s8", str(self.workspace)),
            event(0, "user/message",
                  content=[{"type": "image", "attachmentId": "a1"},
                           {"type": "image", "attachmentId": "a2"}],
                  source={"kind": "user"}, role="user", id="u0"),
            assistant_message(1, text="I see the screenshot."),
        ]
        self.write_session("s8", records)
        self.run_backfill()
        events = self.read_events("s8")
        self.assertEqual(events[0]["role"], "user")
        self.assertIn("2 image", events[0]["text"])
        self.assertEqual(events[0]["metadata"]["images_dropped"], 2)

    @unittest.skipUnless(shutil.which("zstd"), "zstd CLI not installed")
    def test_zstd_artifact_decoded(self):
        records = default_records("s9", str(self.workspace))
        self.write_session("s9", records, compress=True,
                           suffix=".zstd")
        self.assertEqual(self.run_backfill(), 1)
        self.assertEqual(len(self.read_events("s9")), 4)

    def test_end_to_end_validate_log(self):
        """Runs the CLI a contestant runs, then the judge-side validator."""
        home = self.tmp / "user-home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "contest-collector.env").write_text(
            f"TEAM_ID={TEAM}\nGITHUB_LOGIN={LOGIN}\n", encoding="utf-8")
        self.write_session("s10", default_records("s10", str(self.workspace)))

        env = {
            **os.environ,
            "HOME": str(home),
            "DSH_HOME_BACKFILL_OVERRIDE": str(self.home),
        }
        exported = subprocess.run(
            [sys.executable, str(EXPORT_PY), "--backfill",
             "--source", "dsh", "--dest", str(self.workspace)],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(exported.returncode, 0,
                         exported.stdout + exported.stderr)
        self.assertIn("1 session(s) imported", exported.stdout)

        validated = subprocess.run(
            [sys.executable, str(VALIDATE_PY),
             str(self.workspace / "logs")],
            capture_output=True, text=True,
        )
        self.assertEqual(validated.returncode, 0,
                         validated.stdout + validated.stderr)
        self.assertIn("ALL OK", validated.stdout)


if __name__ == "__main__":
    unittest.main()
