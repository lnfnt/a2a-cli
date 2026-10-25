"""v0.4.8: _artifact_proofs не должен превращать живую директорию в
exists=False (IsADirectoryError на sha256 ложно клеймил запись UNVERIFIED_CLAIM,
проявилось 2026-10-21 на артефакте-репозитории docx-cli)."""
from a2a.journal import _artifact_proofs


def test_directory_artifact_keeps_exists_true(tmp_path):
    d = tmp_path / "repo"
    (d / "sub").mkdir(parents=True)
    (d / "f.txt").write_text("x", encoding="utf-8")
    p = _artifact_proofs([str(d)])[0]
    assert p["exists"] is True
    assert p.get("sha256") is None
    assert p["sha256_note"] == "directory"
    assert "error" not in p
    assert "mtime" in p


def test_file_artifact_still_hashes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    p = _artifact_proofs([str(f)])[0]
    assert p["exists"] is True
    assert p["sha256"]


def test_missing_path_still_reports_false(tmp_path):
    p = _artifact_proofs([str(tmp_path / "nope")])[0]
    assert p["exists"] is False
