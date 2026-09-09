#!/usr/bin/env python3
"""
contest-log-upload validation tool

Validate demo repo's logs/ directory:
  1. manifest.json conforms to manifest.schema.json
  2. Each line in every .jsonl file conforms to event.schema.json
  3. seq within each session is monotonic (tamper detection);
     a session may span several date files (pre-2026-07 collector
     versions split one session across daily files) - all files named
     <tool>__<sid>.jsonl are validated as one continuous seq stream
     and compared against the manifest's cumulative event_count
  4. Files declared in manifest actually exist, and vice versa;
     byte-identical duplicate lines (plugin re-export artifacts) are
     reported as warnings, only content-conflicting duplicates are
     anti-cheat errors
  5. team_id / tool consistency across files

Usage:
    validate-log.py <logs_dir>
    validate-log.py contest-demo-team-001-zhangsan/logs/

Exit codes:
    0  All passed
    1  Errors found
    2  Warnings found (non-fatal)
    3  Usage error (args / IO)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft7Validator
except ImportError:
    print(
        "ERROR: missing dependency 'jsonschema'. Install via: pip install jsonschema",
        file=sys.stderr,
    )
    sys.exit(3)


SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
EVENT_SCHEMA_PATH = SCHEMA_DIR / "event.schema.json"
MANIFEST_SCHEMA_PATH = SCHEMA_DIR / "manifest.schema.json"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files_checked: int = 0
    events_checked: int = 0

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


def load_schema(path: Path) -> dict[str, Any]:
    if not path.exists():
        print(f"ERROR: schema not found: {path}", file=sys.stderr)
        sys.exit(3)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_event(
    event: dict[str, Any],
    validator: Draft7Validator,
    line_no: int,
    file_label: str,
    report: Report,
) -> None:
    for err in validator.iter_errors(event):
        # jsonschema's absolute_path is a list of segments, not a JSONPath string;
        # we synthesize "$.foo[3].bar" form for human-readable error messages
        loc = "$" + "".join(
            f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path
        )
        report.err(f"{file_label}:{line_no}: {loc}: {err.message}")


def validate_jsonl_file(
    jsonl_path: Path,
    event_validator: Draft7Validator,
    expected_team_id: str,
    expected_github_login: str,
    expected_tool: str,
    expected_session_id: str,
    report: Report,
    seq_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a single .jsonl file. Returns stats {ts_min, ts_max, count}.

    seq_state carries the session-level seq ledger (seen seq -> raw line,
    last seq) so that a session split across several date files is checked
    as one continuous stream. Pass a fresh dict per session; files of the
    same session must share the same dict.
    """
    stats = {
        "ts_min": None,
        "ts_max": None,
        "count": 0,
    }
    if seq_state is None:
        seq_state = {"seen": {}, "last": -1}
    file_label = str(jsonl_path)

    if not jsonl_path.exists():
        report.err(f"{file_label}: file does not exist (declared in manifest)")
        return stats

    try:
        content = jsonl_path.read_text(encoding="utf-8")
    except Exception as e:
        report.err(f"{file_label}: cannot read: {e}")
        return stats

    if not content.endswith("\n"):
        report.warn(f"{file_label}: file does not end with newline (spec violation)")

    for line_no, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            report.warn(f"{file_label}:{line_no}: empty line in JSONL")
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError as e:
            report.err(f"{file_label}:{line_no}: invalid JSON: {e.msg}")
            continue

        if not isinstance(event, dict):
            report.err(f"{file_label}:{line_no}: expected object, got {type(event).__name__}")
            continue

        validate_event(event, event_validator, line_no, file_label, report)

        if event.get("team_id") and event["team_id"] != expected_team_id:
            report.err(
                f"{file_label}:{line_no}: team_id mismatch: got {event['team_id']!r}, "
                f"expected {expected_team_id!r}"
            )
        if event.get("github_login") and event["github_login"] != expected_github_login:
            report.err(
                f"{file_label}:{line_no}: github_login mismatch: got {event['github_login']!r}, "
                f"expected {expected_github_login!r}"
            )
        if event.get("tool") and event["tool"] != expected_tool:
            report.err(
                f"{file_label}:{line_no}: tool mismatch: got {event['tool']!r}, "
                f"expected {expected_tool!r}"
            )
        if event.get("session_id") and event["session_id"] != expected_session_id:
            report.err(
                f"{file_label}:{line_no}: session_id mismatch: got {event['session_id']!r}, "
                f"expected {expected_session_id!r}"
            )

        if "seq" in event:
            seq = event["seq"]
            first_line = seq_state["seen"].get(seq)
            if first_line is not None:
                if first_line == line:
                    # Byte-identical re-append: early collector versions
                    # re-exported overlapping transcript ranges into the
                    # same file. Harmless artifact, not tampering.
                    report.warn(
                        f"{file_label}:{line_no}: duplicate identical line seq={seq} "
                        f"(plugin re-export artifact)"
                    )
                else:
                    report.err(
                        f"{file_label}:{line_no}: duplicate seq={seq} with different "
                        f"content (anti-cheat violation)"
                    )
            else:
                seq_state["seen"][seq] = line
                if seq <= seq_state["last"]:
                    report.err(
                        f"{file_label}:{line_no}: seq={seq} not monotonically increasing "
                        f"(previous={seq_state['last']}, anti-cheat violation)"
                    )
                seq_state["last"] = max(seq_state["last"], seq)

        if "ts" in event:
            ts = event["ts"]
            if stats["ts_min"] is None or ts < stats["ts_min"]:
                stats["ts_min"] = ts
            if stats["ts_max"] is None or ts > stats["ts_max"]:
                stats["ts_max"] = ts

        stats["count"] += 1
        report.events_checked += 1

    report.files_checked += 1
    return stats


def _validate_one_member(
    member_dir: Path,
    repo_root: Path,
    manifest_validator: Draft7Validator,
    event_validator: Draft7Validator,
    report: Report,
) -> None:
    manifest_path = member_dir / "manifest.json"

    if not manifest_path.exists():
        report.err(f"{manifest_path}: manifest.json not found in member directory")
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        report.err(f"{manifest_path}: invalid JSON: {e}")
        return

    for err in manifest_validator.iter_errors(manifest):
        loc = "$" + "".join(
            f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path
        )
        report.err(f"{manifest_path}: {loc}: {err.message}")

    if "team_id" not in manifest or "sessions" not in manifest:
        return

    team_id = manifest["team_id"]
    github_login = manifest.get("github_login", "")
    if github_login != member_dir.name:
        report.err(
            f"{manifest_path}: manifest.github_login={github_login!r} but parent "
            f"directory is {member_dir.name!r}"
        )

    declared_files: set[Path] = set()

    for idx, session in enumerate(manifest.get("sessions", [])):
        if not isinstance(session, dict):
            continue
        rel_path = session.get("file_path", "")
        sid = session.get("session_id", f"<unknown-{idx}>")
        tool = session.get("tool", "")

        # file_path is "logs/<github_login>/<date>/<tool>__<sid>.jsonl" relative
        # to the demo repo root. We try multiple resolution bases for robustness.
        candidates = [
            repo_root / rel_path,
            member_dir.parent.parent / rel_path,
            member_dir / rel_path.removeprefix(f"logs/{member_dir.name}/"),
        ]
        jsonl_path = None
        for c in candidates:
            if c.exists():
                jsonl_path = c
                break
        if jsonl_path is None:
            report.err(
                f"{manifest_path}: session[{idx}] {sid!r}: declared file_path "
                f"{rel_path!r} does not exist"
            )
            continue

        # Collector versions before 2026-07-10 split one session across
        # daily date files (logs/<login>/<date>/<tool>__<sid>.jsonl) while
        # the manifest kept a single entry with the cumulative count and
        # the last day's file_path. Gather every file of this session so
        # the whole stream is validated together, the cumulative count is
        # compared against the aggregate, and sibling files are not
        # mis-flagged as orphans.
        session_files = {
            p for p in member_dir.glob("*/*.jsonl")
            if p.name == f"{tool}__{sid}.jsonl"
        }
        session_files.add(jsonl_path)
        session_files = sorted(session_files, key=lambda p: (p.parent.name, p.name))

        for sf in session_files:
            declared_files.add(sf.resolve())

        seq_state: dict[str, Any] = {"seen": {}, "last": -1}
        agg_count = 0
        for sf in session_files:
            fstats = validate_jsonl_file(
                sf, event_validator, team_id, github_login, tool, sid, report,
                seq_state=seq_state,
            )
            agg_count += fstats["count"]

        declared_count = session.get("event_count", -1)
        if declared_count != agg_count:
            report.err(
                f"{manifest_path}: session[{idx}] {sid!r}: event_count mismatch: "
                f"manifest={declared_count}, actual={agg_count} "
                f"(aggregated across {len(session_files)} file(s))"
            )

    for jsonl_path in member_dir.rglob("*.jsonl"):
        if jsonl_path.resolve() not in declared_files:
            report.warn(
                f"{jsonl_path}: orphan file (exists but not in manifest.sessions)"
            )


def validate_manifest(
    logs_dir: Path,
    manifest_validator: Draft7Validator,
    event_validator: Draft7Validator,
    report: Report,
) -> None:
    # Two invocation modes are supported:
    #   1. logs_dir = <repo>/logs/        → iterate member subdirs
    #   2. logs_dir = <repo>/logs/<login> → validate that single member only
    if (logs_dir / "manifest.json").exists():
        repo_root = logs_dir.parent.parent
        _validate_one_member(logs_dir, repo_root, manifest_validator, event_validator, report)
        return

    repo_root = logs_dir.parent
    member_dirs = [d for d in logs_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
    if not member_dirs:
        report.err(f"{logs_dir}: no member directories with manifest.json found")
        return
    for member_dir in sorted(member_dirs):
        _validate_one_member(member_dir, repo_root, manifest_validator, event_validator, report)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate contest session log directory"
    )
    parser.add_argument(
        "logs_dir",
        help="Path to demo repo logs/ directory",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Only report errors, not warnings"
    )
    args = parser.parse_args()

    logs_dir = Path(args.logs_dir).resolve()
    if not logs_dir.is_dir():
        print(f"ERROR: not a directory: {logs_dir}", file=sys.stderr)
        return 3

    event_schema = load_schema(EVENT_SCHEMA_PATH)
    manifest_schema = load_schema(MANIFEST_SCHEMA_PATH)
    event_validator = Draft7Validator(event_schema)
    manifest_validator = Draft7Validator(manifest_schema)

    report = Report()
    validate_manifest(logs_dir, manifest_validator, event_validator, report)

    print(f"=== contest-log-upload validation ===")
    print(f"Logs dir:       {logs_dir}")
    print(f"Files checked:  {report.files_checked}")
    print(f"Events checked: {report.events_checked}")

    if report.errors:
        print(f"\n❌ ERRORS ({len(report.errors)}):")
        for e in report.errors:
            print(f"  • {e}")

    if report.warnings and not args.quiet:
        print(f"\n⚠️  WARNINGS ({len(report.warnings)}):")
        for w in report.warnings:
            print(f"  • {w}")

    if report.has_errors:
        print("\n❌ FAILED")
        return 1
    if report.has_warnings:
        print("\n⚠️  PASSED WITH WARNINGS")
        return 2
    print("\n✅ ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
