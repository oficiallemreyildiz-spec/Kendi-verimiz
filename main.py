import asyncio
import html
import json
import logging
import os
import threading
import time
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

# TikTok sessionid varsa Render'a TIKTOK_SESSION_ID olarak eklenebilir.
# Zorunlu değil.
SESSION_ID = os.getenv("TIKTOK_SESSION_ID", "").strip()

# Webshare kullanıyorsan:
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "").strip()
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "").strip()

# Aynı anda kaç LIVE dinlensin?
# Güvenli başlangıç: 3
MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "3")
)

# Bir yayıncıyı maksimum kaç saniye dinleyelim?
# Sonra sıradaki LIVE'a geçilir.
WATCH_SECONDS = int(
    os.getenv("WATCH_SECONDS", "180")
)

# Feed kaç saniyede yenilensin?
DISCOVERY_INTERVAL = int(
    os.getenv("DISCOVERY_INTERVAL", "60")
)

# Feed sayfası başına maksimum 50.
FEED_COUNT = min(
    int(os.getenv("FEED_COUNT", "50")),
    50,
)

# Kaç feed sayfası alınsın?
# 1 = ilk 50 LIVE
# 3 = yaklaşık 150 aday
FEED_PAGES = int(
    os.getenv("FEED_PAGES", "2")
)

REGION = os.getenv(
    "TIKTOK_REGION",
    "TR",
)

CHANNEL_ID = os.getenv(
    "TIKTOK_CHANNEL_ID",
    "87",
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

async def send_telegram(text):

    if not BOT_TOKEN or not CHAT_ID:
        log.error(
            "❌ BOT_TOKEN veya CHAT_ID eksik."
        )
        return

    url = (
        f"https://api.telegram.org/"
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

            r = await client.post(
                url,
                json=payload,
            )

            if r.status_code != 200:

                log.error(
                    "❌ Telegram HTTP %s: %s",
                    r.status_code,
                    r.text[:500],
                )

    except Exception as e:

        log.error(
            "❌ Telegram bağlantı hatası: %s",
            e,
        )


# ============================================================
# PROXY
# ============================================================

def proxy_url():

    if not PROXY_USERNAME or not PROXY_PASSWORD:
        return None

    user = quote(
        PROXY_USERNAME,
        safe="",
    )

    password = quote(
        PROXY_PASSWORD,
        safe="",
    )

    return (
        f"http://{user}:{password}"
        "@p.webshare.io:80"
    )


def proxy_for_httpx():

    url = proxy_url()

    if not url:
        return None

    return httpx.Proxy(url)


# ============================================================
# FEED KEŞFİ
# ============================================================

async def discover_page(
    client,
    cursor=None,
):

    params = {
        "apiKey": API_KEY,
        "region": REGION,
        "channel_id": CHANNEL_ID,
        "count": str(FEED_COUNT),
    }

    if SESSION_ID:
        params["session_id"] = SESSION_ID

    if cursor:
        params["max_time"] = str(cursor)

    try:

        r = await client.get(
            "https://api.tik.tools/webcast/feed",
            params=params,
            timeout=30,
        )

        log.info(
            "🔎 FEED HTTP %s",
            r.status_code,
        )

        if r.status_code != 200:

            log.error(
                "❌ Feed hatası: %s",
                r.text[:700],
            )

            return [], None

        data = r.json()

        signed_url = data.get(
            "signed_url"
        )

        if not signed_url:

            log.error(
                "❌ Feed signed_url yok: %s",
                str(data)[:1000],
            )

            return [], None

        headers = data.get(
            "headers",
            {},
        )

        cookies = data.get(
            "cookies",
            "",
        )

        headers = dict(headers)

        if cookies:
            headers["Cookie"] = cookies

        # TikTok feed'i bizim IP'mizden çekiyoruz.
        response = await client.get(
            signed_url,
            headers=headers,
            timeout=30,
        )

        log.info(
            "🌐 TIKTOK FEED HTTP %s",
            response.status_code,
        )

        if response.status_code != 200:

            log.error(
                "❌ TikTok feed hatası: %s",
                response.text[:500],
            )

            return [], None

        feed = response.json()

        entries = feed.get(
            "data",
            [],
        )

        rooms = []

        for entry in entries:

            if not isinstance(
                entry,
                dict,
            ):
                continue

            room = entry.get(
                "data",
                entry,
            )

            if not isinstance(
                room,
                dict,
            ):
                continue

            owner = room.get(
                "owner",
                {},
            )

            if not isinstance(
                owner,
                dict,
            ):
                owner = {}

            username = (
                owner.get("display_id")
                or owner.get("unique_id")
                or room.get("display_id")
                or room.get("unique_id")
            )

            room_id = (
                room.get("id_str")
                or room.get("room_id")
            )

            if not username:
                continue

            username = (
                str(username)
                .strip()
                .lstrip("@")
            )

            if not username:
                continue

            viewers = (
                room.get("user_count")
                or room.get("viewer_count")
                or 0
            )

            title = (
                room.get("title")
                or ""
            )

            rooms.append(
                {
                    "username": username,
                    "room_id": (
                        str(room_id)
                        if room_id
                        else None
                    ),
                    "viewers": viewers,
                    "title": str(title),
                }
            )

        extra = feed.get(
            "extra",
            {},
        )

        next_cursor = extra.get(
            "max_time"
        )

        return rooms, next_cursor

    except Exception as e:

        log.exception(
            "❌ Feed keşif hatası: %s",
            e,
        )

        return [], None


async def discover_all():

    if not API_KEY:

        log.error(
            "❌ TIKTOOL_API_KEY bulunamadı!"
        )

        return []

    proxy = proxy_for_httpx()

    kwargs = {
        "timeout": 30,
        "follow_redirects": True,
    }

    if proxy:
        kwargs["proxy"] = proxy

    all_rooms = {}
    cursor = None

    try:

        async with httpx.AsyncClient(
            **kwargs
        ) as client:

            for page in range(
                FEED_PAGES
            ):

                log.info(
                    "📡 LIVE FEED sayfa %s/%s",
                    page + 1,
                    FEED_PAGES,
                )

                rooms, next_cursor = (
                    await discover_page(
                        client,
                        cursor,
                    )
                )

                for room in rooms:

                    key = room[
                        "username"
                    ].lower()

                    all_rooms[key] = room

                log.info(
                    "🔴 Bu sayfada %s LIVE",
                    len(rooms),
                )

                if not next_cursor:
                    break

                if str(next_cursor) == "0":
                    break

                cursor = next_cursor

    except Exception as e:

        log.exception(
            "❌ Genel keşif hatası: %s",
            e,
        )

    result = list(
        all_rooms.values()
    )

    log.info(
        "🔥 TOPLAM %s FARKLI LIVE BULUNDU",
        len(result),
    )

    for room in result:

        log.info(
            "🔴 @%s | 👥 %s",
            room["username"],
            room["viewers"],
        )

    return result


# ============================================================
# HAZİNE
# ============================================================

async def treasure_envelope(
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
        or user.get("nickname")
        or ""
    )

    safe_user = html.escape(
        str(username)
    )

    safe_sender = html.escape(
        str(sender)
    )

    log.warning(
        "🎁🎁 HAZİNE | @%s | "
        "💎 %s | 👤 %s | 🆔 %s",
        username,
        diamonds,
        sender,
        envelope_id,
    )

    message = (
        "🎁 <b>HAZİNE BULUNDU!</b>\n\n"
        f"📺 Yayın: @{safe_user}\n"
        f"💎 Değer: {diamonds}\n"
        f"👤 Gönderen: {safe_sender}\n"
        f"🆔 Envelope: {html.escape(str(envelope_id))}"
    )

    await send_telegram(
        message
    )


async def treasure_superfan(
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
        or user.get("nickname")
        or ""
    )

    safe_user = html.escape(
        str(username)
    )

    safe_sender = html.escape(
        str(sender)
    )

    log.warning(
        "🟣🟣 SUPER FAN BOX | @%s | "
        "💎 %s | 👤 %s",
        username,
        diamonds,
        sender,
    )

    message = (
        "🟣 <b>REWARD / SUPER FAN BOX</b>\n\n"
        f"📺 Yayın: @{safe_user}\n"
        f"💎 Değer: {diamonds}\n"
        f"👤 Gönderen: {safe_sender}\n"
        f"🆔 Envelope: {html.escape(str(envelope_id))}"
    )

    await send_telegram(
        message
    )


# ============================================================
# TEK LIVE WEBSOCKET
# ============================================================

async def watch_live(
    room,
    semaphore,
):

    username = room[
        "username"
    ]

    async with semaphore:

        log.info(
            "🔌 BAĞLANILIYOR | @%s",
            username,
        )

        url = (
            "wss://api.tik.tools"
            f"?uniqueId={quote(username)}"
            f"&apiKey={quote(API_KEY)}"
        )

        start_time = time.monotonic()

        try:

            # TikTool WebSocket
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=10 * 1024 * 1024,
            ) as ws:

                log.info(
                    "✅ BAĞLANDI | @%s",
                    username,
                )

                while True:

                    elapsed = (
                        time.monotonic()
                        - start_time
                    )

                    if elapsed >= WATCH_SECONDS:

                        log.info(
                            "⏭️ Süre doldu | @%s",
                            username,
                        )

                        break

                    try:

                        raw = await asyncio.wait_for(
                            ws.recv(),
                            timeout=30,
                        )

                    except asyncio.TimeoutError:

                        log.info(
                            "💓 @%s bağlantı aktif...",
                            username,
                        )

                        continue

                    if raw is None:
                        break

                    try:
                        event = json.loads(
                            raw
                        )
                    except Exception:

                        continue

                    event_name = (
                        event.get("event")
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

                    # ------------------------------------------------
                    # BAĞLANTI
                    # ------------------------------------------------

                    if event_name == "roomInfo":

                        log.info(
                            "🏠 ROOM | @%s | %s",
                            username,
                            event.get(
                                "roomId",
                                room.get(
                                    "room_id"
                                ),
                            ),
                        )

                    elif event_name == "connected":

                        log.info(
                            "🟢 CONNECTED | @%s",
                            username,
                        )

                    # ------------------------------------------------
                    # HAZİNE
                    # ------------------------------------------------

                    elif event_name == "envelope":

                        await treasure_envelope(
                            username,
                            data,
                        )

                    # ------------------------------------------------
                    # REWARD / SUPER FAN BOX
                    # ------------------------------------------------

                    elif event_name == "superFanBox":

                        await treasure_superfan(
                            username,
                            data,
                        )

                    # ------------------------------------------------
                    # STREAM END
                    # ------------------------------------------------

                    elif event_name in (
                        "streamEnd",
                        "control",
                    ):

                        log.info(
                            "⏹️ YAYIN BİTTİ | @%s",
                            username,
                        )

                        break

                    # ------------------------------------------------
                    # HAZİNE İZİ / DEBUG
                    # ------------------------------------------------

                    elif (
                        "envelope"
                        in str(
                            event_name
                        ).lower()
                    ):

                        log.warning(
                            "🔎 ENVELOPE BENZERİ EVENT | "
                            "@%s | %s | %s",
                            username,
                            event_name,
                            str(data)[:500],
                        )

        except websockets.exceptions.InvalidStatus as e:

            log.error(
                "❌ WebSocket HTTP hatası @%s: %s",
                username,
                e,
            )

        except Exception as e:

            log.error(
                "❌ WebSocket @%s: %s",
                username,
                str(e)[:500],
            )

        finally:

            log.info(
                "🔌 BAĞLANTI KAPANDI | @%s",
                username,
            )


# ============================================================
# GENEL TARAMA
# ============================================================

async def global_scanner():

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    currently_watching = set()

    log.info(
        "🚀🚀 GENEL LIVE TARAMA BAŞLADI"
    )

    log.info(
        "⚙️ Aynı anda %s LIVE",
        MAX_CONNECTIONS,
    )

    while True:

        rooms = await discover_all()

        if not rooms:

            log.warning(
                "⚠️ LIVE bulunamadı veya feed alınamadı."
            )

            await asyncio.sleep(
                DISCOVERY_INTERVAL
            )

            continue

        # Yeni adaylar
        candidates = []

        for room in rooms:

            username = room[
                "username"
            ]

            key = username.lower()

            if key in currently_watching:
                continue

            currently_watching.add(
                key
            )

            candidates.append(
                room
            )

        log.info(
            "🆕 %s yeni LIVE kuyruğa alındı.",
            len(candidates),
        )

        # Hepsini görev olarak oluştur.
        # Semaphore bağlantı sayısını kontrol eder.
        for room in candidates:

            asyncio.create_task(
                watch_and_release(
                    room,
                    semaphore,
                    currently_watching,
                )
            )

        await asyncio.sleep(
            DISCOVERY_INTERVAL
        )


async def watch_and_release(
    room,
    semaphore,
    currently_watching,
):

    key = room[
        "username"
    ].lower()

    try:

        await watch_live(
            room,
            semaphore,
        )

    finally:

        currently_watching.discard(
            key
        )


# ============================================================
# RENDER HEALTH SERVER
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
        "🌐 Render server PORT=%s",
        port,
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    if not API_KEY:

        log.error(
            "❌❌ TIKTOOL_API_KEY YOK!"
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
            "🛑 TreasureAlert kapatıldı."
        )
