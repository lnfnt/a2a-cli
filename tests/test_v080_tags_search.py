"""v0.8.0 §3: теги записей + фильтры поиска."""
from __future__ import annotations


def _reindex(run):
    run("index", "--increment")


def test_tag_and_search_by_tag(run, home, profile):
    code, out, _ = run("log", "запись про метки", "--numbers-ok", "--status", "ok",
                       "--tags", "недвижимость,тест")
    assert code == 0, out
    _reindex(run)
    code, s, _ = run("search", "метки", "--tag", "тест")
    assert code == 0
    assert any("метки" in str(r) for r in s["data"]["results"]), s["data"]


def test_search_author_filter(run, home, profile, other):
    run("log", "от автора A", "--numbers-ok", "--status", "ok", profile_override=profile)
    run("log", "от автора B", "--numbers-ok", "--status", "ok", profile_override=other)
    _reindex(run)
    code, s, _ = run("search", "от", "--author", other)
    assert code == 0
    hits = s["data"]["results"]
    assert all("B" in str(r.get("body", r.get("summary", r))) or r.get("author") == other for r in hits)
    assert any(r.get("author") == other for r in hits), hits


def test_search_since_filter(run, home, profile):
    code, out, _ = run("log", "свежее число 1", "--numbers-ok", "--status", "ok")
    assert code == 0
    _reindex(run)
    code, s, _ = run("search", "свежее", "--since", "7d")
    assert code == 0 and s["data"]["results"]
    code, s2, _ = run("search", "свежее", "--since", "2020-01-01", "--author", "nosuch")
    assert code == 0 and not s2["data"]["results"]


def test_search_kind_limits(run, home, profile):
    code, out, _ = run("mail", "send", "--to", "user-profile", "--subject", "уникальноеКсилофон",
                       "--body", "тело", profile_override=profile)
    assert code == 0, out
    _reindex(run)
    code, s, _ = run("search", "уникальноеКсилофон", "--kind", "journal")
    assert code == 0
    assert not [r for r in s["data"]["results"] if "Ксилофон" in str(r) and r.get("type") == "mail"], s
    code, s2, _ = run("search", "уникальноеКсилофон", "--kind", "mail")
    assert s2["data"]["results"], "kind=mail must find the letter"


def test_tags_on_mail_record(run, home, profile, other):
    code, out, _ = run("mail", "send", "--to", other, "--subject", "метка-письмо",
                       "--body", "b", "--tags", "срочное", profile_override=profile)
    assert code == 0, out
    files = list((home / "mail" / other / "inbox").glob("*.md"))
    assert files
    txt = files[-1].read_text(encoding="utf-8")
    assert "срочное" in txt


def test_search_task_ref_filter(run, home, profile):
    code, out, _ = run("task", "new", "таск-реф", "--owner", profile, "--goal", "g")
    tid = out["data"]["id"]
    code, j, _ = run("log", "работа по задаче", "--numbers-ok", "--status", "ok")
    jid = j["data"]["id"]
    code, lk, _ = run("task", "link", tid, "--ref", jid)
    assert code == 0, lk
    _reindex(run)
    code, s, _ = run("search", "работа", "--task", tid)
    assert code == 0
    assert s["data"]["results"], "journal referencing task id must be found by --task"
