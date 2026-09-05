import asyncio
import html
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

# Aynı anda kaç LIVE dinlenecek?
# Şimdilik Render ENV'deki MAX_CONNECTIONS=1 kullanılacak.
MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "1")
)

# LIVE keşfi
DISCOVERY_INTERVAL = int(
    os.getenv("DISCOVERY_INTERVAL", "300")
)

# Bir taramada en fazla kaç LIVE alınacak?
MAX_LIVES = int(
    os.getenv("MAX_LIVES", "10")
)

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
            timeout=20
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
                    response.text[:500],
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

    if not isinstance(channels, list):

        log.error(
            "❌ channels listesi bulunamadı."
        )

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

        if not username:
            continue

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
# DUPLICATE KORUMASI
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

    # --------------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # DIAMOND
    # --------------------------------------------------------

    diamonds = (
        data.get("diamondCount")
        or data.get("diamond_count")
        or 0
    )

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

    user = data.get(
        "user",
        {},
    )

    if not isinstance(user, dict):
        user = {}

    sender = (
        user.get("uniqueId")
        or user.get("unique_id")
        or user.get("nickname")
        or ""
    )

    sender = str(sender)

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    if event_type == "superFanBox":

        log.warning(
            "🟣🟣🟣 SUPER FAN BOX BULUNDU | "
            "@%s | 💎 %s | 👤 %s | 🆔 %s",
            username,
            diamonds,
            sender,
            envelope_id,
        )

        title = "🟣 SUPER FAN BOX BULUNDU!"

    else:

        log.warning(
            "🎁🎁🎁 HAZİNE BULUNDU | "
            "@%s | 💎 %s | 👤 %s | 🆔 %s",
            username,
            diamonds,
            sender,
            envelope_id,
        )

        title = "🎁 HAZİNE BULUNDU!"

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    message = (
        f"<b>{title}</b>\n\n"

        f"📺 Yayın: "
        f"@{html.escape(username)}\n"

        f"💎 Değer: "
        f"{html.escape(str(diamonds))}\n"

        f"👤 Gönderen: "
        f"{html.escape(sender)}\n"

        f"🆔 Envelope: "
        f"{html.escape(envelope_id)}\n\n"

        f"🌍 Genel LIVE taraması"
    )

    await telegram_send(
        message
    )


# ============================================================
# JWT
# ============================================================

async def mint_jwt(username):

    if not API_KEY:

        log.error(
            "❌ TIKTOOL_API_KEY eksik."
        )

        return None

    url = (
        f"{API_BASE}/authentication/jwt"
        f"?apiKey={quote(API_KEY, safe='')}"
    )

    payload = {
        "allowed_creators": [
            username
        ],

        "expire_after": 600,

        "max_websockets": 1,
    }

    headers = {
        "Content-Type":
            "application/json",

        "Accept":
            "application/json",
    }

    try:

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            proxy=HTTP_PROXY,
            headers=headers,
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
                    "❌ JWT alınamadı -> @%s",
                    username,
                )

                log.error(
                    "JWT cevap: %s",
                    response.text[:500],
                )

                return None

            try:
                result = response.json()

            except Exception as e:

                log.error(
                    "❌ JWT JSON hatası: %s",
                    e,
                )

                return None

    except httpx.ProxyError as e:

        log.error(
            "❌ JWT PROXY HATASI -> @%s: %s",
            username,
            e,
        )

        return None

    except httpx.TimeoutException as e:

        log.error(
            "⏱️ JWT timeout -> @%s: %s",
            username,
            e,
        )

        return None

    except Exception as e:

        log.error(
            "❌ JWT isteği hatası -> @%s: %s",
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
            "❌ JWT token bulunamadı -> @%s",
            username,
        )

        log.error(
            "JWT cevap: %s",
            str(result)[:500],
        )

        return None

    log.info(
        "✅ JWT hazır -> @%s",
        username,
    )

    return token


# ============================================================
# WEBSOCKET EVENT İŞLEYİCİ
# ============================================================

async def process_ws_message(
    username,
    raw,
):

    try:

        event = json_loads_safe(raw)

    except Exception as e:

        log.error(
            "❌ WS JSON hatası -> @%s: %s",
            username,
            e,
        )

        return

    if not isinstance(event, dict):
        return

    event_name = (
        event.get("event")
        or ""
    )

    if not event_name:
        return

    data = event.get(
        "data",
        {},
    )

    if not isinstance(data, dict):
        data = {}

    # --------------------------------------------------------
    # ROOM INFO
    # --------------------------------------------------------

    if event_name == "roomInfo":

        log.info(
            "🏠 RoomInfo geldi -> @%s",
            username,
        )

        return

    # --------------------------------------------------------
    # ENVELOPE
    # --------------------------------------------------------

    if event_name == "envelope":

        await handle_envelope(
            username,
            data,
            "envelope",
        )

        return

    # --------------------------------------------------------
    # SUPER FAN BOX
    # --------------------------------------------------------

    if event_name == "superFanBox":

        await handle_envelope(
            username,
            data,
            "superFanBox",
        )

        return

    # --------------------------------------------------------
    # CONTROL
    # --------------------------------------------------------

    if event_name == "control":

        log.info(
            "🎛️ CONTROL -> @%s | %s",
            username,
            str(data)[:300],
        )

        return

    # --------------------------------------------------------
    # DİĞER EVENTLER
    # --------------------------------------------------------

    if event_name in {
        "chat",
        "gift",
        "like",
        "member",
        "follow",
        "share",
        "roomUser",
        "subscribe",
        "emote",
    }:

        return

    # Tanımadığımız eventleri görmek için:
    log.info(
        "ℹ️ EVENT -> @%s | %s",
        username,
        event_name,
    )


# ============================================================
# JSON
# ============================================================

def json_loads_safe(raw):

    if isinstance(raw, bytes):

        raw = raw.decode(
            "utf-8",
            errors="replace",
        )

    return __import__(
        "json"
    ).loads(raw)


# ============================================================
# TEK LIVE İZLE
# ============================================================

async def watch_live(
    username,
):

    username = (
        str(username)
        .strip()
        .lstrip("@")
    )

    if not username:
        return

    while True:

        token = await mint_jwt(
            username
        )

        if not token:

            log.error(
                "❌ JWT yok, 30 saniye sonra tekrar -> @%s",
                username,
            )

            await asyncio.sleep(30)

            continue

        ws_url = (
            f"{WS_BASE}"
            f"?uniqueId={quote(username, safe='')}"
            f"&jwtKey={quote(token, safe='')}"
        )

        log.info(
            "🔌 BAĞLANIYOR -> @%s",
            username,
        )

        try:

            async with websockets.connect(
                ws_url,
                open_timeout=30,
                close_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=None,
            ) as ws:

                log.info(
                    "🟢 WS BAĞLANDI -> @%s",
                    username,
                )

                async for raw in ws:

                    await process_ws_message(
                        username,
                        raw,
                    )

        except websockets.exceptions.ConnectionClosed as e:

            log.warning(
                "🔌 WS KAPANDI -> @%s | code=%s | reason=%s",
                username,
                getattr(e, "code", ""),
                getattr(e, "reason", ""),
            )

        except asyncio.TimeoutError:

            log.warning(
                "⏱️ WS TIMEOUT -> @%s",
                username,
            )

        except Exception as e:

            log.error(
                "❌ WS HATASI -> @%s | %s",
                username,
                e,
            )

        log.info(
            "🔄 10 saniye sonra yeniden bağlanılacak -> @%s",
            username,
        )

        await asyncio.sleep(10)


# ============================================================
# AKTİF BAĞLANTILAR
# ============================================================

active_tasks = {}


# ============================================================
# GLOBAL SCANNER
# ============================================================

async def global_scanner():

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    log.info(
        "🚀 GLOBAL SCANNER BAŞLADI"
    )

    log.info(
        "⚙️ MAX_CONNECTIONS = %s",
        MAX_CONNECTIONS,
    )

    log.info(
        "⚙️ MAX_LIVES = %s",
        MAX_LIVES,
    )

    while True:

        try:

            lives = await discover_live()

            if not lives:

                log.info(
                    "😴 Şu anda izlenecek LIVE bulunamadı."
                )

            current_users = set()

            for live in lives:

                username = (
                    live["username"]
                )

                key = username.lower()

                current_users.add(
                    key
                )

                if key in active_tasks:

                    continue

                # ------------------------------------------------
                # TASK
                # ------------------------------------------------

                async def runner(
                    user=username,
                    sem=semaphore,
                ):

                    async with sem:

                        log.info(
                            "📡 TARAMAYA EKLENDİ -> @%s",
                            user,
                        )

                        await watch_live(
                            user
                        )

                task = asyncio.create_task(
                    runner()
                )

                active_tasks[key] = task

                log.info(
                    "📡 AKTİF WS TASK -> @%s",
                    username,
                )

            # ----------------------------------------------------
            # BITMIŞ TASK'LARI TEMİZLE
            # ----------------------------------------------------

            dead = []

            for key, task in active_tasks.items():

                if task.done():

                    dead.append(key)

            for key in dead:

                active_tasks.pop(
                    key,
                    None,
                )

                log.info(
                    "🧹 Biten task temizlendi -> %s",
                    key,
                )

            log.info(
                "📊 AKTİF TASK: %s",
                len(active_tasks),
            )

        except Exception as e:

            log.error(
                "❌ GLOBAL SCANNER HATASI: %s",
                e,
            )

        log.info(
            "⏳ %s saniye sonra yeniden LIVE taraması...",
            DISCOVERY_INTERVAL,
        )

        await asyncio.sleep(
            DISCOVERY_INTERVAL
        )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

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
# ANA PROGRAM
# ============================================================

async def main():

    log.info(
        "🔥🔥🔥 TREASURE ALERT GLOBAL BAŞLIYOR 🔥🔥🔥"
    )

    if API_KEY:

        log.info(
            "🔑 TIKTOOL_API_KEY: VAR"
        )

    else:

        log.error(
            "❌ TIKTOOL_API_KEY: YOK"
        )

    if BOT_TOKEN:

        log.info(
            "🤖 BOT_TOKEN: VAR"
        )

    else:

        log.error(
            "❌ BOT_TOKEN: YOK"
        )

    if CHAT_ID:

        log.info(
            "💬 CHAT_ID: VAR"
        )

    else:

        log.error(
            "❌ CHAT_ID: YOK"
        )

    if HTTP_PROXY:

        log.info(
            "🌐 HTTP PROXY: AKTİF"
        )

    else:

        log.info(
            "🌐 HTTP PROXY: KAPALI"
        )

    log.info(
        "⚙️ MAX_CONNECTIONS=%s",
        MAX_CONNECTIONS,
    )

    log.info(
        "⚙️ DISCOVERY_INTERVAL=%s",
        DISCOVERY_INTERVAL,
    )

    log.info(
        "⚙️ MAX_LIVES=%s",
        MAX_LIVES,
    )

    await global_scanner()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )

    health_thread.start()

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
            "💥 ANA PROGRAM HATASI: %s",
            e,
        )

        raise
