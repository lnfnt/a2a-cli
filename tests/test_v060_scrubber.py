"""v0.6.0 §1 (C-05): scrubber на всех каналах — kv/yaml паттерны + redact-режим
с аудит-событием на mail subject/body, mail answer, rpc question/answer,
audit.details, supersede reason, task spec body, journal.

Политика совместимости: legacy высокоэнтропийные токены на hard-reject каналах
(mail/think/user) отвергаются как раньше; НОВЫЕ паттерны (key=value, yaml,
hex-ключ) редактируются в [REDACTED] + событие secret_redacted в audit —
доставка не ломается.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from a2a import auditlog, scrubber, util

REPO = str(Path(__file__).resolve().parent.parent)

KV_VAL = "Sup3rKeyValUE999"      # value для key=... (не совпадает с legacy-паттернами)
YAML_VAL = "Hunt3r2Passw0rd"     # value для key: ...
HEX32 = "0123456789abcdef0123456789abcdef"


def _audit_events(home: Path) -> str:
    p = home / ".state" / "audit.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------------------------------------------------------------- unit: patterns
def test_kv_pattern_catches_key_value():
    dirty, hits = scrubber.check("db config api_key=" + KV_VAL + " ready")
    assert dirty and "kv_secret" in hits
    out, names = scrubber.redact("db config api_key=" + KV_VAL + " ready")
    assert KV_VAL not in out and "[REDACTED:" in out


def test_yaml_pattern_catches_colon_form():
    dirty, hits = scrubber.check("password: " + YAML_VAL)
    assert dirty and "yaml_secret" in hits
    out, _ = scrubber.redact("password: " + YAML_VAL)
    assert YAML_VAL not in out


def test_hex32_pattern_catches_keyed_hex_only():
    dirty, hits = scrubber.check("rotated key: " + HEX32)
    assert dirty and "hex_key_32" in hits
    # голый sha256/id без ключа-префикса не ловим (не ломает front-matter и proofs)
    assert scrubber.check("id 017" + "a" * 29)[0] is False


def test_redact_preserves_legacy_marker_order():
    # sk- сначала, потом kv_secret не должен съесть [REDACTED...]-маркер
    text = "key=sk-" + "T" * 24
    out, hits = scrubber.redact(text)
    assert "[REDACTED:API_KEY_SK]" in out
    assert "sk-" + "T" * 24 not in out


# ---------------------------------------------------------------- mail channels
def test_mail_subject_kv_redacted_and_delivered(run, home, other):
    code, out, _ = run("mail", "send", "--to", "other",
                       "--subject", f"deploy api_key={KV_VAL}", "--body", "plain text")
    assert code == 0, out
    inbox_files = list((home / "mail" / other / "inbox").glob("*.md"))
    assert len(inbox_files) == 1
    text = inbox_files[0].read_text(encoding="utf-8")
    assert KV_VAL not in text and "[REDACTED:" in text
    assert "secret_redacted" in _audit_events(home)


def test_mail_body_yaml_redacted_and_delivered(run, home, other):
    code, out, _ = run("mail", "send", "--to", "other", "--subject", "cfg",
                       "--body", f"password: {YAML_VAL}")
    assert code == 0, out
    text = list((home / "mail" / other / "inbox").glob("*.md"))[0].read_text(encoding="utf-8")
    assert YAML_VAL not in text and "[REDACTED:" in text


def test_mail_body_hex32_redacted(run, home, other):
    code, out, _ = run("mail", "send", "--to", "other", "--subject", "rotated",
                       "--body", f"old token={HEX32}")
    assert code == 0, out
    text = list((home / "mail" / other / "inbox").glob("*.md"))[0].read_text(encoding="utf-8")
    assert HEX32 not in text


def test_mail_legacy_token_still_rejected(run, home, other):
    # обратная совместимость (v0.4.10 контракт): legacy sk- на mail = reject
    fake = "sk-" + "M" * 24
    code, out, _ = run("mail", "send", "--to", "other", "--subject", "s",
                       "--body", f"secret {fake}", check=False)
    assert code == 1
    assert out["reason"] == "secret_detected"


# ---------------------------------------------------------------- mail answer
def test_mail_answer_kv_secret_redacted_in_reply(run, home, profile, other):
    code, sent, _ = run("mail", "send", "--to", other, "--subject", "question", "--body", "what?")
    assert code == 0
    mid = sent["data"]["id"]
    code, out, _ = run("mail", "answer", mid, f"api_key={KV_VAL} in reply",
                       profile_override=other)
    assert code == 0, out
    # письмо-ответ лежит в inbox автора (profile)
    reply = list((home / "mail" / profile / "inbox").glob("*.md"))[0].read_text(encoding="utf-8")
    assert KV_VAL not in reply and "[REDACTED:" in reply
    assert "secret_redacted" in _audit_events(home)


def test_mail_answer_legacy_secret_still_refused(run, home, profile, other):
    _, sent, _ = run("mail", "send", "--to", other, "--subject", "q2", "--body", "what?")
    mid = sent["data"]["id"]
    fake = "sk-" + "M" * 24
    code, out, _ = run("mail", "answer", mid, f"leak {fake}", profile_override=other, check=False)
    assert code != 0
    assert out["reason"] == "secret_detected_in_reply"


# ---------------------------------------------------------------- rpc channels
def test_rpc_question_kv_secret_redacted_in_request(run, home):
    code, out, _ = run("ask", "--to", "other", f"token={KV_VAL} ?",
                       "--timeout", "1", check=False)
    assert code == 124  # ask_timeout — но файл запроса уже чистый
    reqs = list((home / "rpc" / "requests").glob("*.json"))
    assert len(reqs) == 1
    text = reqs[0].read_text(encoding="utf-8")
    assert KV_VAL not in text and "[REDACTED:" in text
    assert "secret_redacted" in _audit_events(home)


def test_rpc_answer_secret_redacted_in_reply(run, home, tmp_path):
    # e2e: ask крутится в отдельном процессе; ответ пишет тест, подсмотрев call_id
    env = os.environ.copy()
    env["A2A_HOME"] = str(home)
    env["A2A_PROFILE"] = "tester"
    proc = subprocess.Popen(
        [sys.executable, "-m", "a2a.cli", "ask", "--to", "other",
         "plain question", "--timeout", "8", "--json"],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    req_file = None
    deadline = time.time() + 6
    while time.time() < deadline:
        reqs = list((home / "rpc" / "requests").glob("*.json"))
        if reqs:
            req_file = reqs[0]
            break
        time.sleep(0.1)
    assert req_file is not None, "rpc request file never appeared"
    req = json.loads(req_file.read_text(encoding="utf-8"))
    resp_dir = home / "rpc" / "responses"
    resp_dir.mkdir(parents=True, exist_ok=True)
    (resp_dir / f"{req['id']}.json").write_text(
        json.dumps({"call_id": req["id"], "from": "other",
                    "answer": f"here you go: api_key={KV_VAL}"}) + "\n", encoding="utf-8")
    stdout, _ = proc.communicate(timeout=15)
    assert proc.returncode == 0, stdout
    answer = json.loads(stdout)["data"]["answer"]
    assert KV_VAL not in answer and "[REDACTED:" in answer


# ---------------------------------------------------------------- audit details
def test_audit_log_details_redacted(tmp_path):
    old_home = util.HOME
    util.HOME = tmp_path
    try:
        auditlog.log("probe_event", "caller", "target", {"note": f"password: {YAML_VAL}"})
        line = (tmp_path / ".state" / "audit.log").read_text(encoding="utf-8")
        assert YAML_VAL not in line and "[REDACTED:" in line
    finally:
        util.HOME = old_home


# ---------------------------------------------------------------- supersede reason
def test_supersede_reason_yaml_secret_redacted(run, home, make_artifact):
    art = make_artifact("sup.txt", "v1")
    _, lg, _ = run("log", "artifact attached", "--artifacts", str(art))
    jid = lg["data"]["id"]
    art.write_text("v2 changed", encoding="utf-8")
    code, out, _ = run("verify", "--supersede", jid, "--artifact", str(art),
                       "--reason", f"rotated secret: {YAML_VAL}")
    assert code == 0, out
    jfiles = list((home / "journal").rglob(f"*_{jid}.md"))
    text = jfiles[0].read_text(encoding="utf-8")
    assert YAML_VAL not in text and "[REDACTED:" in text


# ---------------------------------------------------------------- task spec
def test_task_spec_kv_secret_redacted(run, home):
    code, out, _ = run("task", "new", f"Release api_key={KV_VAL}",
                       "--goal", f"password: {YAML_VAL}")
    assert code == 0, out
    tid = out["data"]["id"]
    text = (home / "tasks" / f"{tid}.md").read_text(encoding="utf-8")
    assert KV_VAL not in text and YAML_VAL not in text and "[REDACTED:" in text
    assert "secret_redacted" in _audit_events(home)


def test_task_pem_still_hard_rejected(run, home):
    code, out, _ = run("task", "new", "job", "--goal",
                       "-----BEGIN RSA PRIVATE KEY----- leak", check=False)
    assert code == 1
    assert out["reason"] == "secret_detected"


# ---------------------------------------------------------------- journal redact audit
def test_journal_kv_redact_emits_audit_event(run, home):
    code, out, _ = run("log", f"rotated api_key={KV_VAL} in journal")
    assert code == 0, out
    assert "kv_secret" in out["data"].get("scrubbed", "")
    files = list((home / "journal").rglob("*.md"))
    text = files[0].read_text(encoding="utf-8")
    assert KV_VAL not in text and "[REDACTED:" in text
    assert "secret_redacted" in _audit_events(home)
