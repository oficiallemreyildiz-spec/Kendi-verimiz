import os
import asyncio
import logging
import threading
import requests

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, EnvelopeEvent
from httpx import Proxy


# ============================================================
# RENDER PORT SERVER
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
        f"Web server aktif - PORT {port}"
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

PROXY_URL = os.getenv(
    "PROXY_URL",
    ""
).strip()

MIN_DIAMONDS = int(
    os.getenv(
        "MIN_DIAMONDS",
        "1"
    )
)


# ============================================================
# STREAM LIST
# ============================================================

TARGET_STREAMERS = [
    "sakura12p4",
    "sanackindy3",
    "aikanyan0727"
]


# ============================================================
# PROXY
# ============================================================

def get_proxy():

    if not PROXY_URL:

        logging.warning(
            "PROXY_URL bulunamadi."
        )

        return None

    try:

        proxy_url = PROXY_URL.strip()

        # Olası markdown kalıntılarını temizle
        if "](" in proxy_url:

            proxy_url = (
                proxy_url
                .split("](")[0]
                .replace("[", "")
                .strip()
            )

        if proxy_url.endswith(")"):

            proxy_url = proxy_url[:-1].strip()

        parsed = urlparse(
            proxy_url
        )

        if not parsed.hostname:

            logging.error(
                "Proxy hostname bulunamadi."
            )

            return None

        if not parsed.port:

            logging.error(
                "Proxy port bulunamadi."
            )

            return None

        scheme = parsed.scheme or "http"

        username = (
            unquote(parsed.username)
            if parsed.username
            else None
        )

        password = (
            unquote(parsed.password)
            if parsed.password
            else None
        )

        hostname = parsed.hostname
        port = parsed.port

        logging.info(
            f"Proxy: {scheme}://"
            f"{hostname}:{port}"
        )

        if username:

            logging.info(
                "Proxy kullanici adi mevcut."
            )

        else:

            logging.warning(
                "Proxy kullanici adi yok."
            )

        if password:

            logging.info(
                "Proxy sifresi mevcut."
            )

        else:

            logging.warning(
                "Proxy sifresi yok."
            )

        # Kullanıcı adı/şifreyi Proxy'ye
        # ayrı olarak veriyoruz.

        clean_url = (
            f"{scheme}://"
            f"{hostname}:{port}"
        )

        if username and password:

            proxy = Proxy(
                clean_url,
                auth=(
                    username,
                    password
                )
            )

        else:

            proxy = Proxy(
                clean_url
            )

        logging.info(
            "Proxy nesnesi olusturuldu."
        )

        return proxy

    except Exception as e:

        logging.exception(
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
# SINGLE STREAM
# ============================================================

async def monitor_stream(username):

    while True:

        client = None

        try:

            logging.info(
                "----------------------------------------"
            )

            logging.info(
                f"Baglanti deneniyor: @{username}"
            )

            # Her bağlantıda yeni proxy oluştur
            proxy = get_proxy()

            client_kwargs = {
                "unique_id": username
            }

            if proxy:

                client_kwargs[
                    "web_proxy"
                ] = proxy

                client_kwargs[
                    "ws_proxy"
                ] = proxy

                logging.info(
                    f"Proxy aktif: @{username}"
                )

            else:

                logging.warning(
                    f"Proxy yok: @{username}"
                )

            client = TikTokLiveClient(
                **client_kwargs
            )


            # =================================================
            # CONNECT
            # =================================================

            @client.on(ConnectEvent)
            async def on_connect(
                event: ConnectEvent
            ):

                logging.info(
                    f"YAYINA BAGLANDI: @{username}"
                )


            # =================================================
            # ENVELOPE
            # =================================================

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
                        f"Envelope: @{username} "
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
                        f"KUTU BULUNDU: "
                        f"@{username} "
                        f"({diamonds})"
                    )

                except Exception as e:

                    logging.exception(
                        f"Envelope hatasi "
                        f"@{username}: {e}"
                    )


            # =================================================
            # START
            # =================================================

            logging.info(
                f"TikTokLive start(): "
                f"@{username}"
            )

            await client.start()

            # start() normal şekilde dönerse
            logging.warning(
                f"TikTokLive baglantisi sonlandi: "
                f"@{username}"
            )

        except Exception as e:

            error = str(e)

            if "407" in error:

                logging.error(
                    f"407 PROXY AUTHENTICATION "
                    f"REQUIRED: @{username}"
                )

                logging.error(
                    "Proxy kullanici adi veya "
                    "sifresi kabul edilmiyor."
                )

            elif "403" in error:

                logging.error(
                    f"403 TIKTOK ERISIM HATASI: "
                    f"@{username}"
                )

            else:

                logging.error(
                    f"Baglanti hatasi "
                    f"@{username}: {e}"
                )

        finally:

            try:

                if client:

                    await client.disconnect()

            except Exception:

                pass


        # =====================================================
        # RECONNECT
        # =====================================================

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
        "TikTok Live Tarama Botu BASLATILDI"
    )

    logging.info(
        f"Hesap sayisi: "
        f"{len(TARGET_STREAMERS)}"
    )

    logging.info(
        "========================================"
    )


    # --------------------------------------------------------
    # ENV CHECK
    # --------------------------------------------------------

    if BOT_TOKEN:

        logging.info(
            "BOT_TOKEN OK"
        )

    else:

        logging.error(
            "BOT_TOKEN YOK!"
        )


    if CHAT_ID:

        logging.info(
            "CHAT_ID OK"
        )

    else:

        logging.error(
            "CHAT_ID YOK!"
        )


    if PROXY_URL:

        logging.info(
            "PROXY_URL OK"
        )

    else:

        logging.warning(
            "PROXY_URL YOK!"
        )


    # --------------------------------------------------------
    # STREAMLER
    # --------------------------------------------------------

    tasks = []

    for username in TARGET_STREAMERS:

        tasks.append(
            asyncio.create_task(
                monitor_stream(username)
            )
        )


    # --------------------------------------------------------
    # BOTU SUREKLI CALISTIR
    # --------------------------------------------------------

    await asyncio.gather(
        *tasks
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    # Render health server
    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    # Botu başlat
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
