import os
import re
import json
import base64
import time
import asyncio
import threading
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = 36135300
API_HASH = "737566711ac17fecd1ebeab1e2123773"

STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHAT_ID = -1003999489709

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445
]

class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheck)
    server.serve_forever()

def send_alert(msg):
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TARGET_CHAT_ID,
        "text": msg,
        "disable_web_page_preview": False
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
        else:
            print("[BAŞARILI] TikTok Canlı Yayın Linki iletildi!")
    except Exception as e:
        print(f"[İstek Hatası]: {e}")

def extract_tiktok_from_url(url_str):
    """Link içindeki Base64 verisinden gerçek kullanıcı adını çözer"""
    try:
        parsed = urlparse(url_str.strip())
        qs = parse_qs(parsed.query)
        # ?r= veya ?p= parametresini kontrol et
        encoded = qs.get('r', [None])[0] or qs.get('p', [None])[0]
        if encoded:
            # Base64 padding tamamlama
            padded = encoded + '=' * (-len(encoded) % 4)
            decoded_bytes = base64.b64decode(padded)
            decoded_str = decoded_bytes.decode('utf-8', errors='ignore')
            data = json.loads(decoded_str)
            user = data.get('user')
            if user:
                return user
    except Exception as e:
        print(f"[Link Çözme Hatası]: {e}")
    return None

def parse_tiktok_message(text, chat_title):
    username = None
    
    # 1. YOL: Mesaj içindeki linki yakala ve şifresini çöz
    urls = re.findall(r'https?://[^\s]+', text)
    for u in urls:
        extracted = extract_tiktok_from_url(u)
        if extracted:
            username = extracted
            break

    # 2. YOL (Yedek): Linkten çıkmazsa başlık satırından kullanıcı adını al
    if not username:
        for line in text.splitlines():
            if "##" in line and ">" in line:
                parts = line.split(">", 1)
                if len(parts) > 1 and parts[1].strip():
                    username = parts[1].strip()
                    break

    # Eski çöp link satırlarını temizle
    clean_lines = []
    for line in text.splitlines():
        if any(bad in line for bad in ["dichvu321", "junb.io.vn", "box-countdown"]) or line.strip() in [">", "=", "-"]:
            continue
        clean_lines.append(line)
    
    body = "\n".join(clean_lines).strip()

    # Link oluştur
    if username:
        live_link = f"https://www.tiktok.com/@{username}/live"
        return (
            f"🚨 YENİ SANDIK DÜŞTÜ!\n"
            f"Kaynak: {chat_title}\n"
            f"Yayıncı: @{username}\n\n"
            f"{body}\n\n"
            f"🔗 TİKTOK CANLI YAYIN LİNKİ:\n{live_link}"
        )
    return f"🚨 YENİ SANDIK!\nKaynak: {chat_title}\n\n{body}"

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
    print("=== 5 Kaynak Grup Dinleniyor ===")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
