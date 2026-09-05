import os
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
TARGET_CHAT_ID = -1003999489709  # Bildirimlerin düşeceği senin grubun

# Mesajların toplanacağı 5 kaynak grup
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
        print("[HATA] BOT_TOKEN eksik!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TARGET_CHAT_ID,
        "text": msg
    }
    try:
        r = requests.post(url, json=payload, timeout=5)
        res = r.json()
        if not res.get("ok"):
            print(f"[GÖNDERME HATASI]: {res.get('description')}")
        else:
            print("[BAŞARILI] Bildirim grubuna iletildi!")
    except Exception as e:
        print(f"[İstek Hatası]: {e}")

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def message_listener(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', f"Grup ({event.chat_id})")
    text = event.raw_text or ""

    print(f"\n[YENİ VERİ] Kaynak: {chat_title} (ID: {event.chat_id})")
    alert_msg = f"🚨 YENİ SANDIK!\nKaynak: {chat_title}\n\n{text}"
    send_alert(alert_msg)

async def main():
    print("=== 5 Kaynak Grup Dinleniyor ===")
    send_alert("🤖 Userbot Başlatıldı! 5 kaynak grup dinleniyor, sandıklar bu gruba aktarılacak.")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
