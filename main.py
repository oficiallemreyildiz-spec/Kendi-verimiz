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
# ENV
# ============================================================

API_KEY = os.getenv("TIKTOOL_API_KEY", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SESSION_ID = os.getenv("TIKTOK_SESSION_ID", "").strip()

MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "5")
)

DISCOVERY_INTERVAL = int(
    os.getenv("DISCOVERY_INTERVAL", "45")
)

WATCH_SECONDS = int(
    os.getenv("WATCH_SECONDS", "180")
)

FEED_COUNT = min(
    int(os.getenv("FEED_COUNT", "50")),
    50,
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

async def telegram(text):

    if not BOT_TOKEN or not CHAT_ID:
        log.error("❌ BOT_TOKEN / CHAT_ID eksik.")
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
            "❌ Telegram: %s",
            e,
        )


# ============================================================
# FEED İSTEĞİ
# ============================================================

async def get_feed(
    region,
    channel,
    cursor=None,
):

    params = {
        "apiKey": API_KEY,
        "region": region,
        "channel_id": str(channel),
        "count": str(FEED_COUNT),
    }

    if SESSION_ID:
        params["session_id"] = SESSION_ID

    if cursor:
        params["max_time"] = str(cursor)

    try:

        # ÖNEMLİ:
        # Feed keşfini Webshare proxy'den geçirmiyoruz.
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
        ) as client:

            r = await client.get(
                "https://api.tik.tools/webcast/feed",
                params=params,
            )

            log.info(
                "🔎 FEED %s / channel=%s -> HTTP %s",
                region,
                channel,
                r.status_code,
            )

            if r.status_code != 200:

                log.error(
                    "❌ Feed cevap: %s",
                    r.text[:500],
                )

                return [], None

            result = r.json()

            # ------------------------------------------------
            # TikTool signed URL
            # ------------------------------------------------

            signed_url = result.get(
                "signed_url"
            )

            if not signed_url:

                log.error(
                    "❌ signed_url yok. Keys=%s",
                    list(result.keys()),
                )

                return [], None

            headers = dict(
                result.get(
                    "headers",
                    {},
                )
            )

            cookies = result.get(
                "cookies",
                "",
            )

            if cookies:
                headers["Cookie"] = cookies

            # ------------------------------------------------
            # TikTok feed
            # ------------------------------------------------

            rr = await client.get(
                signed_url,
                headers=headers,
                timeout=30,
            )

            log.info(
                "🌐 TIKTOK FEED -> HTTP %s",
                rr.status_code,
            )

            if rr.status_code != 200:

                log.error(
                    "❌ TikTok feed: %s",
                    rr.text[:500],
                )

                return [], None

            feed = rr.json()

            return parse_feed(
                feed
            )

    except Exception as e:

        log.error(
            "❌ Feed exception: %s",
            e,
        )

        return [], None


# ============================================================
# FEED PARSER
# ============================================================

def parse_feed(feed):

    rooms = []

    if not isinstance(
        feed,
        dict,
    ):
        return [], None

    entries = feed.get(
        "data",
        [],
    )

    if not isinstance(
        entries,
        list,
    ):
        entries = []

    for entry in entries:

        if not isinstance(
            entry,
            dict,
        ):
            continue

        # Normal TikTok yapı:
        #
        # entry
        #   └── data
        #       ├── id_str
        #       ├── owner
        #       └── title

        room = entry.get(
            "data"
        )

        if not isinstance(
            room,
            dict,
        ):
            room = entry

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
            or owner.get("displayId")
            or room.get("display_id")
            or room.get("unique_id")
        )

        room_id = (
            room.get("id_str")
            or room.get("room_id")
            or room.get("roomId")
        )

        if not username:
            continue

        username = str(
            username
        ).strip().lstrip("@")

        if not username:
            continue

        viewers = (
            room.get("user_count")
            or room.get("userCount")
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
                    else ""
                ),
                "viewers": viewers,
                "title": str(title),
            }
        )

    extra = feed.get(
        "extra",
        {},
    )

    if not isinstance(
        extra,
        dict,
    ):
        extra = {}

    cursor = (
        extra.get("max_time")
        or extra.get("maxTime")
    )

    # Tekilleştir
    unique = {}

    for room in rooms:

        unique[
            room["username"].lower()
        ] = room

    result = list(
        unique.values()
    )

    return result, cursor


# ============================================================
# GENEL LIVE KEŞFİ
# ============================================================

async def discover():

    # Birden fazla kanal deniyoruz.
    channels = [
        "87",       # Recommended
        "86",       # Suggested
        "42",       # Following
        "1111006",  # Gaming
    ]

    regions = [
        "US",
        "TR",
        "GB",
        "DE",
        "BR",
        "ID",
        "JP",
    ]

    all_rooms = {}

    # İlk aşamada çok fazla API tüketmemek için
    # birkaç bölge/kanal kombinasyonu.
    combinations = [
        ("US", "87"),
        ("TR", "87"),
        ("GB", "87"),
        ("DE", "87"),
        ("BR", "87"),
        ("ID", "87"),
        ("JP", "87"),
        ("US", "86"),
        ("TR", "86"),
    ]

    for region, channel in combinations:

        rooms, _ = await get_feed(
            region,
            channel,
        )

        for room in rooms:

            key = room[
                "username"
            ].lower()

            all_rooms[key] = room

        if rooms:

            log.info(
                "🔥 %s / %s -> %s LIVE",
                region,
                channel,
                len(rooms),
            )

    result = list(
        all_rooms.values()
    )

    log.info(
        "🔥🔥 GENEL TARAMA SONUCU: %s FARKLI LIVE",
        len(result),
    )

    for room in result[:100]:

        log.info(
            "🔴 @%s | 👥 %s",
            room["username"],
            room["viewers"],
        )

    return result


# ============================================================
# ENVELOPE / HAZİNE
# ============================================================

async def handle_event(
    username,
    event,
    data,
):

    event_lower = str(
        event
    ).lower()

    # --------------------------------------------------------
    # ENVELOPE
    # --------------------------------------------------------

    if (
        event_lower == "envelope"
        or "envelope" in event_lower
    ):

        log.warning(
            "🎁🎁🎁 HAZİNE BULUNDU | @%s",
            username,
        )

        log.warning(
            "📦 DATA: %s",
            json.dumps(
                data,
                ensure_ascii=False,
            )[:3000],
        )

        diamonds = (
            data.get("diamondCount")
            or data.get("diamond_count")
            or data.get("amount")
            or 0
        )

        sender = ""

        user = data.get(
            "user",
            {},
        )

        if isinstance(
            user,
            dict,
        ):

            sender = (
                user.get("uniqueId")
                or user.get("unique_id")
                or user.get("nickname")
                or ""
            )

        envelope_id = (
            data.get("envelopeId")
            or data.get("envelope_id")
            or ""
        )

        message = (
            "🎁 <b>HAZİNE BULUNDU!</b>\n\n"
            f"📺 @{html.escape(username)}\n"
            f"💎 Değer: {html.escape(str(diamonds))}\n"
            f"👤 Gönderen: {html.escape(str(sender))}\n"
            f"🆔 {html.escape(str(envelope_id))}\n\n"
            "🔎 Genel tarama tarafından yakalandı."
        )

        await telegram(
            message
        )

        return


    # --------------------------------------------------------
    # SUPER FAN BOX
    # --------------------------------------------------------

    if (
        event_lower == "superfanbox"
        or "superfan" in event_lower
    ):

        log.warning(
            "🟣 SUPER FAN BOX | @%s | %s",
            username,
            json.dumps(
                data,
                ensure_ascii=False,
            )[:2000],
        )

        return


# ============================================================
# WEBSOCKET
# ============================================================

async def watch(
    room,
    semaphore,
):

    username = room[
        "username"
    ]

    async with semaphore:

        log.info(
            "🔌 WS -> @%s",
            username,
        )

        ws_url = (
            "wss://api.tik.tools"
            "?uniqueId="
            + quote(username)
            + "&apiKey="
            + quote(API_KEY)
        )

        try:

            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=20 * 1024 * 1024,
            ) as ws:

                log.info(
                    "✅ WS BAĞLANDI -> @%s",
                    username,
                )

                started = time.monotonic()

                while (
                    time.monotonic()
                    - started
                    < WATCH_SECONDS
                ):

                    try:

                        raw = await asyncio.wait_for(
                            ws.recv(),
                            timeout=30,
                        )

                    except asyncio.TimeoutError:

                        log.info(
                            "💓 @%s aktif",
                            username,
                        )

                        continue

                    if not raw:
                        break

                    try:

                        msg = json.loads(
                            raw
                        )

                    except Exception:
                        continue

                    if not isinstance(
                        msg,
                        dict,
                    ):
                        continue

                    event = msg.get(
                        "event",
                        "",
                    )

                    data = msg.get(
                        "data",
                        {},
                    )

                    if not isinstance(
                        data,
                        dict,
                    ):
                        data = {}

                    await handle_event(
                        username,
                        event,
                        data,
                    )

        except Exception as e:

            log.error(
                "❌ WS @%s: %s",
                username,
                str(e)[:500],
            )

        finally:

            log.info(
                "🔌 WS KAPANDI -> @%s",
                username,
            )


# ============================================================
# WATCH QUEUE
# ============================================================

async def global_scanner():

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    active = set()

    log.info(
        "🚀🚀🚀 GLOBAL LIVE TARAMA BAŞLADI"
    )

    while True:

        rooms = await discover()

        if not rooms:

            log.warning(
                "⚠️ LIVE bulunamadı."
            )

        else:

            for room in rooms:

                username = room[
                    "username"
                ]

                key = username.lower()

                if key in active:
                    continue

                active.add(key)

                asyncio.create_task(
                    watch_release(
                        room,
                        semaphore,
                        active,
                    )
                )

        await asyncio.sleep(
            DISCOVERY_INTERVAL
        )


async def watch_release(
    room,
    semaphore,
    active,
):

    key = room[
        "username"
    ].lower()

    try:

        await watch(
            room,
            semaphore,
        )

    finally:

        active.discard(
            key
        )


# ============================================================
# RENDER SERVER
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
            b"TreasureAlert GLOBAL OK"
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

    asyncio.run(
        global_scanner()
    )
