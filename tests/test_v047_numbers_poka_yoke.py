"""v0.4.7 poka-yoke: числа без проганной verify-команды в журнал не пишутся.
Норма lnfnt 2026-10-21: система не даёт ошибиться, иначе как умышленно."""

from __future__ import annotations

from a2a.journal import number_claims


def test_number_claims_unit():
    assert number_claims("исправлено 2007 тестов") == ["2007 тестов"]
    assert number_claims("588 коммитов в журнале") == ["588 коммитов"]
    assert number_claims("релиз v0.4.6, баг P0-3, лог 12:30, дата 17.10") == []
    assert number_claims("id synthetic-id sha256 1d73c8934") == []
    assert number_claims("198.51.100.30:3000") == []
    assert number_claims("93 заметки, 209 wikilinks") == ["93 заметки", "209 wikilinks"]
    assert number_claims("год 2026 — не клейм") == []
    assert number_claims("80% точность") == ["80%"]
    assert number_claims("обычная запись без чисел") == []


def test_log_blocked_without_verify(run):
    code, out, _ = run("log", "исправлено 2007 тестов", check=False)
    assert code != 0
    assert out.get("reason") == "numbers_without_verify"
    assert "2007 тестов" in out["data"]["hits"]


def test_log_allowed_with_run_verify(run, tmp_path):
    marker = tmp_path / "m"
    marker.write_text("x")
    code, out, _ = run("log", "исправлено 2007 тестов",
                       "--verify", f"test -s {marker}", "--verify-run")
    assert code == 0, out


def test_log_allowed_with_numbers_ok(run):
    code, out, _ = run("log", "закрыто 42 задачи", "--numbers-ok")
    assert code == 0, out


def test_log_version_strings_pass_freely(run):
    code, out, _ = run("log", "релиз v0.4.7: P0-3 починен, тесты зелёные, дата 17.10")
    assert code == 0, out
