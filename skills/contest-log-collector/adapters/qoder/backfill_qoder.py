#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# openvela AI Contest - Qoder backfill adapter.
#
# Qoder (Alibaba's agentic IDE) stores complete session transcripts as
# local JSONL files whose per-line shape is isomorphic to Claude Code's
# transcript ({type, sessionId, timestamp, cwd, message:{role, content:
# [text|thinking|tool_use|tool_result]}}) - the shared expand_claude_
# event() parser is reused directly. Storage locations differ across
# Qoder generations, so three roots are scanned:
#   1. ~/.qoder/projects/**/*.jsonl                        (CLI / SDK)
#   2. <user-data>/User/projects/**/transcript/*.jsonl     (IDE, older)
#      user-data: ~/.config/Qoder (Linux), ~/Library/Application
#      Support/Qoder (macOS), %APPDATA%\Qoder (Windows)
#   3. <user-data>/SharedClientCache/cli/projects/*.jsonl  (IDE, newer)
#
# Docs: docs.qoder.com/cli/sessions (CLI format is officially public);
# IDE paths per community readers (agentscope-ai/QwenPaw,
# alibaba/loongsuite-pilot). state.vscdb / local.db only index UI
# metadata (titles, models, tokens) and are intentionally not read -
# the JSONL transcripts carry the full conversation.
#
# Workspace gate: transcript lines carry `cwd`; a session is imported
# only when its cwd resolves under the openvela workspace root (dir
# with .repo/). Idempotent via manifest; SHA256 integrity recorded.
# isSidechain lines are skipped (sub-agent internals, not contestant
# dialogue).

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_ID = "qoder"
SCHEMA_VERSION = "1.0"


def qoder_transcript_candidates() -> list[Path]:
    override = os.environ.get("QODER_ROOT_OVERRIDE")
    if override:
        return [Path(override)]
    home = Path.home()
    system = sys.platform
    if system == "darwin":
        user_data = home / "Library" / "Application Support" / "Qoder"
    elif system == "win32":
        appdata = os.environ.get("APPDATA")
        user_data = Path(appdata) / "Qoder" if appdata else \
            home / "AppData" / "Roaming" / "Qoder"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        user_data = (Path(xdg) if xdg else home / ".config") / "Qoder"
    return [
        home / ".qoder" / "projects",
        user_data / "User" / "projects",
        user_data / "SharedClientCache" / "cli" / "projects",
    ]


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_workspace_root(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".repo").is_dir():
            return candidate
    return None


def parse_qoder_transcript(path: Path) -> dict[str, Any] | None:
    """Read one Qoder JSONL transcript. Returns {"session_id", "cwd",
    "lines": [raw events]} or None when the file has no usable
    session/cwd header line. Only the FIRST session encountered is
    used: Qoder writes one session per file (CLI) or per transcript
    dir (IDE); mixed files are not a known shape.
    """
    session_id = None
    cwd = None
    lines: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        sys.stderr.write(f"[qoder] cannot read {path}: {e}\n")
        return None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("isSidechain"):
            continue
        if session_id is None and rec.get("sessionId"):
            session_id = rec["sessionId"]
        if cwd is None and rec.get("cwd"):
            cwd = rec["cwd"]
        lines.append(rec)
    if session_id is None or cwd is None:
        return None
    return {"session_id": session_id, "cwd": cwd, "lines": lines}


def _write_manifest(dest: Path, github_login: str, team_id: str,
                    session_id: str, events: list[dict], rel_path: str,
                    integrity: dict) -> None:
    member_dir = dest / "logs" / github_login
    manifest_path = member_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    else:
        manifest = {}
    manifest.setdefault("schema_version", SCHEMA_VERSION)
    manifest["team_id"] = team_id
    manifest["github_login"] = github_login
    manifest.setdefault("generator", f"backfill-qoder@{TOOL_ID}")
    sessions = manifest.setdefault("sessions", [])
    entry = next(
        (s for s in sessions if s.get("session_id") == session_id), None)
    new_entry = {
        "session_id": session_id,
        "tool": TOOL_ID,
        "started_at": events[0]["ts"],
        "last_event_at": events[-1]["ts"],
        "event_count": len(events),
        "file_path": rel_path,
        "collection_mode": "backfill-sqlite",
        "health": "ok",
        "source_integrity": integrity,
    }
    if entry:
        entry.update(new_entry)
    else:
        sessions.append(new_entry)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8")


def _session_in_manifest(dest: Path, github_login: str,
                         session_id: str) -> bool:
    manifest_path = dest / "logs" / github_login / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(
        s.get("session_id") == session_id and s.get("tool") == TOOL_ID
        for s in manifest.get("sessions", []))


def backfill(dest: Path, team_id: str, github_login: str) -> int:
    workspace_root = _find_workspace_root(dest)
    if workspace_root is None:
        print("[qoder] destination is not inside an openvela workspace "
              "(no .repo/); skipping")
        return 0
    workspace_str = str(workspace_root.resolve())

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    try:
        from snapshot_core import expand_claude_event
    except ImportError as e:
        sys.stderr.write(f"[qoder] cannot import shared parser: {e}\n")
        return 0

    transcripts: dict[str, Path] = {}
    for root in qoder_transcript_candidates():
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            if path.name in ("state.json",):
                continue
            transcripts.setdefault(str(path.resolve()), path)

    imported = 0
    scanned = 0
    for _, path in sorted(transcripts.items()):
        parsed = parse_qoder_transcript(path)
        if parsed is None:
            continue
        scanned += 1
        sid = str(parsed["session_id"])
        if not str(Path(str(parsed["cwd"])).resolve()).startswith(
                workspace_str):
            continue
        if _session_in_manifest(dest, github_login, sid):
            continue

        events: list[dict] = []
        state: dict = {}
        for raw in parsed["lines"]:
            ts = raw.get("timestamp") or ""
            events.extend(expand_claude_event(
                raw, fallback_ts=ts or datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z"), codex_state=state))
        events = [e for e in events if e.get("ts")]
        if not events:
            continue

        integrity = {
            "main_sha256": _sha256_file(path),
            "main_size": path.stat().st_size if path.is_file() else 0,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        date_str = events[0]["ts"][:10]
        rel_path = (
            f"logs/{github_login}/{date_str}/{TOOL_ID}__{sid}.jsonl")
        jsonl_path = dest / rel_path
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        seq = 0
        with jsonl_path.open("w", encoding="utf-8") as f:
            for e in events:
                out = {
                    "schema_version": SCHEMA_VERSION,
                    "session_id": sid,
                    "team_id": team_id,
                    "github_login": github_login,
                    "tool": TOOL_ID,
                    "seq": seq,
                    "ts": e["ts"],
                    "role": e["role"],
                    "text": e.get("text") or e.get("thinking") or "",
                }
                if e.get("model"):
                    out["model"] = e["model"]
                if e.get("tool_name"):
                    out["tool_name"] = e["tool_name"]
                if e.get("tool_call_id"):
                    out["tool_call_id"] = e["tool_call_id"]
                if "input" in e and e.get("role") == "tool":
                    out["input"] = e.get("input")
                if "output" in e and e.get("role") == "tool":
                    out["output"] = e.get("output")
                if not out["text"] and not out.get("tool_name"):
                    continue
                f.write(json.dumps(out, ensure_ascii=False) + "\n")
                seq += 1
        _write_manifest(dest, github_login, team_id, sid, events[:seq],
                        rel_path, integrity)
        imported += 1
        print(f"  [qoder] {sid[:20]}  wrote {seq} event(s) -> {rel_path}")

    print(f"[qoder] scanned {scanned} transcript(s), imported {imported}")
    return imported
