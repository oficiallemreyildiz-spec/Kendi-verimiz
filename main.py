import os
import asyncio
import requests
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, GiftEvent

# Render Ortam Değişkenleri
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
    """Belirtilen canlı yayını dinler"""
    client = TikTokLiveClient(unique_id=unique_id)

    @client.on(ConnectEvent)
    async def on_connect(event: ConnectEvent):
        print(f"[*] Yayına başarıyla bağlanıldı: @{unique_id} (Room ID: {client.room_id})")

    @client.on(GiftEvent)
    async def on_gift(event: GiftEvent):
        event_dict = str(event).lower()
        if "chest" in event_dict or "box" in event_dict or "treasure" in event_dict:
            msg = (
                f"🚨 <b>YENİ SANDIK / KUTU BULUNDU!</b>\n\n"
                f"👤 <b>Yayıncı:</b> @{unique_id}\n"
                f"🔗 <b>Yayın Linki:</b> https://www.tiktok.com/@{unique_id}/live"
            )
            print(f"[BULDUM] @{unique_id} odasında kutu yakalandı!")
            send_telegram_msg(msg)

    try:
        await client.start()
    except Exception as e:
        print(f"[@{unique_id} Bağlantı Hatası]: {e}")

async def main():
    print("TikTok Canlı Akış Dinleyici Başlatılıyor...")
    send_telegram_msg("🤖 <b>TikTok Akış Botu Başlatıldı!</b>")
    
    # Test için aktif kullanıcı
    target_users = ["tiktok"]
    
    tasks = [listen_room(user) for user in target_users]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
