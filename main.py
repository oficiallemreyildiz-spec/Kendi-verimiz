import os
import asyncio
import requests
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, CustomEvent

# Render Environment Variables (Ortam Değişkenleri) üzerinden alıyoruz
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram_msg(message: str):
    """Telegram'a anlık mesaj iletir"""
    if not BOT_TOKEN or not CHAT_ID:
        print("[HATA] Bot Token veya Chat ID tanımlı değil!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Telegram Hatası]: {e}")

async def listen_room(unique_id: str):
    """Belirtilen canlı yayına bağlanıp kutu/sandık eventlerini dinler"""
    client = TikTokLiveClient(unique_id=unique_id)

    @client.on(ConnectEvent)
    async def on_connect(event: ConnectEvent):
        print(f"[*] Yayına bağlanıldı: @{unique_id} (Room ID: {client.room_id})")

    # TikTok kutu / sandık / hediye verilerini yakalama
    @client.on(CustomEvent)
    async def on_custom(event: CustomEvent):
        event_name = getattr(event, "name", "").lower()
        # TikTok içindeki sandık / kutu event anahtarları
        if "treasure" in event_name or "box" in event_name or "envelope" in event_name:
            msg = (
                f"🚨 <b>YENİ SANDIK / KUTU BULUNDU!</b>\n\n"
                f"👤 <b>Yayıncı:</b> @{unique_id}\n"
                f"📦 <b>Event:</b> {event_name}\n"
                f"🔗 <b>Yayın Linki:</b> https://www.tiktok.com/@{unique_id}/live"
            )
            print(f"[BULDUM] @{unique_id} odasında kutu yakalandı!")
            send_telegram_msg(msg)

    try:
        await client.start()
    except Exception as e:
        print(f"[@{unique_id} Bağlantı Koptu/Hata]: {e}")

async def main():
    print("TikTok Canlı Akış Dinleyici Başlatılıyor...")
    send_telegram_msg("🤖 <b>TikTok Akış Botu Başlatıldı!</b> Veri dinleniyor...")
    
    # Buraya taranacak yayıncı havuzunu veya otomatik döngüyü ekliyoruz
    # Örnek: Takip edilecek hedef canlı yayıncı listesi
    target_users = ["hedef_kullanici_1", "hedef_kullanici_2"]
    
    tasks = [listen_room(user) for user in target_users]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
