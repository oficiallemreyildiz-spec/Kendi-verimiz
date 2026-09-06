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

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/tiktok':
            qs = parse_qs(parsed_path.query)
            room = qs.get('room', [''])[0]
            user = qs.get('user', [''])[0]
            
            # HyperOS / Android uyumlu zorunlu açılış kodu
            html_content = f"""<!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>TikTok'a Geçiliyor</title>
                <script>
                    setTimeout(function() {{
                        if("{room}" !== "") {{
                            window.location.href = "intent://webcast/room/{room}#Intent;scheme=snssdk1233;package=com.zhiliaoapp.musically;end;";
                        }}
                    }}, 100);
                    setTimeout(function() {{
                        window.location.href = "https://www.tiktok.com/search/user?q={user}";
                    }}, 2000);
                </script>
            </head>
            <body style="background:#000; color:#fff; text-align:center; padding: 50px; font-family: sans-serif;">
                <h2>⚡ Odaya Bağlanıyor...</h2>
                <p style="color:#aaa;">Bağlanamazsa otomatik arama sayfasına geçilecek.</p>
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
        r.json()
    except Exception:
        pass

def extract_room_id(text):
    urls = re.findall(r'https?://[^\s]+', text)
    for url in urls:
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            p_val = qs.get('p', [None])[0]
            r_val = qs.get('r', [None])[0]
            
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
    
    msg = f"🚨 <b>YENİ SANDIK!</b>\nKaynak: <i>{escaped_title}</i>\n\n{escaped_body}\n\n"
    
    if room_id and safe_user:
        redirect_link = f"{APP_URL}/tiktok?room={room_id}&user={safe_user}"
        msg += f"🔥 <a href='{redirect_link}'>DİREKT ODAYA GİR</a>\n\n"
        
        # Hata anında direkt arama motoruna atan yedek link
        search_link = f"https://www.tiktok.com/search/user?q={safe_user}"
        msg += f"🔍 <a href='{search_link}'>TİKTOK'TA ARA (Garanti Yöntem)</a>"
    elif safe_user:
        search_link = f"https://www.tiktok.com/search/user?q={safe_user}"
        msg += f"🔍 <a href='{search_link}'>TİKTOK'TA ARA (Garanti Yöntem)</a>"
    
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
    print("=== Arama Destekli Bot Aktif ===")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
