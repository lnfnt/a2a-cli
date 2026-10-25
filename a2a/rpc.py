"""Synchronous inter-agent RPC (ask)."""

from __future__ import annotations

import json
import time
from argparse import Namespace

from . import cards as cards_mod
from . import auditlog, schema, scrubber, util


def cmd_ask(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    if not util.validate_profile_name(args.to):
        util.fail(util.EXIT_USAGE_ERROR, "invalid_profile_name")
    if profile == args.to:
        util.fail(util.EXIT_USAGE_ERROR, "self_ask_forbidden")

    # P1-7 enforcement (fail-closed при наличии каталога cards; kill-switch A2A_CARDS_ENFORCE=0)
    import os
    cdir = cards_mod.cards_dir()
    if os.environ.get("A2A_CARDS_ENFORCE", "1").strip() != "0" and cdir.is_dir():
        allowed, reason = cards_mod.gate_call(profile, "a2a.ask", args.to, auditlog)
        if not allowed:
            util.fail(util.EXIT_ERROR, "card_denied",
                      {"target": args.to, "reason": reason},
                      elapsed_ms=int((time.time() - start) * 1000))

    call_id = util.new_id()
    created = util.utc_iso()
    # v0.6.0 §1: rpc question — redact-канал (PEM/PGP/url-secret = reject,
    # остальное редактируется + аудит-событие).
    question = args.question or ""
    q_hits = scrubber.scan(question)
    if q_hits and set(q_hits) & scrubber.UNREDACTABLE:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, question, q_hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a ask --to other question", {"hits": q_hits})
    if q_hits:
        question, _ = scrubber.redact(question)
        auditlog.log("secret_redacted", profile, "rpc_question", {"hits": sorted(set(q_hits))})
    req = {
        "id": call_id,
        "from": profile,
        "to": args.to,
        "question": question,
        "timeout": args.timeout,
        "created_at": created,
    }
    ok, err = schema.validate_rpc_request(req)
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, err)

    req_dir = util.HOME / "rpc" / "requests"
    req_path = req_dir / f"{call_id}_{profile}_{args.to}.json"
    resp_path = util.HOME / "rpc" / "responses" / f"{call_id}.json"

    util.write_atomically(req_path, json.dumps(req, ensure_ascii=False, sort_keys=True) + "\n")

    # v0.4.6: ask будит целевой профиль — раньше запрос лежал в файле, а спящий
    # профиль не знал о нём никогда (главная причина ask_timeout «не отвечают»).
    # DND/busy-адресат не дёргается; timeout ask = lifetime wake-маркера.
    try:
        from . import mail as _mail
        _state = _mail._target_state(args.to)
        if _state not in ("dnd", "busy"):
            _mail._write_wake_marker(args.to, profile, call_id, created,
                                     int(args.timeout) + 60)
            _mail._trigger_wake_hook(args.to, call_id, profile)
    except Exception:
        pass  # wake — best effort; ask остаётся валидным (файл-запрос записан)

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if resp_path.exists():
            try:
                resp = json.loads(resp_path.read_text(encoding="utf-8"))
            except Exception:
                util.fail(util.EXIT_INVALID_RECORD, "invalid_rpc_response")
            ok, err = schema.validate_rpc_response(resp)
            if not ok:
                util.fail(util.EXIT_INVALID_RECORD, err)
            # v0.6.0 §1: rpc answer — никогда не эхомим секреты через ask.
            answer = resp["answer"]
            a_hits = scrubber.scan(answer)
            if a_hits:
                if set(a_hits) & scrubber.UNREDACTABLE:
                    answer = "[REDACTED:UNREDACTABLE_PAYLOAD]"
                else:
                    answer, _ = scrubber.redact(answer)
                auditlog.log("secret_redacted", resp.get("from", "rpc"), "rpc_answer",
                             {"hits": sorted(set(a_hits)), "call_id": resp["call_id"]})
            data = {"call_id": resp["call_id"], "from": resp["from"], "answer": answer}
            if getattr(args, "json", False):
                util.ok(data, elapsed_ms=int((time.time() - start) * 1000))
            else:
                print(answer)
            raise SystemExit(util.EXIT_OK)
        time.sleep(0.25)

    util.fail(util.EXIT_TIMEOUT, "ask_timeout", {"to": args.to}, elapsed_ms=int((time.time() - start) * 1000))
