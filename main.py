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

# Webshare / HTTP proxy
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# İstersen PROXY_URL yerine bunlar da kullanılabilir.
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "").strip()
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "").strip()
PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()


# ============================================================
# AYARLAR
# ============================================================

# Tik.Tools public top-channels maksimum 10 kanal döndürüyor.
MAX_LIVES = 10

# Public ticker için 5 dakika.
DISCOVERY_INTERVAL = 300

# Aynı anda maksimum WebSocket.
MAX_CONNECTIONS = int(
    os.getenv("MAX_CONNECTIONS", "5")
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
# PROXY OLUŞTUR
# ============================================================

def build_proxy_url():

    # Öncelik: hazır PROXY_URL
    if PROXY_URL:

        proxy = PROXY_URL.strip()

        # Kullanıcı http:// yazmadıysa ekle.
        if "://" not in proxy:
            proxy = "http://" + proxy

        return proxy

    # Alternatif ENV yapısı:
    #
    # PROXY_HOST
    # PROXY_PORT
    # PROXY_USERNAME
    # PROXY_PASSWORD

    if PROXY_HOST and PROXY_PORT:

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

    return None


TIKTOOL_PROXY = build_proxy_url()


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

            else:

                log.info(
                    "📨 Telegram bildirimi gönderildi."
                )

    except Exception as e:

        log.error(
            "❌ Telegram bağlantı hatası: %s",
            e,
        )


# ============================================================
# LIVE KEŞFİ
# ============================================================

async def discover_live():

    if not API_KEY:

        log.error(
            "❌ TIKTOOL_API_KEY bulunamadı!"
        )

        return []

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
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://tik.tools/",
    }

    try:

        log.info(
            "🔎 Tik.Tools LIVE keşfi başlıyor..."
        )

        if TIKTOOL_PROXY:

            log.info(
                "🌐 LIVE keşfinde proxy kullanılıyor."
            )

        else:

            log.warning(
                "⚠️ PROXY_URL bulunamadı. "
                "Direkt bağlantı deneniyor."
            )

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            proxy=TIKTOOL_PROXY,
            headers=headers,
        ) as client:

            response = await client.get(
                "https://api.tik.tools/api/live/top-channels"
            )

            log.info(
                "🔎 TOP-CHANNELS HTTP %s",
                response.status_code,
            )

            # ------------------------------------------------
            # BAŞARILI
            # ------------------------------------------------

            if response.status_code == 200:

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

            # ------------------------------------------------
            # 403
            # ------------------------------------------------

            elif response.status_code == 403:

                log.error(
                    "🚫 TOP-CHANNELS 403 FORBIDDEN"
                )

                log.error(
                    "Sunucu Render/proxy isteğini reddetti."
                )

                log.error(
                    "Cevap: %s",
                    response.text[:300],
                )

                return []

            # ------------------------------------------------
            # 429
            # ------------------------------------------------

            elif response.status_code == 429:

                log.error(
                    "⏳ TOP-CHANNELS 429 RATE LIMIT"
                )

                log.error(
                    "Public ticker limitine ulaşıldı."
                )

                return []

            # ------------------------------------------------
            # DİĞER
            # ------------------------------------------------

            else:

                log.error(
                    "❌ TOP-CHANNELS HTTP %s",
                    response.status_code,
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

    except httpx.ConnectError as e:

        log.error(
            "❌ BAĞLANTI HATASI: %s",
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

    # ========================================================
    # CHANNELS
    # ========================================================

    channels = result.get(
        "channels",
        [],
    )

    if not isinstance(
        channels,
        list,
    ):

        log.error(
            "❌ API cevabında channels listesi yok."
        )

        log.info(
            "API keys: %s",
            list(result.keys())[:30],
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

        username = str(
            username
        ).strip().lstrip("@")

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

    # ========================================================
    # SONUÇ
    # ========================================================

    log.info(
        "🔥 GENEL TARAMA: %s LIVE BULUNDU",
        len(lives),
    )

    if not lives:

        log.warning(
            "⚠️ Şu anda keşfedilen LIVE yok."
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

    # --------------------------------------------------------
    # DUPLICATE ENGELLE
    # --------------------------------------------------------

    if envelope_id:

        dedupe_key = (
            f"{username.lower()}:{envelope_id}"
        )

        if dedupe_key in sent_envelopes:

            log.info(
                "♻️ Tekrarlanan envelope atlandı -> @%s",
                username,
            )

            return

        sent_envelopes.add(
            dedupe_key
        )

        if len(sent_envelopes) > 5000:

            sent_envelopes.clear()

    # --------------------------------------------------------
    # DEĞER
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

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    log.warning(
        "🎁🎁🎁 HAZİNE BULUNDU | "
        "@%s | 💎 %s | 👤 %s | 🆔 %s",
        username,
        diamonds,
        sender,
        envelope_id,
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

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
        "🟣 SUPER FAN BOX | "
        "@%s | 💎 %s | 👤 %s",
        username,
        diamonds,
        sender,
    )

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
# TEK LIVE DİNLE
# ============================================================

async def watch_live(
    live,
    semaphore,
):

    username = live["username"]

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

                            log.warning(
                                "⚠️ JSON olmayan WS mesajı -> @%s",
                                username,
                            )

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

                        # ====================================
                        # HAZİNE
                        # ====================================

                        if event_name == "envelope":

                            await handle_envelope(
                                username,
                                data,
                            )

                        # ====================================
                        # SUPER FAN BOX
                        # ====================================

                        elif event_name == "superFanBox":

                            await handle_superfanbox(
                                username,
                                data,
                            )

                        # ====================================
                        # YAYIN SONU
                        # ====================================

                        elif event_name in (
                            "streamEnd",
                            "stream_end",
                        ):

                            log.info(
                                "⏹️ YAYIN BİTTİ -> @%s",
                                username,
                            )

                            return

                        # ====================================
                        # CONTROL
                        # ====================================

                        elif event_name == "control":

                            log.info(
                                "🎛️ CONTROL -> @%s",
                                username,
                            )

                            # Bazı sistemlerde control
                            # yayının kapanması anlamına
                            # gelebilir; bağlantıyı kapat.
                            return

            except asyncio.CancelledError:

                log.info(
                    "🛑 WATCH İPTAL -> @%s",
                    username,
                )

                raise

            except websockets.exceptions.InvalidStatus as e:

                log.warning(
                    "⚠️ @%s WS HTTP durumu: %s",
                    username,
                    e,
                )

            except Exception as e:

                log.warning(
                    "⚠️ @%s WS kapandı: %s",
                    username,
                    str(e)[:300],
                )

            # ------------------------------------------------
            # RECONNECT
            # ------------------------------------------------

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

    log.info(
        "📡 Maksimum WS: %s",
        MAX_CONNECTIONS,
    )

    log.info(
        "🔎 Keşif aralığı: %s saniye",
        DISCOVERY_INTERVAL,
    )

    if TIKTOOL_PROXY:

        log.info(
            "🌐 Tik.Tools keşif proxy: AKTİF"
        )

    else:

        log.warning(
            "⚠️ Tik.Tools keşif proxy: YOK"
        )

    while True:

        try:

            lives = await discover_live()

            current = {
                live["username"].lower(): live
                for live in lives
            }

            # ================================================
            # YENİ LIVE'LAR
            # ================================================

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

            # ================================================
            # ARTIK LIVE OLMAYANLAR
            # ================================================

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

            # ================================================
            # AKTİF WS
            # ================================================

            # Tamamlanmış task'leri temizle.
            for key in list(active):

                task = active[key]

                if task.done():

                    active.pop(
                        key,
                        None,
                    )

            log.info(
                "📡 AKTİF WS: %s",
                len(active),
            )

        except Exception as e:

            log.error(
                "❌ GLOBAL SCANNER HATASI: %s",
                e,
            )

        # ====================================================
        # 5 DAKİKA BEKLE
        # ====================================================

        log.info(
            "⏳ Sonraki LIVE keşfi %s saniye sonra.",
            DISCOVERY_INTERVAL,
        )

        await asyncio.sleep(
            DISCOVERY_INTERVAL
        )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class Handler(
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

    # --------------------------------------------------------
    # ENV KONTROL
    # --------------------------------------------------------

    if not API_KEY:

        log.error(
            "❌ TIKTOOL_API_KEY YOK!"
        )

    else:

        log.info(
            "🔑 TIKTOOL_API_KEY: OK"
        )

    if not BOT_TOKEN:

        log.error(
            "❌ BOT_TOKEN YOK!"
        )

    else:

        log.info(
            "🤖 BOT_TOKEN: OK"
        )

    if not CHAT_ID:

        log.error(
            "❌ CHAT_ID YOK!"
        )

    else:

        log.info(
            "💬 CHAT_ID: OK"
        )

    if TIKTOOL_PROXY:

        log.info(
            "🌐 PROXY: OK"
        )

    else:

        log.warning(
            "⚠️ PROXY: YOK"
        )

    # --------------------------------------------------------
    # RENDER SERVER
    # --------------------------------------------------------

    threading.Thread(
        target=start_server,
        daemon=True,
    ).start()

    # --------------------------------------------------------
    # SCANNER
    # --------------------------------------------------------

    try:

        asyncio.run(
            global_scanner()
        )

    except KeyboardInterrupt:

        log.info(
            "🛑 Sistem kapatıldı."
        )
