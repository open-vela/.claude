#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# openvela AI Contest - Cursor IDE backfill adapter.
#
# Cursor is a VS Code fork; its chat data lives in SQLite `state.vscdb`
# files but uses a KV schema (ItemTable + cursorDiskKV) with JSON blob
# values, not row-oriented tables. This module extracts current-format
# Composer conversations (cursorDiskKV.composerData:* + bubbleId:*),
# gates by openvela workspace via workspaceStorage/*/workspace.json,
# and outputs standard contest JSONL events.
#
# Legacy formats (aichat.chatdata / aiService.prompts / early workspace
# composer.composerData) are NOT supported by design - contest scope is
# limited to conversations produced after Cursor's move to global
# cursorDiskKV storage (late 2024 / 2025).
#
# Integrity: for each Composer imported, records SHA256 + mtime of the
# source state.vscdb (and -wal / -shm if present) into manifest, so post-
# hoc audits can detect if the source was mutated after backfill.

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_ID = "cursor"
SCHEMA_VERSION = "1.0"
COMPOSER_HEADERS_KEY = "composer.composerHeaders"
COMPOSER_DATA_KEY = "composer.composerData"


def cursor_root_candidates() -> list[Path]:
    override = os.environ.get("CURSOR_ROOT_OVERRIDE")
    if override:
        return [Path(override)]
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return [
            home / "Library" / "Application Support" / "Cursor",
            home / ".config" / "Cursor",
        ]
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        localappdata = os.environ.get("LOCALAPPDATA")
        cands = []
        if appdata:
            cands.append(Path(appdata) / "Cursor")
        if localappdata:
            cands.append(Path(localappdata) / "Cursor")
        return cands
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else home / ".config"
    return [
        root / "Cursor",
        home / ".config" / "Cursor",
        home / ".local" / "share" / "Cursor",
    ]


def find_cursor_dbs() -> tuple[Path | None, list[Path]]:
    """Return (global_db_path, [workspace_db_paths])."""
    global_db = None
    ws_dbs: list[Path] = []
    seen: set[Path] = set()
    for root in cursor_root_candidates():
        gd = root / "User" / "globalStorage" / "state.vscdb"
        if gd.is_file() and gd.resolve() not in seen:
            seen.add(gd.resolve())
            if global_db is None:
                global_db = gd
        ws_dir = root / "User" / "workspaceStorage"
        if ws_dir.is_dir():
            for entry in ws_dir.iterdir():
                if not entry.is_dir():
                    continue
                dbf = entry / "state.vscdb"
                if dbf.is_file() and dbf.resolve() not in seen:
                    seen.add(dbf.resolve())
                    ws_dbs.append(dbf)
    return global_db, ws_dbs


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def source_integrity(db_path: Path) -> dict[str, Any]:
    """Snapshot source db integrity: SHA256 of state.vscdb + WAL/SHM +
    mtime, so post-hoc audit can detect tampering.
    """
    out: dict[str, Any] = {}
    main = db_path
    wal = db_path.parent / (db_path.name + "-wal")
    shm = db_path.parent / (db_path.name + "-shm")
    if main.is_file():
        st = main.stat()
        out["main_sha256"] = _hash_file(main)
        out["main_size"] = st.st_size
        out["main_mtime"] = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc).isoformat()
    if wal.is_file():
        out["wal_sha256"] = _hash_file(wal)
    if shm.is_file():
        out["shm_sha256"] = _hash_file(shm)
    out["captured_at"] = datetime.now(timezone.utc).isoformat()
    return out


def open_db_readonly(db_path: Path) -> sqlite3.Connection | None:
    """Open a WAL-safe read-only snapshot: copy main + WAL + SHM to a
    temp dir, then open the copy. If Cursor is running, this avoids
    reading a partial state from the live db.
    """
    if not db_path.is_file():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="cursor-backfill-"))
    try:
        shutil.copy2(db_path, tmp / db_path.name)
        for suffix in ("-wal", "-shm"):
            src = db_path.parent / (db_path.name + suffix)
            if src.is_file():
                shutil.copy2(src, tmp / (db_path.name + suffix))
        conn = sqlite3.connect(
            f"file:{tmp / db_path.name}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except (OSError, sqlite3.Error) as e:
        sys.stderr.write(f"[cursor] cannot open {db_path}: {e}\n")
        return None


def _get_json(conn: sqlite3.Connection, table: str, key: str) -> Any:
    try:
        row = conn.execute(
            f"SELECT value FROM {table} WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    val = row[0]
    if isinstance(val, bytes):
        try:
            val = val.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, AttributeError):
            return None
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return None


def workspace_folder_from_ws_db(ws_db: Path) -> str | None:
    """workspace.json sits next to the workspace state.vscdb and contains
    the actual project fsPath. Prefer it over any path inside the db,
    because Composer records may reference stale or hashed paths.
    """
    ws_json = ws_db.parent / "workspace.json"
    if not ws_json.is_file():
        return None
    try:
        data = json.loads(ws_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    folder = data.get("folder")
    if isinstance(folder, str) and folder.startswith("file://"):
        return folder[len("file://"):]
    return None


def _bubble_role(bubble: dict) -> str:
    t = bubble.get("type")
    if t == 1:
        return "user"
    if t == 2:
        return "assistant"
    return "user"


def _bubble_text(bubble: dict) -> str:
    text = bubble.get("text") or ""
    if isinstance(text, str) and text.strip():
        return text.strip()
    rich = bubble.get("richText")
    if isinstance(rich, str) and rich.strip():
        return rich.strip()
    return ""


def _bubble_ts(bubble: dict, fallback_ts: str) -> str:
    ts = bubble.get("createdAt")
    if isinstance(ts, str) and ts:
        return ts
    if isinstance(ts, (int, float)):
        try:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.") + \
                f"{dt.microsecond // 1000:03d}Z"
        except (OSError, ValueError):
            pass
    return fallback_ts


def _millis_to_iso(millis: Any) -> str:
    try:
        dt = datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{dt.microsecond // 1000:03d}Z"
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")


def composer_to_events(global_conn: sqlite3.Connection, composer_id: str,
                       team_id: str, github_login: str) -> list[dict]:
    """Extract events for a single Composer. Follows fullConversation-
    HeadersOnly[] order (not sqlite key order, which is arbitrary).
    """
    composer_data = _get_json(
        global_conn, "cursorDiskKV", f"composerData:{composer_id}")
    if not isinstance(composer_data, dict):
        return []
    headers = composer_data.get("fullConversationHeadersOnly") or []
    if not isinstance(headers, list):
        return []
    started_ms = composer_data.get("createdAt") or 0
    fallback_ts = _millis_to_iso(started_ms) if started_ms else \
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    events: list[dict] = []
    seq = 0
    for header in headers:
        if not isinstance(header, dict):
            continue
        bubble_id = header.get("bubbleId")
        if not bubble_id:
            continue
        bubble = _get_json(
            global_conn, "cursorDiskKV",
            f"bubbleId:{composer_id}:{bubble_id}")
        if not isinstance(bubble, dict):
            continue
        text = _bubble_text(bubble)
        if not text:
            continue
        ts = _bubble_ts(bubble, fallback_ts)
        role = _bubble_role(bubble)
        ev = {
            "schema_version": SCHEMA_VERSION,
            "session_id": composer_id,
            "team_id": team_id,
            "github_login": github_login,
            "tool": TOOL_ID,
            "seq": seq,
            "ts": ts,
            "role": role,
            "text": text,
        }
        model_info = bubble.get("modelInfo") or {}
        if role == "assistant" and isinstance(model_info, dict):
            model_name = model_info.get("modelName")
            if model_name:
                ev["model"] = model_name
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
    manifest.setdefault("generator", f"backfill-cursor@{TOOL_ID}")
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
        "collection_mode": "backfill-cursor",
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


def collect_composer_workspace_map(
        global_conn: sqlite3.Connection,
        ws_dbs: list[Path]) -> dict[str, str]:
    """Build composer_id -> workspace fsPath map. Priority:
    1. global composer.composerHeaders[].workspaceIdentifier.uri.fsPath
    2. workspace-DB composer.composerData.allComposers[].composerId +
       sibling workspace.json.folder
    """
    mapping: dict[str, str] = {}
    headers = _get_json(global_conn, "ItemTable", COMPOSER_HEADERS_KEY)
    if isinstance(headers, dict):
        for c in headers.get("allComposers") or []:
            if not isinstance(c, dict):
                continue
            cid = c.get("composerId")
            wid = c.get("workspaceIdentifier") or {}
            uri = wid.get("uri") if isinstance(wid, dict) else None
            if isinstance(uri, dict):
                fs_path = uri.get("fsPath") or uri.get("path")
                if cid and isinstance(fs_path, str):
                    mapping[cid] = fs_path
    for ws_db in ws_dbs:
        folder = workspace_folder_from_ws_db(ws_db)
        if not folder:
            continue
        ws_conn = open_db_readonly(ws_db)
        if not ws_conn:
            continue
        try:
            ws_composers = _get_json(
                ws_conn, "ItemTable", COMPOSER_DATA_KEY)
        finally:
            ws_conn.close()
        if not isinstance(ws_composers, dict):
            continue
        for c in ws_composers.get("allComposers") or []:
            if not isinstance(c, dict):
                continue
            cid = c.get("composerId")
            if cid and cid not in mapping:
                mapping[cid] = folder
    return mapping


def backfill(dest: Path, team_id: str, github_login: str) -> int:
    global_db, ws_dbs = find_cursor_dbs()
    if global_db is None:
        print("[cursor] no global state.vscdb found; skipping")
        return 0

    workspace_root = _find_workspace_root(dest)
    if workspace_root is None:
        print("[cursor] destination is not inside an openvela workspace "
              "(no .repo/); skipping")
        return 0
    workspace_str = str(workspace_root.resolve())

    global_conn = open_db_readonly(global_db)
    if global_conn is None:
        return 0
    try:
        mapping = collect_composer_workspace_map(global_conn, ws_dbs)
    except sqlite3.Error as e:
        sys.stderr.write(f"[cursor] cannot build composer map: {e}\n")
        global_conn.close()
        return 0

    if not mapping:
        print("[cursor] no composers with resolvable workspace found")
        global_conn.close()
        return 0

    matched = {
        cid: path for cid, path in mapping.items()
        if str(Path(path).resolve()).startswith(workspace_str)
    }
    print(f"[cursor] found {len(matched)} composer(s) inside workspace "
          f"(out of {len(mapping)} total)")
    if not matched:
        global_conn.close()
        return 0

    integrity = source_integrity(global_db)
    success = 0
    try:
        for cid in matched:
            events = composer_to_events(
                global_conn, cid, team_id, github_login)
            if not events:
                continue
            date_str = events[0]["ts"][:10]
            rel_path = (
                f"logs/{github_login}/{date_str}/{TOOL_ID}__{cid}.jsonl")
            jsonl_path = dest / rel_path
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with jsonl_path.open("w", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            _write_manifest(dest, github_login, team_id, cid, events,
                            rel_path, integrity)
            success += 1
            print(f"  [cursor] {cid[:20]}  wrote {len(events)} event(s) "
                  f"-> {rel_path}")
    finally:
        global_conn.close()
    return success
