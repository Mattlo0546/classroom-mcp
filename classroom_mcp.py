#!/usr/bin/env python3
"""Google Classroom MCP server (stdio, Python standard library only).

Tools: gc_login, gc_status, gc_courses, gc_upcoming, gc_coursework, gc_announcements, gc_materials, gc_download,
gc_sync, gc_sync_status. `gc_sync` downloads class materials (PDFs, Google Slides/Docs exported as PDF) into
CLASSROOM_DIR/<course>/<topic>/, which is the folder layout SlideMate reads.

Setup (once): create a Google Cloud OAuth client of type "Desktop app", enable the Classroom and Drive APIs, and
save the downloaded JSON as ~/.config/classroom-mcp/client_secret.json (or point GOOGLE_CLIENT_SECRETS at it).
Then call gc_login. See README.md.
"""
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "0.1.0"
HOME = Path(os.environ.get("CLASSROOM_MCP_HOME", Path.home() / ".config" / "classroom-mcp"))
SECRETS = Path(os.environ.get("GOOGLE_CLIENT_SECRETS", HOME / "client_secret.json"))
TOKEN = HOME / "token.json"
STATE = HOME / "sync-state.json"
DEST = Path(os.environ.get("CLASSROOM_DIR", Path.home() / "Classroom")).expanduser()
FAKE = os.environ.get("CLASSROOM_MCP_FAKE")  # tests: a JSON file of canned API responses (see tests/)

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.topics.readonly",
    "https://www.googleapis.com/auth/drive.readonly",  # class files live in the teacher's Drive
    "openid", "email",
]
CLASSROOM = "https://classroom.googleapis.com/v1"
DRIVE = "https://www.googleapis.com/drive/v3"
# Google-native files are exported; everything else is downloaded as-is.
EXPORT = {
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}
WANT = (".pdf", ".pptx", ".ppt", ".key", ".docx", ".doc")  # what a sync downloads (slides and handouts)


class LoginRequired(Exception):
    pass


def log(*a):
    print("[classroom-mcp]", *a, file=sys.stderr, flush=True)


def _load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def _save(path, data, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    if private:
        os.chmod(tmp, 0o600)
    tmp.replace(path)


# ------------------------------------------------------------------ OAuth (installed-app loopback flow + PKCE)


def _client():
    data = _load(SECRETS, None)
    if not data:
        raise RuntimeError(f"No Google OAuth client found at {SECRETS}. Create a 'Desktop app' OAuth client in Google "
                           "Cloud Console (APIs & Services → Credentials), download its JSON and save it there. "
                           "See the README.")
    c = data.get("installed") or data.get("web") or data
    return c["client_id"], c.get("client_secret", "")


def _post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"{e.code} from Google: {body[:300]}")


def login(timeout=300):
    """Open the browser for Google sign-in and wait for the redirect to a local port."""
    client_id, client_secret = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got.update({k: v[0] for k, v in q.items()})
            ok = "code" in got
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in to Google Classroom. You can close this tab." if ok else "Sign-in didn't complete. You can close this tab and try again."
            self.wfile.write(f"<!doctype html><meta charset=utf-8><body style='font:16px -apple-system,sans-serif;padding:40px'>{msg}</body>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}/"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
    })
    log("opening browser for sign-in:", url)
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    else:
        webbrowser.open(url)
    srv.timeout = 2
    end = time.time() + timeout
    while "code" not in got and "error" not in got and time.time() < end:
        srv.handle_request()
    srv.server_close()
    if "code" not in got:
        raise RuntimeError("Sign-in was cancelled or timed out." + (f" ({got['error']})" if "error" in got else ""))
    tok = _post_form("https://oauth2.googleapis.com/token", {
        "code": got["code"], "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier,
    })
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    tok["email"] = _email_from_id_token(tok.get("id_token", ""))
    _save(TOKEN, tok, private=True)
    return tok


def _email_from_id_token(idt):
    try:
        payload = idt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def access_token():
    if FAKE:
        return "fake"
    tok = _load(TOKEN, None)
    if not tok or not tok.get("refresh_token"):
        raise LoginRequired("Not signed in to Google Classroom. Run gc_login.")
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    client_id, client_secret = _client()
    try:
        new = _post_form("https://oauth2.googleapis.com/token", {
            "refresh_token": tok["refresh_token"], "client_id": client_id, "client_secret": client_secret,
            "grant_type": "refresh_token",
        })
    except RuntimeError as e:
        if "invalid_grant" in str(e):  # revoked, or a Testing-mode app's 7-day refresh token expired
            raise LoginRequired("Google sign-in expired. Run gc_login again.")
        raise
    tok.update(new)
    tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
    _save(TOKEN, tok, private=True)
    return tok["access_token"]


# ------------------------------------------------------------------ API


_fake = None


def _fake_get(url):
    global _fake
    if _fake is None:
        _fake = _load(FAKE, {})
    path = url.split("googleapis.com", 1)[-1].split("?")[0]
    if "alt=media" in url:
        path += "?alt=media"
    for key in (path,):
        if key in _fake:
            v = _fake[key]
            return v.encode() if isinstance(v, str) else json.dumps(v).encode()
    raise RuntimeError(f"404 (fake) {path}")


def get(url, params=None, raw=False):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    if FAKE:
        body = _fake_get(url)
        return body if raw else json.loads(body)
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + access_token()})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:400]
            if e.code == 401:
                tok = _load(TOKEN, {})
                tok["expires_at"] = 0
                _save(TOKEN, tok, private=True)
                if attempt == 0:
                    continue
                raise LoginRequired("Google rejected the sign-in (401). Run gc_login again.")
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if e.code == 403 and "disabled" in msg.lower() and "api" in msg.lower():
                raise RuntimeError("The Classroom or Drive API isn't enabled for this Google Cloud project. Enable "
                                   "both under APIs & Services → Library, then try again. " + msg)
            raise RuntimeError(f"{e.code} {url.split('?')[0]}: {msg}")


def paged(url, key, params=None):
    params = dict(params or {})
    out = []
    while True:
        d = get(url, params)
        out += d.get(key, [])
        if not d.get("nextPageToken"):
            return out
        params["pageToken"] = d["nextPageToken"]


def courses():
    return paged(f"{CLASSROOM}/courses", "courses", {"studentId": "me", "courseStates": "ACTIVE", "pageSize": 100})


def _course(course):
    """Find a course by id or (part of) its name."""
    cs = courses()
    for c in cs:
        if c["id"] == str(course):
            return c
    q = str(course).lower()
    hits = [c for c in cs if q in c.get("name", "").lower() or q in (c.get("section") or "").lower()]
    if not hits:
        raise RuntimeError(f"No active course matches '{course}'. Courses: " + ", ".join(c["name"] for c in cs))
    return hits[0]


def topics(cid):
    return {t["topicId"]: t["name"] for t in paged(f"{CLASSROOM}/courses/{cid}/topics", "topic", {"pageSize": 100})}


def coursework(cid):
    return paged(f"{CLASSROOM}/courses/{cid}/courseWork", "courseWork", {"pageSize": 100})


def submissions(cid):
    subs = paged(f"{CLASSROOM}/courses/{cid}/courseWork/-/studentSubmissions", "studentSubmissions",
                 {"userId": "me", "pageSize": 100})
    return {s["courseWorkId"]: s for s in subs}


def materials(cid):
    return paged(f"{CLASSROOM}/courses/{cid}/courseWorkMaterials", "courseWorkMaterial", {"pageSize": 100})


def announcements(cid, limit=10):
    d = get(f"{CLASSROOM}/courses/{cid}/announcements", {"pageSize": max(1, min(limit, 100)), "orderBy": "updateTime desc"})
    return d.get("announcements", [])[:limit]


def _due(cw):
    d = cw.get("dueDate")
    if not d:
        return None
    t = cw.get("dueTime") or {}
    return datetime(d["year"], d["month"], d["day"], t.get("hours", 23), t.get("minutes", 59), tzinfo=timezone.utc)


def _attachments(item):
    out = []
    for m in item.get("materials") or []:
        if "driveFile" in m:
            f = m["driveFile"].get("driveFile", {})
            out.append({"type": "drive", "id": f.get("id"), "title": f.get("title"), "url": f.get("alternateLink")})
        elif "link" in m:
            out.append({"type": "link", "title": m["link"].get("title") or m["link"].get("url"), "url": m["link"].get("url")})
        elif "youtubeVideo" in m:
            v = m["youtubeVideo"]
            out.append({"type": "youtube", "title": v.get("title"), "url": v.get("alternateLink")})
        elif "form" in m:
            out.append({"type": "form", "title": m["form"].get("title"), "url": m["form"].get("formUrl")})
    return out


# ------------------------------------------------------------------ Drive download


def _safe(name, limit=120):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "untitled").strip().strip(".")
    return re.sub(r"\s+", " ", name)[:limit] or "untitled"


def download(file_id, folder, convert=True):
    """Download a Drive file into `folder`; Google Slides/Docs are exported as PDF. Returns (path, meta)."""
    meta = get(f"{DRIVE}/files/{file_id}", {"fields": "id,name,mimeType,modifiedTime,size", "supportsAllDrives": "true"})
    mime, name = meta.get("mimeType", ""), meta.get("name") or file_id
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if mime in EXPORT:
        out_mime, ext = EXPORT[mime]
        body = get(f"{DRIVE}/files/{file_id}/export", {"mimeType": out_mime}, raw=True)
        path = folder / (_safe(Path(name).stem if name.lower().endswith(ext) else name) + ext)
    elif mime.startswith("application/vnd.google-apps"):
        raise RuntimeError(f"'{name}' is a {mime.split('.')[-1]} and can't be downloaded")
    else:
        body = get(f"{DRIVE}/files/{file_id}", {"alt": "media", "supportsAllDrives": "true"}, raw=True)
        path = folder / _safe(name)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    if convert and path.suffix.lower() in (".pptx", ".ppt", ".key", ".docx", ".doc"):
        pdf = _to_pdf(path)
        if pdf:
            path = pdf
    return path, meta


def _to_pdf(path):
    """Best effort: PowerPoint/Word → PDF with LibreOffice if it's installed (SlideMate reads PDFs)."""
    soffice = shutil.which("soffice") or ("/Applications/LibreOffice.app/Contents/MacOS/soffice"
                                          if Path("/Applications/LibreOffice.app").exists() else None)
    if not soffice:
        return None
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(path.parent), str(path)],
                       check=True, capture_output=True, timeout=300)
        pdf = path.with_suffix(".pdf")
        if pdf.exists():
            path.unlink()
            return pdf
    except Exception as e:
        log("convert failed", path.name, e)
    return None


# ------------------------------------------------------------------ sync (background job, polled via gc_sync_status)


JOB = {"running": False, "progress": "", "report": None, "started": None}
_job_lock = threading.Lock()


def _sync_worker(only_course=None):
    state = _load(STATE, {"files": {}})
    got, skipped, failed, other = [], 0, [], 0
    try:
        cs = courses()
        if only_course:
            cs = [_course(only_course)]
        for ci, c in enumerate(cs, 1):
            cname = _safe(c.get("name", c["id"]), 80)
            JOB["progress"] = f"{c.get('name')} ({ci}/{len(cs)})"
            tmap = topics(c["id"])
            items = [("Materials", m) for m in materials(c["id"])] + [("Assignments", w) for w in coursework(c["id"])]
            for kind, it in items:
                folder = DEST / cname / _safe(tmap.get(it.get("topicId"), kind), 80)
                for a in _attachments(it):
                    if a["type"] != "drive" or not a.get("id"):
                        other += 1
                        continue
                    title = (a.get("title") or "").lower()
                    known = state["files"].get(a["id"])
                    if known and Path(known["path"]).exists():
                        skipped += 1
                        continue
                    # Skip obvious non-slides (images, videos, spreadsheets) before downloading.
                    if re.search(r"\.(png|jpe?g|gif|mp4|mov|mp3|zip|xlsx?|csv)$", title):
                        other += 1
                        continue
                    try:
                        JOB["progress"] = f"{c.get('name')}: {a.get('title')}"
                        path, meta = download(a["id"], folder)
                        if not (path.suffix.lower() in WANT):
                            path.unlink(missing_ok=True)
                            other += 1
                            continue
                        state["files"][a["id"]] = {"path": str(path), "modified": meta.get("modifiedTime"), "course": c.get("name")}
                        got.append(str(path))
                        _save(STATE, state)
                    except LoginRequired:
                        raise
                    except Exception as e:
                        failed.append(f"{a.get('title')}: {str(e)[:160]}")
        summary = f"Downloaded {len(got)} new file{'s' * (len(got) != 1)} to {DEST}"
        if skipped:
            summary += f", {skipped} already there"
        if failed:
            summary += f", {len(failed)} failed"
        JOB["report"] = {"summary": summary + ".", "new_files": got, "failed": failed, "folder": str(DEST),
                         "skipped_links_or_non_slides": other}
    except LoginRequired as e:
        JOB["report"] = {"login_required": True, "error": str(e)}
    except Exception as e:
        JOB["report"] = {"error": str(e)[:500]}
    finally:
        JOB["running"] = False
        JOB["progress"] = (JOB["report"] or {}).get("summary") or (JOB["report"] or {}).get("error", "")


def start_sync(course=None):
    with _job_lock:
        if not JOB["running"]:
            access_token()  # fail fast (login_required) before going to the background
            JOB.update(running=True, progress="Starting…", report=None, started=time.time())
            threading.Thread(target=_sync_worker, args=(course,), daemon=True).start()
    return dict(JOB)


# ------------------------------------------------------------------ tools


def _fmt_due(cw):
    d = _due(cw)
    return d.astimezone().strftime("%a %d %b %H:%M") if d else "no due date"


def t_login(args):
    tok = login()
    return {"signed_in": True, "email": tok.get("email", ""), "token_file": str(TOKEN)}


def t_status(args):
    tok = _load(TOKEN, None)
    out = {"client_secret_found": SECRETS.exists(), "signed_in": bool(tok and tok.get("refresh_token")),
           "email": (tok or {}).get("email", ""), "download_folder": str(DEST)}
    if out["signed_in"]:
        try:
            out["courses"] = len(courses())
        except LoginRequired as e:
            out.update(signed_in=False, login_required=True, error=str(e))
        except Exception as e:
            out["error"] = str(e)[:300]
    return out


def t_courses(args):
    return [{"id": c["id"], "name": c.get("name"), "section": c.get("section", ""), "url": c.get("alternateLink")}
            for c in courses()]


def t_upcoming(args):
    days = int(args.get("days", 14))
    now = datetime.now(timezone.utc)
    out = []
    for c in courses():
        subs = submissions(c["id"])
        for cw in coursework(c["id"]):
            d = _due(cw)
            if not d or not (now - timedelta(days=1) <= d <= now + timedelta(days=days)):
                continue
            s = subs.get(cw["id"], {})
            out.append({"course": c.get("name"), "title": cw.get("title"), "due": _fmt_due(cw), "due_iso": d.isoformat(),
                        "type": cw.get("workType"), "status": s.get("state", "NEW"), "late": s.get("late", False),
                        "url": cw.get("alternateLink")})
    out.sort(key=lambda x: x["due_iso"])
    return out


def t_coursework(args):
    c = _course(args["course"])
    subs, tmap = submissions(c["id"]), topics(c["id"])
    return {"course": c.get("name"), "coursework": [
        {"id": cw["id"], "title": cw.get("title"), "topic": tmap.get(cw.get("topicId"), ""), "due": _fmt_due(cw),
         "type": cw.get("workType"), "status": subs.get(cw["id"], {}).get("state", "NEW"),
         "description": (cw.get("description") or "")[:1500], "attachments": _attachments(cw), "url": cw.get("alternateLink")}
        for cw in coursework(c["id"])]}


def t_announcements(args):
    c = _course(args["course"])
    return {"course": c.get("name"), "announcements": [
        {"posted": a.get("creationTime", "")[:16].replace("T", " "), "text": (a.get("text") or "")[:3000],
         "attachments": _attachments(a), "url": a.get("alternateLink")}
        for a in announcements(c["id"], int(args.get("limit", 10)))]}


def t_materials(args):
    c = _course(args["course"])
    tmap = topics(c["id"])
    return {"course": c.get("name"), "materials": [
        {"title": m.get("title"), "topic": tmap.get(m.get("topicId"), ""), "description": (m.get("description") or "")[:800],
         "attachments": _attachments(m), "url": m.get("alternateLink")}
        for m in materials(c["id"])]}


def t_download(args):
    path, meta = download(args["file_id"], Path(args.get("folder") or DEST / "_Downloads").expanduser())
    return {"path": str(path), "name": meta.get("name"), "mimeType": meta.get("mimeType")}


def t_sync(args):
    return start_sync(args.get("course"))


def t_sync_status(args):
    return dict(JOB)


TOOLS = {
    "gc_login": (t_login, "Sign in to Google Classroom (opens the browser). Use when a tool says sign-in is needed.", {}),
    "gc_status": (t_status, "Whether Google Classroom is set up and signed in, and where synced files go.", {}),
    "gc_courses": (t_courses, "List the student's active Google Classroom courses.", {}),
    "gc_upcoming": (t_upcoming, "Assignments due soon across all courses, with submission status.",
                    {"days": {"type": "integer", "description": "How many days ahead (default 14)"}}),
    "gc_coursework": (t_coursework, "All assignments in one course: due dates, status, descriptions, attachments.",
                      {"course": {"type": "string", "description": "Course id or part of its name"}}, ["course"]),
    "gc_announcements": (t_announcements, "Recent announcements (stream posts) in one course.",
                         {"course": {"type": "string", "description": "Course id or part of its name"},
                          "limit": {"type": "integer", "description": "How many (default 10)"}}, ["course"]),
    "gc_materials": (t_materials, "Class materials (slides, handouts, links) in one course, grouped by topic.",
                     {"course": {"type": "string", "description": "Course id or part of its name"}}, ["course"]),
    "gc_download": (t_download, "Download one Drive file from Classroom (Google Slides/Docs are saved as PDF).",
                    {"file_id": {"type": "string"}, "folder": {"type": "string", "description": "Where to save (optional)"}},
                    ["file_id"]),
    "gc_sync": (t_sync, "Download new class materials (slides and handouts as PDF) into the Classroom folder, one "
                        "sub-folder per course. Runs in the background; poll gc_sync_status.",
                {"course": {"type": "string", "description": "Only this course (optional)"}}),
    "gc_sync_status": (t_sync_status, "Progress and result of the last gc_sync.", {}),
}


def tool_list():
    out = []
    for name, spec in TOOLS.items():
        props = spec[2]
        schema = {"type": "object", "properties": props}
        if len(spec) > 3:
            schema["required"] = spec[3]
        out.append({"name": name, "description": spec[1], "inputSchema": schema})
    return out


def call_tool(name, args):
    if name not in TOOLS:
        return {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True}
    try:
        result = TOOLS[name][0](args or {})
        return {"content": [{"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}]}
    except LoginRequired as e:
        return {"content": [{"type": "text", "text": json.dumps({"login_required": True, "error": str(e)})}], "isError": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


# ------------------------------------------------------------------ MCP stdio (JSON-RPC 2.0, one message per line)


def handle(msg):
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:  # notification
        return None
    if method == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        res = {"protocolVersion": pv, "capabilities": {"tools": {}},
               "serverInfo": {"name": "google-classroom", "version": VERSION},
               "instructions": "Google Classroom for a student. If a tool reports login_required, call gc_login "
                               "(opens a browser). gc_sync downloads class slides into folders SlideMate can read."}
    elif method == "tools/list":
        res = {"tools": tool_list()}
    elif method == "tools/call":
        p = msg.get("params") or {}
        res = call_tool(p.get("name"), p.get("arguments"))
    elif method == "ping":
        res = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main():
    out_lock = threading.Lock()

    def reply(resp):
        if resp is not None:
            with out_lock:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        # tools/call can take a while (sign-in waits for the browser): answer it on a thread so pings still work
        if msg.get("method") == "tools/call":
            threading.Thread(target=lambda m=msg: reply(handle(m)), daemon=True).start()
        else:
            reply(handle(msg))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "login":  # `python3 classroom_mcp.py login` from a terminal
        print(json.dumps(t_login({}), indent=1))
    elif len(sys.argv) > 1 and sys.argv[1] == "sync":
        start_sync(sys.argv[2] if len(sys.argv) > 2 else None)
        while JOB["running"]:
            time.sleep(1)
        print(json.dumps(JOB["report"], indent=1))
    else:
        main()
