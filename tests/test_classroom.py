"""Offline tests: run with `python3 tests/test_classroom.py`. No Google account or network needed."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ.update(CLASSROOM_MCP_HOME=str(TMP / "home"), CLASSROOM_DIR=str(TMP / "Classroom"))
sys.path.insert(0, str(ROOT))
import classroom_mcp as gc  # noqa: E402


def rpc(proc, mid, method, params=None):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def test_stdio_protocol():
    env = dict(os.environ, CLASSROOM_MCP_FAKE=str(ROOT / "tests" / "fixtures.json"))
    p = subprocess.Popen([sys.executable, str(ROOT / "classroom_mcp.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, env=env)
    try:
        init = rpc(p, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
        assert init["result"]["serverInfo"]["name"] == "google-classroom"
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        names = [t["name"] for t in rpc(p, 2, "tools/list")["result"]["tools"]]
        assert {"gc_login", "gc_sync", "gc_sync_status", "gc_upcoming"} <= set(names), names
        r = rpc(p, 3, "tools/call", {"name": "gc_coursework", "arguments": {"course": "history"}})
        data = json.loads(r["result"]["content"][0]["text"])
        assert data["course"] == "Year 12 History" and data["coursework"][0]["status"] == "TURNED_IN"
        assert rpc(p, 4, "nope")["error"]["code"] == -32601
    finally:
        p.kill()


def test_not_signed_in():
    gc.FAKE = None
    r = gc.call_tool("gc_sync", {})
    assert r.get("isError") and json.loads(r["content"][0]["text"])["login_required"] is True
    st = gc.t_status({})
    assert st["signed_in"] is False


def test_missing_client_secret_message():
    gc.FAKE = None
    r = gc.call_tool("gc_login", {})
    assert r["isError"] and "Desktop app" in r["content"][0]["text"]


def test_oauth_loopback_flow():
    """Simulate Google redirecting back to the local port; the code is exchanged and the token saved."""
    gc.FAKE = None
    (TMP / "home").mkdir(parents=True, exist_ok=True)
    gc.SECRETS = TMP / "home" / "client_secret.json"
    gc.SECRETS.write_text(json.dumps({"installed": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "s"}}))
    opened, exchanged = {}, {}
    gc.subprocess.run = lambda cmd, check=False: opened.setdefault("url", cmd[-1])
    gc.webbrowser.open = lambda url: opened.setdefault("url", url)
    idt = "x." + __import__("base64").urlsafe_b64encode(json.dumps({"email": "student@school.org"}).encode()).decode().rstrip("=") + ".y"
    gc._post_form = lambda url, fields: exchanged.update(fields) or {"access_token": "a", "refresh_token": "r",
                                                                       "expires_in": 3600, "id_token": idt}
    result = {}
    t = threading.Thread(target=lambda: result.update(gc.login(timeout=20)))
    t.start()
    for _ in range(100):
        if "url" in opened:
            break
        time.sleep(0.05)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(opened["url"]).query)
    assert "classroom.courses.readonly" in q["scope"][0] and q["code_challenge_method"][0] == "S256"
    redirect = q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"state": q["state"][0], "code": "CODE123"})
    page = urllib.request.urlopen(redirect, timeout=5).read().decode()
    t.join(10)
    assert "Signed in" in page and exchanged["code"] == "CODE123" and exchanged["code_verifier"]
    tok = json.loads(gc.TOKEN.read_text())
    assert tok["refresh_token"] == "r" and tok["email"] == "student@school.org"
    assert oct(gc.TOKEN.stat().st_mode & 0o777) == "0o600"


def test_sync_skips_already_downloaded():
    gc.FAKE = str(ROOT / "tests" / "fixtures.json")
    gc._fake = None
    for _ in range(2):
        gc.start_sync()
        while gc.JOB["running"]:
            time.sleep(0.05)
    rep = gc.JOB["report"]
    assert rep["new_files"] == [] and "3 already there" in rep["summary"], rep
    files = sorted(p.relative_to(TMP / "Classroom").as_posix() for p in (TMP / "Classroom").rglob("*.pdf"))
    assert files == ["Year 12 Biology/Unit 3 - Cell Biology/Cell worksheet.pdf",
                     "Year 12 Biology/Unit 3 - Cell Biology/Mitosis.pdf",
                     "Year 12 History/Assignments/Sources pack.pdf"], files


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("✓", t.__name__)
    print(f"{len(tests)} passed")
