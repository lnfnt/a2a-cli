"""v0.6.0 §7 (G3-pisma): poka-yoke на mail send — числа в body/subject при
--priority today без проганной --verify = numbers_without_verify, точно как
журнал; --numbers-ok выход; fyi без цифр не затронут; канал ответов не душим
(унаследованные из оригинала клеймы не блокируют reply)."""
from __future__ import annotations

import json


def _send(run, *args, **kw):
    return run("mail", "send", "--to", "other", *args, **kw)


def test_today_numbers_blocked_without_verify(run, home, other):
    code, out, _ = _send(run, "--subject", "статус", "--body", "исправлено 2007 тестов",
                        "--priority", "today", check=False)
    assert code == 1
    assert out["reason"] == "numbers_without_verify"
    assert any("2007" in h for h in out["data"]["hits"])
    # доставка не состоялась
    assert not list((home / "mail" / other / "inbox").glob("*.md"))


def test_numbers_ok_bypass(run, home, other):
    code, out, _ = _send(run, "--subject", "статус", "--body", "исправлено 2007 тестов",
                        "--priority", "today", "--numbers-ok")
    assert code == 0, out


def test_verify_run_bypass(run, home, other, tmp_path):
    marker = tmp_path / "m"
    marker.write_text("x")
    code, out, _ = _send(run, "--subject", "статус", "--body", "закрыто 350 багов",
                        "--priority", "today", "--verify", f"test -s {marker}", "--verify-run")
    assert code == 0, out


def test_verify_without_run_does_not_bypass_like_journal(run, home, other):
    # журнал: обход только при verify И verify_run — идентичная семантика
    code, out, _ = _send(run, "--subject", "статус", "--body", "закрыто 350 багов",
                        "--priority", "today", "--verify", "echo hi", check=False)
    assert code == 1
    assert out["reason"] == "numbers_without_verify"


def test_fyi_with_numbers_unaffected(run, home, other):
    code, out, _ = _send(run, "--subject", "репорт", "--body", "исправлено 2007 тестов")
    assert code == 0, out


def test_today_subject_claim_gated(run, home, other):
    code, out, _ = _send(run, "--subject", "починено 4888 ошибок", "--body", "детали в задаче",
                        "--priority", "today", check=False)
    assert code == 1
    assert out["reason"] == "numbers_without_verify"


def test_verify_failure_still_delivers_but_marked(run, home, other):
    # как журнал: VERIFY_FAILED пишется честно, канал не затыкается
    code, out, _ = _send(run, "--subject", "s", "--body", "исправлено 2007 тестов",
                        "--priority", "today", "--verify", "false", "--verify-run")
    assert code == 0, out
    assert out["data"]["verify_status"] == "VERIFY_FAILED"
    files = list((home / "mail" / other / "inbox").glob("*.md"))
    assert files
    text = files[0].read_text(encoding="utf-8")
    assert "VERIFY_FAILED" in text


def test_plain_today_mail_unaffected(run, home, other):
    code, out, _ = _send(run, "--subject", "план", "--body", "обсудим завтра", "--priority", "today")
    assert code == 0, out


def test_answer_with_numbers_gated_and_bypass(run, home, profile, other):
    _, ms, _ = _send(run, "--subject", "вопрос", "--body", "сколько тестов?")
    mid = ms["data"]["id"]
    code, out, _ = run("mail", "answer", mid, "исправлено 2007 тестов",
                       profile_override=other, check=False)
    assert code == 1
    assert out["reason"] == "numbers_without_verify"
    code2, out2, _ = run("mail", "answer", mid, "исправлено 2007 тестов",
                         "--numbers-ok", profile_override=other)
    assert code2 == 0, out2


def test_reply_inherits_subject_claims_without_blocking(run, home, profile, other):
    # автор пишет числами (осознанно --numbers-ok), адресат отвечает ЧИСТО —
    # унаследованный subject-клейм не должен душить ответ
    _, ms, _ = _send(run, "--subject", "починено 2007 тестов", "--body", "обсудим",
                     "--priority", "today", "--numbers-ok")
    mid = ms["data"]["id"]
    code, out, _ = run("mail", "answer", mid, "ок, принял в работу",
                       profile_override=other)
    assert code == 0, out
