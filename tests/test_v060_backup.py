"""v0.6.0 §5 (G-06): `a2a backup [--dest DIR]` — tar.gz шины (без .state) + SHA256SUMS
внутри, имя дня (идемпотентно); `a2a backup --restore FILE [--yes]` — dry-run по
умолчанию со списком конфликтов; не затирает файлы, чей контент differs И новее."""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import time
from pathlib import Path


def _make_bus(run, home, other):
    _, ms, _ = run("mail", "send", "--to", other, "--subject", "backup letter", "--body", "text one")
    mid = ms["data"]["id"]
    _, lg, _ = run("log", "backup journal entry")
    _, tk, _ = run("task", "new", "backup task")
    return mid, lg["data"]["id"], tk["data"]["id"]


def test_backup_creates_daily_archive_with_sums(run, home, other):
    mid, jid, tid = _make_bus(run, home, other)
    code, out, _ = run("backup")
    assert code == 0, out
    arch = Path(out["data"]["archive"])
    assert arch.exists() and arch.name.endswith(".tar.gz")
    with tarfile.open(arch, "r:gz") as tf:
        names = tf.getnames()
        assert "SHA256SUMS.txt" in names
        assert any(n.startswith("journal/") for n in names)
        assert any(n.startswith("mail/") for n in names)
        assert any(n.startswith("tasks/") for n in names)
        assert not any(n.startswith(".state") for n in names)
        sums = tf.extractfile("SHA256SUMS.txt").read().decode()
        jn = next(n for n in names if n.startswith("journal/"))
        want = hashlib.sha256(tf.extractfile(jn).read()).hexdigest()
        assert f"{want}  {jn}" in sums
    # идемпотентно по имени файла дня: повторная запись — тот же путь
    code2, out2, _ = run("backup")
    assert Path(out2["data"]["archive"]) == arch
    assert len(list(arch.parent.glob("*.tar.gz"))) == 1


def test_backup_dest_override(run, home, other, tmp_path):
    _make_bus(run, home, other)
    dest = tmp_path / "off-bus"
    code, out, _ = run("backup", "--dest", str(dest))
    assert code == 0, out
    assert Path(out["data"]["archive"]).parent == dest


def test_restore_dry_run_by_default_lists_conflicts(run, home, other):
    mid, jid, tid = _make_bus(run, home, other)
    _, b, _ = run("backup")
    arch = b["data"]["archive"]
    # конфликт: локальная версия отличается И новее
    jfile = list((home / "journal").rglob(f"*_{jid}.md"))[0]
    jfile.write_text(jfile.read_text(encoding="utf-8") + "\nlocal edit NEWER\n", encoding="utf-8")
    future = time.time() + 300
    os.utime(jfile, (future, future))
    # законно удалённый файл — попадёт в would_extract
    tfile = home / "tasks" / f"{tid}.md"
    tfile.unlink()
    code, out, _ = run("backup", "--restore", arch)
    assert code == 0, out
    d = out["data"]
    assert d["dry_run"] is True
    assert any(jid in c["path"] or c["path"].endswith(jfile.name) for c in d["conflicts"])
    assert d["would_extract"] >= 1
    # dry-run ничего не меняет на диске
    assert tfile.exists() is False
    assert "NEWER" in jfile.read_text(encoding="utf-8")


def test_restore_yes_skips_conflicts_never_overwrites_newer(run, home, other):
    mid, jid, tid = _make_bus(run, home, other)
    _, b, _ = run("backup")
    arch = b["data"]["archive"]
    jfile = list((home / "journal").rglob(f"*_{jid}.md"))[0]
    jfile.write_text(jfile.read_text(encoding="utf-8") + "\nlocal edit NEWER\n", encoding="utf-8")
    future = time.time() + 300
    os.utime(jfile, (future, future))
    mfile = list((home / "mail" / other / "inbox").glob(f"*_{mid}.md"))[0]
    mfile.unlink()
    code, out, _ = run("backup", "--restore", arch, "--yes")
    assert code == 0, out
    d = out["data"]
    assert d["dry_run"] is False
    assert d["restored"] >= 1
    assert mfile.exists()  # удалённое письмо восстановлено
    assert "NEWER" in jfile.read_text(encoding="utf-8")  # конфликт НЕ затёрт
    assert d["conflicts"]


def test_restore_rejects_path_traversal(run, home, tmp_path):
    evil = tmp_path / "evil-src.txt"
    evil.write_text("x", encoding="utf-8")
    arch = tmp_path / "malicious.tar.gz"
    with tarfile.open(arch, "w:gz") as tf:
        tf.add(evil, arcname="../escaped-evil.txt")
    code, out, _ = run("backup", "--restore", str(arch), "--yes", check=False)
    assert not (home.parent / "escaped-evil.txt").exists()
    assert code == 0
    assert any("escaped-evil" in u for u in out["data"]["unsafe"])


def test_restore_missing_file_usage_error(run, tmp_path):
    code, out, _ = run("backup", "--restore", str(tmp_path / "nope.tar.gz"), check=False)
    assert code == 4
    assert out.get("reason") == "backup_not_found"
