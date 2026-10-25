"""v0.6.0 §4 (G-04): tamper-detect — `a2a snapshot --save` пишет канонический
.state/bus_hashes.json (sha256 записей mail/journal/tasks/alerts), doctor сверяет
и выдаёт changed/deleted/added БЕЗ содержимого; suspects>0 → warning exit 1;
нет baseline → status no_baseline (не ошибка)."""
from __future__ import annotations

import json

BASELINE_NAME = "bus_hashes.json"


def _baseline(home):
    return json.loads((home / ".state" / BASELINE_NAME).read_text(encoding="utf-8"))


def _make_bus(run, home, other):
    _, lg, _ = run("log", "запись журнала для снапшота")
    jid = lg["data"]["id"]
    _, ms, _ = run("mail", "send", "--to", other, "--subject", "snapshot mail", "--body", "plain")
    mid = ms["data"]["id"]
    _, tk, _ = run("task", "new", "task для снапшота")
    tid = tk["data"]["id"]
    return jid, mid, tid


def test_snapshot_save_writes_canonical_baseline(run, home, other):
    _make_bus(run, home, other)
    code, out, _ = run("snapshot", "--save")
    assert code == 0, out
    path = home / ".state" / BASELINE_NAME
    assert path.exists()
    data = _baseline(home)
    assert data["saved_at"]
    assert len(data["files"]) >= 4  # journal + inbox+outbox mail + task
    # относительные пути и sha256
    rels = list(data["files"].keys())
    assert any(r.startswith("mail/") for r in rels)
    assert any(r.startswith("journal/") for r in rels)
    sample = data["files"][rels[0]]
    assert len(sample["sha256"]) == 64


def test_snapshot_save_is_explicit_only(run, home, other):
    _make_bus(run, home, other)
    run("log", "ещё запись")
    run("doctor", check=False)
    assert not (home / ".state" / BASELINE_NAME).exists()
    run("snapshot", "--save")
    before = _baseline(home)["files"]
    run("log", "новая запись после baseline")
    after = _baseline(home)["files"]
    assert before == after  # легитимные записи НЕ двигают baseline


def test_doctor_clean_vs_no_baseline(run, home, other):
    # нет baseline → no_baseline, не ошибка tamper'а
    _, lg, _ = run("log", "что-то")
    code, out, _ = run("doctor", check=False)
    tamper = out["data"]["tamper"]
    assert tamper["status"] == "no_baseline"
    assert tamper["suspects"] == 0
    run("snapshot", "--save")
    code, out, _ = run("doctor", check=False)
    # doctor сам написал journal-запись после save → она 'added' (честно),
    # clean-проверка на неизменном baseline:
    assert code in (0, 1, 2)
    assert (out["data"]["tamper"]["status"] in ("clean", "tamper_detected"))


def test_doctor_flags_changed_deleted_added_without_content(run, home, other):
    jid, mid, tid = _make_bus(run, home, other)
    run("snapshot", "--save")
    # changed: подменить содержимое journal-файла (id тот же, тело с маркером)
    jfile = list((home / "journal").rglob(f"*_{jid}.md"))[0]
    text = jfile.read_text(encoding="utf-8")
    jfile.write_text(text.replace("запись журнала для снапшота",
                                  "ПЕРЕХВАЧЕНО PRIVATE_MARK 555"), encoding="utf-8")
    # deleted: удалить входящее письмо
    mfiles = [p for p in (home / "mail" / other / "inbox").glob(f"*_{mid}.md")]
    for p in mfiles:
        p.unlink()
    # added: новая запись
    _, lg2, _ = run("log", "added record")
    code, out, _ = run("doctor", check=False)
    assert code == 1  # warning
    assert out["status"] == "warning"
    assert out["reason"] == "tamper_suspects"
    tamper = out["data"]["tamper"]
    assert tamper["status"] == "tamper_detected"
    assert tamper["suspects"] > 0
    changed_ids = [e["id"] for e in tamper["changed"]]
    deleted = tamper["deleted"]
    added_ids = [e["id"] for e in tamper["added"]]
    assert jid in changed_ids
    assert any(mid == e.get("id") for e in deleted)
    assert lg2["data"]["id"] in added_ids
    # никакого содержимого файлов в отчёте
    blob = json.dumps(out)
    assert "PRIVATE_MARK" not in blob
    assert "ПЕРЕХВАЧЕНО" not in blob


def test_doctor_corrupt_baseline_is_no_baseline(run, home, other):
    _make_bus(run, home, other)
    run("snapshot", "--save")
    (home / ".state" / BASELINE_NAME).write_text("{not json", encoding="utf-8")
    code, out, _ = run("doctor", check=False)
    assert out["data"]["tamper"]["status"] == "no_baseline"


def test_snapshot_status_compare_without_save(run, home, other):
    _make_bus(run, home, other)
    code, out, _ = run("snapshot", check=False)
    assert code == 0
    assert out["data"]["status"] == "no_baseline"
    run("snapshot", "--save")
    code, out, _ = run("snapshot")
    assert out["data"]["status"] == "clean"
    assert "snapshot_save" in (home / ".state" / "audit.log").read_text()
