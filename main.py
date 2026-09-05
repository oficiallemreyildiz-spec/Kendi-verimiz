import os
import re
import time
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
        r = requests.post(url, json=payload, timeout=5)
        res = r.json()
        if not res.get("ok"):
            err = res.get("description", "")
            print(f"[GÖNDERME HATASI]: {err}")
            # Rate limit yakalanırsa bekle
            if "retry after" in err:
                retry_sec = int(re.search(r'\d+', err).group()) if re.search(r'\d+', err) else 5
                time.sleep(retry_sec + 1)
        else:
            print("[BAŞARILI] Temiz bildirim iletildi.")
    except Exception as e:
        print(f"[İstek Hatası]: {e}")

def parse_tiktok_message(text, chat_title):
    # Kullanıcı adını çek: ## Txxxxx> username
    match = re.search(r'##\s*[^>\n]+>\s*([a-zA-Z0-9_.-]+)', text)
    if not match:
        # Alternatif eşleşme: sadece > sonrasındaki ilk kelime
        match = re.search(r'>\s*([a-zA-Z0-9_.-]+)', text)
    
    username = match.group(1).strip() if match else None

    # Mesajdan dichvu321 ve çöp link satırlarını temizle
    lines = text.split('\n')
    clean_lines = []
    for line in lines:
        if "dichvu321.com" in line or line.strip() == ">" or line.strip() == "=":
            continue
        clean_lines.append(line)
    
    body = "\n".join(clean_lines).strip()

    if username:
        live_link = f"https://www.tiktok.com/@{username}/live"
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
    await asyncio.sleep(1.2)  # Telegram limitine takılmamak için hafif bekleme

async def main():
    print("=== 5 Kaynak Grup Dinleniyor ===")
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
