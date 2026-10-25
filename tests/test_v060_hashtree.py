"""v0.6.0 §8 (B-02): _artifact_proofs — каталог получает ПОЛНОЕ хэш-дерево
(рекурсивно, лимиты 500 файлов / 100MB, превышение = честный tree_overflow с
частичным списком); файл остаётся sha256. Существующие verify-контракты не
сужены: каталоги по-прежнему не хэшируются файловым сверщиком."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from a2a.journal import _artifact_proofs

MAX_FILES = 500
MAX_BYTES = 100 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_directory_proof_contains_full_hash_tree(tmp_path):
    d = tmp_path / "repo"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("alpha", encoding="utf-8")
    (d / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    (d / "c.bin").write_bytes(b"\x00\x01\x02")
    p = _artifact_proofs([str(d)])[0]
    tree = p["tree"]
    assert tree["status"] == "ok"
    assert tree["truncated"] is False
    rels = [f["path"] for f in tree["files"]]
    assert rels == ["a.txt", "c.bin", os.path.join("sub", "b.txt")]
    by_rel = {f["path"]: f["sha256"] for f in tree["files"]}
    assert by_rel["a.txt"] == _sha(b"alpha")
    assert by_rel["c.bin"] == _sha(b"\x00\x01\x02")
    # контракт v0.4.8 сохранён: sha256 None, note directory, exists True
    assert p["exists"] is True and p["sha256"] is None and p["sha256_note"] == "directory"


def test_tree_sha_changes_with_content(tmp_path):
    d = tmp_path / "repo2"
    d.mkdir()
    (d / "f.txt").write_text("one", encoding="utf-8")
    t1 = _artifact_proofs([str(d)])[0]["tree"]
    (d / "f.txt").write_text("two", encoding="utf-8")
    t2 = _artifact_proofs([str(d)])[0]["tree"]
    assert t1["tree_sha256"] != t2["tree_sha256"]
    # детерминированность: повторный расчёт одинаков
    t3 = _artifact_proofs([str(d)])[0]["tree"]
    assert t3["tree_sha256"] == t2["tree_sha256"]


def test_tree_overflow_by_file_count_partial_list(tmp_path):
    d = tmp_path / "many"
    d.mkdir()
    for i in range(MAX_FILES + 3):
        (d / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
    tree = _artifact_proofs([str(d)])[0]["tree"]
    assert tree["status"] == "tree_overflow"
    assert tree["truncated"] is True
    assert len(tree["files"]) == MAX_FILES  # частичный список, не пустой


def test_tree_overflow_by_total_size(tmp_path):
    d = tmp_path / "sparse"
    d.mkdir()
    big = d / "huge.bin"
    with open(big, "wb") as fh:
        fh.truncate(MAX_BYTES + 1024)  # sparse: размер есть, данных нет
    # сортировка по relpath: aaa-мелкий попадает в дерево до упора в лимит
    (d / "aaa-small.txt").write_text("ok", encoding="utf-8")
    tree = _artifact_proofs([str(d)])[0]["tree"]
    assert tree["status"] == "tree_overflow"
    assert tree["truncated"] is True
    assert [f["path"] for f in tree["files"]] == ["aaa-small.txt"]
    assert tree["total_bytes"] <= MAX_BYTES


def test_file_artifact_unchanged_no_tree(tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("data", encoding="utf-8")
    p = _artifact_proofs([str(f)])[0]
    assert p["sha256"] == _sha(b"data")
    assert "tree" not in p


def test_log_directory_artifact_records_tree_e2e(run, home, tmp_path):
    d = tmp_path / "release-tree"
    d.mkdir()
    (d / "MANIFEST").write_text("rel", encoding="utf-8")
    code, out, _ = run("log", "релиз собран", "--artifacts", str(d))
    assert code == 0, out
    text = Path(out["data"]["path"]).read_text(encoding="utf-8")
    assert "tree_sha256" in text
    # verify-контракт не сузился: a2a verify проходит
    code2, vout, _ = run("verify")
    assert code2 == 0, vout


def test_verify_not_narrowed_after_directory_content_change(run, home, tmp_path):
    # v0.5 каталоги не хэшировались verify-й — и теперь не обязаны падать:
    d = tmp_path / "gitrepo"
    d.mkdir()
    (d / "f.txt").write_text("v1", encoding="utf-8")
    _, lg, _ = run("log", "дир-артефакт", "--artifacts", str(d))
    (d / "f.txt").write_text("v2 changed", encoding="utf-8")
    code, out, _ = run("verify")
    assert code == 0, out
