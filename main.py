import os
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"TikTok Bot is running!")
    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

import requests
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, EnvelopeEvent
from httpx import Proxy

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MIN_DIAMONDS = int(os.getenv("MIN_DIAMONDS", 1))
PROXY_URL = os.getenv("PROXY_URL")

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
    try:
        client_kwargs = {"unique_id": unique_id}
        
        if PROXY_URL:
            clean_proxy = PROXY_URL.strip().split("(")[0].strip().replace("[", "").replace("]", "").rstrip(")")
            proxy_obj = Proxy(clean_proxy)
            client_kwargs["web_proxy"] = proxy_obj
            client_kwargs["ws_proxy"] = proxy_obj

        client = TikTokLiveClient(**client_kwargs)

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
