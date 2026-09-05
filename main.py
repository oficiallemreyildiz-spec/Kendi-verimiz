import os
import asyncio
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from TikTokLive import TikTokLiveClient
from TikTokLive.events import EnvelopeEvent

# Render Uptime Sunucusu
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"TikTok Independent Bot Active!")

    def log_message(self, format, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

# Ortam Değişkenleri
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
MIN_DIAMONDS = int(os.getenv("MIN_DIAMONDS", 30))

http_session = requests.Session()
LOCAL_CACHE = set()
ACTIVE_CLIENTS = set()

# TikTok'tan Doğrudan Aktif Canlı Yayın Listesini Çeken Fonksiyon
def fetch_active_tiktok_lives():
    url = "https://www.tiktok.com/api/live/feed/?aid=1988&count=30"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/live"
    }
    users = []
    try:
        res = http_session.get(url, headers=headers, timeout=5)
        if res.ok:
            data = res.json()
            feed_list = data.get("itemList", []) or data.get("data", [])
            for item in feed_list:
                user_info = item.get("author") or item.get("owner") or {}
                username = user_info.get("uniqueId") or user_info.get("unique_id")
                if username:
                    users.append(username)
    except Exception as e:
        print(f"⚠️ TikTok Canlı Akış Tarama Hatası: {e}")
    return users

def is_duplicate(cache_key):
    if cache_key in LOCAL_CACHE:
        return True
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return False

    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    try:
        url = f"{UPSTASH_URL}/set/{cache_key}/1/NX/EX/15"
        res = http_session.get(url, headers=headers, timeout=2)
        if res.ok and res.json().get("result") == "OK":
            LOCAL_CACHE.add(cache_key)
            return False
        return True
    except Exception:
        return False

async def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True}
    try:
        await asyncio.to_thread(http_session.post, url, json=payload, timeout=2)
    except Exception:
        pass

async def listen_room(username):
    if username in ACTIVE_CLIENTS:
        return
    ACTIVE_CLIENTS.add(username)

    client = TikTokLiveClient(unique_id=username)

    @client.on(EnvelopeEvent)
    async def on_envelope(event: EnvelopeEvent):
        try:
            diamonds = getattr(event.envelope, "diamond_count", 0) or 0
            people = getattr(event.envelope, "people_count", 0) or 0

            if diamonds < MIN_DIAMONDS:
                return

            cache_key = f"hazine:{username}:{diamonds}"
            if is_duplicate(cache_key):
                return

            live_link = f"https://www.tiktok.com/@{username}/live"
            msg = (
                f"🎁 HAZİNE SANDIĞI (Doğrudan TikTok)\n"
                f"👤 YAYINCI: @{username}\n"
                f"💎 ELMAS: {diamonds}\n"
                f"👥 KİŞİ SAYSII: {people}\n"
                f"🔗 {live_link}"
            )
            print(f"[+] Sandık Bulundu: @{username} | Elmas: {diamonds}")
            await send_telegram(msg)
        except Exception as e:
            print(f"⚠️ Sandık ayrıştırma hatası (@{username}): {e}")

    try:
        print(f"🔗 Doğrudan TikTok Odisına Bağlanılıyor: @{username}")
        await client.start()
    except Exception:
        pass
    finally:
        ACTIVE_CLIENTS.remove(username)

async def scanner_loop():
    print("🚀 Doğrudan TikTok Canlı Yayın Taraması Başlatıldı...")
    while True:
        # TikTok'tan anlık canlı yayın yapan kullanıcıları çek
        live_users = await asyncio.to_thread(fetch_active_tiktok_lives)
        
        for user in live_users:
            if user not in ACTIVE_CLIENTS:
                asyncio.create_task(listen_room(user))
                
        await asyncio.sleep(10) # 10 saniyede bir yeni yayıncı tara

if __name__ == "__main__":
    Thread(target=run_dummy_server, daemon=True).start()
    asyncio.run(scanner_loop())
