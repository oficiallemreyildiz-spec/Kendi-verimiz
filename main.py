import os
import re
import asyncio
import threading
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

def parse_tiktok_message(text, chat_title):
    username = None
    
    # İsmi yakalamak için en basit yöntem (ister > ister › olsun yakalar)
    match = re.search(r'##.*?[>›]\s*([^\s]+)', text)
    if match:
        username = match.group(1).strip()
    
    # Orijinal mesajdaki çöp kısımları temizle
    clean_lines = []
    for line in text.splitlines():
        if any(bad in line for bad in ["dichvu321", "junb.io.vn", "box-countdown", "http"]):
            continue
        if line.strip() in [">", "=", "-", ""]:
            continue
        clean_lines.append(line)
    
    body = "\n".join(clean_lines).strip()
    
    # Senin tam olarak istediğin saf link yapısı
    if username:
        link = f"https://www.tiktok.com/@{username}/live"
        msg = f"🚨 YENİ SANDIK!\nKaynak: {chat_title}\n\n{body}\n\n🔗 {link}"
    else:
        msg = f"🚨 YENİ SANDIK!\nKaynak: {chat_title}\n\n{body}"
        
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
    print("=== Saf Link Motoru Aktif ===")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
