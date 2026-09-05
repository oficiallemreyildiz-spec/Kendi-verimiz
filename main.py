import os
import json
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import websockets

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

API_BASE = "https://api.tik.tools"
WS_BASE = "wss://api.tik.tools"
DEMO_KEY = "your_api_key"

# Render Web Service port kontrolünü susturmak için mini sunucu
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

def send_telegram(msg: str):
    """Telegram'a bildirim gönderir"""
    if not BOT_TOKEN or not CHAT_ID:
        print("[HATA] Bot Token veya Chat ID eksik!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Telegram Hatası]: {e}")

def get_live_users():
    """Canlı yayıncıları çeker, API yanıt vermezse yedek global havuzu kullanır"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(f"{API_BASE}/api/live/top-channels", headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            channels = data.get("channels", [])
            names = [c.get("uniqueId") for c in channels if c.get("uniqueId")]
            if names:
                return names
    except Exception as e:
        print(f"[API Hatası]: {e}")

    # Fallback: Sürekli canlıda olan popüler hesap havuzu
    return ["tiktok", "aljazeeraenglish", "pubgmobile", "nbcnews"]

def get_jwt_token(username: str):
    """WebSocket bağlantısı için JWT token üretir"""
    try:
        url = f"{API_BASE}/authentication/jwt?apiKey={DEMO_KEY}"
        payload = {
            "allowed_creators": [username],
            "expire_after": 600,
            "max_websockets": 1
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            return res.json().get("data", {}).get("token")
    except Exception as e:
        print(f"[JWT Hatası]: {e}")
    return None

async def stream_monitor():
    print("=== Tik.Tools Canlı Akış Dinleyici Başlatıldı ===")
    send_telegram("🤖 <b>Tik.Tools Akış Takipçisi Başlatıldı!</b>")

    while True:
        users = get_live_users()
        user = users[0] if users else None

        if not user:
            print("[UYARI] Yayıncı listesi boş, 10sn bekleniyor...")
            await asyncio.sleep(10)
            continue

        print(f"[*] Canlı yayın seçildi: @{user}")
        token = get_jwt_token(user)
        if not token:
            print(f"[HATA] @{user} için JWT alınamadı, diğer yayına geçiliyor...")
            await asyncio.sleep(10)
            continue

        ws_url = f"{WS_BASE}?uniqueId={user}&jwtKey={token}"
        print(f"[BAĞLANTI] @{user} yayınına bağlanılıyor...")

        try:
            async with websockets.connect(ws_url) as ws:
                print(f"[BAŞARILI] @{user} canlı akışı dinleniyor!")
                
                async for raw in ws:
                    event = json.loads(raw)
                    event_type = event.get("event")
                    
                    if event_type == "roomInfo":
                        continue

                    # Kırmızı Zarf / Kutu / Sandık tespiti
                    if event_type in ["envelope", "treasure", "box"] or "envelope" in str(event).lower():
                        alert = (
                            f"🚨 <b>KUTU / SANDIK / ZARF YAKALANDI!</b>\n\n"
                            f"👤 <b>Yayıncı:</b> @{user}\n"
                            f"📦 <b>Tür:</b> {event_type}\n"
                            f"🔗 <b>Yayın:</b> https://www.tiktok.com/@{user}/live"
                        )
                        print(f"[BULDUM] @{user} odasında kutu tespit edildi!")
                        send_telegram(alert)

        except websockets.exceptions.ConnectionClosed:
            print(f"[KOPMA] @{user} bağlantısı kapandı, sıradakine geçiliyor...")
        except Exception as e:
            print(f"[WebSocket Hatası]: {e}")

        await asyncio.sleep(5)

if __name__ == "__main__":
    # Render port hatasını engellemek için arka planda port dinleyici başlat
    threading.Thread(target=run_health_server, daemon=True).start()
    asyncio.run(stream_monitor())
