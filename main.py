import asyncio
import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import websockets


# =========================================================
# AYARLAR
# =========================================================

TIKTOOL_API_KEY = os.getenv("TIKTOOL_API_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

PROXY_HOST = os.getenv("PROXY_HOST", "")
PROXY_PORT = os.getenv("PROXY_PORT", "")
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")

MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "3"))
WATCH_SECONDS = int(os.getenv("WATCH_SECONDS", "45"))
MAX_LIVES = int(os.getenv("MAX_LIVES", "10"))
DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL", "300"))

API_BASE = "https://api.tik.tools"
WS_BASE = "wss://api.tik.tools"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
)

# API key'in Render loglarında görünmesini engelle
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger("TREASURE")


# =========================================================
# PROXY
# =========================================================

def build_proxy():
    if not all([
        PROXY_HOST,
        PROXY_PORT,
        PROXY_USERNAME,
        PROXY_PASSWORD,
    ]):
        return None

    return (
        f"http://{PROXY_USERNAME}:"
        f"{PROXY_PASSWORD}@"
        f"{PROXY_HOST}:{PROXY_PORT}"
    )


HTTP_PROXY = build_proxy()


# =========================================================
# HTTP SERVER - RENDER HEALTH CHECK
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TREASURE ALERT OK")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    log.info(f"HEALTH SERVER -> PORT {port}")

    server.serve_forever()


# =========================================================
# TELEGRAM
# =========================================================

async def telegram_send(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:

            response = await client.post(
                url,
                json=payload
            )

            if response.status_code != 200:
                log.warning(
                    f"TELEGRAM HTTP {response.status_code}"
                )

    except Exception as e:
        log.warning(f"TELEGRAM HATA -> {e}")


# =========================================================
# DUPLICATE
# =========================================================

sent_envelopes = set()


# =========================================================
# ENVELOPE
# =========================================================

async def handle_envelope(data, username):

    try:

        envelope_id = (
            data.get("envelopeId")
            or data.get("id")
            or ""
        )

        diamond_count = int(
            data.get("diamondCount")
            or data.get("diamonds")
            or 0
        )

        people_count = int(
            data.get("peopleCount")
            or data.get("people")
            or 0
        )

        sender = (
            data.get("sendUserName")
            or data.get("sender")
            or data.get("userName")
            or "Bilinmiyor"
        )

        unique_key = (
            f"{username}:"
            f"{envelope_id}:"
            f"{diamond_count}:"
            f"{people_count}"
        )

        if unique_key in sent_envelopes:
            return False

        sent_envelopes.add(unique_key)

        # Belleğin sonsuza kadar büyümesini engelle
        if len(sent_envelopes) > 5000:
            sent_envelopes.clear()

        message = (
            "🎁 <b>TREASURE ALERT</b>\n\n"
            f"👤 LIVE: <b>@{username}</b>\n"
            f"💎 Elmas: <b>{diamond_count}</b>\n"
            f"👥 Kişi: <b>{people_count}</b>\n"
            f"🎁 Gönderen: <b>{sender}</b>"
        )

        log.info(
            f"🎁 ZARF BULUNDU -> "
            f"@{username} | "
            f"{diamond_count} 💎 | "
            f"{people_count} kişi"
        )

        await telegram_send(message)

        return True

    except Exception as e:

        log.warning(
            f"ENVELOPE PARSE HATA @{username} -> {e}"
        )

        return False


# =========================================================
# JWT CACHE
# =========================================================

jwt_token = None
jwt_users = set()
jwt_expires_at = 0

jwt_lock = asyncio.Lock()


async def get_batch_jwt(usernames):

    global jwt_token
    global jwt_users
    global jwt_expires_at

    users = sorted(
        set(
            u.lstrip("@").strip()
            for u in usernames
            if u
        )
    )

    if not users:
        return None

    now = time.monotonic()

    # Mevcut token hâlâ geçerli ve bütün kullanıcıları kapsıyorsa
    if (
        jwt_token
        and now < jwt_expires_at - 30
        and set(users).issubset(jwt_users)
    ):
        return jwt_token

    async with jwt_lock:

        # Lock beklerken başka görev token aldıysa tekrar kontrol et
        now = time.monotonic()

        if (
            jwt_token
            and now < jwt_expires_at - 30
            and set(users).issubset(jwt_users)
        ):
            return jwt_token

        payload = {
            "allowed_creators": users,
            "expire_after": 600,
            "max_websockets": MAX_CONNECTIONS,
        }

        log.info(
            f"JWT İSTENİYOR -> {len(users)} LIVE | "
            f"MAX WS={MAX_CONNECTIONS}"
        )

        try:

            async with httpx.AsyncClient(
                proxy=HTTP_PROXY,
                timeout=20
            ) as client:

                response = await client.post(
                    f"{API_BASE}/authentication/jwt"
                    f"?apiKey={TIKTOOL_API_KEY}",
                    json=payload
                )

            log.info(
                f"JWT HTTP -> {response.status_code}"
            )

            if response.status_code == 429:

                retry_after = response.headers.get(
                    "Retry-After",
                    "60"
                )

                try:
                    wait_seconds = int(retry_after)
                except:
                    wait_seconds = 60

                wait_seconds = min(
                    max(wait_seconds, 10),
                    300
                )

                log.warning(
                    f"JWT 429 -> {wait_seconds} saniye bekleniyor"
                )

                await asyncio.sleep(wait_seconds)

                return None

            if response.status_code != 200:

                log.warning(
                    f"JWT REDDEDİLDİ -> "
                    f"{response.status_code}"
                )

                return None

            result = response.json()

            token = (
                result
                .get("data", {})
                .get("token")
            )

            if not token:

                log.warning("JWT TOKEN YOK")

                return None

            jwt_token = token
            jwt_users = set(users)

            jwt_expires_at = (
                time.monotonic() + 600
            )

            log.info(
                "JWT HAZIR -> "
                f"{len(users)} LIVE için ortak token"
            )

            return jwt_token

        except Exception as e:

            log.warning(
                f"JWT HATA -> {e}"
            )

            return None


# =========================================================
# TIKTOOL LIVE DISCOVERY
# =========================================================

async def discover_live():

    try:

        async with httpx.AsyncClient(
            proxy=HTTP_PROXY,
            timeout=20
        ) as client:

            response = await client.get(
                f"{API_BASE}/api/live/top-channels"
            )

        log.info(
            f"TOP-CHANNELS HTTP {response.status_code}"
        )

        if response.status_code != 200:
            return []

        result = response.json()

        channels = result.get(
            "channels",
            []
        )

        lives = []

        for item in channels:

            username = (
                item.get("uniqueId")
                or item.get("unique_id")
            )

            if not username:
                continue

            username = username.lstrip("@")

            lives.append({
                "username": username,
                "displayName": (
                    item.get("displayName")
                    or username
                ),
                "roomId": (
                    item.get("roomId")
                    or item.get("room_id")
                ),
            })

        lives = lives[:MAX_LIVES]

        log.info(
            f"GENEL TARAMA: "
            f"{len(lives)}/{MAX_LIVES} LIVE BULUNDU"
        )

        if lives:

            log.info(
                "LIVE LISTESİ -> "
                + ", ".join(
                    "@" + x["username"]
                    for x in lives
                )
            )

        return lives

    except Exception as e:

        log.warning(
            f"DISCOVERY HATA -> {e}"
        )

        return []


# =========================================================
# WEBSOCKET MESSAGE
# =========================================================

async def process_ws_message(
    raw,
    username
):

    try:

        message = json.loads(raw)

    except Exception:
        return False

    event = message.get("event")

    data = message.get(
        "data",
        {}
    )

    if event == "envelope":

        return await handle_envelope(
            data,
            username
        )

    if event == "superFanBox":

        log.info(
            f"⭐ SUPER FAN BOX -> @{username}"
        )

        return await handle_envelope(
            data,
            username
        )

    # Gereksiz eventleri sessizce geç
    return False


# =========================================================
# LIVE WATCH
# =========================================================

async def watch_live(
    username,
    token
):

    ws_url = (
        f"{WS_BASE}"
        f"?uniqueId={username}"
        f"&jwtKey={token}"
    )

    log.info(
        f"WS BAŞLIYOR -> @{username}"
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
                f"WS BAĞLANDI -> @{username}"
            )

            deadline = (
                asyncio.get_running_loop().time()
                + WATCH_SECONDS
            )

            while True:

                remaining = (
                    deadline
                    - asyncio.get_running_loop().time()
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

                except websockets.ConnectionClosed:
                    break

                found = await process_ws_message(
                    raw,
                    username
                )

                if found:
                    break

            log.info(
                f"TARAMA BİTTİ -> @{username}"
            )

    except Exception as e:

        log.warning(
            f"WS HATA @{username} -> {e}"
        )


# =========================================================
# 3'LÜ BATCH
# =========================================================

async def scan_batch(
    lives,
    start_index,
    token
):

    batch = lives[
        start_index:
        start_index + MAX_CONNECTIONS
    ]

    if not batch:
        return

    usernames = [
        x["username"]
        for x in batch
    ]

    log.info(
        "PARALEL GRUP -> "
        + ", ".join(
            "@" + x
            for x in usernames
        )
    )

    await asyncio.gather(
        *[
            watch_live(
                username,
                token
            )
            for username in usernames
        ],
        return_exceptions=True
    )


# =========================================================
# GLOBAL SCANNER
# =========================================================

async def global_scanner():

    log.info(
        "GLOBAL SCANNER BAŞLADI"
    )

    current_lives = []
    last_discovery = 0

    while True:

        now = time.monotonic()

        # Yeni LIVE listesi zamanı
        if (
            not current_lives
            or now - last_discovery
            >= DISCOVERY_INTERVAL
        ):

            discovered = await discover_live()

            if discovered:

                current_lives = discovered
                last_discovery = now

            else:

                log.warning(
                    "LIVE BULUNAMADI"
                )

                await asyncio.sleep(10)
                continue

        usernames = [
            x["username"]
            for x in current_lives
        ]

        # =================================================
        # TEK JWT
        # =================================================

        token = await get_batch_jwt(
            usernames
        )

        if not token:

            log.warning(
                "JWT ALINAMADI -> "
                "10 saniye sonra tekrar"
            )

            await asyncio.sleep(10)
            continue

        # =================================================
        # 3'ERLİ TARAMA
        # =================================================

        for index in range(
            0,
            len(current_lives),
            MAX_CONNECTIONS
        ):

            # Token süresi dolmak üzereyse
            # bu turu kesip yeni token al
            if (
                time.monotonic()
                >= jwt_expires_at - 30
            ):
                log.info(
                    "JWT SÜRESİ DOLMAK ÜZERE"
                )
                break

            await scan_batch(
                current_lives,
                index,
                token
            )

        # Listeyi sonsuza kadar körü körüne
        # kullanmak yerine discovery zamanını kontrol et
        if (
            time.monotonic()
            - last_discovery
            >= DISCOVERY_INTERVAL
        ):

            current_lives = []

        else:

            # Hemen tekrar tarayabiliriz;
            # WATCH_SECONDS=45 ile günlük WS
            # limiti aşılmayacak şekilde ayarlanmıştır.
            await asyncio.sleep(1)


# =========================================================
# MAIN
# =========================================================

async def main():

    log.info(
        "🔥 TREASURE ALERT GLOBAL BAŞLIYOR"
    )

    log.info(
        f"TIKTOOL API KEY: "
        f"{'VAR' if TIKTOOL_API_KEY else 'YOK'}"
    )

    log.info(
        f"BOT TOKEN: "
        f"{'VAR' if BOT_TOKEN else 'YOK'}"
    )

    log.info(
        f"CHAT ID: "
        f"{'VAR' if CHAT_ID else 'YOK'}"
    )

    log.info(
        f"HTTP PROXY: "
        f"{'AKTİF' if HTTP_PROXY else 'YOK'}"
    )

    log.info(
        f"MAX_CONNECTIONS={MAX_CONNECTIONS}"
    )

    log.info(
        f"WATCH_SECONDS={WATCH_SECONDS}"
    )

    log.info(
        f"MAX_LIVES={MAX_LIVES}"
    )

    log.info(
        f"DISCOVERY_INTERVAL={DISCOVERY_INTERVAL}"
    )

    await global_scanner()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    # Health server'ı ayrı thread'de çalıştır
    import threading

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        log.info("BOT DURDURULDU")

    except Exception as e:
        log.exception(
            f"FATAL HATA -> {e}"
        )
