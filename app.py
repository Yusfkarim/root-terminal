#!/usr/bin/env python3
"""Mobile-friendly persistent web terminal. No websockets, no xterm."""
import json, os, pty, re, secrets, select, signal, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\r")

PORT = int(os.environ.get("PORT", "7682"))
LOGIN_USER = os.environ.get("TERM_USER", "yusf")
LOGIN_PASS = os.environ.get("TERM_PASS", "12345678rk")
SESSION = secrets.token_hex(16)

def get_cookie(headers, name):
    for part in (headers.get("Cookie") or "").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k == name:
                return v
    return ""

def authorized(headers, path):
    if get_cookie(headers, "auth") == SESSION:
        return True
    qs = parse_qs(path.split("?", 1)[1] if "?" in path else "")
    return qs.get("token", [""])[0] == SESSION
shell = None
lock = threading.Lock()
output_buf = bytearray()

PAGE = """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROOT Terminal</title><style>
*{box-sizing:border-box}
body{background:#000;color:#00ff41;font-family:'Courier New',monospace;margin:0;padding:0;overflow-x:hidden}
#matrix{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;opacity:.25}
#wrap{position:relative;z-index:1}
#head{display:flex;align-items:center;gap:10px;padding:12px;background:rgba(0,20,0,.85);border-bottom:1px solid #00ff41;position:sticky;top:0;z-index:5;box-shadow:0 0 15px #00ff4144}
#root{font-size:24px;font-weight:900;color:#fff;background:linear-gradient(135deg,#ff0000,#7a0000);padding:6px 18px;border-radius:8px;letter-spacing:3px;text-shadow:0 0 8px #fff;box-shadow:0 0 20px #ff0000aa;animation:blink 2s infinite}
@keyframes blink{50%{box-shadow:0 0 35px #ff0000}}
#title{font-size:15px;color:#00ff41;text-shadow:0 0 6px #00ff41}
#clr{margin-right:auto;background:#001a00;color:#00ff41;border:1px solid #00ff41;border-radius:8px;padding:8px 14px;font-size:14px;font-family:inherit}
#out{padding:10px;padding-bottom:90px}
.blk{background:rgba(0,15,0,.9);border:1px solid #00ff41;border-radius:10px;margin-bottom:10px;overflow:hidden;box-shadow:0 0 10px #00ff4133}
.cmd{padding:8px 12px;color:#00ff41;font-weight:bold;font-size:14px;border-bottom:1px dashed #00ff4155;word-break:break-all;text-shadow:0 0 5px #00ff41}
.cmd::before{content:"#> "}
.res{padding:8px 12px;white-space:pre-wrap;word-break:break-word;font-size:13px;color:#ccffcc;min-height:8px}
.cpy{display:block;width:100%;background:#001a00;color:#00ff41;border:none;border-top:1px solid #00ff4155;padding:9px;font-size:13px;font-family:inherit}
#bar{display:flex;gap:6px;position:fixed;bottom:0;left:0;right:0;background:rgba(0,20,0,.92);padding:10px;border-top:1px solid #00ff41;z-index:5}
#cmd{flex:1;font-size:16px;padding:12px;background:#000;color:#00ff41;border:1px solid #00ff41;border-radius:10px;font-family:inherit;box-shadow:0 0 8px #00ff4166}
#go{font-size:18px;padding:10px 20px;background:#003300;color:#00ff41;border:1px solid #00ff41;border-radius:10px}
#login{position:fixed;inset:0;z-index:10;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.9);padding:20px}
#lbox{width:100%;max-width:340px;background:#000;border:1px solid #00ff41;border-radius:14px;padding:28px 22px;text-align:center;box-shadow:0 0 30px #00ff4166}
#lbox h1{color:#ff0000;font-size:34px;letter-spacing:5px;margin:0 0 4px;text-shadow:0 0 12px #ff0000}
#lbox p{color:#00ff41;font-size:14px;margin:0 0 18px}
#lbox input{width:100%;margin-bottom:10px;padding:12px;background:#001100;color:#00ff41;border:1px solid #00ff41;border-radius:8px;font-size:16px;font-family:inherit;text-align:center}
#lgo{width:100%;padding:12px;background:#003300;color:#00ff41;border:1px solid #00ff41;border-radius:8px;font-size:17px;font-family:inherit}
#lerr{color:#ff3333;min-height:20px;font-size:13px;margin-top:8px}
.hidden{display:none!important}
</style></head><body>
<canvas id="matrix"></canvas>
<div id="wrap" class="hidden">
<div id="head"><span id="root">ROOT</span><span id="title">طرفية السيرفر</span><button id="clr">مسح</button></div>
<div id="out"></div><div id="end"></div>
<div id="bar"><input id="cmd" placeholder="اكتب الأمر هنا..." autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"><button id="go">▶</button></div>
</div>
<div id="login"><div id="lbox">
<h1>ROOT</h1><p>// الدخول إلى الطرفية //</p>
<input id="lu" placeholder="اسم المستخدم" autocomplete="off"><input id="lp" type="password" placeholder="كلمة المرور">
<button id="lgo">دخول</button><div id="lerr"></div>
</div></div>
<script>
let off=0;let out=document.getElementById('out');let cur=null;
let cv=document.getElementById('matrix'),cx=cv.getContext('2d');
function msize(){cv.width=innerWidth;cv.height=innerHeight;}
msize();addEventListener('resize',msize);
let cols=Math.floor(innerWidth/16),drops=Array(cols).fill(0);
setInterval(()=>{cx.fillStyle='rgba(0,0,0,.08)';cx.fillRect(0,0,cv.width,cv.height);
cx.fillStyle='#00ff41';cx.font='14px monospace';
for(let i=0;i<drops.length;i++){let t=String.fromCharCode(0x30A0+Math.random()*96);
cx.fillText(t,i*16,drops[i]*16);if(drops[i]*16>cv.height&&Math.random()>.975)drops[i]=0;drops[i]++;}},50);
function scrollDown(){document.getElementById('end').scrollIntoView(false);}
function newBlock(c){let d=document.createElement('div');d.className='blk';
d.innerHTML='<div class="cmd"></div><div class="res"></div><button class="cpy">نسخ المخرجات</button>';
d.querySelector('.cmd').textContent=c;out.appendChild(d);cur=d.querySelector('.res');
let myres=cur;
d.querySelector('.cpy').onclick=(e)=>{navigator.clipboard.writeText(myres.textContent).then(()=>{e.target.textContent='تم النسخ';setTimeout(()=>e.target.textContent='نسخ المخرجات',1500);});};
scrollDown();}
async function poll(){try{
let r=await fetch('/read?off='+off);let j=await r.json();
if(j.data){if(!cur)newBlock('...');cur.textContent+=j.data;scrollDown();}
off=j.off;}catch(e){}setTimeout(poll,800);}
async function send(){let c=document.getElementById('cmd').value;if(!c)return;
document.getElementById('cmd').value='';newBlock(c);
await fetch('/write',{method:'POST',body:c+'\\n'});}
document.getElementById('go').onclick=send;
document.getElementById('clr').onclick=()=>{out.innerHTML='';cur=null;};
document.getElementById('cmd').addEventListener('keydown',e=>{if(e.key==='Enter')send();});
async function doLogin(){let u=document.getElementById('lu').value,p=document.getElementById('lp').value;
let r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({u:u,p:p})});
if(r.ok){document.getElementById('login').classList.add('hidden');document.getElementById('wrap').classList.remove('hidden');poll();}
else{document.getElementById('lerr').textContent='بيانات خاطئة';}}
document.getElementById('lgo').onclick=doLogin;
document.getElementById('lp').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
(async()=>{let r=await fetch('/read?off=0');if(r.ok){document.getElementById('login').classList.add('hidden');document.getElementById('wrap').classList.remove('hidden');off=(await r.json()).off;poll();}})();
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
    def _deny(self):
        b = b"Unauthorized"
        self.send_response(401)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            return self._send(PAGE)
        if not authorized(self.headers, self.path):
            return self._deny()
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
        if self.path.startswith("/login"):
            try:
                ln = int(self.headers.get("Content-Length", 0))
                cred = json.loads(self.rfile.read(ln) or b"{}")
            except Exception:
                cred = {}
            if cred.get("u") == LOGIN_USER and cred.get("p") == LOGIN_PASS:
                b = b'{"ok":1}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "auth=" + SESSION + "; Path=/; Max-Age=31536000; SameSite=Lax")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self._deny()
            return
        if not authorized(self.headers, self.path):
            return self._deny()
        if self.path.startswith("/write"):
            ln = int(self.headers.get("Content-Length", 0))
            cmd = self.rfile.read(ln)
            try: os.write(shell["m"], cmd)
            except OSError: pass
            return self._send('{"ok":1}', "application/json")
        self.send_error(404)

spawn_shell()
HTTPServer(("0.0.0.0", PORT), H).serve_forever()
