#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# openvela AI Contest - VS Code Copilot Chat backfill adapter.
#
# Reads Copilot Chat sessions from the VS Code user-data dir:
#   <user-data>/User/workspaceStorage/<hash>/chatSessions/<uuid>.json   (v3 snapshot)
#   <user-data>/User/workspaceStorage/<hash>/chatSessions/<uuid>.jsonl  (mutation log)
#   <user-data>/User/globalStorage/emptyWindowChatSessions/...          (no-workspace chats)
# The workspace project folder comes from the sibling workspace.json.
#
# Two formats must both be handled (VS Code chatSessionStore.ts /
# objectMutationLog.ts):
#   .json  - a full session object (version 3): requests[] with
#            message.text, response[] parts, timestamp, modelId
#   .jsonl - an append-only object mutation log; each line is
#            {kind, k, v} where kind 0=initial object, 1=set property,
#            2=array splice, 3=delete. The log must be REPLAYED to
#            reconstruct the session; treating lines as messages
#            would produce garbage.
#
# Verified against real local data on 2026-09-17 (VS Code + Copilot
# Chat, format v3: 40 sessions across 23 workspaces) plus VS Code
# source study. The format is a private VS Code implementation with
# no stability promise: unknown versions and unknown response part
# kinds are skipped defensively (stderr warning, never fatal).
#
# Response part kinds observed in real data and their handling:
#   plain text part (has "value")          -> assistant text event
#   toolInvocationSerialized               -> tool event (name via
#                                             invocationMessage)
#   prepareToolInvocation / inlineReference / textEditGroup /
#   codeblockUri / undoStop / progressTaskSerialized /
#   confirmation / warning / progressMessage / elicitation /
#   command / unknown                      -> skipped
# Image variables (variableData) are skipped: the contest schema has
# no image slot.

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_ID = "copilot"
SCHEMA_VERSION = "1.0"
SUPPORTED_VERSIONS = {2, 3}

DB_REL_PATH = Path("User") / "globalStorage" / "github.copilot-chat" / \
    "session-store.db"


def vscode_user_data_candidates() -> list[Path]:
    override = os.environ.get("VSCODE_USER_DATA_OVERRIDE")
    if override:
        return [Path(override)]
    system = sys.platform
    home = Path.home()
    if system == "darwin":
        return [
            home / "Library" / "Application Support" / "Code",
            home / ".vscode-server" / "data",
        ]
    if system == "win32":
        appdata = os.environ.get("APPDATA")
        cands = []
        if appdata:
            cands.append(Path(appdata) / "Code")
        cands.append(home / ".vscode-remote" / "data")
        return cands
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return [
        (Path(xdg) if xdg else home / ".config") / "Code",
        home / ".config" / "Code",
        home / ".vscode-server" / "data",
    ]


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _folder_uri_to_path(folder: str) -> str | None:
    if not isinstance(folder, str) or not folder:
        return None
    if folder.startswith("file://"):
        raw = folder[len("file://"):]
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        from urllib.parse import unquote
        return unquote(raw)
    return folder


def workspace_folder_of(ws_dir: Path) -> str | None:
    ws_json = ws_dir / "workspace.json"
    if not ws_json.is_file():
        return None
    try:
        data = json.loads(ws_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _folder_uri_to_path(data.get("folder"))


def replay_mutation_log(lines: list[str]) -> dict[str, Any] | None:
    """Replay a VS Code objectMutationLog into the session object.

    Line semantics (objectMutationLog.ts):
      kind 0: {v: <full object>}          - initial state
      kind 1: {k: [path], v: value}       - set/replace property
      kind 2: {k: [path], v: [items]}     - array splice (append)
      kind 3: {k: [path]}                 - delete property
    Path segments may be string keys (object) or integer indices
    (array element), e.g. ["requests", 0, "response"] - the form VS
    Code writes for incremental updates into nested requests.
    Returns None when the log has no initial object.
    """
    root: Any = None

    def _navigate(node: Any, path: list) -> Any:
        for seg in path:
            if isinstance(node, dict) and isinstance(seg, str):
                node = node.get(seg)
            elif isinstance(node, list) and isinstance(seg, int):
                node = node[seg] if -len(node) <= seg < len(node) else None
            else:
                return None
        return node

    def _navigate_to_parent(node: Any, path: list) -> Any:
        return _navigate(node, path[:-1])

    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("kind")
        path = rec.get("k") or []
        value = rec.get("v")
        if kind == 0:
            root = value
        elif kind == 3:
            continue
        elif not isinstance(root, (dict, list)) or not path:
            continue
        elif kind in (1, 2):
            parent = _navigate_to_parent(root, path)
            leaf = path[-1]
            if kind == 1:
                if isinstance(parent, dict):
                    parent[leaf] = value
                elif isinstance(parent, list) and isinstance(leaf, int) \
                        and -len(parent) <= leaf < len(parent):
                    parent[leaf] = value
            else:
                if isinstance(parent, dict):
                    arr = parent.get(leaf)
                    if isinstance(arr, list) and isinstance(value, list):
                        arr.extend(value)
                    elif isinstance(value, list):
                        parent[leaf] = value
                elif isinstance(parent, list) and isinstance(leaf, int) \
                        and -len(parent) <= leaf < len(parent) \
                        and isinstance(parent[leaf], list) \
                        and isinstance(value, list):
                    parent[leaf].extend(value)
    return root if isinstance(root, dict) else None


def load_session(path: Path) -> dict[str, Any] | None:
    """Load a .json snapshot or .jsonl mutation log into a session dict."""
    try:
        if path.suffix == ".jsonl":
            text = path.read_text(encoding="utf-8", errors="replace")
            return replay_mutation_log(text.splitlines())
        return json.loads(path.read_text(encoding="utf-8",
                                         errors="replace"))
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"[copilot] cannot read {path}: {e}\n")
        return None


def _ms_to_iso(ms: Any) -> str:
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{dt.microsecond // 1000:03d}Z"
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")


def _response_text(parts: Any) -> str:
    """Concatenate assistant text parts; tool calls become tool events
    handled separately by the caller, so only 'value'-bearing parts
    are collected here."""
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, dict) and "value" in p and "kind" not in p:
            v = p.get("value")
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    return "\n".join(out)


def _response_tool_parts(parts: Any) -> list[dict[str, Any]]:
    if not isinstance(parts, list):
        return []
    out = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("kind") != "toolInvocationSerialized":
            continue
        inv = p.get("invocationMessage")
        inv_text = inv if isinstance(inv, str) else \
            (inv.get("value") if isinstance(inv, dict) else None)
        if not inv_text:
            continue
        entry = {
            "tool_name": "copilot-tool",
            "invocation": str(inv_text),
            "tool_call_id": str(
                p.get("toolCallId") or p.get("requestId") or
                f"copilot-call-{len(out)}"),
        }
        tsd = p.get("toolSpecificData")
        if isinstance(tsd, dict):
            entry["tool_name"] = str(tsd.get("kind") or
                                     p.get("toolId") or "copilot-tool")
        elif p.get("toolId"):
            entry["tool_name"] = str(p["toolId"])
        out.append(entry)
    return out


def session_to_events(session: dict[str, Any], session_id: str,
                      team_id: str, github_login: str) -> list[dict]:
    version = session.get("version")
    if version not in SUPPORTED_VERSIONS:
        sys.stderr.write(
            f"[copilot] session {session_id[:16]}: unsupported format "
            f"version {version!r}, skipping\n")
        return []

    events: list[dict] = []
    seq = 0
    requests = session.get("requests") or []
    if not isinstance(requests, list):
        return []

    for req in requests:
        if not isinstance(req, dict):
            continue
        msg = req.get("message") or {}
        user_text = msg.get("text") if isinstance(msg, dict) else None
        ts = _ms_to_iso(req.get("timestamp"))
        model = req.get("modelId")

        if isinstance(user_text, str) and user_text.strip():
            events.append({
                "ts": ts, "role": "user", "text": user_text.strip(),
            })
            seq += 1

        response = req.get("response") or []
        tool_parts = _response_tool_parts(response)
        for tp in tool_parts:
            events.append({
                "ts": ts, "role": "tool", "text": tp["invocation"],
                "tool_name": tp["tool_name"],
                "tool_call_id": tp["tool_call_id"],
            })
            seq += 1

        assistant_text = _response_text(response)
        if assistant_text:
            ev = {"ts": ts, "role": "assistant", "text": assistant_text}
            if isinstance(model, str) and model:
                ev["model"] = model
            events.append(ev)
            seq += 1

    return events


def _find_workspace_root(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".repo").is_dir():
            return candidate
    return None


def _write_manifest(dest: Path, github_login: str, team_id: str,
                    session_id: str, events: list[dict], rel_path: str,
                    integrity: dict, source: str) -> None:
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
    manifest.setdefault("generator", f"backfill-copilot@{TOOL_ID}")
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
        "data_source": source,
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


def _export_session(dest: Path, team_id: str, github_login: str,
                    session_id: str, events: list[dict],
                    integrity: dict, source: str) -> bool:
    if not events:
        return False
    date_str = events[0]["ts"][:10]
    rel_path = (
        f"logs/{github_login}/{date_str}/"
        f"{TOOL_ID}__{session_id}.jsonl")
    jsonl_path = dest / rel_path
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    seq = 0
    with jsonl_path.open("w", encoding="utf-8") as f:
        for e in events:
            out = {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "team_id": team_id,
                "github_login": github_login,
                "tool": TOOL_ID,
                "seq": seq,
                "ts": e["ts"],
                "role": e["role"],
                "text": e["text"],
            }
            if e.get("model"):
                out["model"] = e["model"]
            if e.get("tool_name"):
                out["tool_name"] = e["tool_name"]
            if e.get("tool_call_id"):
                out["tool_call_id"] = e["tool_call_id"]
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
            seq += 1
    _write_manifest(dest, github_login, team_id, session_id,
                    events, rel_path, integrity, source)
    print(f"  [copilot] {str(session_id)[:20]}  wrote "
          f"{len(events)} event(s) -> {rel_path}  (source: {source})")
    return True


def backfill_session_store_db(dest: Path, workspace_str: str,
                              team_id: str, github_login: str) -> int:
    """Primary source per contest feedback (PR #53, yunline): the Copilot
    extension's own SQLite store. The db carries a `cwd` per session
    (no workspace.json dependency, works in remote-server setups) and
    `turn_index` keeps turns naturally ordered. Tables observed in the
    wild: sessions (id, cwd, repository, branch, agent_name, created_at
    ...), turns (session ref, turn_index, user prompt, assistant_response),
    session_files (files/tools touched). Schema is extension-private:
    every query is guarded and unknown shapes are skipped, never fatal.
    """
    imported = 0
    for user_data in vscode_user_data_candidates():
        db_path = user_data / DB_REL_PATH
        if not db_path.is_file():
            continue
        try:
            conn = sqlite3.connect(
                f"file:{db_path}?mode=ro&immutable=0", uri=True)
        except sqlite3.Error as e:
            sys.stderr.write(f"[copilot] cannot open {db_path}: {e}\n")
            continue
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "sessions" not in tables or "turns" not in tables:
                sys.stderr.write(
                    f"[copilot] {db_path}: expected sessions/turns "
                    f"tables not found; skipping\n")
                continue
            session_rows = conn.execute(
                "SELECT * FROM sessions").fetchall()
        except sqlite3.Error as e:
            sys.stderr.write(f"[copilot] {db_path} read error: {e}\n")
            conn.close()
            continue

        integrity = {
            "main_sha256": _sha256_file(db_path),
            "main_size": db_path.stat().st_size,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        count_in_ws = 0
        for row in session_rows:
            s = dict(row)
            sid = s.get("id") or s.get("session_id")
            cwd = s.get("cwd") or s.get("working_directory")
            if not sid or not cwd:
                continue
            if not str(Path(str(cwd)).resolve()).startswith(workspace_str):
                continue
            if _session_in_manifest(dest, github_login, str(sid)):
                continue
            count_in_ws += 1
            turns = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? "
                "ORDER BY turn_index", (sid,)).fetchall()
            events: list[dict] = []
            for t in turns:
                td = dict(t)
                idx = td.get("turn_index", 0)
                ts = _ms_to_iso(td.get("created_at") or
                                td.get("timestamp")) if any(
                    k in td for k in ("created_at", "timestamp")) else \
                    _ms_to_iso(s.get("created_at"))
                prompt = td.get("prompt") or td.get("user_prompt") or \
                    td.get("user_message")
                if isinstance(prompt, str) and prompt.strip():
                    events.append({"ts": ts, "role": "user",
                                   "text": prompt.strip()})
                resp = td.get("assistant_response") or \
                    td.get("response")
                if isinstance(resp, str) and resp.strip():
                    ev = {"ts": ts, "role": "assistant",
                          "text": resp.strip()}
                    model = td.get("model")
                    if isinstance(model, str) and model:
                        ev["model"] = model
                    events.append(ev)
            if _export_session(dest, team_id, github_login, str(sid),
                               events, integrity, "session-store.db"):
                imported += 1
        conn.close()
        print(f"[copilot] session-store.db at {db_path}: "
              f"{count_in_ws} session(s) in workspace, "
              f"{imported} imported")
    return imported


def backfill(dest: Path, team_id: str, github_login: str) -> int:
    workspace_root = _find_workspace_root(dest)
    if workspace_root is None:
        print("[copilot] destination is not inside an openvela workspace "
              "(no .repo/); skipping")
        return 0
    workspace_str = str(workspace_root.resolve())

    imported = backfill_session_store_db(
        dest, workspace_str, team_id, github_login)

    candidates: list[tuple[str, Path]] = []
    for user_data in vscode_user_data_candidates():
        ws_root = user_data / "User" / "workspaceStorage"
        if ws_root.is_dir():
            for ws_dir in sorted(ws_root.iterdir()):
                chat_dir = ws_dir / "chatSessions"
                if not chat_dir.is_dir():
                    continue
                folder = workspace_folder_of(ws_dir) or ""
                candidates.append((folder, chat_dir))
        empty_chat = user_data / "User" / "globalStorage" / \
            "emptyWindowChatSessions"
        if empty_chat.is_dir():
            candidates.append(("", empty_chat))

    scanned = 0
    for folder, chat_dir in candidates:
        resolved = str(Path(folder).resolve()) if folder else ""
        in_ws = bool(resolved) and resolved.startswith(workspace_str)
        if not in_ws:
            continue
        for path in sorted(chat_dir.iterdir()):
            if path.suffix not in (".json", ".jsonl"):
                continue
            scanned += 1
            session = load_session(path)
            if not isinstance(session, dict):
                continue
            session_id = session.get("sessionId") or path.stem
            if _session_in_manifest(dest, github_login, session_id):
                continue
            events = session_to_events(
                session, str(session_id), team_id, github_login)
            if not events:
                continue
            integrity = {
                "main_sha256": _sha256_file(path),
                "main_size": path.stat().st_size if path.is_file() else 0,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            if _export_session(dest, team_id, github_login,
                               str(session_id), events, integrity,
                               "chatSessions"):
                imported += 1

    print(f"[copilot] scanned {scanned} chatSessions file(s), "
          f"total imported {imported}")
    return imported
