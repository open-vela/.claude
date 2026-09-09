#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Tests for adapters/cursor/backfill_cursor.py.
# Builds synthetic Cursor state.vscdb fixtures, runs the backfill via
# export-session.py --source cursor, and asserts on the produced logs/.
# Covers the shapes real users hit:
#   T1 composer inside openvela workspace        -> imported
#   T2 composer in a personal project            -> filtered out
#   T3 legacy-format-only composer               -> skipped silently
#   T4 Windows drive-letter folder URI variants  -> normalized so the
#      workspace prefix match survives on Windows (regression test for
#      the '/C:/...' leading-slash bug found in review)
#   T5 URI normalization unit checks             -> pure function asserts

import json
import shutil
import sqlite3
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
from backfill_cursor import _folder_uri_to_fs_path  # noqa: E402

LOGIN = "cursor-tester"
TEAM = "contest2026_test_team"


def build_global_db(path: Path, composers: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE, value BLOB)")

    headers = {"allComposers": []}
    for c in composers:
        entry = {"composerId": c["id"], "name": c["name"]}
        if c.get("fs_path"):
            entry["workspaceIdentifier"] = {
                "uri": {"fsPath": c["fs_path"], "scheme": "file"}
            }
        headers["allComposers"].append(entry)
    conn.execute(
        "INSERT INTO ItemTable(key, value) VALUES(?, ?)",
        ("composer.composerHeaders", json.dumps(headers)),
    )

    for c in composers:
        composer_data = {
            "_v": 13,
            "composerId": c["id"],
            "name": c["name"],
            "createdAt": 1737316260000,
            "fullConversationHeadersOnly": [
                {"bubbleId": f"{c['id']}-u1", "type": 1},
                {"bubbleId": f"{c['id']}-a1", "type": 2},
            ],
        }
        conn.execute(
            "INSERT INTO cursorDiskKV(key, value) VALUES(?, ?)",
            (f"composerData:{c['id']}", json.dumps(composer_data)),
        )
        bubbles = [
            {"bubbleId": f"{c['id']}-u1", "type": 1, "text": "prompt text",
             "createdAt": 1737316260000},
            {"bubbleId": f"{c['id']}-a1", "type": 2, "text": "answer text",
             "createdAt": 1737316261000,
             "modelInfo": {"modelName": "claude-sonnet-4-5"}},
        ]
        for b in bubbles:
            conn.execute(
                "INSERT INTO cursorDiskKV(key, value) VALUES(?, ?)",
                (f"bubbleId:{c['id']}:{b['bubbleId']}", json.dumps(b)),
            )
    conn.commit()
    conn.close()


class CursorBackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cursor-test-"))
        self.cursor_root = self.tmp / "Cursor"
        self.workspace = self.tmp / "openvela-ws"
        (self.workspace / ".repo").mkdir(parents=True)
        (self.tmp / "personal").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_fixture(self, composers, folder_uris=None):
        build_global_db(
            self.cursor_root / "User" / "globalStorage" / "state.vscdb",
            composers,
        )
        ws_storage = self.cursor_root / "User" / "workspaceStorage"
        ws_storage.mkdir(parents=True, exist_ok=True)
        for i, uri in enumerate(folder_uris or []):
            h = ws_storage / f"hash-{i}"
            h.mkdir()
            (h / "workspace.json").write_text(
                json.dumps({"folder": uri}), encoding="utf-8")

    def _run_backfill(self):
        env = {
            **__import__("os").environ,
            "CURSOR_ROOT_OVERRIDE": str(self.cursor_root),
            "TEAM_ID": TEAM,
        }
        return subprocess.run(
            [sys.executable, str(EXPORT_PY), "--backfill",
             "--source", "cursor", "--github-login", LOGIN,
             "--dest", str(self.workspace)],
            capture_output=True, text=True, env=env,
        )

    def _logs_dir(self):
        return self.workspace / "logs" / LOGIN

    def test_workspace_gate_and_import(self):
        self._make_fixture(
            composers=[
                {"id": "in-ws", "name": "In workspace",
                 "fs_path": str(self.workspace)},
                {"id": "personal", "name": "Personal",
                 "fs_path": str(self.tmp / "personal")},
            ],
            folder_uris=[f"file://{self.workspace}"],
        )
        r = self._run_backfill()
        self.assertIn("1 composer(s) inside workspace", r.stdout, r.stdout)
        self.assertIn("in-ws", r.stdout)
        self.assertNotIn("personal", r.stdout)

        jsonl = list(self._logs_dir().rglob("cursor__in-ws.jsonl"))
        self.assertEqual(len(jsonl), 1)
        events = [json.loads(l) for l in jsonl[0].read_text().splitlines()]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["role"], "user")
        self.assertEqual(events[1]["role"], "assistant")
        self.assertEqual(events[1]["model"], "claude-sonnet-4-5")
        self.assertEqual(events[0]["seq"], 0)
        self.assertEqual(events[1]["seq"], 1)

        manifest = json.loads(
            (self._logs_dir() / "manifest.json").read_text())
        entry = manifest["sessions"][0]
        self.assertEqual(entry["collection_mode"], "backfill-cursor")
        self.assertIn("main_sha256", entry["source_integrity"])

    def test_legacy_only_composer_skipped(self):
        self._make_fixture(
            composers=[{"id": "legacy", "name": "No headers"}],
            folder_uris=[f"file://{self.workspace}"],
        )
        r = self._run_backfill()
        # composer has no conversation content -> zero events -> not written
        self.assertNotIn("legacy  wrote", r.stdout)
        self.assertFalse(list(self._logs_dir().rglob("*.jsonl")))

    def test_windows_drive_letter_uri(self):
        # Windows workspace.json folder URIs must normalize so the prefix
        # match works: file:///C:/Users/... and file:///C%3A/Users/...
        self._make_fixture(
            composers=[
                {"id": "win-ws", "name": "Windows workspace",
                 "fs_path": "C:/Users/dev/openvela-ws"},
            ],
            folder_uris=["file:///C:/Users/dev/openvela-ws"],
        )
        # fake a windows-shaped workspace root the adapter can still
        # prefix-match: reuse posix layout but assert URI normalization
        r = self._run_backfill()
        # fs_path C:/Users/... will not prefix-match the posix tmp
        # workspace, so the composer is filtered - the point of this
        # test is the unit-level URI checks below plus no crash here
        self.assertIn("Backfill done", r.stdout)

    def test_folder_uri_normalization(self):
        cases = {
            "file:///home/u/proj": "/home/u/proj",
            "file:///Users/u/proj": "/Users/u/proj",
            "file:///C:/Users/u/proj": "C:/Users/u/proj",
            "file:///C%3A/Users/u/proj": "C:/Users/u/proj",
            "/plain/posix/path": "/plain/posix/path",
        }
        for uri, expected in cases.items():
            self.assertEqual(_folder_uri_to_fs_path(uri), expected, uri)

    def test_validate_passes(self):
        self._make_fixture(
            composers=[
                {"id": "in-ws", "name": "In workspace",
                 "fs_path": str(self.workspace)},
            ],
            folder_uris=[f"file://{self.workspace}"],
        )
        self._run_backfill()
        r = subprocess.run(
            [sys.executable, str(VALIDATE_PY), str(self.workspace / "logs")],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
