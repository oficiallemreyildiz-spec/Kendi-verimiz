import os
import re
import asyncio
import threading
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler
import aiohttp
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = 36135300
API_HASH = "737566711ac17fecd1ebeab1e2123773"
STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHAT_ID = -1003999489709

SOURCE_CHATS = [
    -1004421946217,  # Yeni eklenen kaynak kanal
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
    def log_message(self, *args):
        pass

def start_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheck)
    server.serve_forever()

async def send_alert(session, msg):
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TARGET_CHAT_ID,
        "text": msg,
        "disable_web_page_preview": True
    }
    try:
        async with session.post(url, json=payload, timeout=3) as res:
            await res.read()
    except Exception:
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
        clean_user = username.replace("_", "")
        safe_clean = quote(clean_user)
        msg += f"🔗 DİREKT LİNK:\nhttps://www.tiktok.com/@{safe_clean}/live"
        
    return msg

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
http_session = None

@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def message_listener(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', f"Grup ({event.chat_id})")
    text = event.raw_text or ""

    formatted_msg = parse_tiktok_message(text, chat_title)
    if http_session:
        asyncio.create_task(send_alert(http_session, formatted_msg))

async def main():
    global http_session
    print("=== VIP Kaynak Dinleyici Aktif ===")
    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=connector) as session:
        http_session = session
        await client.start()
        await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
