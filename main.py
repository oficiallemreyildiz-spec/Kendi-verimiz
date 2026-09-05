import os
import asyncio
import logging
import threading
import requests

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import quote

from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, EnvelopeEvent
from httpx import Proxy


# ============================================================
# RENDER WEB SERVER
# ============================================================

class SimpleHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        self.wfile.write(
            b"TikTok Bot is running!"
        )

    def log_message(self, format, *args):
        pass


def run_web_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        SimpleHandler
    )

    logging.info(
        f"Web server aktif - PORT={port}"
    )

    server.serve_forever()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True
)


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()

CHAT_ID = os.getenv(
    "CHAT_ID",
    ""
).strip()

MIN_DIAMONDS = int(
    os.getenv(
        "MIN_DIAMONDS",
        "1"
    )
)

PROXY_USERNAME = os.getenv(
    "PROXY_USERNAME",
    ""
).strip()

PROXY_PASSWORD = os.getenv(
    "PROXY_PASSWORD",
    ""
).strip()


# ============================================================
# 3 TIKTOK + 3 WEBSHARE PROXY
# ============================================================

STREAM_PROXIES = {

    "sakura12p4": (
        "31.59.20.176",
        6754
    ),

    "sanackindy3": (
        "45.38.107.97",
        6014
    ),

    "aikanyan0727": (
        "198.105.121.200",
        6462
    ),
}


# ============================================================
# PROXY OLUSTUR
# ============================================================

def create_proxy(host, port):

    if not PROXY_USERNAME:
        logging.error(
            "PROXY_USERNAME bulunamadi!"
        )
        return None

    if not PROXY_PASSWORD:
        logging.error(
            "PROXY_PASSWORD bulunamadi!"
        )
        return None

    try:

        # URL karakterlerini güvenli hale getir
        username = quote(
            PROXY_USERNAME,
            safe=""
        )

        password = quote(
            PROXY_PASSWORD,
            safe=""
        )

        proxy_url = (
            f"http://{username}:"
            f"{password}@{host}:{port}"
        )

        proxy = Proxy(proxy_url)

        logging.info(
            f"Proxy hazir: {host}:{port}"
        )

        return proxy

    except Exception as e:

        logging.error(
            f"Proxy olusturma hatasi "
            f"{host}:{port}: {e}"
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
        "https://api.telegram.org/"
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
            timeout=15
        )

        response.raise_for_status()

        logging.info(
            "Telegram bildirimi gonderildi."
        )

    except Exception as e:

        logging.error(
            f"Telegram hatasi: {e}"
        )


async def telegram_message(text):

    await asyncio.to_thread(
        send_telegram_message,
        text
    )


# ============================================================
# TEK TIKTOK HESABINI IZLE
# ============================================================

async def monitor_stream(username):

    host, port = STREAM_PROXIES[username]

    while True:

        client = None

        try:

            logging.info(
                "========================================"
            )

            logging.info(
                f"@{username} baglaniyor..."
            )

            logging.info(
                f"Proxy: {host}:{port}"
            )

            proxy = create_proxy(
                host,
                port
            )

            if proxy is None:

                logging.error(
                    f"@{username} proxy "
                    f"olusturulamadi."
                )

                await asyncio.sleep(30)
                continue


            # ------------------------------------------------
            # TikTokLive Client
            # ------------------------------------------------

            client = TikTokLiveClient(
                unique_id=username,
                web_proxy=proxy,
                ws_proxy=proxy
            )


            # ------------------------------------------------
            # CONNECT
            # ------------------------------------------------

            @client.on(ConnectEvent)
            async def on_connect(
                event: ConnectEvent
            ):

                logging.info(
                    f"✅ YAYINA BAGLANDI: "
                    f"@{username}"
                )


            # ------------------------------------------------
            # ENVELOPE
            # ------------------------------------------------

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
                        f"🎁 ENVELOPE: "
                        f"@{username} "
                        f"diamonds={diamonds}"
                    )

                    if diamonds < MIN_DIAMONDS:
                        return


                    message = (
                        "🎁 <b>"
                        "TREASURE BOX / GOODY BAG"
                        "</b>\n\n"

                        f"👤 <b>Yayıncı:</b> "
                        f"@{username}\n"

                        f"💎 <b>Elmas:</b> "
                        f"{diamonds}\n"

                        f"⚡ <a href="
                        f"'https://www.tiktok.com/"
                        f"@{username}/live'>"
                        f"YAYINA GİT"
                        f"</a>"
                    )

                    await telegram_message(
                        message
                    )

                    logging.info(
                        f"🚨 KUTU BULUNDU: "
                        f"@{username} "
                        f"({diamonds})"
                    )

                except Exception as e:

                    logging.exception(
                        f"Envelope hatasi "
                        f"@{username}: {e}"
                    )


            # ------------------------------------------------
            # START
            # ------------------------------------------------

            logging.info(
                f"TikTokLive baslatiliyor: "
                f"@{username}"
            )

            await client.start()

            logging.warning(
                f"@{username} baglantisi sona erdi."
            )


        except Exception as e:

            error = str(e)

            if "407" in error:

                logging.error(
                    f"❌ 407 PROXY AUTH: "
                    f"@{username} "
                    f"-> {host}:{port}"
                )

                logging.error(
                    "Webshare proxy kimlik "
                    "dogrulamasi reddedildi."
                )

            elif "403" in error:

                logging.error(
                    f"❌ 403 TIKTOK: "
                    f"@{username}"
                )

            else:

                logging.error(
                    f"❌ BAGLANTI HATASI: "
                    f"@{username}: {e}"
                )


        finally:

            try:

                if client:
                    await client.disconnect()

            except Exception:
                pass


        # ------------------------------------------------
        # RECONNECT
        # ------------------------------------------------

        logging.warning(
            f"@{username} 30 saniye sonra "
            f"yeniden denenecek..."
        )

        await asyncio.sleep(30)


# ============================================================
# MAIN
# ============================================================

async def main():

    logging.info(
        "========================================"
    )

    logging.info(
        "🚀 TikTok Live Tarama Botu BASLATILDI"
    )

    logging.info(
        "3 hesap / 3 ayri Webshare proxy"
    )

    logging.info(
        "========================================"
    )


    # --------------------------------------------------------
    # ENV KONTROL
    # --------------------------------------------------------

    logging.info(
        f"BOT_TOKEN: "
        f"{'OK' if BOT_TOKEN else 'YOK'}"
    )

    logging.info(
        f"CHAT_ID: "
        f"{'OK' if CHAT_ID else 'YOK'}"
    )

    logging.info(
        f"PROXY_USERNAME: "
        f"{'OK' if PROXY_USERNAME else 'YOK'}"
    )

    logging.info(
        f"PROXY_PASSWORD: "
        f"{'OK' if PROXY_PASSWORD else 'YOK'}"
    )


    # --------------------------------------------------------
    # PROXY LISTESI
    # --------------------------------------------------------

    for username, (host, port) in STREAM_PROXIES.items():

        logging.info(
            f"@{username} "
            f"-> {host}:{port}"
        )


    # --------------------------------------------------------
    # 3 HESABI AYNI ANDA BASLAT
    # --------------------------------------------------------

    tasks = []

    for username in STREAM_PROXIES:

        task = asyncio.create_task(
            monitor_stream(username)
        )

        tasks.append(task)


    await asyncio.gather(
        *tasks
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logging.info(
            "Bot durduruldu."
        )

    except Exception as e:

        logging.exception(
            f"ANA PROGRAM HATASI: {e}"
        )
