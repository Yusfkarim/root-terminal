#!/usr/bin/env python3
"""ROOT web terminal: multi-session, Arabic hacker UI, cookie login."""
import json, os, pty, re, secrets, select, shlex, signal, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

def clean_chunk(raw):
    """Keep ANSI colors, collapse \\r progress lines and \\b backspaces."""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\n{3,}", "\n\n", text)
    out_lines = []
    for line in text.split("\n"):
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        while "\x08" in line:
            line = re.sub(r"[^\x08]\x08", "", line, count=1)
            line = line.replace("\x08", "")
        out_lines.append(line)
    return "\n".join(out_lines)


ANSI_STRIP = re.compile(r"\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\x1b\[(?![0-9;]*m)[0-9;?]*[a-zA-Z]")

PORT = int(os.environ.get("PORT", "7682"))
BASE = os.path.dirname(os.path.abspath(__file__))
LOGIN_USER = os.environ.get("TERM_USER", "yusf")
LOGIN_PASS = os.environ.get("TERM_PASS", "12345678rk")
SESSION = secrets.token_hex(16)
MAX_SESSIONS = 10

lock = threading.Lock()
sessions = {}
SESS_FILE = "/data/sessions.json"
BUF_DIR = "/data/sbuf"
MAXBUF = 200000


def save_sessions():
    try:
        if os.path.isdir("/data"):
            with lock:
                data = [{"id": sid, "name": s["name"]} for sid, s in sessions.items()]
            with open(SESS_FILE + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(SESS_FILE + ".tmp", SESS_FILE)
    except OSError:
        pass


def load_sessions():
    try:
        with open(SESS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def buf_path(sid):
    return os.path.join(BUF_DIR, sid + ".log")


def persist_buf(sid, chunk):
    try:
        if not os.path.isdir("/data"):
            return
        os.makedirs(BUF_DIR, exist_ok=True)
        with open(buf_path(sid), "ab") as f:
            f.write(chunk)
    except OSError:
        pass

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
        try:
            with open(rc) as f:
                content = f.read()
        except OSError:
            content = ""
        need = ""
        if "PIP_TARGET" not in content:
            need += ('export PATH="/data/.local/bin:/data/.npm/bin:$PATH"\n'
                     'export PIP_TARGET=/data/.pylibs\n'
                     'export PYTHONPATH=/data/.pylibs:$PYTHONPATH\n'
                     'export NPM_CONFIG_PREFIX=/data/.npm\n'
                     'stty -echo 2>/dev/null\n')
        if "ROOTGUARD" not in content:
            need += ('# ROOTGUARD: keep session shell alive\n'
                     'exit(){ echo "⚠ الجلسة دائمة — للإيقاف استخدم زر ⏹"; }\n'
                     'logout(){ exit; }\n')
        if "install()" not in content:
            need += ('install(){ local p="$1"; echo "[1/3] apt: $p...";'
                     ' if apt-get install -y "$p" 2>/dev/null; then echo "$p" >> /data/apt.txt; sort -u /data/apt.txt -o /data/apt.txt; echo "OK apt + saved"; return 0; fi;'
                     ' echo "[2/3] pip: $p...";'
                     ' if pip install --quiet "$p" 2>&1 | tail -1; pip show "$p" >/dev/null 2>&1; then echo "OK pip (saved)"; return 0; fi;'
                     ' echo "[3/3] npm: $p...";'
                     ' if npm i -g "$p" 2>&1 | tail -1; [ -x "/data/.npm/bin/$p" ] || command -v "$p" >/dev/null; then echo "OK npm (saved)"; return 0; fi;'
                     ' echo "FAIL: not found"; return 1; }\n')
        if need:
            try:
                with open(rc, "a") as f:
                    f.write(need)
            except OSError:
                pass
    except OSError:
        pass
    return home


def spawn_shell_for(s):
    m, sl = pty.openpty()
    home = shell_env_home()
    env = dict(os.environ, HOME=home,
               PATH="/data/.local/bin:/data/.npm/bin:" + os.environ.get("PATH", ""),
               PIP_TARGET="/data/.pylibs",
               PYTHONPATH="/data/.pylibs:" + os.environ.get("PYTHONPATH", ""),
               NPM_CONFIG_PREFIX="/data/.npm",
               TERM="xterm-256color", LINES="40", COLUMNS="100")
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


CLEAN = re.compile(r"\x1b\[[0-9;?]*[JK]|\x1b[H]|\x1bc")

def drain(s):
    fd = s["m"]
    while True:
        try:
            r, _, _ = select.select([fd], [], [], 1.0)
            if r:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                if CLEAN.search(chunk.decode("utf-8", "replace")):
                    with lock:
                        s["buf"] = bytearray()
                        s["gen"] = s.get("gen", 0) + 1
                    try:
                        if os.path.isdir("/data"):
                            open(buf_path(s.get("sid", "?")), "wb").close()
                    except OSError:
                        pass
                clean = ANSI_STRIP.sub("", clean_chunk(chunk))
                with lock:
                    s["buf"] += clean.encode()
                    del s["buf"][:-MAXBUF]
                persist_buf(s.get("sid", "?"), clean.encode())
        except OSError:
            break
    time.sleep(1)
    try:
        if s.get("alive", True):
            spawn_shell_for(s)
    except Exception:
        pass


def create_session(name, sid=None, buf=None):
    with lock:
        if len(sessions) >= MAX_SESSIONS:
            return None
        sid = sid or ("s" + secrets.token_hex(4))
        if sid in sessions:
            sid = "s" + secrets.token_hex(4)
        s = {"sid": sid, "name": name[:30] or "session",
             "buf": bytearray(buf or b""), "gen": 0,
             "m": None, "p": None, "alive": True, "created": time.time()}
        sessions[sid] = s
    spawn_shell_for(s)
    save_sessions()
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
        try:
            if os.path.isfile(buf_path(sid)):
                os.remove(buf_path(sid))
        except OSError:
            pass
    save_sessions()


def list_sessions():
    with lock:
        return [{"id": sid, "name": s["name"]} for sid, s in sessions.items()]


def resolve_path(p):
    p = (p or "").strip()
    if not p:
        return None
    if not p.startswith("/"):
        p = "/data/work/" + p
    p = os.path.normpath(p)
    return p


def list_dir(d):
    d = resolve_path(d) or "/data/work"
    try:
        items = []
        for name in sorted(os.listdir(d)):
            full = os.path.join(d, name)
            try:
                st = os.stat(full)
                items.append({"name": name, "path": full,
                              "dir": os.path.isdir(full), "size": st.st_size})
            except OSError:
                pass
        return {"cwd": d, "items": items}
    except OSError as e:
        return {"error": str(e)}


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
        if u.path == "/api/files":
            qs = parse_qs(u.query)
            return self._send(json.dumps(
                list_dir(qs.get("dir", ["/data/work"])[0])), "application/json")
        if u.path == "/api/file":
            qs = parse_qs(u.query)
            p = resolve_path(qs.get("path", [""])[0])
            if not p or os.path.isdir(p):
                self.send_error(404)
                return
            try:
                with open(p, "rb") as f:
                    content = f.read(300000).decode("utf-8", "replace")
                return self._send(json.dumps({"path": p, "content": content}),
                                  "application/json")
            except OSError:
                self.send_error(404)
                return
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
                gen = s.get("gen", 0)
            return self._send(json.dumps(
                {"data": data.decode("utf-8", "replace"),
                 "off": off + len(data) if off <= total else total,
                 "gen": gen}),
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
        if u.path == "/api/stop":
            try:
                body = json.loads(self._body() or b"{}")
            except Exception:
                body = {}
            with lock:
                s = sessions.get(body.get("sid", ""))
            if s:
                try:
                    os.write(s["m"], b"\x03")
                except OSError:
                    pass
            return self._send('{"ok":1}', "application/json")
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
        if u.path == "/api/file":
            try:
                body = json.loads(self._body() or b"{}")
            except Exception:
                body = {}
            p = resolve_path(body.get("path", ""))
            if not p:
                self.send_error(400)
                return
            try:
                os.makedirs(os.path.dirname(p) or "/", exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(body.get("content", ""))
                return self._send(json.dumps({"ok": 1, "path": p}), "application/json")
            except OSError as e:
                return self._send(json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/run":
            try:
                body = json.loads(self._body() or b"{}")
            except Exception:
                body = {}
            p = resolve_path(body.get("path", ""))
            sid = body.get("sid", "")
            if not p or not os.path.isfile(p):
                return self._send(json.dumps({"error": "no file"}), "application/json")
            with lock:
                s = sessions.get(sid) or next(iter(sessions.values()), None)
            if p.endswith(".py"):
                cmd = "python3 " + shlex.quote(p) + "\n"
            elif p.endswith(".sh"):
                cmd = "bash " + shlex.quote(p) + "\n"
            else:
                cmd = shlex.quote(p) + "\n"
            if s:
                try:
                    os.write(s["m"], cmd.encode())
                except OSError:
                    pass
            return self._send(json.dumps({"ok": 1}), "application/json")
        self.send_error(404)

    def do_DELETE(self):
        u = urlparse(self.path)
        if not authorized(self.headers):
            return self._deny()
        if u.path == "/api/file":
            qs = parse_qs(u.query)
            p = resolve_path(qs.get("path", [""])[0])
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
                    return self._send('{"ok":1}', "application/json")
            except OSError:
                pass
            self.send_error(404)
            return
        m = re.match(r"^/api/sessions/([A-Za-z0-9]+)$", u.path)
        if m and m.group(1) in sessions:
            if len(sessions) <= 1:
                return self._send(json.dumps({"error": "last"}), "application/json")
            kill_session(m.group(1))
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
                    renamed = True
                else:
                    renamed = False
            if renamed:
                save_sessions()
                return self._send('{"ok":1}', "application/json")
        self.send_error(404)


restored = load_sessions()
if restored:
    for item in restored[:MAX_SESSIONS]:
        sid = item.get("id", "")
        if not sid or not re.match(r"^[A-Za-z0-9]+$", sid):
            continue
        buf = b""
        try:
            with open(buf_path(sid), "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - MAXBUF))
                buf = f.read()[-MAXBUF:]
        except OSError:
            pass
        create_session(item.get("name", "session"), sid=sid, buf=buf)
else:
    create_session(" الرئيسية")
print("ROOT terminal on port", PORT, flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
