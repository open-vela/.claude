#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# openvela AI Contest - DeepSeek Harness (dsh) backfill adapter.
#
# Reads dsh session logs from $DSH_HOME/sessions (default ~/.dsh/sessions).
# Root resolution matches the harness itself (dsh-home-paths): an explicit
# DSH_HOME wins over the default ~/.dsh, and a blank value counts as unset.
#
# Layout:
#   <home>/sessions/<encoded-project-dir>/<session-id>/session.v4.jsonl.zstd
# The artifact is JSONL, compressed with zstd when the harness runs its
# default configuration (compression: "none" produces a plain .jsonl).
#
# --- Format (session format v4) ----------------------------------
# Verified against dsh 0.2.0-rc.2 on 2026-11-03 over 49 sessions /
# 44k events, cross-checked against the harness source
# (@deepseek-ai/dsh-session-persistence-jsonl, dsh-session).
#
#   line 1   {"type":"session","version":4,"id":...,"createdAt":ms,
#             "cwd":...,"parentSession"?,"origin"?,"delegationDepth":n,
#             "agentPreset":...}
#   rest     {"type":...,"seq":n,"time":ms,"data":{...}}
#
# `seq` is contiguous from 0 and `time` is epoch milliseconds; both were
# monotonic across the whole corpus. The contest schema requires its own
# seq, so events are renumbered 0..n-1 and the source seq is not kept.
#
# The zstd artifact is a SEQUENCE OF FRAMES, one per append batch (209
# frames in the largest file here). Each frame decodes independently, so
# a session that is still being written yields every complete frame and
# one torn tail - which is why decompression accepts the zstd CLI exit
# code 1 as long as it produced output.
#
# --- Idempotency --------------------------------------------------
# Re-runs are keyed on the source file's SHA256, not on the session id
# alone: an open session keeps appending frames, so "already in the
# manifest" is not the same as "nothing new". Unchanged source -> skip;
# changed source -> rewrite that session's JSONL and manifest entry.
# A session exported mid-flight therefore picks up its remaining turns
# on the next run instead of staying frozen at whatever it had.
#
# --- Event mapping ------------------------------------------------
# The harness emits ~60 event types; the contest schema needs four
# roles. Mapped:
#   user/message       role=user       ONLY data.source.kind=="user"
#   assistant/message  role=assistant  text + reasoning + usage
#   tool/call          role=tool       name / arguments / callId
#   tool/result        role=tool       "<result>" + output, by callId
#   session/title      manifest.title  (last title wins)
# Dropped as harness machinery, not contestant dialogue:
#   system/message     the system prompt
#   request/header     tool schemas + model config (~21 KB each)
#   request/context    context-window bookkeeping
#   agent/inbox/spliced  inbox delivery that user/message mirrors
#   turn/* step/*      loop framing
#   compaction/*       context-compaction bookkeeping (the shadowed
#                      events stay in the log, so history is complete)
#   assistant/attempt  empty failed attempts (it has an empty stream)
#   todo/write, deliverables/presented, workspace/changes,
#   subagent/catalog, subagent/descriptor, session/end-seed,
#   sandbox/mode, approval/policy, permission/preset,
#   session-log-deepseek/*, web/deepseek-search-llm-request
#
# --- Why user/message needs a source filter -----------------------
# user/message is the model-visible message, not "what the human typed".
# Of 494 user/message events in the corpus, 147 were injected by the
# harness and carry a different data.source.kind:
#   agent-instructions (53)  AGENTS.md system-reminder
#   runtime-context    (53)  sandbox/approval snapshot
#   tool-jobs          (27)  background job notifications
#   compact-checkpoint  (5)  compaction checkpoint
#   agent-message       (5)  message from another agent
#   subagent-settled    (3)  subagent completion notice
#   repeat-tool-reminder(1)  loop guard
# Emitting those as `user` would show the judges paragraphs of harness
# boilerplate attributed to the contestant. Only kind=="user" is used.
# The same human text also appears in agent/inbox/spliced; taking it
# from user/message (and never from spliced) is what keeps it once.
#
# --- Subagents ----------------------------------------------------
# A subagent runs as a full session with its own log file and a
# parentSession id, so its work is imported as its own session and the
# manifest entry records parent_session_id / origin / delegation_depth.
# The parent log only carries the subagent's closing message, so
# dropping child sessions would lose the actual work.
#
# --- Images -------------------------------------------------------
# The contest schema has no image slot (same as the other adapters).
# Image blocks are dropped, counted, and reported: a user message that
# carries only images becomes a text placeholder instead of
# disappearing, so the turn stays visible to the judges.

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_ID = "dsh"
ADAPTER_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
SESSION_FORMAT_VERSION = 4
COLLECTION_MODE = "backfill-local-log"

USER_SOURCE_KIND = "user"

# Harness event types with no contest-schema meaning (see the header).
IGNORED_EVENT_TYPES = frozenset({
    "agent/inbox/spliced",
    "approval/policy",
    "assistant/attempt",
    "compaction/end",
    "compaction/prune",
    "compaction/start",
    "compaction/summary",
    "deliverables/presented",
    "permission/preset",
    "request/context",
    "request/header",
    "sandbox/mode",
    "session",
    "session-log-deepseek/delivery-accepted",
    "session/end-seed",
    "session/title-llm-request",
    "step/end",
    "step/start",
    "subagent/catalog",
    "subagent/descriptor",
    "system/message",
    "todo/write",
    "turn/end",
    "turn/start",
    "web/deepseek-search-llm-request",
    "workspace/changes",
})

_SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
try:
    # Reuse the collector's redaction rules instead of duplicating them;
    # the pattern list lives in snapshot_core.DEFAULT_REDACT_RULES.
    from snapshot_core import DEFAULT_REDACT_RULES, redact_value
except ImportError:  # pragma: no cover - shared/ ships next to adapters/
    sys.stderr.write(
        "[dsh] WARNING: adapters/shared/snapshot_core.py not importable; "
        "events are written WITHOUT secret redaction\n")
    DEFAULT_REDACT_RULES = []

    def redact_value(value: Any, rules: Any, counter: list[int]) -> Any:
        return value


def dsh_home() -> Path:
    # Precedence mirrors @deepseek-ai/dsh-home-paths.
    override = os.environ.get("DSH_HOME_BACKFILL_OVERRIDE")
    if override:
        return Path(override)
    env = os.environ.get("DSH_HOME")
    if env and env.strip():
        return Path(env)
    return Path.home() / ".dsh"


def _ms_to_iso(ms: Any) -> str:
    if isinstance(ms, (int, float)):
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _zstd_bytes(path: Path) -> bytes:
    """Decode a multi-frame zstd session log.

    The `zstandard` module is preferred when installed; the `zstd` CLI is
    the common fallback. A torn final frame (session still being written)
    is expected, so exit code 1 with output is a success.
    """
    try:
        import zstandard  # type: ignore

        data = path.read_bytes()
        return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 - fall back to the CLI
        sys.stderr.write(
            f"[dsh] {path.name}: zstandard module failed ({e}); "
            f"trying the zstd CLI\n")

    try:
        proc = subprocess.run(["zstd", "-dc", str(path)],
                              capture_output=True)
    except FileNotFoundError:
        sys.stderr.write(
            "[dsh] cannot decompress session logs: neither the `zstandard` "
            "Python module nor the `zstd` CLI is available "
            "(pip install zstandard, or install zstd).\n")
        return b""
    if proc.stdout and proc.returncode in (0, 1):
        return proc.stdout
    if proc.returncode != 0:
        sys.stderr.write(
            f"[dsh] zstd failed on {path.name}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}\n")
    return b""


def read_session_log(path: Path) -> list[dict[str, Any]] | None:
    """Parse one session artifact into records, tolerating a torn tail."""
    raw = _zstd_bytes(path) if path.suffix == ".zstd" else path.read_bytes()
    if not raw:
        return None
    records: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # The writer appends whole lines; an unparsable line can only
            # be the last one of a log that is being written right now.
            break
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _blocks_text(content: Any, block_type: str) -> str:
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != block_type:
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts)


def _count_blocks(content: Any, block_type: str) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content
               if isinstance(b, dict) and b.get("type") == block_type)


def _parse_arguments(raw: Any) -> Any:
    """Keep tool arguments structured; the schema asks for an object."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _meta_paths(meta: Any) -> list[str]:
    if not isinstance(meta, dict):
        return []
    out: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in out:
            out.append(value)

    add(meta.get("path"))
    for value in meta.get("paths") or []:
        add(value)
    for key in ("files", "diffs"):
        for item in meta.get(key) or []:
            if isinstance(item, dict):
                add(item.get("path"))
    return out


def _meta_summary(meta: Any) -> dict[str, Any] | None:
    """Keep the audit-worthy scalars of tool/result meta, drop the bulk.

    `lines` / `diffs.oldText` / `newText` repeat the tool output that is
    already in the event, and would roughly double the log size.
    """
    if not isinstance(meta, dict):
        return None
    summary: dict[str, Any] = {}
    for key, value in meta.items():
        if key in ("lines", "oldText", "newText"):
            continue
        if key in ("diffs", "files", "paths"):
            paths = _meta_paths({key: value})
            if paths:
                summary[key] = paths
        elif key == "sources":
            urls = [s.get("url") for s in value
                    if isinstance(s, dict) and s.get("url")]
            if urls:
                summary["sources"] = urls
        elif isinstance(value, (str, int, float, bool)):
            summary[key] = value
    return summary or None


def session_to_events(records: list[dict[str, Any]]) -> tuple[
        dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    """Convert one parsed session log into (header, events, stats).

    Events keep the log's order, which is already time-ordered (zero
    non-monotonic `time` transitions in the verified corpus).
    """
    if not records:
        return None, [], {}
    header = records[0]
    if header.get("type") != "session":
        return None, [], {}

    events: list[dict[str, Any]] = []
    stats = {
        "images_dropped": 0,
        "unknown_types": set(),
        "tokens_in": 0,
        "tokens_out": 0,
        "model": None,
        "title": None,
    }

    version = header.get("version")
    if version != SESSION_FORMAT_VERSION:
        sys.stderr.write(
            f"[dsh] session {header.get('id')}: unexpected format "
            f"v{version} (adapter knows v{SESSION_FORMAT_VERSION}); "
            f"parsing defensively\n")

    for rec in records[1:]:
        etype = rec.get("type")
        data = rec.get("data")
        if not isinstance(data, dict):
            data = {}
        ts = _ms_to_iso(rec.get("time"))

        if etype == "user/message":
            source = data.get("source")
            if not isinstance(source, dict) or \
                    source.get("kind") != USER_SOURCE_KIND:
                continue
            content = data.get("content")
            text = _blocks_text(content, "text")
            images = _count_blocks(content, "image")
            stats["images_dropped"] += images
            ev: dict[str, Any] = {"ts": ts, "role": "user"}
            if text:
                ev["text"] = text
            elif images:
                ev["text"] = f"[{images} image attachment(s)]"
                ev["metadata"] = {"images_dropped": images}
            else:
                continue
            events.append(ev)

        elif etype == "assistant/message":
            message = data.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            text = _blocks_text(content, "text")
            thinking = _blocks_text(content, "reasoning")
            stats["images_dropped"] += _count_blocks(content, "image")
            if not text and not thinking:
                continue
            ev = {"ts": ts, "role": "assistant"}
            if text:
                ev["text"] = text
            if thinking:
                ev["thinking"] = thinking
            source = message.get("source")
            model = source.get("model") if isinstance(source, dict) else None
            if isinstance(model, str) and model:
                ev["model"] = model
                stats["model"] = model
            usage = data.get("usage")
            if isinstance(usage, dict):
                if isinstance(usage.get("inputTokens"), int):
                    ev["tokens_in"] = usage["inputTokens"]
                    stats["tokens_in"] += usage["inputTokens"]
                if isinstance(usage.get("outputTokens"), int):
                    ev["tokens_out"] = usage["outputTokens"]
                    stats["tokens_out"] += usage["outputTokens"]
            if data.get("interrupted"):
                ev["metadata"] = {"interrupted": True}
            events.append(ev)

        elif etype == "tool/call":
            events.append({
                "ts": ts,
                "role": "tool",
                "tool_name": data.get("name") or "unknown",
                "tool_call_id": data.get("callId") or "",
                "input": _parse_arguments(data.get("arguments")),
                "output": None,
            })

        elif etype == "tool/result":
            message = data.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            stats["images_dropped"] += _count_blocks(content, "image")
            ev = {
                "ts": ts,
                "role": "tool",
                "tool_name": "<result>",
                "tool_call_id": message.get("toolCallId")
                or data.get("toolCallId") or "",
                "input": None,
                "output": _blocks_text(content, "text"),
            }
            if message.get("isError"):
                ev["is_error"] = True
            meta = _meta_summary(data.get("meta"))
            error = data.get("error")
            if isinstance(error, dict):
                meta = {**(meta or {}), "error": error}
            if meta:
                ev["metadata"] = meta
            paths = _meta_paths(data.get("meta"))
            if paths:
                ev["files_touched"] = paths
            events.append(ev)

        elif etype == "session/title":
            title = data.get("title")
            if isinstance(title, str) and title:
                stats["title"] = title

        elif etype in IGNORED_EVENT_TYPES:
            continue

        else:
            # Additive format changes are expected; count and continue.
            stats["unknown_types"].add(str(etype))

    return header, events, stats


def _find_workspace_root(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".repo").is_dir():
            return candidate
    return None


def _manifest_path(dest: Path, github_login: str) -> Path:
    return dest / "logs" / github_login / "manifest.json"


def _load_manifest(dest: Path, github_login: str) -> dict[str, Any]:
    path = _manifest_path(dest, github_login)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _manifest_entry(dest: Path, github_login: str,
                    session_id: str) -> dict[str, Any] | None:
    manifest = _load_manifest(dest, github_login)
    for entry in manifest.get("sessions", []):
        if isinstance(entry, dict) and entry.get("session_id") == session_id \
                and entry.get("tool") == TOOL_ID:
            return entry
    return None


def _write_manifest(dest: Path, github_login: str, team_id: str,
                    session_id: str, events: list[dict], rel_path: str,
                    integrity: dict, stats: dict,
                    header: dict, redacted: int) -> None:
    manifest = _load_manifest(dest, github_login)
    manifest.setdefault("schema_version", SCHEMA_VERSION)
    manifest["team_id"] = team_id
    manifest["github_login"] = github_login
    manifest.setdefault("generator", f"backfill-{TOOL_ID}@{ADAPTER_VERSION}")
    sessions = manifest.setdefault("sessions", [])
    if not isinstance(sessions, list):
        sessions = manifest["sessions"] = []

    entry = next(
        (s for s in sessions
         if isinstance(s, dict) and s.get("session_id") == session_id), None)
    new_entry: dict[str, Any] = {
        "session_id": session_id,
        "tool": TOOL_ID,
        "started_at": events[0]["ts"],
        "last_event_at": events[-1]["ts"],
        "event_count": len(events),
        "file_path": rel_path,
        "collection_mode": COLLECTION_MODE,
        "health": "ok",
        "source_integrity": integrity,
    }
    if stats.get("model"):
        new_entry["model"] = stats["model"]
    if stats.get("title"):
        new_entry["title"] = stats["title"]
    if stats["tokens_in"] or stats["tokens_out"]:
        new_entry["tokens_total"] = \
            stats["tokens_in"] + stats["tokens_out"]
    if redacted:
        new_entry["redacted_count_total"] = redacted
    # Subagent provenance: the child session is a full session of its own.
    if header.get("parentSession"):
        new_entry["parent_session_id"] = header["parentSession"]
    if header.get("origin"):
        new_entry["origin"] = header["origin"]
    if isinstance(header.get("delegationDepth"), int):
        new_entry["delegation_depth"] = header["delegationDepth"]

    if entry is not None:
        entry.update(new_entry)
    else:
        sessions.append(new_entry)

    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = _manifest_path(dest, github_login)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def iter_session_logs(home: Path) -> list[Path]:
    """All session artifacts under <home>/sessions, newest format first.

    A session directory can in principle hold more than one format
    generation after a migration; the highest version wins so it is
    imported once.
    """
    root = home / "sessions"
    if not root.is_dir():
        return []
    chosen: dict[Path, tuple[int, Path]] = {}
    for path in root.rglob("session.v*.jsonl*"):
        if not path.is_file():
            continue
        name = path.name
        try:
            version = int(name.split(".v", 1)[1].split(".", 1)[0])
        except (IndexError, ValueError):
            continue
        key = path.parent
        if key not in chosen or version > chosen[key][0]:
            chosen[key] = (version, path)
    return sorted(entry[1] for entry in chosen.values())


def backfill(dest: Path, team_id: str, github_login: str) -> int:
    home = dsh_home()
    logs = iter_session_logs(home)
    if not logs:
        print(f"[dsh] no session logs under {home}; skipping")
        return 0

    workspace_root = _find_workspace_root(dest)
    if workspace_root is None:
        print("[dsh] destination is not inside an openvela workspace "
              "(no .repo/); skipping")
        return 0
    workspace_root = workspace_root.resolve()

    total = imported = present = failed = 0
    for path in logs:
        try:
            records = read_session_log(path)
            header, events, stats = session_to_events(records or [])
        except OSError as e:
            sys.stderr.write(f"[dsh] cannot read {path}: {e}\n")
            failed += 1
            continue
        if header is None:
            continue
        total += 1

        cwd = header.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            continue
        try:
            inside = Path(cwd).resolve().is_relative_to(workspace_root)
        except (OSError, ValueError):
            inside = False
        if not inside:
            continue

        sid = header.get("id") or path.parent.name
        sid = str(sid)

        # A session that is still open keeps appending frames. The source
        # hash decides whether there is anything new: unchanged -> skip
        # (idempotent re-runs), changed -> rewrite this session's export.
        # Without this, a session exported mid-flight would never pick up
        # its remaining turns, because the manifest already lists it.
        integrity = {
            "main_sha256": _sha256_file(path),
            "main_size": path.stat().st_size,
            "main_mtime": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "format_version": header.get("version"),
            "compression": "zstd" if path.suffix == ".zstd" else "none",
        }
        previous = _manifest_entry(dest, github_login, sid)
        if previous is not None:
            recorded = previous.get("source_integrity")
            recorded_hash = recorded.get("main_sha256") \
                if isinstance(recorded, dict) else None
            if recorded_hash == integrity["main_sha256"]:
                present += 1
                continue
        if not events:
            continue

        date_str = events[0]["ts"][:10]
        rel_path = f"logs/{github_login}/{date_str}/{TOOL_ID}__{sid}.jsonl"
        jsonl_path = dest / rel_path
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)

        redacted_total = 0
        with jsonl_path.open("w", encoding="utf-8") as f:
            for seq, event in enumerate(events):
                counter = [0]
                payload = redact_value(event, DEFAULT_REDACT_RULES, counter)
                redacted_total += counter[0]
                out = {
                    "schema_version": SCHEMA_VERSION,
                    "session_id": sid,
                    "team_id": team_id,
                    "github_login": github_login,
                    "tool": TOOL_ID,
                    "seq": seq,
                    **payload,
                }
                if counter[0]:
                    out["redacted_count"] = counter[0]
                f.write(json.dumps(out, ensure_ascii=False) + "\n")

        _write_manifest(dest, github_login, team_id, sid, events, rel_path,
                        integrity, stats, header, redacted_total)
        imported += 1
        notes = []
        if previous is not None:
            notes.append("source changed, re-exported")
        if stats["images_dropped"]:
            notes.append(f"{stats['images_dropped']} image(s) dropped")
        if stats["unknown_types"]:
            notes.append("unknown event(s): "
                         + ", ".join(sorted(stats["unknown_types"])))
        suffix = f"  ({'; '.join(notes)})" if notes else ""
        print(f"  [dsh] {sid[:30]:30s} {len(events):5d} event(s) "
              f"-> {rel_path}{suffix}")

    print(f"[dsh] scanned {len(logs)} session log(s) under {home}; "
          f"imported {imported}, already present {present}"
          + (f", unreadable {failed}" if failed else ""))
    return imported
