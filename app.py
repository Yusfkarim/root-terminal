#!/usr/bin/env python3
"""Mobile-friendly persistent web terminal. No websockets, no xterm."""
import json, os, pty, re, select, signal, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\r")

PORT = int(os.environ.get("PORT", "7682"))
TOKEN = os.environ.get("TERM_TOKEN", "")
TOKENS = set(t for t in TOKEN.split(",") if t)

def authorized(path):
    if not TOKENS:
        return True
    return any(("token=" + t) in (path + "&") for t in TOKENS)
shell = None
lock = threading.Lock()
output_buf = bytearray()

PAGE = """<!DOCTYPE html><html lang="ckb" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROOT Terminal</title><style>
*{box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:monospace;margin:0;padding:0}
#head{display:flex;align-items:center;gap:10px;padding:12px;background:#161b22;border-bottom:1px solid #30363d;position:sticky;top:0;z-index:5}
#root{font-size:22px;font-weight:900;color:#fff;background:linear-gradient(135deg,#da3633,#a40e26);padding:6px 16px;border-radius:10px;letter-spacing:2px;box-shadow:0 0 12px #da363388}
#title{font-size:15px;color:#8b949e}
#clr{margin-right:auto;background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:8px 14px;font-size:14px}
#out{padding:10px;padding-bottom:90px}
.blk{background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:10px;overflow:hidden}
.cmd{padding:8px 12px;color:#3fb950;font-weight:bold;font-size:14px;border-bottom:1px solid #30363d;word-break:break-all}
.cmd::before{content:"$ "}
.res{padding:8px 12px;white-space:pre-wrap;word-break:break-word;font-size:13px;color:#e6edf3;min-height:8px}
#bar{display:flex;gap:6px;position:fixed;bottom:0;left:0;right:0;background:#161b22;padding:10px;border-top:1px solid #30363d}
#cmd{flex:1;font-size:16px;padding:12px;background:#0d1117;color:#3fb950;border:1px solid #3fb950;border-radius:10px;font-family:monospace}
#go{font-size:18px;padding:10px 20px;background:#238636;color:#fff;border:none;border-radius:10px}
</style></head><body>
<div id="head"><span id="root">ROOT</span><span id="title">تێرمیناڵی سێرڤەر</span><button id="clr">سڕینەوە</button></div>
<div id="out"></div><div id="end"></div>
<div id="bar"><input id="cmd" placeholder="فەرمان بنووسە..." autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"><button id="go">▶</button></div>
<script>
let off=0;let q=location.search;let out=document.getElementById('out');let cur=null;
function scrollDown(){document.getElementById('end').scrollIntoView(false);}
function newBlock(c){let d=document.createElement('div');d.className='blk';
d.innerHTML='<div class="cmd"></div><div class="res"></div>';
d.querySelector('.cmd').textContent=c;out.appendChild(d);cur=d.querySelector('.res');scrollDown();}
async function poll(){try{
let r=await fetch('/read'+q+(q?'&':'?')+'off='+off);let j=await r.json();
if(j.data){if(!cur)newBlock('(خروجی)');cur.textContent+=j.data;scrollDown();}
off=j.off;}catch(e){}setTimeout(poll,800);}
async function send(){let c=document.getElementById('cmd').value;if(!c)return;
document.getElementById('cmd').value='';newBlock(c);
await fetch('/write'+q,{method:'POST',body:c+'\\n'});}
document.getElementById('go').onclick=send;
document.getElementById('clr').onclick=()=>{out.innerHTML='';cur=null;};
document.getElementById('cmd').addEventListener('keydown',e=>{if(e.key==='Enter')send();});
poll();
</script></body></html>"""

def spawn_shell():
    global shell
    home = "/data/home" if os.path.isdir("/data") else os.path.expanduser("~")
    try:
        os.makedirs(home, exist_ok=True)
        os.makedirs("/data/work", exist_ok=True)
        os.makedirs("/data/.local/bin", exist_ok=True)
        rc = os.path.join(home, ".bashrc")
        if not os.path.exists(rc):
            with open(rc, "w") as f:
                f.write('export PATH="/data/.local/bin:$PATH"\n'
                        'stty -echo 2>/dev/null\n'
                        'keep-apt(){ echo "$1" >> /data/apt.txt; sort -u /data/apt.txt -o /data/apt.txt; apt-get update -qq && apt-get install -y "$1"; }\n'
                        'cd /data/work 2>/dev/null\n')
    except OSError:
        pass
    m, s = pty.openpty()
    env = dict(os.environ, HOME=home, PATH="/data/.local/bin:" + os.environ.get("PATH", ""))
    shell = {"m": m, "p": subprocess.Popen(["bash", "-i"], stdin=s, stdout=s, stderr=s,
             preexec_fn=os.setsid, close_fds=True, env=env, cwd="/data/work" if os.path.isdir("/data/work") else "/app")}
    os.close(s)
    threading.Thread(target=drain, args=(m,), daemon=True).start()

def drain(fd):
    global output_buf
    while True:
        try:
            r, _, _ = select.select([fd], [], [], 1.0)
            if r:
                chunk = os.read(fd, 65536)
                if not chunk: break
                with lock:
                    output_buf += chunk
                    del output_buf[:-200000]
        except OSError:
            break
    time.sleep(1)
    try: spawn_shell()
    except Exception: pass

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if not authorized(self.path):
            return self._send("Unauthorized", "text/plain")
        if self.path == "/" or self.path.startswith("/?"):
            return self._send(PAGE)
        if self.path.startswith("/read"):
            off = 0
            try: off = int(self.path.split("off=")[1].split("&")[0])
            except Exception: pass
            with lock:
                data = bytes(output_buf[off:off+65536])
                total = len(output_buf)
            text = ANSI.sub("", data.decode("utf-8", "replace"))
            text = text.replace("\x08", "")
            return self._send(json.dumps({"data": text, "off": off+len(data) if off <= total else total}), "application/json")
        self.send_error(404)
    def do_POST(self):
        if not authorized(self.path):
            return self._send("Unauthorized", "text/plain")
        if self.path.startswith("/write"):
            ln = int(self.headers.get("Content-Length", 0))
            cmd = self.rfile.read(ln)
            try: os.write(shell["m"], cmd)
            except OSError: pass
            return self._send('{"ok":1}', "application/json")
        self.send_error(404)

spawn_shell()
HTTPServer(("0.0.0.0", PORT), H).serve_forever()
