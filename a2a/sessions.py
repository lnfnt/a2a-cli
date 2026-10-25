"""a2a.session — P1-8 event sourcing сессий (паттерн DeepSeek Harness).

Append-only JSONL: shared_workspace/journal/sessions/<YYYY-MM-DD>/<profile>-<session>.jsonl
События: session_start, user_msg, tool_call, tool_result, model_msg, decision, session_end.
Каждое событие: {seq, ts, profile, session, type, data, truncated, scrubbed}.
- seq монотонный в рамках файла; запись одной строки O_APPEND+fsync — при kill -9
  последняя запись либо полная, либо её нет (приёмка плана).
- data проходит scrub_echo (redact-and-write; unredactable — suppression), большие
  payload'ы урезаются до MAX_DATA с флагом truncated.
- Доступ = тот же A2A_HOME, что у journal.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

from . import scrubber, util

EVENT_TYPES = {"session_start", "user_msg", "tool_call", "tool_result",
               "model_msg", "decision", "session_end", "note"}
MAX_DATA = 4096


def session_file(session: str, profile: str, when=None) -> Path:
    dt = when or util.now_utc()
    return (util.resolve_home() / "journal" / "sessions" / dt.strftime("%Y-%m-%d") /
            f"{profile}-{session}.jsonl")


def _append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _next_seq(path: Path) -> int:
    if not path.exists():
        return 1
    last = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            last = max(last, int(json.loads(raw)["seq"]))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return last + 1


def emit(profile: str, session: str, etype: str, data: Any) -> dict:
    if etype not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {etype}")
    path = session_file(session, profile)
    raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    truncated = len(raw) > MAX_DATA
    clean = scrubber.scrub_echo(raw[: MAX_DATA * 2], cap=MAX_DATA)
    event = {"seq": _next_seq(path), "ts": util.utc_iso(), "profile": profile,
             "session": session, "type": etype, "data": clean,
             "truncated": truncated, "scrubbed": clean != raw[:len(clean)]}
    _append_event(path, event)
    return event


def replay_lines(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            raise ValueError(f"corrupt line in {path.name}: {raw[:60]!r}")
    return out


def check_integrity(path: Path) -> tuple[bool, str]:
    try:
        events = replay_lines(path)
    except ValueError as exc:
        return False, str(exc)
    if not events:
        return True, "empty session log"
    seqs = [e["seq"] for e in events]
    if seqs != list(range(1, len(seqs) + 1)):
        return False, f"seq не монотонный без пропусков: {seqs}"
    types = {e["type"] for e in events}
    if "session_start" not in types:
        return False, "нет session_start"
    return True, f"{len(events)} событий, seq 1..{seqs[-1]}, start ok"


# --- CLI handlers -------------------------------------------------------------

def cmd_session(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    action = args.session_action
    session = args.session_id or "dev"

    if action == "start":
        ev = emit(profile, session, "session_start", {"note": args.note or ""})
        util.ok({"file": str(session_file(session, profile)), "event": ev},
                elapsed_ms=int((time.time() - start) * 1000))
    elif action == "end":
        ev = emit(profile, session, "session_end", {"note": args.note or ""})
        util.ok({"event": ev}, elapsed_ms=int((time.time() - start) * 1000))
    elif action == "event":
        data = args.data
        if data is None and not sys.stdin.isatty():
            data = sys.stdin.read()
        ev = emit(profile, session, args.type, data or "")
        util.ok({"event": ev}, elapsed_ms=int((time.time() - start) * 1000))
    elif action == "wrap":
        # a2a session wrap --session ID --cmd "<command...>": обёртка без правки чужого рантайма
        import shlex
        cmd = shlex.split(args.cmd or "")
        if not cmd:
            util.fail(util.EXIT_USAGE_ERROR, "wrap_requires_command")
        emit(profile, session, "tool_call", {"argv": cmd})
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
            rc = proc.returncode
            emit(profile, session, "tool_result",
                 {"rc": rc, "stdout": proc.stdout, "stderr": proc.stderr,
                  "elapsed_s": round(time.time() - t0, 2)})
        except subprocess.TimeoutExpired:
            rc = 124
            emit(profile, session, "tool_result", {"rc": rc, "note": "timeout"})
        util.ok({"rc": rc, "session": session}, elapsed_ms=int((time.time() - start) * 1000))
    else:
        util.fail(util.EXIT_USAGE_ERROR, "unknown session action")


def cmd_replay(args: Namespace) -> None:
    start = time.time()
    path = Path(args.session_file)
    if not path.exists():
        util.fail(util.EXIT_ERROR, "session file not found")
    if args.check:
        ok, msg = check_integrity(path)
        if ok:
            util.ok({"check": "PASS", "detail": msg},
                    elapsed_ms=int((time.time() - start) * 1000))
        util.fail(util.EXIT_ERROR, "integrity", {"detail": msg})
    events = replay_lines(path)
    lines = []
    for e in events:
        lines.append(f"#{e['seq']} {e['ts']} {e['type']}"
                     + (" [TRUNC]" if e.get("truncated") else "")
                     + (" [SCRUB]" if e.get("scrubbed") else "")
                     + f" | {str(e['data'])[:200]}")
    util.ok({"events": len(events), "replay": lines},
            elapsed_ms=int((time.time() - start) * 1000))
