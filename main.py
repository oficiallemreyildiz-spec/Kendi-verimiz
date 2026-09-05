import os
import asyncio
import logging
import threading
import requests

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, EnvelopeEvent
from httpx import Proxy


# ============================================================
# RENDER WEB SERVER
# ============================================================

class SimpleHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TikTok Bot is running!")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        SimpleHandler
    )

    logging.info(f"Web server baslatildi: PORT={port}")

    server.serve_forever()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

MIN_DIAMONDS = int(
    os.getenv("MIN_DIAMONDS", "1")
)

PROXY_URL = os.getenv(
    "PROXY_URL",
    ""
).strip()


# ============================================================
# PROXY BILGISI
# ============================================================

def create_proxy():

    if not PROXY_URL:
        logging.warning(
            "PROXY_URL bulunamadi! Proxy olmadan devam edilecek."
        )
        return None

    try:

        # Render'da bazen deger su sekilde gelebiliyor:
        #
        # [http://user:pass@host:port](http://...)
        #
        # Gereksiz markdown kisimlarini temizle.

        proxy_url = PROXY_URL

        if "](" in proxy_url:

            proxy_url = proxy_url.split("](")[0]
            proxy_url = proxy_url.replace("[", "")

        proxy_url = proxy_url.strip()

        if proxy_url.endswith(")"):
            proxy_url = proxy_url[:-1]

        logging.info(
            "Proxy yapilandiriliyor..."
        )

        parsed = urlparse(proxy_url)

        if not parsed.hostname:
            logging.error(
                "Proxy URL icinde hostname bulunamadi."
            )
            return None

        if not parsed.port:
            logging.error(
                "Proxy URL icinde port bulunamadi."
            )
            return None

        scheme = parsed.scheme or "http"

        logging.info(
            f"Proxy host: {parsed.hostname}"
        )

        logging.info(
            f"Proxy port: {parsed.port}"
        )

        if parsed.username:
            logging.info(
                "Proxy kullanici adi mevcut."
            )
        else:
            logging.warning(
                "Proxy kullanici adi YOK."
            )

        if parsed.password:
            logging.info(
                "Proxy sifresi mevcut."
            )
        else:
            logging.warning(
                "Proxy sifresi YOK."
            )

        # URL'nin tamamini httpx'e veriyoruz.
        # Kullanici adi/sifreyi elle parcalaMIYORUZ.

        clean_proxy_url = (
            f"{scheme}://"
            f"{parsed.netloc}"
        )

        proxy = Proxy(
            clean_proxy_url
        )

        logging.info(
            "Proxy basariyla olusturuldu."
        )

        return proxy

    except Exception as e:

        logging.error(
            f"Proxy olusturma hatasi: {e}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(text):

    if not BOT_TOKEN:

        logging.error(
            "BOT_TOKEN bulunamadi!"
        )

        return

    if not CHAT_ID:

        logging.error(
            "CHAT_ID bulunamadi!"
        )

        return

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        response.raise_for_status()

        logging.info(
            "Telegram bildirimi gonderildi."
        )

    except Exception as e:

        logging.error(
            f"Telegram mesaj hatasi: {e}"
        )


# ============================================================
# TELEGRAM ASYNC WRAPPER
# ============================================================

async def send_telegram_async(text):

    await asyncio.to_thread(
        send_telegram_message,
        text
    )


# ============================================================
# STREAM MONITOR
# ============================================================

async def monitor_stream(
    unique_id,
    proxy_obj
):

    client = None

    try:

        logging.info(
            f"Baglanti deneniyor: @{unique_id}"
        )

        client_kwargs = {
            "unique_id": unique_id
        }

        # Proxy varsa TikTokLive'a ver.

        if proxy_obj:

            client_kwargs["web_proxy"] = proxy_obj
            client_kwargs["ws_proxy"] = proxy_obj

            logging.info(
                f"Proxy aktif: @{unique_id}"
            )

        client = TikTokLiveClient(
            **client_kwargs
        )


        # ====================================================
        # CONNECT
        # ====================================================

        @client.on(ConnectEvent)
        async def on_connect(
            event: ConnectEvent
        ):

            logging.info(
                f"YAYIN BAGLANDI: @{unique_id}"
            )


        # ====================================================
        # ENVELOPE / TREASURE
        # ====================================================

        @client.on(EnvelopeEvent)
        async def on_envelope(
            event: EnvelopeEvent
        ):

            try:

                diamonds = getattr(
                    event,
                    "diamonds",
                    0
                )

                if not diamonds:

                    diamonds = getattr(
                        event,
                        "coins",
                        0
                    )

                diamonds = diamonds or 0

                logging.info(
                    f"Envelope bulundu: "
                    f"@{unique_id} "
                    f"diamonds={diamonds}"
                )

                if diamonds < MIN_DIAMONDS:
                    return

                msg = (
                    "🎁 <b>TREASURE BOX / GOODY BAG</b>\n\n"
                    f"👤 <b>Yayıncı:</b> @{unique_id}\n"
                    f"💎 <b>Elmas:</b> {diamonds}\n"
                    f"⚡ <a href="
                    f"'https://www.tiktok.com/"
                    f"@{unique_id}/live'>"
                    f"YAYINA GİT</a>"
                )

                await send_telegram_async(
                    msg
                )

                logging.info(
                    f"KUTU BULUNDU: "
                    f"@{unique_id} "
                    f"({diamonds} Elmas)"
                )

            except Exception as err:

                logging.error(
                    f"Kutu okuma hatasi "
                    f"@{unique_id}: {err}"
                )


        # ====================================================
        # START
        # ====================================================

        await client.start()

    except Exception as e:

        error_text = str(e)

        if "407" in error_text:

            logging.warning(
                f"PROXY AUTH HATASI: "
                f"@{unique_id} -> "
                f"407 Proxy Authentication Required"
            )

        elif "403" in error_text:

            logging.warning(
                f"TIKTOK 403: "
                f"@{unique_id}"
            )

        else:

            logging.warning(
                f"Baglanti kurulamadi: "
                f"@{unique_id}: {e}"
            )

    finally:

        try:

            if client:

                await client.disconnect()

        except Exception:

            pass


# ============================================================
# MAIN
# ============================================================

async def main():

    logging.info(
        "========================================"
    )

    logging.info(
        "TikTok Live Tarama Botu Baslatildi"
    )

    logging.info(
        "========================================"
    )


    # --------------------------------------------------------
    # Telegram kontrol
    # --------------------------------------------------------

    if BOT_TOKEN:

        logging.info(
            "BOT_TOKEN bulundu."
        )

    else:

        logging.error(
            "BOT_TOKEN YOK!"
        )


    if CHAT_ID:

        logging.info(
            "CHAT_ID bulundu."
        )

    else:

        logging.error(
            "CHAT_ID YOK!"
        )


    # --------------------------------------------------------
    # Proxy
    # --------------------------------------------------------

    proxy_obj = create_proxy()


    # --------------------------------------------------------
    # Takip edilecek hesaplar
    # --------------------------------------------------------

    target_streamers = [
        "sakura12p4",
        "sanackindy3",
        "aikanyan0727"
    ]


    logging.info(
        f"Takip edilen hesap sayisi: "
        f"{len(target_streamers)}"
    )


    # --------------------------------------------------------
    # Paralel baglantilar
    # --------------------------------------------------------

    tasks = []

    for username in target_streamers:

        tasks.append(
            monitor_stream(
                username,
                proxy_obj
            )
        )


    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    # Render health-check server
    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    # Bot
    asyncio.run(
        main()
    )
