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
# ENV
# ============================================================

API_KEY = os.getenv("TIKTOOL_API_KEY", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# Top-channels maksimum 10 canlı döndürüyor.
MAX_LIVES = 10

# Public ticker 5 dakikada bir kullanılmalı.
DISCOVERY_INTERVAL = 300

# Aynı anda kaç WebSocket?
# Free/Sandbox için düşük tutuyoruz.
MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "10")
)


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
# TELEGRAM
# ============================================================

async def telegram_send(text):

    if not BOT_TOKEN or not CHAT_ID:
        log.error(
            "❌ BOT_TOKEN veya CHAT_ID eksik."
        )
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

        async with httpx.AsyncClient(
            timeout=15
        ) as client:

            response = await client.post(
                url,
                json=payload,
            )

            if response.status_code != 200:

                log.error(
                    "❌ Telegram HTTP %s: %s",
                    response.status_code,
                    response.text[:300],
                )

    except Exception as e:

        log.error(
            "❌ Telegram bağlantı hatası: %s",
            e,
        )


# ============================================================
# GENEL LIVE KEŞFİ
# ============================================================

async def discover_live():

    if not API_KEY:

        log.error(
            "❌ TIKTOOL_API_KEY bulunamadı!"
        )

        return []

    try:

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
        ) as client:

            response = await client.get(
                "https://api.tik.tools/api/live/top-channels"
            )

            log.info(
                "🔎 TOP-CHANNELS HTTP %s",
                response.status_code,
            )

            if response.status_code != 200:

                log.error(
                    "❌ Top channels: %s",
                    response.text[:500],
                )

                return []

            result = response.json()

    except Exception as e:

        log.error(
            "❌ LIVE keşif hatası: %s",
            e,
        )

        return []

    channels = result.get(
        "channels",
        [],
    )

    if not isinstance(
        channels,
        list,
    ):
        channels = []

    lives = []
    seen = set()

    for channel in channels:

        if not isinstance(
            channel,
            dict,
        ):
            continue

        username = (
            channel.get("uniqueId")
            or channel.get("unique_id")
        )

        if not username:
            continue

        username = str(
            username
        ).strip().lstrip("@")

        key = username.lower()

        if not username or key in seen:
            continue

        seen.add(key)

        lives.append(
            {
                "username": username,
                "room_id": str(
                    channel.get("roomId")
                    or ""
                ),
                "region": channel.get(
                    "region",
                    "",
                ),
                "display_name": channel.get(
                    "displayName",
                    username,
                ),
            }
        )

    lives = lives[:MAX_LIVES]

    log.info(
        "🔥 GENEL TARAMA: %s LIVE BULUNDU",
        len(lives),
    )

    for live in lives:

        log.info(
            "🔴 @%s | %s | ROOM=%s",
            live["username"],
            live["region"],
            live["room_id"],
        )

    return lives


# ============================================================
# HAZİNE
# ============================================================

sent_envelopes = set()


async def handle_envelope(
    username,
    data,
):

    envelope_id = (
        data.get("envelopeId")
        or data.get("envelope_id")
        or ""
    )

    # Aynı olay tekrar gelirse Telegram'a tekrar gönderme.
    if envelope_id:

        dedupe_key = (
            f"{username.lower()}:{envelope_id}"
        )

        if dedupe_key in sent_envelopes:
            return

        sent_envelopes.add(
            dedupe_key
        )

        # Sonsuza kadar büyümesin.
        if len(sent_envelopes) > 5000:

            sent_envelopes.clear()

    diamonds = (
        data.get("diamondCount")
        or data.get("diamond_count")
        or 0
    )

    user = data.get(
        "user",
        {},
    )

    if not isinstance(
        user,
        dict,
    ):
        user = {}

    sender = (
        user.get("uniqueId")
        or user.get("unique_id")
        or user.get("nickname")
        or ""
    )

    log.warning(
        "🎁🎁🎁 HAZİNE BULUNDU | "
        "@%s | 💎 %s | 👤 %s | 🆔 %s",
        username,
        diamonds,
        sender,
        envelope_id,
    )

    message = (
        "🎁 <b>HAZİNE BULUNDU!</b>\n\n"
        f"📺 Yayın: @{html.escape(username)}\n"
        f"💎 Değer: {html.escape(str(diamonds))}\n"
        f"👤 Gönderen: {html.escape(str(sender))}\n"
        f"🆔 Envelope: {html.escape(str(envelope_id))}\n\n"
        "🌍 Genel LIVE taraması"
    )

    await telegram_send(
        message
    )


# ============================================================
# SUPER FAN BOX
# ============================================================

async def handle_superfanbox(
    username,
    data,
):

    envelope_id = (
        data.get("envelopeId")
        or data.get("envelope_id")
        or ""
    )

    diamonds = (
        data.get("diamondCount")
        or data.get("diamond_count")
        or 0
    )

    user = data.get(
        "user",
        {},
    )

    if not isinstance(
        user,
        dict,
    ):
        user = {}

    sender = (
        user.get("uniqueId")
        or user.get("unique_id")
        or user.get("nickname")
        or ""
    )

    log.warning(
        "🟣 SUPER FAN BOX | @%s | "
        "💎 %s | 👤 %s",
        username,
        diamonds,
        sender,
    )

    # Bunu şimdilik ayrı mesaj olarak gönderiyoruz.
    message = (
        "🟣 <b>SUPER FAN BOX</b>\n\n"
        f"📺 Yayın: @{html.escape(username)}\n"
        f"💎 Değer: {html.escape(str(diamonds))}\n"
        f"👤 Gönderen: {html.escape(str(sender))}\n"
        f"🆔 {html.escape(str(envelope_id))}"
    )

    await telegram_send(
        message
    )


# ============================================================
# TEK LIVE'I DİNLE
# ============================================================

async def watch_live(
    live,
    semaphore,
):

    username = live[
        "username"
    ]

    async with semaphore:

        log.info(
            "🔌 BAĞLANIYOR -> @%s",
            username,
        )

        ws_url = (
            "wss://api.tik.tools"
            "?uniqueId="
            + quote(username)
            + "&apiKey="
            + quote(API_KEY)
        )

        while True:

            try:

                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=20 * 1024 * 1024,
                ) as ws:

                    log.info(
                        "✅ LIVE BAĞLANDI -> @%s",
                        username,
                    )

                    async for raw in ws:

                        try:

                            event = json.loads(
                                raw
                            )

                        except Exception:

                            continue

                        if not isinstance(
                            event,
                            dict,
                        ):
                            continue

                        event_name = str(
                            event.get(
                                "event",
                                "",
                            )
                        )

                        data = event.get(
                            "data",
                            {},
                        )

                        if not isinstance(
                            data,
                            dict,
                        ):
                            data = {}

                        # --------------------------------------------
                        # HAZİNE
                        # --------------------------------------------

                        if event_name == "envelope":

                            await handle_envelope(
                                username,
                                data,
                            )

                        # --------------------------------------------
                        # SUPER FAN BOX
                        # --------------------------------------------

                        elif event_name == "superFanBox":

                            await handle_superfanbox(
                                username,
                                data,
                            )

                        # --------------------------------------------
                        # YAYIN BİTTİ
                        # --------------------------------------------

                        elif event_name in (
                            "streamEnd",
                            "control",
                        ):

                            log.info(
                                "⏹️ YAYIN BİTTİ -> @%s",
                                username,
                            )

                            return

            except Exception as e:

                log.warning(
                    "⚠️ @%s WS kapandı: %s",
                    username,
                    str(e)[:300],
                )

            # Bağlantı koptuğunda hemen saldırgan
            # reconnect yapmıyoruz.
            await asyncio.sleep(10)

            log.info(
                "🔄 @%s yeniden bağlanıyor...",
                username,
            )


# ============================================================
# GLOBAL SCANNER
# ============================================================

async def global_scanner():

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    active = {}

    log.info(
        "🚀🚀 GLOBAL HAZİNE TARAMASI BAŞLADI"
    )

    while True:

        lives = await discover_live()

        current = {
            live["username"].lower(): live
            for live in lives
        }

        # --------------------------------------------------------
        # Yeni LIVE'lar
        # --------------------------------------------------------

        for key, live in current.items():

            if key in active:
                continue

            task = asyncio.create_task(
                watch_live(
                    live,
                    semaphore,
                )
            )

            active[key] = task

            log.info(
                "➕ TARAMAYA EKLENDİ -> @%s",
                live["username"],
            )

        # --------------------------------------------------------
        # Artık LIVE olmayanları kapat
        # --------------------------------------------------------

        for key in list(active):

            if key not in current:

                task = active.pop(
                    key
                )

                if not task.done():

                    task.cancel()

                log.info(
                    "➖ TARAMADAN ÇIKTI -> @%s",
                    key,
                )

        log.info(
            "📡 AKTİF WS: %s",
            len(active),
        )

        # Public ticker'ın 5 dakikalık limitine uyuyoruz.
        await asyncio.sleep(
            DISCOVERY_INTERVAL
        )


# ============================================================
# RENDER HEALTH
# ============================================================

class Handler(
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
            b"TreasureAlert GLOBAL SCANNER OK"
        )

    def log_message(
        self,
        *args,
    ):
        pass


def start_server():

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        Handler,
    )

    log.info(
        "🌐 Render PORT=%s",
        port,
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    log.info(
        "========================================"
    )

    log.info(
        "🔥 TREASURE ALERT GLOBAL"
    )

    log.info(
        "========================================"
    )

    if not API_KEY:

        log.error(
            "❌ TIKTOOL_API_KEY YOK!"
        )

    if not BOT_TOKEN:

        log.error(
            "❌ BOT_TOKEN YOK!"
        )

    if not CHAT_ID:

        log.error(
            "❌ CHAT_ID YOK!"
        )

    threading.Thread(
        target=start_server,
        daemon=True,
    ).start()

    try:

        asyncio.run(
            global_scanner()
        )

    except KeyboardInterrupt:

        log.info(
            "🛑 Sistem kapatıldı."
        )
