"""v0.4.1 features: fsync-atomic writes, monotonic journal seq, session binding."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from a2a import journal, util


def test_write_atomically_fsyncs_file_and_dir(tmp_path, monkeypatch):
    calls = []
    real_fsync = util.os.fsync

    def spy(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(util.os, "fsync", spy)
    target = tmp_path / "nested" / "rec.md"
    util.write_atomically(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    # минимум 2 fsync: файл + каталог
    assert len(calls) >= 2, f"fsync не вызван для файла/каталога: {calls}"
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_write_atomically_leaves_no_half_file_on_error(tmp_path, monkeypatch):
    class Boom(Exception):
        pass

    def fake_replace(src, dst):
        raise Boom("power cut simulation")

    monkeypatch.setattr(util.os, "replace", fake_replace)
    target = tmp_path / "x.md"
    try:
        util.write_atomically(target, "data")
    except Boom:
        pass
    assert not target.exists(), "целевой файл не должен появиться при сбое rename"
    assert list(tmp_path.glob("x.md.*.tmp")), "tmp переживает сбой для диагностики (v0.4.9: имя уникально)"


def test_journal_seq_monotonic_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "HOME", tmp_path)
    (tmp_path / ".state").mkdir(parents=True, exist_ok=True)

    def writer(_):
        return journal._next_journal_seq()

    with ThreadPoolExecutor(max_workers=8) as pool:
        seqs = sorted(pool.map(writer, range(32)))
    assert seqs == list(range(1, 33)), f"дыры/дубли в seq: {seqs}"


def test_journal_seq_persists_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "HOME", tmp_path)
    first = journal._next_journal_seq()
    second = journal._next_journal_seq()
    assert second == first + 1
    assert (tmp_path / ".state" / "journal_seq").read_text().strip() == str(second)


def test_log_session_and_filters(run, home):
    code, out, _ = run("log", "session test entry one", "--session", "sess-alpha")
    assert code == 0, out
    assert out["data"]["session"] == "sess-alpha"
    assert isinstance(out["data"]["seq"], int)

    code, out2, _ = run("log", "session test entry two", "--session", "sess-alpha")
    assert code == 0
    assert out2["data"]["seq"] == out["data"]["seq"] + 1

    code, out3, _ = run("log", "no session here")
    assert code == 0

    # фильтр по сессии
    code, listing, _ = run("journal", "--session", "sess-alpha")
    assert code == 0
    assert listing["data"]["count"] == 2
    assert all(e["session"] == "sess-alpha" for e in listing["data"]["entries"])
    assert listing["data"]["entries"][0]["seq"] < listing["data"]["entries"][1]["seq"]

    # инкрементальный sync: только записи после первой
    code, tail, _ = run("journal", "--min-seq", str(out["data"]["seq"] + 1))
    assert code == 0
    seqs = [e["seq"] for e in tail["data"]["entries"]]
    assert out["data"]["seq"] not in seqs
    assert out2["data"]["seq"] in seqs
