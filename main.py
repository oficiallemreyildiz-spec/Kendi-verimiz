import os
import re
import time
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
        r = requests.post(url, json=payload, timeout=5)
        res = r.json()
        if not res.get("ok"):
            err = res.get("description", "")
            print(f"[GÖNDERME HATASI]: {err}")
            if "retry after" in err:
                sec = int(re.search(r'\d+', err).group()) if re.search(r'\d+', err) else 5
                time.sleep(sec + 1)
        else:
            print("[BAŞARILI] Linkli bildirim iletildi!")
    except Exception as e:
        print(f"[İstek Hatası]: {e}")

def parse_tiktok_message(text, chat_title):
    username = None
    clean_lines = []

    for line in text.splitlines():
        # ## T12345> kullanıcıadı satırını yakala (Arapça, sembol vs. hepsini kapsar)
        if "##" in line and ">" in line:
            parts = line.split(">", 1)
            if len(parts) > 1:
                raw_user = parts[1].strip()
                if raw_user:
                    username = raw_user

        # Çöp link satırlarını temizle
        if "dichvu321" in line or line.strip() in [">", "=", "-"]:
            continue
        clean_lines.append(line)

    body = "\n".join(clean_lines).strip()

    if username:
        # Arapça veya özel karakterli isimleri güvenli URL formatına çevirir
        encoded_user = quote(username)
        live_link = f"https://www.tiktok.com/@{encoded_user}/live"
        return (
            f"🚨 YENİ SANDIK!\n"
            f"Kaynak: {chat_title}\n\n"
            f"{body}\n\n"
            f"🔗 CANLI YAYIN LİNKİ:\n{live_link}"
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
