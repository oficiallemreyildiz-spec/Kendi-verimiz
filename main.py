import os
import re
import time
import json
import base64
import html
import asyncio
import threading
from urllib.parse import quote, urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = 36135300
API_HASH = "737566711ac17fecd1ebeab1e2123773"
STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHAT_ID = -1003999489709
APP_URL = "https://kendi-verimiz.onrender.com"

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445
]

# Render üzerinde çalışan ve TikTok uygulamasını zorla odaya sokan köprü
class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/tiktok':
            qs = parse_qs(parsed_path.query)
            room = qs.get('room', [''])[0]
            user = qs.get('user', [''])[0]
            
            html_content = f"""<!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>TikTok'a Geçiliyor...</title>
                <script>
                    // Doğrudan uygulamanın içindeki odayı tetikler
                    setTimeout(function() {{
                        if("{room}" !== "") {{
                            window.location.href = "snssdk1233://webcast/room/{room}";
                        }}
                    }}, 100);
                    // Uygulama açılmazsa veya hata verirse yedek web linkine gider
                    setTimeout(function() {{
                        window.location.href = "https://www.tiktok.com/@{user}/live";
                    }}, 2000);
                </script>
            </head>
            <body style="background:#111; color:#fff; text-align:center; padding: 50px; font-family: sans-serif;">
                <h2>🚀 TikTok Doğrudan Odaya Yönlendiriliyor...</h2>
                <p style="color:#aaa;">Lütfen bekleyin...</p>
                <br><br>
                <a href="https://www.tiktok.com/@{user}/live" style="color:#00f2fe; text-decoration:none; font-size:18px; border:1px solid #00f2fe; padding:10px 20px; border-radius:5px;">Otomatik açılmazsa buraya tıklayın</a>
            </body>
            </html>"""
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
    
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), RequestHandler)
    server.serve_forever()

def send_alert(msg):
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TARGET_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=5)
        res = r.json()
        if not res.get("ok"):
            err = res.get("description", "")
            print(f"[GÖNDERME HATASI]: {err}")
            if "retry after" in err:
                sec = int(re.search(r'\d+', err).group()) if re.search(r'\d+', err) else 5
                time.sleep(sec + 1)
    except Exception as e:
        print(f"[İstek Hatası]: {e}")

def extract_room_id(text):
    urls = re.findall(r'https?://[^\s]+', text)
    for url in urls:
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            p_val = qs.get('p', [None])[0]
            r_val = qs.get('r', [None])[0]
            
            # Şifreli Oda ID'sini çözer
            if p_val:
                p_val += "=" * ((4 - len(p_val) % 4) % 4)
                decoded = base64.b64decode(p_val).decode('utf-8', errors='ignore').strip()
                if decoded.isdigit():
                    return decoded
            
            if r_val:
                r_val += "=" * ((4 - len(r_val) % 4) % 4)
                decoded = base64.b64decode(r_val).decode('utf-8', errors='ignore')
                if "{" in decoded:
                    data = json.loads(decoded)
                    return str(data.get('room', ''))
        except Exception:
            continue
    return None

def get_username(text):
    for line in text.splitlines():
        if "##" in line:
            cleaned = re.sub(r'##\s*\S+', '', line).strip()
            cleaned = re.sub(r'^[\s>›:|-]+', '', cleaned).strip()
            if cleaned:
                return cleaned
    return None

def parse_tiktok_message(text, chat_title):
    username = get_username(text)
    room_id = extract_room_id(text)
    
    clean_lines = []
    for line in text.splitlines():
        if any(bad in line for bad in ["dichvu321", "junb.io.vn", "box-countdown", "http"]):
            continue
        if line.strip() in [">", "=", "-", ""]:
            continue
        clean_lines.append(line)
    
    body = "\n".join(clean_lines).strip()
    escaped_body = html.escape(body)
    escaped_title = html.escape(chat_title)
    
    safe_user = quote(username) if username else ""
    live_link = f"https://www.tiktok.com/@{safe_user}/live" if safe_user else ""
    
    msg = f"🚨 <b>YENİ SANDIK!</b>\nKaynak: <i>{escaped_title}</i>\n\n{escaped_body}\n\n"
    
    if room_id and safe_user:
        redirect_link = f"{APP_URL}/tiktok?room={room_id}&user={safe_user}"
        msg += f"🔥 <a href='{redirect_link}'>DİREKT ODAYA GİR (Kısayol)</a>\n\n"
        msg += f"🔗 <a href='{live_link}'>Alternatif Web Linki</a>"
    elif safe_user:
        msg += f"🔗 <a href='{live_link}'>TIKLA VE YAYINA GİT</a>"
    
    return msg

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def message_listener(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', f"Grup ({event.chat_id})")
    text = event.raw_text or ""

    formatted_msg = parse_tiktok_message(text, chat_title)
    send_alert(formatted_msg)
    await asyncio.sleep(1.2)

async def main():
    print("=== 5 Kaynak Grup Dinleniyor (DeepLink Aktif) ===")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
