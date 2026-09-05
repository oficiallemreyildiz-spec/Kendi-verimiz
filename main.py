import asyncio
import html
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

import httpx
import websockets


# ============================================================
# AYARLAR
# ============================================================

API_KEY = os.getenv("TIKTOOL_API_KEY", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "").strip()
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "").strip()

MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "3"))
WATCH_SECONDS = int(os.getenv("WATCH_SECONDS", "3"))
MAX_LIVES = int(os.getenv("MAX_LIVES", "10"))
DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL", "300"))

API_BASE = "https://api.tik.tools"
WS_BASE = "wss://api.tik.tools"


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True,
)

log = logging.getLogger("TreasureAlert")


# ============================================================
# PROXY
# ============================================================

def build_proxy():
    if not PROXY_HOST or not PROXY_PORT:
        return None

    if PROXY_USERNAME and PROXY_PASSWORD:
        return (
            "http://"
            + quote(PROXY_USERNAME, safe="")
            + ":"
            + quote(PROXY_PASSWORD, safe="")
            + "@"
            + PROXY_HOST
            + ":"
            + PROXY_PORT
        )

    return (
        "http://"
        + PROXY_HOST
        + ":"
        + PROXY_PORT
    )


HTTP_PROXY = build_proxy()


# ============================================================
# TELEGRAM
# ============================================================

async def telegram_send(text):

    if not BOT_TOKEN or not CHAT_ID:
        log.error("❌ Telegram ENV eksik.")
        return

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:

            response = await client.post(
                url,
                json=payload,
            )

            if response.status_code == 200:
                log.info("📨 Telegram gönderildi.")
            else:
                log.error(
                    "❌ Telegram HTTP %s",
                    response.status_code,
                )

    except Exception as e:
        log.error(
            "❌ Telegram hatası: %s",
            e,
        )


# ============================================================
# LIVE KEŞFİ
# ============================================================

async def discover_live():

    log.info("🔎 LIVE listesi yenileniyor...")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Linux; Android 16) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://tik.tools/",
    }

    try:

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            proxy=HTTP_PROXY,
            headers=headers,
        ) as client:

            response = await client.get(
                f"{API_BASE}/api/live/top-channels"
            )

            log.info(
                "🔎 TOP-CHANNELS HTTP %s",
                response.status_code,
            )

            if response.status_code != 200:
                log.error(
                    "❌ LIVE keşfi başarısız: HTTP %s",
                    response.status_code,
                )
                return []

            result = response.json()

    except Exception as e:

        log.error(
            "❌ LIVE keşif hatası: %s",
            e,
        )

        return []

    channels = result.get("channels", [])

    if not isinstance(channels, list):
        return []

    lives = []
    seen = set()

    for channel in channels:

        if not isinstance(channel, dict):
            continue

        username = (
            channel.get("uniqueId")
            or channel.get("unique_id")
            or channel.get("username")
        )

        if not username:
            continue

        username = (
            str(username)
            .strip()
            .lstrip("@")
        )

        key = username.lower()

        if key in seen:
            continue

        seen.add(key)

        lives.append(
            {
                "username": username,
                "room_id": str(
                    channel.get("roomId")
                    or channel.get("room_id")
                    or ""
                ),
                "region": str(
                    channel.get("region")
                    or ""
                ),
            }
        )

        if len(lives) >= MAX_LIVES:
            break

    log.info(
        "🔥 %s LIVE bulundu.",
        len(lives),
    )

    for i, live in enumerate(lives, 1):
        log.info(
            "🔴 %s/%s -> @%s | %s",
            i,
            len(lives),
            live["username"],
            live["region"],
        )

    return lives


# ============================================================
# DUPLICATE
# ============================================================

sent_envelopes = set()


# ============================================================
# HAZİNE
# ============================================================

async def handle_envelope(
    username,
    data,
    event_type="envelope",
):

    if not isinstance(data, dict):
        data = {}

    envelope_id = (
        data.get("envelopeId")
        or data.get("envelope_id")
        or ""
    )

    envelope_id = str(envelope_id)

    if envelope_id:

        key = (
            f"{username.lower()}:{envelope_id}"
        )

        if key in sent_envelopes:
            return

        sent_envelopes.add(key)

    diamonds = (
        data.get("diamondCount")
        or data.get("diamond_count")
        or 0
    )

    people = (
        data.get("peopleCount")
        or data.get("people_count")
        or 0
    )

    sender = (
        data.get("sendUserName")
        or data.get("send_user_name")
        or ""
    )

    if not sender:

        user = data.get("user", {})

        if isinstance(user, dict):
            sender = (
                user.get("uniqueId")
                or user.get("unique_id")
                or user.get("nickname")
                or ""
            )

    sender = str(sender)

    if event_type == "superFanBox":

        title = "🟣 SUPER FAN BOX BULUNDU!"

        log.warning(
            "🟣🟣 SUPER FAN BOX -> @%s | 💎 %s",
            username,
            diamonds,
        )

    else:

        title = "🎁 HAZİNE BULUNDU!"

        log.warning(
            "🎁🎁 HAZİNE -> @%s | 💎 %s",
            username,
            diamonds,
        )

    message = (
        f"<b>{title}</b>\n\n"
        f"📺 Yayın: @{html.escape(username)}\n"
        f"💎 Değer: {html.escape(str(diamonds))}\n"
        f"👥 Kişi: {html.escape(str(people))}\n"
        f"👤 Gönderen: {html.escape(sender)}\n"
        f"🆔 Envelope: {html.escape(envelope_id)}\n\n"
        f"🌍 Global hızlı tarama"
    )

    await telegram_send(message)


# ============================================================
# JWT
# ============================================================

async def mint_jwt(username):

    if not API_KEY:
        log.error("❌ TIKTOOL_API_KEY yok.")
        return None

    url = (
        f"{API_BASE}/authentication/jwt"
        f"?apiKey={quote(API_KEY, safe='')}"
    )

    payload = {
        "allowed_creators": [username],
        "expire_after": 600,
        "max_websockets": 1,
    }

    try:

        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            proxy=HTTP_PROXY,
        ) as client:

            response = await client.post(
                url,
                json=payload,
            )

            log.info(
                "🔐 JWT HTTP %s -> @%s",
                response.status_code,
                username,
            )

            if response.status_code != 200:

                log.error(
                    "❌ JWT reddedildi -> @%s",
                    username,
                )

                return None

            result = response.json()

    except Exception as e:

        log.error(
            "❌ JWT hatası -> @%s | %s",
            username,
            e,
        )

        return None

    token = (
        result
        .get("data", {})
        .get("token")
    )

    if not token:

        log.error(
            "❌ JWT token yok -> @%s",
            username,
        )

        return None

    return token


# ============================================================
# WS EVENT
# ============================================================

async def process_ws_message(
    username,
    raw,
):

    if isinstance(raw, bytes):

        raw = raw.decode(
            "utf-8",
            errors="replace",
        )

    try:

        event = json.loads(raw)

    except Exception:

        return

    if not isinstance(event, dict):
        return

    event_name = (
        event.get("event")
        or event.get("type")
        or ""
    )

    data = event.get(
        "data",
        {},
    )

    if not isinstance(data, dict):
        data = {}

    if event_name == "envelope":

        await handle_envelope(
            username,
            data,
            "envelope",
        )

        return True

    if event_name == "superFanBox":

        await handle_envelope(
            username,
            data,
            "superFanBox",
        )

        return True

    return False


# ============================================================
# TEK LIVE - 3 SANİYE
# ============================================================

async def watch_live(username):

    username = (
        str(username)
        .strip()
        .lstrip("@")
    )

    log.info(
        "🎯 TARAMA -> @%s | %ss",
        username,
        WATCH_SECONDS,
    )

    token = await mint_jwt(username)

    if not token:
        return

    ws_url = (
        f"{WS_BASE}"
        f"?uniqueId={quote(username, safe='')}"
        f"&jwtKey={quote(token, safe='')}"
    )

    try:

        async with websockets.connect(
            ws_url,
            proxy=HTTP_PROXY,
            open_timeout=10,
            close_timeout=3,
            ping_interval=None,
            ping_timeout=None,
            max_size=None,
        ) as ws:

            log.info(
                "🟢 WS BAĞLANDI -> @%s",
                username,
            )

            loop = asyncio.get_running_loop()

            deadline = (
                loop.time()
                + WATCH_SECONDS
            )

            while True:

                remaining = (
                    deadline
                    - loop.time()
                )

                if remaining <= 0:
                    break

                try:

                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=remaining,
                    )

                except asyncio.TimeoutError:

                    break

                found = await process_ws_message(
                    username,
                    raw,
                )

                if found:

                    log.info(
                        "💰 HAZİNE YAKALANDI -> @%s",
                        username,
                    )

                    break

            log.info(
                "⏭️ SONRAKİ LIVE -> @%s",
                username,
            )

    except websockets.exceptions.InvalidStatus as e:

        log.error(
            "❌ WS HTTP -> @%s | %s",
            username,
            e,
        )

    except Exception as e:

        log.error(
            "❌ WS -> @%s | %s",
            username,
            e,
        )


# ============================================================
# PARALEL TARAMA
# ============================================================

async def scan_batch(
    lives,
    start_index,
):

    batch = lives[
        start_index:
        start_index + MAX_CONNECTIONS
    ]

    if not batch:
        return

    names = [
        live["username"]
        for live in batch
    ]

    log.info(
        "⚡ PARALEL GRUP -> %s",
        ", ".join(
            "@" + x
            for x in names
        ),
    )

    await asyncio.gather(
        *[
            watch_live(
                live["username"]
            )
            for live in batch
        ],
        return_exceptions=True,
    )


# ============================================================
# GLOBAL HIZLI SCANNER
# ============================================================

async def global_scanner():

    log.info(
        "🚀🚀🚀 HIZLI GLOBAL SCANNER 🚀🚀🚀"
    )

    log.info(
        "⚙️ MAX_CONNECTIONS = %s",
        MAX_CONNECTIONS,
    )

    log.info(
        "⚙️ WATCH_SECONDS = %s",
        WATCH_SECONDS,
    )

    log.info(
        "⚙️ MAX_LIVES = %s",
        MAX_LIVES,
    )

    lives = []
    last_discovery = 0

    while True:

        now = asyncio.get_running_loop().time()

        # ====================================================
        # LIVE LİSTESİNİ SADECE GEREKTİĞİNDE YENİLE
        # ====================================================

        if (
            not lives
            or now - last_discovery
            >= DISCOVERY_INTERVAL
        ):

            new_lives = await discover_live()

            if new_lives:

                lives = new_lives

                last_discovery = now

            else:

                await asyncio.sleep(5)

                continue

        # ====================================================
        # LİSTEYİ 3'LÜ GRUPLARLA TARA
        # ====================================================

        for index in range(
            0,
            len(lives),
            MAX_CONNECTIONS,
        ):

            await scan_batch(
                lives,
                index,
            )

        # ====================================================
        # TUR BİTTİ
        # ====================================================

        log.info(
            "🔄 TUR BİTTİ -> HEMEN YENİ TURA GEÇİLİYOR"
        )

        # Bekleme YOK.
        # Aynı LIVE listesi tekrar taranıyor.


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"TreasureAlert OK\n"
        )

    def log_message(
        self,
        format,
        *args,
    ):
        return


def start_health_server():

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    server = HTTPServer(
        (
            "0.0.0.0",
            port,
        ),
        HealthHandler,
    )

    log.info(
        "🌐 Render health server -> PORT %s",
        port,
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

async def main():

    log.info(
        "🔥🔥🔥 TREASURE ALERT GLOBAL BAŞLIYOR 🔥🔥🔥"
    )

    log.info(
        "🔑 TIKTOOL_API_KEY: %s",
        "VAR" if API_KEY else "YOK",
    )

    log.info(
        "🤖 BOT_TOKEN: %s",
        "VAR" if BOT_TOKEN else "YOK",
    )

    log.info(
        "💬 CHAT_ID: %s",
        "VAR" if CHAT_ID else "YOK",
    )

    log.info(
        "🌐 HTTP PROXY: %s",
        "AKTİF" if HTTP_PROXY else "KAPALI",
    )

    await global_scanner()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        log.info(
            "🛑 TreasureAlert durduruldu."
        )

    except Exception as e:

        log.exception(
            "💥 ANA HATA: %s",
            e,
        )

        raise
