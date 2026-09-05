import os
import asyncio
import logging
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, TreasureBoxEvent
import requests

# Log yapılandırması
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MIN_DIAMONDS = int(os.getenv("MIN_DIAMONDS", 1))
# Render üzerinde tanımlayabileceğiniz opsiyonel PROXY_URL (örneğin: http://user:pass@ip:port)
PROXY_URL = os.getenv("PROXY_URL", None)

def send_telegram_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        logging.error("BOT_TOKEN veya CHAT_ID bulunamadı!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        logging.error(f"Telegram mesajı gönderilemedi: {e}")

async def monitor_stream(unique_id):
    # Client yapılandırması (Proxy ve Custom Headers desteği ile)
    client_kwargs = {
        "unique_id": unique_id,
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }
    }
    
    if PROXY_URL:
        client_kwargs["proxy"] = PROXY_URL

    client = TikTokLiveClient(**client_kwargs)

    @client.on(ConnectEvent)
    async def on_connect(event: ConnectEvent):
        logging.info(f"Yayın bağlantısı kuruldu: {unique_id}")

    @client.on(TreasureBoxEvent)
    async def on_treasure_box(event: TreasureBoxEvent):
        try:
            diamonds = getattr(event, "diamonds", 0) or getattr(event, "coins", 0)
            if diamonds >= MIN_DIAMONDS:
                msg = (
                    f"🎁 <b>TREASURE BOX / GOODY BAG</b>\n\n"
                    f"👤 <b>Yayıncı:</b> @{unique_id}\n"
                    f"💎 <b>Elmas:</b> {diamonds}\n"
                    f"⚡ <a href='https://www.tiktok.com/@{unique_id}/live'>YAYINA GİT</a>"
                )
                send_telegram_message(msg)
                logging.info(f"Hazine kutusu bildirildi: {unique_id} ({diamonds} Elmas)")
        except Exception as err:
            logging.error(f"Kutu işleme hatası: {err}")

    try:
        await client.start()
    except Exception as e:
        logging.warning(f"{unique_id} yayınına bağlanılamadı: {e}")

async def main():
    logging.info("TikTok Live Tarama Botu Başlatıldı...")
    # Örnek yayıncı listesi veya dinamik canlı yayın tarama döngüsü
    target_streamers = ["sakura12p4", "sanackindy3", "aikanyan0727"]
    
    tasks = [monitor_stream(username) for username in target_streamers]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
