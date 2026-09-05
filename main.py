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
CHAT_ID = os.getenv("CHAT_ID")

# Render port kapanmasını önleyen sunucu
class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def start_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheck)
    server.serve_forever()

def send_alert(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("[HATA] Bot Token veya Chat ID eksik!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Telegram Hatası]: {e}")

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

@client.on(events.NewMessage)
async def message_listener(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', '')
    text = event.raw_text or ""

    # Gruptan gelen sandık verilerini filtrele
    if "BOT : 20 - 16" in chat_title or "BOX:" in text or "dichvu321.com" in text:
        print(f"[YENİ SANDIK] {chat_title} grubundan yakalandı!")
        alert_msg = (
            f"🚨 <b>YENİ SANDIK BİLDİRİMİ!</b>\n\n"
            f"{text}\n\n"
            f"⚡ <i>Otomatik Aktarım</i>"
        )
        send_alert(alert_msg)

async def main():
    print("=== Telegram Hesap Dinleyici Başlatıldı ===")
    await client.start()
    send_alert("🤖 <b>Userbot Başlatıldı!</b> Sandık grubu 7/24 dinleniyor...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
