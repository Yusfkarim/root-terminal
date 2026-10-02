#!/usr/bin/env python3
"""ROOT web terminal: multi-session, Arabic hacker UI, cookie login."""
import json, os, pty, re, secrets, select, signal, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\r")

PORT = int(os.environ.get("PORT", "7682"))
BASE = os.path.dirname(os.path.abspath(__file__))
LOGIN_USER = os.environ.get("TERM_USER", "yusf")
LOGIN_PASS = os.environ.get("TERM_PASS", "12345678rk")
SESSION = secrets.token_hex(16)
MAX_SESSIONS = 10

lock = threading.Lock()
sessions = {}

PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html"),
            encoding="utf-8").read() if os.path.exists(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")) else "<h1>ui.html missing</h1>"


def get_cookie(headers, name):
    for part in (headers.get("Cookie") or "").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k == name:
                return v
    return ""


def authorized(headers):
    return get_cookie(headers, "auth") == SESSION


def shell_env_home():
    home = "/data/home" if os.path.isdir("/data") else os.path.expanduser("~")
    try:
        os.makedirs(home, exist_ok=True)
        os.makedirs("/data/work", exist_ok=True)
        os.makedirs("/data/.local/bin", exist_ok=True)
        rc = os.path.join(home, ".bashrc")
        if not os.path.exists(rc):
            with open(rc, "w") as f:
                f.write('export PATH="/data/.local/bin:$PATH"\n'
                        'stty -echo 2>/dev/null\n')
    except OSError:
        pass
    return home


def spawn_shell_for(s):
    m, sl = pty.openpty()
    home = shell_env_home()
    env = dict(os.environ, HOME=home,
               PATH="/data/.local/bin:" + os.environ.get("PATH", ""))
    try:
        cwd = "/data/work" if os.path.isdir("/data/work") else "/app"
    except Exception:
        cwd = "/app"
    p = subprocess.Popen(["bash", "-i"], stdin=sl, stdout=sl, stderr=sl,
                         preexec_fn=os.setsid, close_fds=True, env=env, cwd=cwd)
    os.close(sl)
    s["m"] = m
    s["p"] = p
    threading.Thread(target=drain, args=(s,), daemon=True).start()


def drain(s):
    fd = s["m"]
    while True:
        try:
            r, _, _ = select.select([fd], [], [], 1.0)
            if r:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                clean = ANSI.sub("", chunk.decode("utf-8", "replace")).replace("\x08", "")
                with lock:
                    s["buf"] += clean.encode()
                    del s["buf"][:-200000]
        except OSError:
            break
    time.sleep(1)
    try:
        if s.get("alive", True):
            spawn_shell_for(s)
    except Exception:
        pass


def create_session(name):
    with lock:
        if len(sessions) >= MAX_SESSIONS:
            return None
        sid = "s" + secrets.token_hex(4)
        s = {"name": name[:30] or "session", "buf": bytearray(),
             "m": None, "p": None, "alive": True, "created": time.time()}
        sessions[sid] = s
    spawn_shell_for(s)
    return sid


def kill_session(sid):
    with lock:
        s = sessions.pop(sid, None)
    if s:
        s["alive"] = False
        try:
            os.close(s["m"])
        except Exception:
            pass
        try:
            os.killpg(os.getpgid(s["p"].pid), signal.SIGKILL)
        except Exception:
            pass


def list_sessions():
    with lock:
        return [{"id": sid, "name": s["name"]} for sid, s in sessions.items()]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", cookie=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(b)

    def _deny(self):
        b = b"Unauthorized"
        self.send_response(401)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        try:
            ln = int(self.headers.get("Content-Length", 0))
        except Exception:
            ln = 0
        return self.rfile.read(ln) if ln > 0 else b""

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(PAGE)
        if u.path == "/manifest.json":
            return self._send(json.dumps({
                "name": "ROOT Terminal", "short_name": "ROOT",
                "description": "طرفية السيرفر",
                "start_url": "/", "display": "standalone",
                "dir": "rtl", "lang": "ar",
                "background_color": "#000000", "theme_color": "#000000",
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]
            }), "application/manifest+json")
        if u.path == "/sw.js":
            return self._send(
                "self.addEventListener('fetch',e=>{});",
                "application/javascript")
        if u.path in ("/icon-192.png", "/icon-512.png"):
            try:
                with open(os.path.join(BASE, u.path[1:]), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except OSError:
                self.send_error(404)
                return
        if not authorized(self.headers):
            return self._deny()
        if u.path == "/api/sessions":
            return self._send(json.dumps(list_sessions()), "application/json")
        if u.path == "/read":
            qs = parse_qs(u.query)
            sid = qs.get("sid", [""])[0]
            try:
                off = int(qs.get("off", ["0"])[0])
            except Exception:
                off = 0
            with lock:
                s = sessions.get(sid)
                if not s:
                    return self._send(json.dumps({"error": "no session"}), "application/json")
                data = bytes(s["buf"][off:off + 65536])
                total = len(s["buf"])
            return self._send(json.dumps(
                {"data": data.decode("utf-8", "replace"),
                 "off": off + len(data) if off <= total else total}),
                "application/json")
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/login":
            try:
                cred = json.loads(self._body() or b"{}")
            except Exception:
                cred = {}
            if cred.get("u") == LOGIN_USER and cred.get("p") == LOGIN_PASS:
                return self._send('{"ok":1}', "application/json",
                                  "auth=" + SESSION + "; Path=/; Max-Age=31536000; SameSite=Lax")
            return self._deny()
        if not authorized(self.headers):
            return self._deny()
        if u.path == "/api/sessions":
            try:
                name = (json.loads(self._body() or b"{}").get("name") or "session")[:30]
            except Exception:
                name = "session"
            sid = create_session(name)
            if not sid:
                return self._send(json.dumps({"error": "limit"}), "application/json")
            return self._send(json.dumps({"id": sid, "name": name}), "application/json")
        if u.path == "/write":
            qs = parse_qs(u.query)
            sid = qs.get("sid", [""])[0]
            cmd = self._body()
            with lock:
                s = sessions.get(sid)
            if s and cmd:
                try:
                    os.write(s["m"], cmd)
                except OSError:
                    pass
            return self._send('{"ok":1}', "application/json")
        self.send_error(404)

    def do_PATCH(self):
        u = urlparse(self.path)
        if not authorized(self.headers):
            return self._deny()
        m = re.match(r"^/api/sessions/([A-Za-z0-9]+)$", u.path)
        if m:
            try:
                name = (json.loads(self._body() or b"{}").get("name") or "")[:30]
            except Exception:
                name = ""
            with lock:
                s = sessions.get(m.group(1))
                if s and name:
                    s["name"] = name
                    return self._send('{"ok":1}', "application/json")
        self.send_error(404)

    def do_DELETE(self):
        u = urlparse(self.path)
        if not authorized(self.headers):
            return self._deny()
        m = re.match(r"^/api/sessions/([A-Za-z0-9]+)$", u.path)
        if m and m.group(1) in sessions:
            if len(sessions) <= 1:
                return self._send(json.dumps({"error": "last"}), "application/json")
            kill_session(m.group(1))
            return self._send('{"ok":1}', "application/json")
        self.send_error(404)


create_session(" الرئيسية")
print("ROOT terminal on port", PORT, flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
