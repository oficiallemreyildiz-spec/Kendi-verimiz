import asyncio
import html
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

import httpx
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, DisconnectEvent, EnvelopeEvent
from TikTokLive.client.errors import UserOfflineError


# ============================================================
# AYARLAR
# ============================================================

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

# Render'da mevcut API key'in hangi isimdeyse onu kullanır.
API_KEY = (
    os.getenv("TIKTOOLS_API_KEY")
    or os.getenv("TIKHUB_API_KEY")
    or os.getenv("EULER_API_KEY")
    or os.getenv("TIKTOK_API_KEY")
    or ""
)

PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

# Aynı anda bağlanılacak maksimum LIVE.
MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "3")
)

# Feed yenileme süresi.
DISCOVERY_INTERVAL = int(
    os.getenv("DISCOVERY_INTERVAL", "60")
)

# Aynı odayı tekrar tekrar bağlamamak için.
ROOM_TTL = int(
    os.getenv("ROOM_TTL", "7200")
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

    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.error("BOT_TOKEN veya CHAT_ID eksik.")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
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
                    "Telegram %s: %s",
                    r.status_code,
                    r.text[:300],
                )

    except Exception as e:

        log.error(
            "Telegram bağlantı hatası: %s",
            e,
        )


# ============================================================
# PROXY
# ============================================================

def make_proxy():

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

    return httpx.Proxy(
        "http://"
        f"{user}:{password}"
        "@p.webshare.io:80"
    )


# ============================================================
# DISCOVERY
# ============================================================

async def discover_live_streams():

    if not API_KEY:
        log.error(
            "❌ Feed API anahtarı bulunamadı."
        )
        return []

    # tik.tools feed endpoint'i.
    url = "https://api.tik.tools/webcast/feed"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
    }

    params = {
        "channel": "87",
        "region": "TR",
    }

    try:

        async with httpx.AsyncClient(
            timeout=30
        ) as client:

            response = await client.get(
                url,
                headers=headers,
                params=params,
            )

            log.info(
                "🔎 LIVE FEED HTTP %s",
                response.status_code,
            )

            if response.status_code != 200:

                log.error(
                    "Feed hatası: %s",
                    response.text[:500],
                )

                return []

            data = response.json()

    except Exception as e:

        log.error(
            "Feed bağlantı hatası: %s",
            e,
        )

        return []

    rooms = []

    # API cevabının olası yapıları.
    candidates = []

    if isinstance(data, list):
        candidates = data

    elif isinstance(data, dict):

        root = data.get("data", data)

        if isinstance(root, dict):

            for key in (
                "rooms",
                "items",
                "results",
                "lives",
            ):

                value = root.get(key)

                if isinstance(value, list):
                    candidates = value
                    break

        elif isinstance(root, list):
            candidates = root

    for item in candidates:

        if not isinstance(item, dict):
            continue

        owner = item.get("owner") or {}

        username = (
            owner.get("display_id")
            or item.get("display_id")
            or item.get("unique_id")
            or item.get("username")
        )

        room_id = (
            item.get("id_str")
            or item.get("room_id")
            or item.get("roomId")
        )

        if not username:
            continue

        username = str(
            username
        ).lstrip("@").strip()

        if not username:
            continue

        rooms.append(
            {
                "username": username,
                "room_id": str(room_id)
                if room_id
                else None,
                "title": str(
                    item.get("title") or ""
                ),
                "viewers": item.get(
                    "user_count",
                    item.get(
                        "viewer_count",
                        0,
                    ),
                ),
            }
        )

    # Aynı kullanıcıyı tekilleştir.
    unique = {}

    for room in rooms:
        unique[
            room["username"].lower()
        ] = room

    result = list(unique.values())

    log.info(
        "📡 FEED %s canlı yayın buldu.",
        len(result),
    )

    return result


# ============================================================
# HAZİNE
# ============================================================

async def handle_envelope(
    username,
    event,
):

    try:

        info = getattr(
            event,
            "envelope_info",
            None,
        )

        if info is None:
            log.warning(
                "🎁 Envelope geldi fakat bilgi yok: @%s",
                username,
            )
            return

        business_type = getattr(
            info,
            "business_type",
            None,
        )

        diamonds = getattr(
            info,
            "diamond_count",
            0,
        )

        people = getattr(
            info,
            "people_count",
            0,
        )

        sender = getattr(
            info,
            "send_user_name",
            "",
        )

        envelope_id = getattr(
            info,
            "envelope_id",
            "",
        )

        log.warning(
            "🎁🎁 HAZİNE BULUNDU | "
            "@%s | type=%s | diamonds=%s | "
            "people=%s | sender=%s",
            username,
            business_type,
            diamonds,
            people,
            sender,
        )

        safe_username = html.escape(
            username
        )

        safe_sender = html.escape(
            str(sender)
        )

        message = (
            "🎁 <b>HAZİNE BULUNDU!</b>\n\n"
            f"📺 @{safe_username}\n"
            f"💎 Diamond: {diamonds}\n"
            f"👥 Kişi: {people}\n"
            f"👤 Gönderen: {safe_sender}\n"
            f"🔢 BusinessType: {business_type}\n"
            f"🆔 {envelope_id}"
        )

        await telegram_send(
            message
        )

    except Exception as e:

        log.exception(
            "Envelope hatası @%s: %s",
            username,
            e,
        )


# ============================================================
# LIVE BAĞLANTISI
# ============================================================

async def watch_room(
    room,
    semaphore,
):

    username = room["username"]
    room_id = room.get("room_id")

    async with semaphore:

        client = None

        try:

            proxy = make_proxy()

            kwargs = {
                "unique_id": username,
            }

            if proxy:

                kwargs["web_proxy"] = proxy
                kwargs["ws_proxy"] = proxy

            client = TikTokLiveClient(
                **kwargs
            )

            @client.on(ConnectEvent)
            async def on_connect(event):

                log.info(
                    "✅ BAĞLANDI | @%s | ROOM=%s",
                    username,
                    client.room_id,
                )

            @client.on(DisconnectEvent)
            async def on_disconnect(event):

                log.info(
                    "⚠️ AYRILDI | @%s",
                    username,
                )

            @client.on(EnvelopeEvent)
            async def on_envelope(event):

                await handle_envelope(
                    username,
                    event,
                )

            # Feed'den room ID geldiyse
            # mümkün olduğunca doğrudan odaya bağlan.
            if room_id:

                task = await client.start(
                    room_id=int(room_id),
                    fetch_live_check=False,
                )

                await task

            else:

                await client.connect()

        except UserOfflineError:

            log.info(
                "⏸️ @%s artık LIVE değil.",
                username,
            )

        except Exception as e:

            log.error(
                "❌ @%s: %s",
                username,
                str(e)[:500],
            )

        finally:

            try:

                if client and client.connected:
                    await client.disconnect()

            except Exception:
                pass


# ============================================================
# GENEL TARAMA
# ============================================================

async def global_scanner():

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    active = set()

    log.info(
        "🚀 GENEL LIVE TARAMA BAŞLADI"
    )

    while True:

        try:

            rooms = (
                await discover_live_streams()
            )

            new_rooms = []

            for room in rooms:

                key = (
                    room["username"].lower()
                )

                if key in active:
                    continue

                active.add(key)
                new_rooms.append(room)

            if new_rooms:

                log.info(
                    "🆕 %s yeni LIVE bulundu.",
                    len(new_rooms),
                )

                for room in new_rooms:

                    log.info(
                        "➡️ @%s | viewers=%s",
                        room["username"],
                        room["viewers"],
                    )

                    asyncio.create_task(
                        watch_and_release(
                            room,
                            semaphore,
                            active,
                        )
                    )

            else:

                log.info(
                    "ℹ️ Yeni yayın bulunmadı."
                )

        except Exception as e:

            log.exception(
                "Genel tarama hatası: %s",
                e,
            )

        await asyncio.sleep(
            DISCOVERY_INTERVAL
        )


async def watch_and_release(
    room,
    semaphore,
    active,
):

    key = room[
        "username"
    ].lower()

    try:

        await watch_room(
            room,
            semaphore,
        )

    finally:

        active.discard(
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

    threading.Thread(
        target=start_server,
        daemon=True,
    ).start()

    asyncio.run(
        global_scanner()
    )
