import os
import asyncio
import logging
import requests

PROXY_URL = os.getenv("PROXY_URL")
if PROXY_URL:
    os.environ["HTTP_PROXY"] = PROXY_URL
    os.environ["HTTPS_PROXY"] = PROXY_URL

from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, EnvelopeEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MIN_DIAMONDS = int(os.getenv("MIN_DIAMONDS", 1))

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
        logging.error(f"Telegram mesaj hatası: {e}")

async def monitor_stream(unique_id):
    client = TikTokLiveClient(unique_id=unique_id)

    @client.on(ConnectEvent)
    async def on_connect(event: ConnectEvent):
        logging.info(f"Yayın bağlantısı kuruldu: {unique_id}")

    @client.on(EnvelopeEvent)
    async def on_envelope(event: EnvelopeEvent):
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
                logging.info(f"Kutu bulundu: {unique_id} ({diamonds} Elmas)")
        except Exception as err:
            logging.error(f"Kutu okuma hatası: {err}")

    try:
        await client.start()
    except Exception as e:
        logging.warning(f"{unique_id} yayınına bağlanılamadı: {e}")

async def main():
    logging.info("TikTok Live Tarama Botu Başlatıldı...")
    target_streamers = ["sakura12p4", "sanackindy3", "aikanyan0727"]
    tasks = [monitor_stream(username) for username in target_streamers]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
