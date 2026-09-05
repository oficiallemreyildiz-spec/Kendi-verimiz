import os
import json
import asyncio
import requests
import websockets

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

API_BASE = "https://api.tik.tools"
WS_BASE = "wss://api.tik.tools"
DEMO_KEY = "your_api_key"

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

def get_top_live_user():
    """Canlıdaki aktif yayıncılardan birini çeker"""
    try:
        res = requests.get(f"{API_BASE}/api/live/top-channels", timeout=10)
        if res.status_code == 200:
            channels = res.json().get("channels", [])
            if channels:
                return channels[0].get("uniqueId")
    except Exception as e:
        print(f"[Kanal Alma Hatası]: {e}")
    return None

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
        try:
            user = get_top_live_user()
            if not user:
                print("[UYARI] Canlı yayıncı bulunamadı, 30sn sonra tekrar denenecek...")
                await asyncio.sleep(30)
                continue

            print(f"[*] Canlı yayın seçildi: @{user}")
            token = get_jwt_token(user)
            if not token:
                print(f"[HATA] @{user} için JWT alınamadı, bekleniyor...")
                await asyncio.sleep(15)
                continue

            ws_url = f"{WS_BASE}?uniqueId={user}&jwtKey={token}"
            print(f"[BAĞLANTI] @{user} yayınına bağlanılıyor...")

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
            print("[KOPMA] WebSocket bağlantısı kapandı, yeni kanala geçiliyor...")
        except Exception as e:
            print(f"[Döngü Hatası]: {e}")

        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(stream_monitor())
