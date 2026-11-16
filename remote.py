"""手机遥控银月：手机和电脑连同一个 Wi-Fi，手机浏览器打开聊天网页，就能和银月聊天、让她操作电脑。

- 地址和口令在 data/remote_url.txt（启动时生成，也写在日志里）。没有口令的请求一律拒绝。
- 只在局域网监听；出门在外要用，建议手机和电脑都装 Tailscale，别把端口暴露到公网。
- 手机发来的话和语音走同一个大脑和工具（Assistant.handle），桌面形象也会弹浮窗。
"""
import html
import json
import logging
import secrets
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
TOKEN_FILE = ROOT / "data" / "remote_token.txt"
FRAME_PORT = 8771
_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="theme-color" content="#5c6bc0">
<link rel="manifest" href="/manifest.json?token=__TOKEN__">
<title>银月</title><style>
*{box-sizing:border-box}body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:linear-gradient(#e9ecfb,#f6f2fb);color:#334;display:flex;flex-direction:column;height:100dvh}
#stage{height:42dvh;display:flex;justify-content:center;align-items:flex-end;flex-shrink:0}
#yy{height:100%;filter:drop-shadow(0 6px 10px #5c6bc033)}
#bar{display:flex;gap:6px;overflow-x:auto;padding:8px 12px;flex-shrink:0}
#bar button,#bar select{flex-shrink:0;font-size:14px;padding:7px 12px;border:1px solid #c9cdf0;border-radius:16px;background:#fff;color:#4a55a2}
#log{flex:1;overflow-y:auto;padding:4px 14px}
.m{max-width:82%;margin:7px 0;padding:9px 12px;border-radius:14px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.u{background:#5c6bc0;color:#fff;margin-left:auto;border-bottom-right-radius:4px}
.y{background:#fff;border-bottom-left-radius:4px;box-shadow:0 1px 2px #0001}
form{display:flex;gap:8px;padding:10px 12px calc(10px + env(safe-area-inset-bottom));background:#fff;border-top:1px solid #dde}
input[type=text]{flex:1;font-size:16px;padding:10px 12px;border:1px solid #ccd;border-radius:20px;outline:none}
form button{font-size:16px;padding:0 18px;border:0;border-radius:20px;background:#5c6bc0;color:#fff}
#mic{padding:0 14px;background:#eef0fb;color:#5c6bc0;user-select:none;-webkit-user-select:none;touch-action:none}
#mic.rec{background:#e53935;color:#fff}
label{font-size:13px;color:#667;display:flex;align-items:center;gap:4px;flex-shrink:0}
</style></head><body>
<div id="stage"><img id="yy" alt="银月"></div>
<div id="bar">
 <button data-a="kpop">跳舞</button><button data-a="heart">比心</button><button data-a="spin">转圈</button>
 <button data-a="wave">挥手</button><button data-a="run">跑一圈</button>
 <select id="outfit"><option value="">换装…</option>__OUTFITS__</select>
 <label><input type="checkbox" id="voice" checked>手机放声音</label>
</div>
<div id="log"><div class="m y">主人，我在。想聊天或者让我操作电脑，直接打字就行。</div></div>
<form id="f"><button type="button" id="mic" title="按住说话">🎤</button><input id="t" type="text" placeholder="比如：打开B站搜索凡人修仙传" autocomplete="off"><button>发送</button></form>
<script>
const token=new URLSearchParams(location.search).get("token")||"";
const log=document.getElementById("log"),t=document.getElementById("t"),yy=document.getElementById("yy");
function frame(){const n=new Image();n.onload=()=>{yy.src=n.src;setTimeout(frame,120)};n.onerror=()=>setTimeout(frame,1000);n.src="/frame?token="+token+"&t="+Date.now()}
frame();
function add(text,cls){const d=document.createElement("div");d.className="m "+cls;d.textContent=text;log.appendChild(d);log.scrollTop=1e9;return d}
function presentReply(w,j,voice){
 const text=j.reply||j.error||"（没有回复）";
 if(!j.reply||!voice){w.textContent=text;return}
 w.textContent="准备朗读……";
 const sound=new Audio("/tts?token="+token+"&text="+encodeURIComponent(text));
 sound.onplaying=()=>{w.textContent=text;log.scrollTop=1e9};
 const failed=()=>{w.textContent=text+"（语音暂时无法播放）"};
 sound.onerror=failed;
 sound.play().catch(failed);
}
async function post(url,body){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(Object.assign({token},body))});return r.json()}
document.querySelectorAll("#bar button").forEach(b=>b.onclick=()=>post("/action",{kind:"action",name:b.dataset.a}));
document.getElementById("outfit").onchange=e=>{if(e.target.value)post("/action",{kind:"outfit",name:e.target.value});e.target.value=""};
document.getElementById("f").onsubmit=async e=>{e.preventDefault();const text=t.value.trim();if(!text)return;t.value="";add(text,"u");
 const w=add("……","y"),voice=document.getElementById("voice").checked;
 try{const j=await post("/chat",{text,phone_voice:voice});presentReply(w,j,voice);
 }catch(err){w.textContent="连接失败："+err}};
// 按住说话：录音 → 传回电脑识别 → 和打字一样处理。浏览器只在 https 地址下允许用麦克风
const mic=document.getElementById("mic");let rec=null,held=false,requesting=false;
async function startRec(e){e.preventDefault();
 if(held||requesting||rec)return;
 if(!window.isSecureContext||!navigator.mediaDevices){add("手机浏览器只在 https 地址下允许用麦克风。请用 Tailscale 的 https 地址打开，或者用输入法自带的语音输入。","y");return}
 held=true;requesting=true;mic.setPointerCapture(e.pointerId);let st=null;
 try{st=await navigator.mediaDevices.getUserMedia({audio:true});
  if(!held){st.getTracks().forEach(x=>x.stop());return} // 等待权限时已经松手，不再开始录音
  const current=new MediaRecorder(st),chunks=[];rec=current;
  current.ondataavailable=ev=>chunks.push(ev.data);
  current.onstop=async()=>{clearTimeout(limit);st.getTracks().forEach(x=>x.stop());if(rec===current)rec=null;mic.classList.remove("rec");
   const blob=new Blob(chunks,{type:current.mimeType});if(blob.size<2000)return;
   const u=add("（语音识别中…）","u"),w=add("……","y"),voice=document.getElementById("voice").checked;
   try{const r=await fetch("/voice?token="+token+"&phone_voice="+(voice?1:0),{method:"POST",body:blob});const j=await r.json();
    u.textContent=j.text||"（没听清）";presentReply(w,j,voice);
   }catch(err){w.textContent="连接失败："+err}};
  current.start();mic.classList.add("rec");const limit=setTimeout(()=>stopRec(),60000);
 }catch(err){if(st)st.getTracks().forEach(x=>x.stop());rec=null;held=false;add("录音失败："+err,"y")}
 finally{requesting=false}}
function stopRec(e){if(e)e.preventDefault();held=false;if(rec&&rec.state==="recording")rec.stop()}
mic.addEventListener("pointerdown",startRec);mic.addEventListener("pointerup",stopRec);mic.addEventListener("pointercancel",stopRec);mic.addEventListener("lostpointercapture",stopRec);
document.addEventListener("visibilitychange",()=>{if(document.hidden)stopRec()});
</script></body></html>"""

MANIFEST = """{"name":"银月","short_name":"银月","start_url":"/?token=__TOKEN__","display":"standalone",
"background_color":"#eef0f8","theme_color":"#5c6bc0"}"""



def all_ips() -> list[str]:
    """电脑上所有 IPv4 地址（家里 Wi-Fi、Tailscale 的 100.x 等都会列出来）。"""
    ips = {lan_ip()}
    try:
        ips |= {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def lan_ip() -> str:
    """本机局域网 IP（UDP connect 不会真的发数据，只是让系统选出对外的网卡地址）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def create_server(port, token, on_reply, tts, outfits, asr, host="0.0.0.0"):
    """构造服务，不加载模型；可绑定本机随机端口做 HTTP 回归验证。"""
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, code, message):
            return self._send(code, json.dumps({"error": message}, ensure_ascii=False))

        def _read_body(self, limit):
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                self._error(400, "请求长度不正确")
                return None
            if length <= 0 or length > limit:
                self._error(413 if length > limit else 400, "请求过大" if length > limit else "请求内容为空")
                return None
            try:
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("incomplete body")
                return data
            except (OSError, ValueError):
                self._error(400, "请求未接收完整，请重试")
                return None

        def _send_bytes(self, code, data, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if q.get("token", [""])[0] != token:
                return self._send(403, "<h3>需要正确的访问口令</h3>", "text/html; charset=utf-8")
            if u.path == "/frame":   # 银月的实时画面（从桌面形象进程转发）
                try:
                    data = urllib.request.urlopen(f"http://127.0.0.1:{FRAME_PORT}/", timeout=2).read()
                    return self._send_bytes(200, data, "image/png")
                except Exception:
                    return self._send_bytes(503, b"", "image/png")
            if u.path == "/tts":     # 用银月的嗓音把回复念出来，给手机播放
                try:
                    return self._send_bytes(200, tts(q.get("text", [""])[0]), "audio/wav")
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
            if u.path == "/manifest.json":
                return self._send(200, MANIFEST.replace("__TOKEN__", token), "application/manifest+json")
            opts = "".join(f'<option value="{html.escape(n)}">{html.escape(n)}</option>' for n in outfits)
            self._send(200, PAGE.replace("__TOKEN__", token).replace("__OUTFITS__", opts), "text/html; charset=utf-8")

        def do_POST(self):
            u = urlparse(self.path)
            path = u.path
            if path == "/voice":     # 手机录音：口令放在网址里，正文是录音文件
                q = parse_qs(u.query)
                if q.get("token", [""])[0] != token:
                    return self._send(403, json.dumps({"error": "口令不对"}, ensure_ascii=False))
                data = self._read_body(8_000_000)
                if data is None:
                    return
                try:
                    text = asr(data)
                    if not text:
                        return self._send(200, json.dumps({"text": "", "reply": "没听清，主人再说一遍？"}, ensure_ascii=False))
                    logging.info("手机语音: %s", text)
                    reply = on_reply(text, q.get("phone_voice", ["0"])[0] == "1")
                except Exception as e:
                    logging.exception("手机语音处理失败")
                    return self._error(500, "语音处理失败，请稍后重试")
                return self._send(200, json.dumps({"text": text, "reply": reply}, ensure_ascii=False))
            if path not in ("/chat", "/action"):
                return self._send(404, json.dumps({"error": "not found"}))
            data = self._read_body(16_384)
            if data is None:
                return
            try:
                req = json.loads(data)
                if not isinstance(req, dict):
                    raise ValueError("expected object")
            except (ValueError, UnicodeError):
                return self._send(400, json.dumps({"error": "bad request"}))
            if req.get("token") != token:
                return self._send(403, json.dumps({"error": "口令不对"}, ensure_ascii=False))
            if path == "/action":   # 快捷按钮：让桌面形象做动作 / 换装
                kind, name = req.get("kind", "action"), req.get("name")
                if kind not in ("action", "outfit") or not isinstance(name, str) or not 0 < len(name) <= 40:
                    return self._error(400, "动作或换装参数不正确")
                if kind == "outfit" and name not in (*outfits, "next", "random"):
                    return self._error(400, "没有这套衣服")
                _udp.sendto(json.dumps({"type": kind, "text": name}, ensure_ascii=False).encode("utf-8"),
                            ("127.0.0.1", 8770))
                return self._send(200, json.dumps({"ok": True}))
            if not isinstance(req.get("text", ""), str):
                return self._error(400, "消息必须是文字")
            text = req.get("text", "").strip()[:500]
            if not text:
                return self._send(400, json.dumps({"error": "空消息"}, ensure_ascii=False))
            logging.info("手机: %s", text)
            try:
                reply = on_reply(text, bool(req.get("phone_voice")))
            except Exception as e:
                logging.exception("手机指令处理失败")
                reply = f"出错了：{e}"
            self._send(200, json.dumps({"reply": reply}, ensure_ascii=False))

    return ThreadingHTTPServer((host, port), Handler)


def start(assistant, port: int, on_reply, tts, outfits, asr) -> str:
    """在后台线程启动遥控网页服务，返回手机要打开的地址。"""
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    if not TOKEN_FILE.exists() or len(TOKEN_FILE.read_text(encoding="utf-8").strip()) < 24:
        TOKEN_FILE.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    server = create_server(port, token, on_reply, tts, outfits, asr)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    urls = [f"http://{ip}:{port}/?token={token}" for ip in all_ips()]
    note = "\n".join(["手机打开下面任意一个能通的地址（Tailscale 的是 100. 开头，在外面也能用）：", ""] + urls + [
        "", "出门在外使用：电脑和手机都安装 Tailscale 并登录同一个账号，然后用 100. 开头的地址。",
        "口令就是地址里 token= 后面那串，别发给别人。"])
    (ROOT / "data" / "remote_url.txt").write_text(note, encoding="utf-8")
    logging.info("手机遥控已开启（端口 %s；完整地址保存在 data/remote_url.txt）", port)
    return urls[0] if urls else ""
