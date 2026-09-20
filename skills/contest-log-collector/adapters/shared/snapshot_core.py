#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# openvela AI Contest - Claude Code & Codex shared snapshot core
#
# Claude Code and OpenAI Codex CLI share ~90% of their hook stdin JSON schema
# (Codex source comments reference Claude Code's schema directly).
# Both tools share the same Python core; only a thin wrapper differentiates the tool name.
#
# Invocation (Claude Code Stop / SessionEnd hook or Codex Stop hook):
#   echo "$STDIN_JSON" | python3 snapshot_core.py --tool claude-code
#   echo "$STDIN_JSON" | python3 snapshot_core.py --tool codex
#
# Required env: TEAM_ID
# Optional env: SESSION_LOG_DIR
#
# Exit codes:
#   0  Success (including throttled skips)
#   1  Failed but recorded in errors/
#   2  TEAM_ID missing or fatal config error

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_github_login import resolve_github_login

VERSION = "1.3.0"
SCHEMA_VERSION = "1.0"

DEFAULT_REDACT_RULES = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "sk-***REDACTED***"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "ghp_***REDACTED***"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]+"), "Bearer ***REDACTED***"),
]

SKIP_BLOCK_TYPES = {"image"}


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def local_date() -> str:
    d = datetime.now()
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def fs_timestamp() -> str:
    d = datetime.now()
    return d.strftime("%Y%m%d_%H%M%S")


def report_error(log_root: Path, kind: str, ctx: dict, exc: BaseException | str) -> None:
    try:
        errors_dir = log_root / "errors"
        errors_dir.mkdir(parents=True, exist_ok=True)
        err_path = errors_dir / f"{fs_timestamp()}_{kind}.err"
        payload = {
            "ts": iso_now(),
            "kind": kind,
            "tool": ctx.get("tool", "unknown"),
            "team_id": os.environ.get("TEAM_ID", ""),
            "version": VERSION,
            "context": ctx,
            "message": str(exc),
        }
        err_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    sys.stderr.write(
        f"[session-log] ERROR kind={kind} tool={ctx.get('tool','?')} "
        f"team={os.environ.get('TEAM_ID','')}: {exc}\n"
    )


def load_redact_rules(log_root: Path) -> list[tuple[re.Pattern[str], str]]:
    custom_path = log_root / "redact.json"
    if not custom_path.exists():
        return DEFAULT_REDACT_RULES
    try:
        custom = json.loads(custom_path.read_text(encoding="utf-8"))
        compiled = []
        for r in custom:
            flags = 0
            if "i" in r.get("flags", ""):
                flags |= re.IGNORECASE
            compiled.append((re.compile(r["pattern"], flags), r.get("replacement", "***REDACTED***")))
        return [*DEFAULT_REDACT_RULES, *compiled]
    except Exception:
        return DEFAULT_REDACT_RULES


def redact_value(value: Any, rules: list[tuple[re.Pattern[str], str]], counter: list[int]) -> Any:
    if isinstance(value, str):
        out = value
        for pattern, replacement in rules:
            new_out, n = pattern.subn(replacement, out)
            counter[0] += n
            out = new_out
        return out
    if isinstance(value, list):
        return [redact_value(v, rules, counter) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v, rules, counter) for k, v in value.items()}
    return value


def load_transcript(transcript_path: Path) -> list[dict] | None:
    if not transcript_path.exists():
        return None
    try:
        events = []
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
    except Exception:
        return None


CODEX_ROLLOUT_LINE_TYPES = {"session_meta", "response_item", "event_msg",
                            "turn_context", "compacted", "token_usage_record",
                            "world_state", "retained_context",
                            "security_risk_score", "realtime_item",
                            "inter_agent_communication",
                            "inter_agent_communication_metadata"}
CODEX_ENV_BLOB_PREFIXES = (
    "<permissions instructions>",
    "<skills_instructions>",
    "<environment_context>",
)


def _codex_content_to_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("input_text", "output_text", "text"):
            t = item.get("text")
            if isinstance(t, str) and t.strip():
                parts.append(t.strip())
    return "\n".join(parts)


def expand_codex_rollout_line(raw_event: dict, fallback_ts: str,
                              state: dict) -> list[dict]:
    """Convert one Codex rollout JSONL line to zero-or-more contest events.

    Codex rollout schema (codex-rs/history, stable across 0.142-0.155;
    re-verified against source 2026-09-20):
      top-level: {timestamp, ordinal?, type, payload}
      - session_meta: payload has session_id/id, cwd, originator
      - turn_context: payload has cwd + model info
      - response_item: payload is a ResponseItem; type "message" carries
        role + content[] of input_text/output_text. Other payload types
        (reasoning / function_call / ghost_snapshot / ...) yield nothing:
        their argument blobs have no contest schema slot; the backfill
        adapter remains the richer extraction path for tool detail.
      - event_msg: payload.type "user_message" echoes the prompt;
        de-duplicated against the preceding response_item user message
        via the state dict passed in by the caller.
    `state` tracks {"last_user_text", "model", "cwd"} ACROSS lines of
    one transcript - the caller must pass the same dict for every line
    of a session, or user-message de-duplication and model attribution
    silently break.
    """
    out: list[dict] = []
    ts = raw_event.get("timestamp") or fallback_ts
    rtype = raw_event.get("type")
    payload = raw_event.get("payload") or {}
    if not isinstance(payload, dict):
        return out

    if rtype == "session_meta":
        state["cwd"] = payload.get("cwd") or state.get("cwd")
        return out

    if rtype == "turn_context":
        model = payload.get("model")
        if isinstance(model, str) and model:
            state["model"] = model
        state["cwd"] = payload.get("cwd") or state.get("cwd")
        return out

    if rtype == "response_item":
        if payload.get("type") != "message":
            return out
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return out
        text = _codex_content_to_text(payload.get("content"))
        if not text:
            return out
        if text.startswith(CODEX_ENV_BLOB_PREFIXES):
            return out
        ev = {"ts": ts, "role": role, "text": text}
        if role == "assistant" and state.get("model"):
            ev["model"] = state["model"]
        if role == "user":
            state["last_user_text"] = text
        out.append(ev)
        return out

    if rtype == "event_msg":
        if payload.get("type") != "user_message":
            return out
        text = payload.get("message")
        if not isinstance(text, str) or not text.strip():
            return out
        text = text.strip()
        if state.get("last_user_text") == text:
            return out
        state["last_user_text"] = text
        out.append({"ts": ts, "role": "user", "text": text})
        return out

    return out


def expand_claude_event(raw_event: dict, fallback_ts: str,
                        codex_state: dict | None = None) -> list[dict]:
    """Convert one Claude Code transcript event to zero-or-more contest events.

    Schema (verified against real ~/.claude/projects/*.jsonl):
      - top-level type: user / assistant / system / progress / file-history-snapshot / ...
      - real conversation lives in .message
      - assistant.message.content is a list of blocks: text / thinking / tool_use
      - user.message.content is str (plain input) OR list (containing tool_result blocks)
      - tool_result blocks carry tool_use_id pairing back to assistant tool_use

    Codex rollout lines ({type: session_meta|response_item|event_msg|...,
    payload: {...}}) are detected and dispatched to the Codex parser, so
    one hook pipeline serves both tools.
    """
    out: list[dict] = []
    top_type = raw_event.get("type")
    ts = raw_event.get("timestamp") or fallback_ts

    if top_type in CODEX_ROLLOUT_LINE_TYPES and "payload" in raw_event \
            and "message" not in raw_event:
        return expand_codex_rollout_line(
            raw_event, fallback_ts,
            codex_state if codex_state is not None else {})

    if top_type in ("progress", "file-history-snapshot", "queue-operation", "permission-mode", "agent-name", "summary", "attachment"):
        return out

    msg = raw_event.get("message") or {}
    role = (msg.get("role") or top_type or "system").lower()
    content = msg.get("content")
    model = msg.get("model")
    usage = msg.get("usage") or {}
    tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("output_tokens")

    common = {"ts": ts}
    if model:
        common["model"] = model
    if isinstance(tokens_in, int):
        common["tokens_in"] = tokens_in
    if isinstance(tokens_out, int):
        common["tokens_out"] = tokens_out

    if isinstance(content, str):
        if content:
            out.append({**common, "role": role, "text": content})
        return out

    if not isinstance(content, list):
        return out

    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt in SKIP_BLOCK_TYPES:
            continue
        if bt == "text":
            text = block.get("text", "")
            if text:
                out.append({**common, "role": role, "text": text})
        elif bt == "thinking":
            thinking = block.get("thinking") or block.get("text") or ""
            if thinking:
                out.append({**common, "role": role, "thinking": thinking})
        elif bt == "tool_use":
            out.append({
                **common,
                "role": "tool",
                "tool_name": block.get("name", "unknown"),
                "tool_call_id": block.get("id", f"call_{len(out)}"),
                "input": block.get("input"),
                "output": None,
            })
        elif bt == "tool_result":
            tool_call_id = block.get("tool_use_id", f"call_{len(out)}")
            result_content = block.get("content")
            out.append({
                **common,
                "role": "tool",
                "tool_name": "<result>",
                "tool_call_id": tool_call_id,
                "input": None,
                "output": result_content,
                "is_error": bool(block.get("is_error")),
            })
        else:
            out.append({
                **common,
                "role": role,
                "text": f"[unsupported block type: {bt}]",
                "metadata": {"dropped": True, "original_type": bt},
            })
    return out


def append_events(jsonl_path: Path, events: list[dict], session_id: str, team_id: str, github_login: str, tool: str, start_seq: int, redact_rules) -> dict:
    seq = start_seq
    redacted_total = 0
    lines = []
    for ev in events:
        counter = [0]
        redacted = redact_value(ev, redact_rules, counter)
        redacted_total += counter[0]
        out = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "team_id": team_id,
            "github_login": github_login,
            "tool": tool,
            "seq": seq,
            **redacted,
        }
        if counter[0] > 0:
            out["redacted_count"] = counter[0]
        lines.append(json.dumps(out, ensure_ascii=False))
        seq += 1
    if not lines:
        return {"written": 0, "last_seq": start_seq - 1, "redacted": 0}
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"written": len(lines), "last_seq": seq - 1, "redacted": redacted_total}


def read_manifest(member_dir: Path, team_id: str, github_login: str, tool: str) -> dict:
    path = member_dir / "manifest.json"
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "team_id": team_id,
            "github_login": github_login,
            "generator": f"{tool}-collector@{VERSION}",
            "updated_at": iso_now(),
            "sessions": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("team_id") != team_id:
            report_error(member_dir, "manifest_team_mismatch", {"tool": tool, "path": str(path)},
                         f"manifest.team_id={data.get('team_id')} but TEAM_ID env={team_id}")
        if data.get("github_login") and data.get("github_login") != github_login:
            report_error(member_dir, "manifest_login_mismatch", {"tool": tool, "path": str(path)},
                         f"manifest.github_login={data.get('github_login')} but detected={github_login}")
        return data
    except Exception as e:
        report_error(member_dir, "manifest_read", {"tool": tool, "path": str(path)}, e)
        return {
            "schema_version": SCHEMA_VERSION,
            "team_id": team_id,
            "github_login": github_login,
            "generator": f"{tool}-collector@{VERSION}",
            "updated_at": iso_now(),
            "sessions": [],
        }


def write_manifest(member_dir: Path, manifest: dict, team_id: str, github_login: str, tool: str) -> None:
    manifest["updated_at"] = iso_now()
    manifest["team_id"] = team_id
    manifest["github_login"] = github_login
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["generator"] = f"{tool}-collector@{VERSION}"
    path = member_dir / "manifest.json"
    try:
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        report_error(member_dir, "manifest_write", {"tool": tool, "path": str(path)}, e)


def get_repo_root() -> Path | None:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], stderr=subprocess.PIPE, text=True)
        return Path(out.strip())
    except Exception:
        return None


# Privacy gate: only collect inside an openvela workspace, identified by a
# `.repo/` dir at the workspace root. Sessions outside it (personal projects)
# must never be collected. Returns the workspace root, or None if not inside.
def find_openvela_workspace_root(start_dir: Path) -> Path | None:
    try:
        cur = Path(start_dir).resolve()
        for candidate in [cur, *cur.parents]:
            if (candidate / ".repo").is_dir():
                return candidate
    except Exception:
        return None
    return None


def get_fallback_author() -> tuple[str, str]:
    fallback = os.environ.get("LOG_FALLBACK_EMAIL", "")
    if fallback:
        return fallback.split("@")[0], fallback
    ssh_dir = Path.home() / ".ssh"
    try:
        for f in ssh_dir.glob("*.pub"):
            content = f.read_text(encoding="utf-8").strip()
            parts = content.split()
            comment = parts[-1] if parts else ""
            if "@" in comment and not comment.startswith("ssh-"):
                return comment.split("@")[0], comment
    except Exception:
        pass
    return "contest-collector", "contest-collector@auto-commit.local"


def process_claude_stdin(stdin_data: dict, tool: str, team_id: str) -> int:
    """Claude Code Stop / SessionEnd hook stdin payload format:
        {
          "session_id": "...",
          "cwd": "...",
          "transcript_path": "/abs/path/.jsonl",
          "hook_event_name": "Stop" | "SessionEnd",
          ...
        }
    Codex Stop hook stdin payload schema is shared with Claude Code (sso_id/cwd/transcript_path use the same names).
    """
    session_id = stdin_data.get("session_id") or stdin_data.get("sessionId")
    cwd = stdin_data.get("cwd") or os.getcwd()
    transcript_path_str = stdin_data.get("transcript_path") or stdin_data.get("transcriptPath")

    if not session_id:
        sys.stderr.write("[session-log] missing session_id in stdin payload\n")
        return 1

    repo_root = get_repo_root() or Path(cwd)

    workspace_root = find_openvela_workspace_root(repo_root)
    if workspace_root is None:
        sys.stderr.write(
            "[session-log] not inside an openvela workspace (no .repo/ found); "
            "collection disabled for this session.\n"
        )
        return 0

    login_result = resolve_github_login(repo_root)
    if not login_result[0]:
        sys.stderr.write(
            "[session-log] FATAL: cannot detect GITHUB_LOGIN. "
            "Set GITHUB_LOGIN env, or run 'gh auth login', or fix git remote URL.\n"
        )
        return 2
    github_login = login_result[0]

    log_root = Path(os.environ.get("SESSION_LOG_DIR")
                    or (Path.home() / ".claude" / "contest-collector-staging"))
    member_dir = log_root / github_login
    try:
        member_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        sys.stderr.write(f"[session-log] cannot create member dir {member_dir}: {e}\n")
        return 1

    hook_event = stdin_data.get("hook_event_name", "Stop")

    if tool == "claude-code" and hook_event == "UserPromptSubmit":
        return 0

    if not transcript_path_str:
        sys.stderr.write(
            "[session-log] skipping: stdin has no transcript_path "
            f"(event={stdin_data.get('hook_event_name', '?')}, "
            f"session={session_id[:12] if session_id else '?'})\n"
        )
        return 0
    transcript_path = Path(transcript_path_str)

    raw_events = load_transcript(transcript_path)
    if raw_events is None:
        report_error(member_dir, "transcript_unreadable", {"tool": tool, "transcript_path": str(transcript_path)},
                     f"cannot read or parse transcript at {transcript_path}")
        return 1

    manifest = read_manifest(member_dir, team_id, github_login, tool)
    entry = next((s for s in manifest.get("sessions", []) if s.get("session_id") == session_id), None)
    is_new = entry is None
    raw_watermark = entry.get("raw_event_count", 0) if entry else 0
    start_seq = entry.get("event_count", 0) if entry else 0

    new_raw = raw_events[raw_watermark:]
    if not new_raw:
        return 0

    contest_events = []
    codex_state: dict = {}
    for raw in new_raw:
        contest_events.extend(expand_claude_event(
            raw, fallback_ts=iso_now(), codex_state=codex_state))

    if not contest_events:
        if entry is not None:
            entry["raw_event_count"] = len(raw_events)
            write_manifest(member_dir, manifest, team_id, github_login, tool)
        return 0

    redact_rules = load_redact_rules(member_dir)
    if entry and entry.get("file_path"):
        existing_rel = entry["file_path"].split(f"{github_login}/", 1)[-1] \
            if f"{github_login}/" in entry["file_path"] else None
        if existing_rel:
            jsonl_path = member_dir / existing_rel
        else:
            jsonl_path = member_dir / local_date() / f"{tool}__{session_id}.jsonl"
    else:
        jsonl_path = member_dir / local_date() / f"{tool}__{session_id}.jsonl"

    try:
        result = append_events(jsonl_path, contest_events, session_id, team_id, github_login, tool, start_seq, redact_rules)
    except Exception as e:
        report_error(member_dir, "jsonl_append", {"tool": tool, "jsonl_path": str(jsonl_path)}, e)
        return 1

    new_event_count = (entry.get("event_count", 0) if entry else 0) + result["written"]
    rel_path = f"logs/{github_login}/{local_date()}/{tool}__{session_id}.jsonl"

    if is_new:
        first_ts = (raw_events[0].get("timestamp") if raw_events else None) or iso_now()
        entry = {
            "session_id": session_id,
            "tool": tool,
            "started_at": first_ts,
            "last_event_at": iso_now(),
            "event_count": new_event_count,
            "raw_event_count": len(raw_events),
            "file_path": rel_path,
            "collection_mode": "cli",
            "health": "ok",
        }
        last_assistant = next((e for e in reversed(raw_events) if (e.get("message") or {}).get("model")), None)
        if last_assistant:
            model = last_assistant.get("message", {}).get("model")
            if model:
                entry["model"] = model
        manifest.setdefault("sessions", []).append(entry)
    else:
        entry["last_event_at"] = iso_now()
        entry["event_count"] = new_event_count
        entry["raw_event_count"] = len(raw_events)
        entry["file_path"] = rel_path
        if result["redacted"] > 0:
            entry["redacted_count_total"] = entry.get("redacted_count_total", 0) + result["redacted"]

    write_manifest(member_dir, manifest, team_id, github_login, tool)

    auto_export_to_repo(
        repo_root, member_dir, jsonl_path, session_id,
        team_id, github_login, tool,
    )

    sys.stderr.write(
        f"[session-log] captured {result['written']} event(s) -> {rel_path}\n"
    )

    return 0


def auto_export_to_repo(repo_root: Path, staging_member_dir: Path, staging_jsonl: Path,
                        session_id: str, team_id: str, github_login: str, tool: str) -> None:
    try:
        dest_member = repo_root / "logs" / github_login
        dest_jsonl = dest_member / staging_jsonl.relative_to(staging_member_dir)
        dest_jsonl.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staging_jsonl, dest_jsonl)

        actual_count = sum(1 for line in dest_jsonl.read_text(encoding="utf-8").splitlines() if line.strip())

        staging_manifest = read_manifest(staging_member_dir, team_id, github_login, tool)
        src_entry = next((s for s in staging_manifest.get("sessions", [])
                          if s.get("session_id") == session_id), None)
        if src_entry is None:
            return
        src_entry["event_count"] = actual_count
        src_entry["file_path"] = f"logs/{github_login}/{staging_jsonl.relative_to(staging_member_dir)}"

        dest_manifest = read_manifest(dest_member, team_id, github_login, tool)
        existing = next((s for s in dest_manifest.get("sessions", [])
                         if s.get("session_id") == session_id), None)
        if existing:
            existing.update(src_entry)
        else:
            dest_manifest.setdefault("sessions", []).append(src_entry)
        write_manifest(dest_member, dest_manifest, team_id, github_login, tool)
    except Exception as e:
        report_error(staging_member_dir, "auto_export", {"repo_root": str(repo_root), "session_id": session_id}, e)


def load_env_file() -> None:
    env_path = Path.home() / ".claude" / "contest-collector.env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True,
                        choices=["claude-code", "codex", "qoder"])
    args = parser.parse_args()

    load_env_file()

    team_id = os.environ.get("TEAM_ID", "").strip()
    if not team_id:
        sys.stderr.write(
            "[session-log] FATAL: TEAM_ID not set (checked env + ~/.claude/contest-collector.env). "
            "Run install.sh --team-id <your-team> --github-login <your-name>.\n"
        )
        return 2

    sys.stderr.write(f"[session-log] tool={args.tool} team={team_id} version={VERSION}\n")

    try:
        stdin_text = sys.stdin.read()
        if not stdin_text.strip():
            sys.stderr.write("[session-log] stdin empty, nothing to do\n")
            return 0
        stdin_data = json.loads(stdin_text)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[session-log] cannot parse stdin JSON: {e}\n")
        return 1

    return process_claude_stdin(stdin_data, args.tool, team_id)


if __name__ == "__main__":
    sys.exit(main())
