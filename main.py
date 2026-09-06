import os
import re
import asyncio
import threading
from urllib.parse import quote
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
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

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
    
    clean_lines = []
    for line in text.splitlines():
        if any(bad in line for bad in ["dichvu321", "junb.io.vn", "box-countdown", "http"]):
            continue
        if line.strip() in [">", "=", "-", ""]:
            continue
        clean_lines.append(line)
    
    body = "\n".join(clean_lines).strip()
    msg = f"🚨 YENİ SANDIK!\nKaynak: {chat_title}\n\n{body}\n\n"
    
    if username:
        safe_original = quote(username)
        msg += f"🔗 1. İHTİMAL (Orijinal İsim):\nhttps://www.tiktok.com/@{safe_original}/live\n\n"
        
        # Eğer isimde alt tire varsa, Vietnamlılar bozmuş demektir. Temizleyip ikinci linki veriyoruz.
        if "_" in username:
            clean_user = username.replace("_", "")
            safe_clean = quote(clean_user)
            msg += f"🔗 2. İHTİMAL (Alt Tiresiz Gerçek İsim):\nhttps://www.tiktok.com/@{safe_clean}/live\n\n"
            
        msg += f"🔍 İKİSİ DE AÇMAZSA (TikTok'ta Ara):\nhttps://www.tiktok.com/search/user?q={safe_original}"
        
    return msg

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def message_listener(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', f"Grup ({event.chat_id})")
    text = event.raw_text or ""

    formatted_msg = parse_tiktok_message(text, chat_title)
    send_alert(formatted_msg)

async def main():
    print("=== Kesin Çözüm: 3 İhtimalli Çıplak Link Sistemi Aktif ===")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
