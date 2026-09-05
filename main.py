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

# Aynı anda kaç WebSocket çalışacak?
MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "1")
)

# Her LIVE kaç saniye taranacak?
WATCH_SECONDS = int(
    os.getenv("WATCH_SECONDS", "5")
)

# Bir API keşfinde alınacak maksimum LIVE
MAX_LIVES = int(
    os.getenv("MAX_LIVES", "10")
)

# Yeni LIVE listesini ne kadar sonra yenileyeceğiz?
DISCOVERY_INTERVAL = int(
    os.getenv("DISCOVERY_INTERVAL", "300")
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
                json=payload
            )

            if response.status_code == 200:

                log.info(
                    "📨 Telegram bildirimi gönderildi."
                )

            else:

                log.error(
                    "❌ Telegram HTTP %s",
                    response.status_code
                )

                log.error(
                    "%s",
                    response.text[:500]
                )

    except Exception as e:

        log.error(
            "❌ Telegram hatası: %s",
            e
        )


# ============================================================
# LIVE KEŞFİ
# ============================================================

async def discover_live():

    log.info(
        "🔎 LIVE listesi alınıyor..."
    )

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
                response.status_code
            )

            if response.status_code != 200:

                log.error(
                    "❌ LIVE keşfi başarısız: HTTP %s",
                    response.status_code
                )

                log.error(
                    "Cevap: %s",
                    response.text[:500]
                )

                return []

            result = response.json()

    except httpx.ProxyError as e:

        log.error(
            "❌ PROXY HATASI: %s",
            e
        )

        return []

    except httpx.TimeoutException:

        log.error(
            "⏱️ LIVE keşfi timeout."
        )

        return []

    except Exception as e:

        log.error(
            "❌ LIVE keşif hatası: %s",
            e
        )

        return []

    channels = result.get(
        "channels",
        []
    )

    if not isinstance(channels, list):

        log.error(
            "❌ channels bulunamadı."
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

        if len(lives) >= MAX_LIVES:
            break

    log.info(
        "🔥 %s LIVE bulundu.",
        len(lives)
    )

    for index, live in enumerate(
        lives,
        start=1
    ):

        log.info(
            "🔴 %s/%s -> @%s | %s",
            index,
            len(lives),
            live["username"],
            live["region"]
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
    event_type="envelope"
):

    if not isinstance(data, dict):
        data = {}

    envelope_id = (
        data.get("envelopeId")
        or data.get("envelope_id")
        or ""
    )

    envelope_id = str(
        envelope_id
    )

    # --------------------------------------------------------
    # DUPLICATE KONTROL
    # --------------------------------------------------------

    if envelope_id:

        dedupe_key = (
            f"{username.lower()}:{envelope_id}"
        )

        if dedupe_key in sent_envelopes:

            log.info(
                "♻️ Tekrarlanan hazine atlandı -> @%s",
                username
            )

            return

        sent_envelopes.add(
            dedupe_key
        )

    # --------------------------------------------------------
    # DEĞER
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # GÖNDEREN
    # --------------------------------------------------------

    sender = (
        data.get("sendUserName")
        or data.get("send_user_name")
        or ""
    )

    if not sender:

        user = data.get(
            "user",
            {}
        )

        if isinstance(user, dict):

            sender = (
                user.get("uniqueId")
                or user.get("unique_id")
                or user.get("nickname")
                or ""
            )

    sender = str(
        sender
    )

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    if event_type == "superFanBox":

        title = "🟣 SUPER FAN BOX BULUNDU!"

        log.warning(
            "🟣 HAZİNE -> SUPER FAN BOX | "
            "@%s | 💎 %s | 👥 %s",
            username,
            diamonds,
            people
        )

    else:

        title = "🎁 HAZİNE BULUNDU!"

        log.warning(
            "🎁 HAZİNE BULUNDU | "
            "@%s | 💎 %s | 👥 %s",
            username,
            diamonds,
            people
        )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    message = (
        f"<b>{title}</b>\n\n"
        f"📺 Yayın: @{html.escape(username)}\n"
        f"💎 Değer: {html.escape(str(diamonds))}\n"
        f"👥 Kişi: {html.escape(str(people))}\n"
        f"👤 Gönderen: {html.escape(sender)}\n"
        f"🆔 Envelope: {html.escape(envelope_id)}\n\n"
        f"🌍 Global LIVE taraması"
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
        "Content-Type": "application/json",
        "Accept": "application/json",
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
                json=payload
            )

            log.info(
                "🔐 JWT HTTP %s -> @%s",
                response.status_code,
                username
            )

            if response.status_code != 200:

                log.error(
                    "❌ JWT alınamadı -> @%s",
                    username
                )

                log.error(
                    "JWT cevap: %s",
                    response.text[:500]
                )

                return None

            result = response.json()

    except httpx.ProxyError as e:

        log.error(
            "❌ JWT PROXY HATASI -> @%s: %s",
            username,
            e
        )

        return None

    except httpx.TimeoutException:

        log.error(
            "⏱️ JWT timeout -> @%s",
            username
        )

        return None

    except Exception as e:

        log.error(
            "❌ JWT hatası -> @%s: %s",
            username,
            e
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
            username
        )

        return None

    log.info(
        "✅ JWT hazır -> @%s",
        username
    )

    return token


# ============================================================
# EVENT
# ============================================================

async def process_ws_message(
    username,
    raw
):

    if isinstance(raw, bytes):

        raw = raw.decode(
            "utf-8",
            errors="replace"
        )

    try:

        event = json.loads(
            raw
        )

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
        {}
    )

    if not isinstance(data, dict):
        data = {}

    # --------------------------------------------------------
    # HAZİNE
    # --------------------------------------------------------

    if event_name == "envelope":

        await handle_envelope(
            username,
            data,
            "envelope"
        )

        return

    # --------------------------------------------------------
    # SUPER FAN BOX
    # --------------------------------------------------------

    if event_name == "superFanBox":

        await handle_envelope(
            username,
            data,
            "superFanBox"
        )

        return

    # --------------------------------------------------------
    # ROOM INFO
    # --------------------------------------------------------

    if event_name == "roomInfo":

        log.info(
            "🏠 ROOM INFO -> @%s",
            username
        )

        return

    # --------------------------------------------------------
    # DİĞERLERİ
    # --------------------------------------------------------

    if event_name in {
        "chat",
        "like",
        "gift",
        "member",
        "follow",
        "share",
        "fanTicket",
        "roomUserSeq",
        "roomUser",
    }:

        return

    if event_name:

        log.info(
            "ℹ️ EVENT -> @%s | %s",
            username,
            event_name
        )


# ============================================================
# TEK LIVE'I SADECE KISA SÜRE TARA
# ============================================================

async def watch_live(username):

    username = (
        str(username)
        .strip()
        .lstrip("@")
    )

    if not username:
        return False

    token = await mint_jwt(
        username
    )

    if not token:

        return False

    ws_url = (
        f"{WS_BASE}"
        f"?uniqueId={quote(username, safe='')}"
        f"&jwtKey={quote(token, safe='')}"
    )

    log.info(
        "🔌 TARAMA BAŞLADI -> @%s | %ss",
        username,
        WATCH_SECONDS
    )

    found = False

    try:

        async with websockets.connect(
            ws_url,

            # REST/JWT ile aynı proxy
            proxy=HTTP_PROXY,

            open_timeout=15,
            close_timeout=5,

            # Kısa taramada ping gerekmez
            ping_interval=None,
            ping_timeout=None,

            max_size=None,
        ) as ws:

            log.info(
                "🟢 WS BAĞLANDI -> @%s",
                username
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
                        timeout=remaining
                    )

                except asyncio.TimeoutError:

                    break

                before = len(
                    sent_envelopes
                )

                await process_ws_message(
                    username,
                    raw
                )

                after = len(
                    sent_envelopes
                )

                if after > before:

                    found = True

                    log.info(
                        "🎯 HAZİNE YAKALANDI -> @%s",
                        username
                    )

                    # Hazineyi aldıktan sonra
                    # bu LIVE'da beklemiyoruz.
                    break

            if found:

                log.info(
                    "💰 HAZİNE ALINDI -> SONRAKİ LIVE -> @%s",
                    username
                )

            else:

                log.info(
                    "⏭️ %ss İÇİNDE HAZİNE YOK -> @%s",
                    WATCH_SECONDS,
                    username
                )

    except websockets.exceptions.InvalidStatus as e:

        log.error(
            "❌ WS HTTP HATASI -> @%s | %s",
            username,
            e
        )

    except websockets.exceptions.ConnectionClosed as e:

        log.warning(
            "🔌 WS KAPANDI -> @%s | code=%s",
            username,
            getattr(e, "code", "")
        )

    except asyncio.TimeoutError:

        log.warning(
            "⏱️ WS TIMEOUT -> @%s",
            username
        )

    except Exception as e:

        log.error(
            "❌ WS HATASI -> @%s | %s",
            username,
            e
        )

    finally:

        log.info(
            "➡️ SONRAKİ LIVE -> @%s",
            username
        )

    return found


# ============================================================
# HIZLI GLOBAL TARAMA
# ============================================================

async def global_scanner():

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    log.info(
        "🚀 HIZLI GLOBAL SCANNER BAŞLADI"
    )

    log.info(
        "⚙️ MAX_CONNECTIONS = %s",
        MAX_CONNECTIONS
    )

    log.info(
        "⚙️ WATCH_SECONDS = %s",
        WATCH_SECONDS
    )

    log.info(
        "⚙️ MAX_LIVES = %s",
        MAX_LIVES
    )

    while True:

        # ====================================================
        # YENİ LIVE LİSTESİ
        # ====================================================

        lives = await discover_live()

        if not lives:

            log.info(
                "😴 LIVE bulunamadı."
            )

            await asyncio.sleep(
                min(DISCOVERY_INTERVAL, 60)
            )

            continue

        # ====================================================
        # BİR TUR
        # ====================================================

        log.info(
            "🚀 %s LIVE TARANACAK",
            len(lives)
        )

        for index, live in enumerate(
            lives,
            start=1
        ):

            username = live[
                "username"
            ]

            log.info(
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )

            log.info(
                "🎯 LIVE %s/%s -> @%s",
                index,
                len(lives),
                username
            )

            # ------------------------------------------------
            # BAĞLANTI LİMİTİ
            # ------------------------------------------------

            async with semaphore:

                await watch_live(
                    username
                )

            # ------------------------------------------------
            # BİR SONRAKİ LIVE
            # ------------------------------------------------

            await asyncio.sleep(
                0.2
            )

        log.info(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        log.info(
            "✅ TUR TAMAMLANDI."
        )

        # ====================================================
        # API'Yİ SÜREKLİ ÇAĞIRMAMAK İÇİN BEKLE
        # ====================================================

        log.info(
            "⏳ %s saniye sonra yeni LIVE listesi...",
            DISCOVERY_INTERVAL
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
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"TreasureAlert OK\n"
        )

    def log_message(
        self,
        format,
        *args
    ):

        return


def start_health_server():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    )

    log.info(
        "🌐 Render health server -> PORT %s",
        port
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
        "VAR" if API_KEY else "YOK"
    )

    log.info(
        "🤖 BOT_TOKEN: %s",
        "VAR" if BOT_TOKEN else "YOK"
    )

    log.info(
        "💬 CHAT_ID: %s",
        "VAR" if CHAT_ID else "YOK"
    )

    log.info(
        "🌐 HTTP PROXY: %s",
        "AKTİF" if HTTP_PROXY else "KAPALI"
    )

    log.info(
        "⚙️ MAX_CONNECTIONS=%s",
        MAX_CONNECTIONS
    )

    log.info(
        "⚙️ WATCH_SECONDS=%s",
        WATCH_SECONDS
    )

    log.info(
        "⚙️ MAX_LIVES=%s",
        MAX_LIVES
    )

    log.info(
        "⚙️ DISCOVERY_INTERVAL=%s",
        DISCOVERY_INTERVAL
    )

    await global_scanner()


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
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
            e
        )

        raise
