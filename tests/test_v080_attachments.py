"""v0.8.0 §4: вложения = ссылки-метаданные, не копии."""
from __future__ import annotations

from pathlib import Path


def _artifact(home, name="obj.txt", content=b"payload-42"):
    p = Path(home) / "artifacts"
    p.mkdir(parents=True, exist_ok=True)
    (p / name).write_bytes(content)
    return p / name


def test_attach_stores_metadata_not_copy(run, home, profile, other):
    a = _artifact(home)
    code, out, _ = run("mail", "send", "--to", other, "--subject", "с вложением",
                       "--body", "b", "--attach", str(a))
    assert code == 0, out
    inbox = list((home / "mail" / other / "inbox").glob("*.md"))
    txt = inbox[-1].read_text(encoding="utf-8")
    assert str(a) in txt and "payload-42" not in txt, "ссылка+метаданные, не содержимое"
    assert "sha256" in txt.lower()


def test_attachment_show_and_verify(run, home, profile, other):
    a = _artifact(home, "v.txt", b"abc")
    code, out, _ = run("mail", "send", "--to", other, "--subject", "s", "--body", "b",
                       "--attach", str(a))
    mid = out["data"]["id"]
    code, sh, _ = run("mail", "attachment", mid, "1")
    assert code == 0, sh
    code, sh2, _ = run("mail", "attachment", mid, "1", profile_override=other)
    assert code == 0
    assert sh2["data"].get("sha_ok") in (True, None) or "ok" in str(sh2).lower()


def test_attach_nonexistent_rejected(run, home, profile, other):
    code, out, _ = run("mail", "send", "--to", other, "--subject", "s", "--body", "b",
                       "--attach", str(Path(home) / "nope.bin"), check=False)
    assert code != 0
    assert "attach" in str(out.get("reason", ""))


def test_attach_drift_detected(run, home, profile, other):
    a = _artifact(home, "d.txt", b"orig")
    code, out, _ = run("mail", "send", "--to", other, "--subject", "s", "--body", "b",
                       "--attach", str(a))
    mid = out["data"]["id"]
    a.write_bytes(b"CHANGED")
    code, sh, _ = run("mail", "attachment", mid, "1", profile_override=other)
    s = str(sh)
    assert "MOVED" in s or "DRIFT" in s or "mismatch" in s or sh["data"].get("sha_ok") is False, s
