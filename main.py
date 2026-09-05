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

PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "").strip()
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "").strip()

# Kaç LIVE aynı anda dinlenecek?
# Şimdilik 1 yapıyoruz: JWT/WS testinde güvenli başlangıç.
MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "1")
)

# Public LIVE keşfi 5 dakikada bir.
DISCOVERY_INTERVAL = 300

# En fazla keşfedilecek LIVE sayısı.
MAX_LIVES = 10


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

    if (
        not PROXY_HOST
        or not PROXY_PORT
    ):
        return None

    if (
        PROXY_USERNAME
        and PROXY_PASSWORD
    ):

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

    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN eksik.")
        return

    if not CHAT_ID:
        log.error("❌ CHAT_ID eksik.")
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

            if response.status_code == 200:

                log.info(
                    "📨 Telegram bildirimi gönderildi."
                )

            else:

                log.error(
                    "❌ Telegram HTTP %s: %s",
                    response.status_code,
                    response.text[:300],
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

    log.info(
        "🔎 Tik.Tools LIVE keşfi başlıyor..."
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Linux; Android 16) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Mobile Safari/537.36"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
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
                "https://api.tik.tools/api/live/top-channels"
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

                log.error(
                    "Cevap: %s",
                    response.text[:500],
                )

                return []

            try:

                result = response.json()

            except Exception as e:

                log.error(
                    "❌ JSON çözülemedi: %s",
                    e,
                )

                log.error(
                    "Cevap: %s",
                    response.text[:500],
                )

                return []

    except httpx.ProxyError as e:

        log.error(
            "❌ PROXY HATASI: %s",
            e,
        )

        return []

    except httpx.TimeoutException as e:

        log.error(
            "⏱️ LIVE keşfi timeout: %s",
            e,
        )

        return []

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

        log.error(
            "❌ channels listesi bulunamadı."
        )

        return []

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

        if not username:
            continue

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
                "display_name": str(
                    channel.get("displayName")
                    or channel.get("display_name")
                    or username
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
# HAZİNE DUPLICATE KORUMASI
# ============================================================

sent_envelopes = set()


# ============================================================
# HAZİNE
# ============================================================

async def handle_envelope(
    username,
    data,
):

    envelope_id = (
        data.get("envelopeId")
        or data.get("envelope_id")
        or ""
    )

    if envelope_id:

        dedupe_key = (
            f"{username.lower()}:{envelope_id}"
        )

        if dedupe_key in sent_envelopes:

            log.info(
                "♻️ Tekrarlanan hazine atlandı -> @%s",
                username,
            )

            return

        sent_envelopes.add(
            dedupe_key
        )

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
# SUPER
