"""v0.4.9 регресс-тесты по независимому аудиту sa-0 (21.10, изолированные шины)."""
import json
import subprocess
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)


def run(bus, profile, *args, code=0):
    p = subprocess.run([sys.executable, "-m", "a2a.cli", *args],
                       env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
                            "A2A_HOME": str(bus), "A2A_PROFILE": profile},
                       capture_output=True, text=True, timeout=60, cwd=REPO)
    if code is not None:
        assert p.returncode == code, (args, p.returncode, p.stdout[-300:], p.stderr[-300:])
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"raw": p.stdout, "err": p.stderr}


def test_handoff_rejects_traversal(tmp_path):
    bus = tmp_path / "bus"
    r = run(bus, "coordinator", "handoff", "--to", "../../escape", "--task", "T", "--acceptance", "A", code=4)
    assert r.get("reason") == "invalid_profile_name"
    assert not (bus.parent / "escape").exists()


def test_handoff_rejects_self(tmp_path):
    bus = tmp_path / "bus"
    r = run(bus, "coordinator", "handoff", "--to", "coordinator", "--task", "T", "--acceptance", "A", code=4)
    assert r.get("reason") == "self_handoff_forbidden"


def test_parallel_writes_no_loss(tmp_path):
    """8 параллельных status set: ни один не падает (было: rc1 после доставки из-за colliding .tmp)."""
    bus = tmp_path / "bus"
    procs = []
    import subprocess as sp
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "A2A_HOME": str(bus), "A2A_PROFILE": "user-profile"}
    for i in range(8):
        procs.append(sp.Popen([sys.executable, "-m", "a2a.cli", "status", "set", "--state", "busy"], env=env, cwd=REPO,
                              stdout=sp.PIPE, stderr=sp.PIPE, text=True))
    codes = [p.wait(timeout=60) for p in procs]
    assert codes.count(0) == 8, codes


def test_supersede_reason_scrubbed(tmp_path):
    bus = tmp_path / "bus"
    art = tmp_path / "a.txt"
    art.write_text("v1")
    log = run(bus, "coordinator", "log", "работа 5 тестов", "--artifacts", str(art),
              "--verify", "true", "--verify-run")
    jid = log["data"]["id"]
    art.write_text("v2")
    run(bus, "coordinator", "verify", "--supersede", jid, "--artifact", str(art),
        "--reason", "ключ sk-abcdefghij0123456789ABCDEM утёк")
    rec = (bus / "journal" / "2026" / "10").glob("*.md")
    text = " ".join(f.read_text(encoding="utf-8-sig") for f in rec)
    assert "sk-abcdefghij" not in text and "REDACTED" in text.upper()


def test_answer_secret_keeps_unread(tmp_path):
    bus = tmp_path / "bus"
    run(bus, "coordinator", "mail", "send", "--to", "user-profile", "--subject", "S",
        "--body", "текст", "--priority", "today", "--no-wake")
    lst = run(bus, "user-profile", "mail", "list", "--inbox")
    mid = lst["data"]["entries"][0]["id"]
    r = run(bus, "user-profile", "mail", "answer", mid, "мой sk-abcdefghij0123456789ABCDEM", code=4)
    assert r.get("reason") == "secret_detected_in_reply"
    after = run(bus, "user-profile", "mail", "list", "--inbox")
    assert after["data"]["entries"][0]["msg_status"] == "unread"


def test_done_case_insensitive(tmp_path):
    bus = tmp_path / "bus"
    r = run(bus, "coordinator", "log", "что-то", "--status", "DONE", code=None)
    assert "artifacts" in json.dumps(r, ensure_ascii=False)
