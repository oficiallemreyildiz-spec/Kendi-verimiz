import os
import re
import time
import json
import asyncio
import threading
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler
import aiohttp
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ---------------------------------------------------------------------------
# Yapılandırma ve Ortam Değişkenleri
# ---------------------------------------------------------------------------
API_ID = int(os.getenv("API_ID", "36135300"))
API_HASH = os.getenv("API_HASH", "737566711ac17fecd1ebeab1e2123773")
STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")

TARGET_CHAT_ID = -1004421946217

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445,
]

# ---------------------------------------------------------------------------
# Bellekte Canlı Tutulan Sandık ve Goody Bag Havuzları
# ---------------------------------------------------------------------------
LIVE_CHESTS = []
LIVE_GOODY_BAGS = []

# ---------------------------------------------------------------------------
# API & Port Sunucusu (Google Sites için Ayrılmış Çift Katman)
# ---------------------------------------------------------------------------
class RadarAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        now = int(time.time())
        global LIVE_CHESTS, LIVE_GOODY_BAGS

        # Süresi dolan sandık ve çantaları bellekten temizle
        LIVE_CHESTS = [b for b in LIVE_CHESTS if b.get("target_time", 0) > now]
        LIVE_GOODY_BAGS = [b for b in LIVE_GOODY_BAGS if b.get("target_time", 0) > now]

        if self.path.startswith("/api/boxes"):
            self.send_json_response(LIVE_CHESTS)
        elif self.path.startswith("/api/goody_bags"):
            self.send_json_response(LIVE_GOODY_BAGS)
        else:
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"VIP Radar API Aktif")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, *args):
        pass

def start_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), RadarAPIHandler)
    server.serve_forever()

# ---------------------------------------------------------------------------
# Telegram Gönderim Kuyruğu (Rate-limit korumalı)
# ---------------------------------------------------------------------------
send_queue: "asyncio.Queue[str]" = asyncio.Queue()
MIN_INTERVAL = 1.0

async def sender_worker(session: aiohttp.ClientSession):
    if not BOT_TOKEN:
        print("❌ HATA: BOT_TOKEN tanımlı değil!")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    last_sent = 0.0

    while True:
        msg = await send_queue.get()
        try:
            elapsed = asyncio.get_event_loop().time() - last_sent
            if elapsed < MIN_INTERVAL:
                await asyncio.sleep(MIN_INTERVAL - elapsed)

            payload = {
                "chat_id": TARGET_CHAT_ID,
                "text": msg,
                "disable_web_page_preview": True,
            }

            for attempt in range(3):
                try:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as res:
                        data = await res.json()
                        if data.get("ok"):
                            print(f"🚀 Telegram'a iletildi -> {TARGET_CHAT_ID}")
                            break
                        if res.status == 429:
                            retry_after = data.get("parameters", {}).get("retry_after", 2)
                            await asyncio.sleep(retry_after)
                            continue
                        break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    await asyncio.sleep(1.5 * (attempt + 1))

            last_sent = asyncio.get_event_loop().time()
        except Exception as e:
            print(f"❌ Sender worker hatası: {e}")
        finally:
            send_queue.task_done()

# ---------------------------------------------------------------------------
# Çok Dilli Regex ve Akıllı Ayrıştırma (İngilizce, Vietnamca, Bengalce, Türkçe)
# ---------------------------------------------------------------------------
def extract_username(text: str):
    # 1. '##' formatı
    for line in text.splitlines():
        if "##" in line:
            cleaned = re.sub(r'##\s*\S+', '', line).strip()
            cleaned = re.sub(r'^[\s>›:|-]+', '', cleaned).strip()
            if cleaned:
                return cleaned.replace(".", "")

    # 2. Doğrudan @etiketi tespiti
    m_user = re.search(r'@([a-zA-Z0-9_.]+)', text)
    if m_user:
        return m_user.group(1).replace(".", "")
    return None

def extract_coins(text: str) -> int:
    # İngilizce (coin), Vietnamca (xu), Bengalce (কয়েন), Türkçe ve ikonlar
    m = re.search(r'(\d+)\s*(?:coin|coins|xu|কয়েন|💎|🪙)', text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 10

def extract_duration(text: str) -> int:
    # '02:30' veya '2m30s'
    m_min_sec = re.search(r'(\d+)\s*[:m]\s*(\d+)\s*s?', text, re.IGNORECASE)
    if m_min_sec:
        return int(m_min_sec.group(1)) * 60 + int(m_min_sec.group(2))
    
    # '120s' veya Bengalce saniye (সেকেন্ড)
    m_sec = re.search(r'(\d+)\s*(?:s|sn|giây|সেকেন্ড)', text, re.IGNORECASE)
    if m_sec:
        return int(m_sec.group(1))
    
    return 180  # Varsayılan 3 dakika

def process_message(text: str, chat_title: str) -> str:
    raw_lower = text.lower()

    # Goody Bag tespiti: İngilizce, Vietnamca, Türkçe ve Bengalce (ব্যাগ / গুডিব্যাগ)
    is_goody = any(k in raw_lower for k in [
        "goody", "túi", "bag", "çanta", "গুডিব্যাগ", "ব্যাগ"
    ])

    username = extract_username(text)
    coins = extract_coins(text)
    duration = extract_duration(text)

    now = int(time.time())
    target_time = now + duration

    box_data = {
        "username": username or "Bilinmiyor",
        "coins": coins,
        "can_open": 5,
        "viewers": 25,
        "box_name": "🎒 ŞANS ÇANTASI (GOODY BAG)" if is_goody else "📦 HAZİNE SANDIĞI",
        "is_gold": (coins >= 100),
        "target_time": target_time,
        "total_duration": duration
    }

    # Ayrıştırılan öğeyi ilgili katman havuzuna ekle
    if username:
        if is_goody:
            LIVE_GOODY_BAGS.append(box_data)
        else:
            LIVE_CHESTS.append(box_data)

    header = "🎒 YENİ GOODY BAG!" if is_goody else "🚨 YENİ SANDIK!"
    live_link = f"https://www.tiktok.com/@{quote(username or '', safe='_-')}/live" if username else ""

    msg = f"{header}\nKaynak: {chat_title}\n💎 Değer: {coins} Coin\n⏱️ Süre: ~{duration}sn\n\n"
    if live_link:
        msg += f"🟢 CANLIYA GİT:\n{live_link}"
    else:
        msg += text.strip()

    return msg

# ---------------------------------------------------------------------------
# Telethon İstemcisi
# ---------------------------------------------------------------------------
client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH,
    connection_retries=None,
    retry_delay=1,
    auto_reconnect=True,
    request_retries=5,
)

http_session: aiohttp.ClientSession | None = None

@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def message_listener(event):
    text = event.raw_text or ""
    try:
        chat = await event.get_chat()
        chat_title = getattr(chat, "title", f"Grup ({event.chat_id})")
    except Exception:
        chat_title = f"Kanal ({event.chat_id})"

    if http_session is None:
        return

    formatted_msg = process_message(text, chat_title)
    await send_queue.put(formatted_msg)

async def main():
    global http_session
    print("=== VIP Telegram Radar Başlatılıyor... ===")

    connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        http_session = session
        asyncio.create_task(sender_worker(session))

        await client.start()
        await client.get_dialogs()
        print(f"✅ Dinleme aktif! Hedef: {TARGET_CHAT_ID}")
        await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
