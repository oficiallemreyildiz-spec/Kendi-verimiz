import os
import re
import time
import json
import base64
import asyncio
import threading
from urllib.parse import unquote

import aiohttp

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from http.server import HTTPServer, BaseHTTPRequestHandler


# ============================================================
# ENV
# ============================================================

API_ID = int(os.getenv("API_ID", "36135300"))
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")

PORT = int(os.getenv("PORT", "10000"))

TARGET_CHAT_ID = -1004421946217


# ============================================================
# KAYNAK TELEGRAM GRUPLARI
# ============================================================

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445,
]


# ============================================================
# RADAR VERİLERİ
# ============================================================

LIVE_CHESTS = []
LIVE_GOODY_BAGS = []

PROCESSED_MESSAGES = set()

telegram_queue = None
http_session = None

last_telegram_send = 0


# ============================================================
# YARDIMCI
# ============================================================

def now_ts():
    return time.time()


# ============================================================
# EXPIRED TEMİZLE
# ============================================================

def clean_expired():

    now = now_ts()

    global LIVE_CHESTS
    global LIVE_GOODY_BAGS

    LIVE_CHESTS = [
        item
        for item in LIVE_CHESTS
        if float(item.get("target_time", 0)) > now
    ]

    LIVE_GOODY_BAGS = [
        item
        for item in LIVE_GOODY_BAGS
        if float(item.get("target_time", 0)) > now
    ]


# ============================================================
# HTTP SERVER
# ============================================================

class RadarHandler(BaseHTTPRequestHandler):

    def send_json(self, data):

        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "*"
        )

        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self):

        self.send_response(200)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "*"
        )

        self.end_headers()

    def do_GET(self):

        clean_expired()

        path = self.path.split("?")[0]

        if path == "/api/boxes":

            self.send_json(LIVE_CHESTS)
            return

        if path == "/api/goody_bags":

            self.send_json(LIVE_GOODY_BAGS)
            return

        if path == "/api/status":

            self.send_json({
                "status": "ok",
                "chests": len(LIVE_CHESTS),
                "goody_bags": len(LIVE_GOODY_BAGS),
                "time": time.time()
            })

            return

        self.send_json({
            "status": "ok",
            "chests": len(LIVE_CHESTS),
            "goody_bags": len(LIVE_GOODY_BAGS)
        })

    def log_message(self, format, *args):
        return


def start_http_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        RadarHandler
    )

    print(
        f"[WEB] Radar API : {PORT}"
    )

    server.serve_forever()


# ============================================================
# TOKEN ÇIKAR
# ============================================================

def extract_token_from_text(text):

    if not text:
        return None

    patterns = [

        # Tam URL
        r'https?://[^)\s]+/tiktok/t\.php\?token=([A-Za-z0-9_-]+={0,2})',

        # URL host fark etmeksizin
        r'(?:https?://)?[^)\s]+/tiktok/t\.php\?token=([A-Za-z0-9_-]+={0,2})',

        # Sadece token parametresi
        r'token=([A-Za-z0-9_-]+={0,2})',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            return match.group(1)

    return None


# ============================================================
# TELEGRAM MESAJ ENTITY İÇİNDEN TOKEN
# ============================================================

async def extract_token_from_event(event):

    text = event.raw_text or ""

    # --------------------------------------------------------
    # 1. RAW TEXT
    # --------------------------------------------------------

    token = extract_token_from_text(text)

    if token:

        print(
            "[TOKEN] raw_text üzerinden bulundu."
        )

        return token

    # --------------------------------------------------------
    # 2. TELEGRAM ENTITY
    # --------------------------------------------------------

    try:

        message = event.message

        entities = message.entities or []

        for entity in entities:

            url = getattr(
                entity,
                "url",
                None
            )

            if not url:
                continue

            if "/tiktok/t.php?token=" not in url:
                continue

            token = extract_token_from_text(
                url
            )

            if token:

                print(
                    "[TOKEN] Telegram entity üzerinden bulundu."
                )

                return token

    except Exception as e:

        print(
            "[TOKEN] Entity okuma hatası:",
            repr(e)
        )

    # --------------------------------------------------------
    # 3. get_entities_text()
    # --------------------------------------------------------

    try:

        message = event.message

        pairs = message.get_entities_text()

        for entity, entity_text in pairs:

            url = getattr(
                entity,
                "url",
                None
            )

            if not url:
                continue

            if "/tiktok/t.php?token=" not in url:
                continue

            token = extract_token_from_text(
                url
            )

            if token:

                print(
                    "[TOKEN] get_entities_text "
                    "üzerinden bulundu."
                )

                return token

    except Exception as e:

        print(
            "[TOKEN] get_entities_text hatası:",
            repr(e)
        )

    return None


# ============================================================
# TOKEN DECODE
# ============================================================

def decode_token(token):

    try:

        token = unquote(
            str(token).strip()
        )

        # Base64 padding
        token += "=" * (
            (-len(token)) % 4
        )

        raw = base64.urlsafe_b64decode(
            token
        )

        data = json.loads(
            raw.decode("utf-8")
        )

        return data

    except Exception as e:

        print(
            "[TOKEN] Decode hatası:",
            repr(e)
        )

        return None


# ============================================================
# COIN BUL
# ============================================================

def extract_coins(text, token_data):

    text = text or ""

    patterns = [

        # TÚI: 50/20
        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',

        # BOX: 20/16
        r'BOX\s*:\s*(\d+)\s*/',

        # Genel 50/20
        r'(\d+)\s*/\s*\d+',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            try:

                return int(
                    match.group(1)
                )

            except Exception:
                pass

    # Token değerleri
    if token_data:

        for key in (
            "maxzem",
            "value",
            "coins",
            "coin"
        ):

            value = token_data.get(key)

            if value is not None:

                try:

                    return int(value)

                except Exception:
                    pass

    return 10


# ============================================================
# JOINED
# ============================================================

def extract_joined(text):

    if not text:
        return 0

    patterns = [

        r'Đã\s*join\s*:\s*(\d+)',

        r'Dã\s*join\s*:\s*(\d+)',

        r'join\s*:\s*(\d+)',

        r'joined\s*:\s*(\d+)',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            try:

                return int(
                    match.group(1)
                )

            except Exception:
                pass

    return 0


# ============================================================
# TIME
# ============================================================

def calculate_target_time(
    token_data,
    text
):

    now = now_ts()

    # --------------------------------------------------------
    # TOKEN TIME
    # --------------------------------------------------------

    if token_data:

        token_time = token_data.get(
            "time"
        )

        if token_time:

            try:

                token_time = float(
                    token_time
                )

                # Token'daki timestamp gelecekteyse
                if token_time > now:

                    return token_time

            except Exception:
                pass

    # --------------------------------------------------------
    # TIME DISPLAY
    #
    # 01:30 - 10:21:28
    # --------------------------------------------------------

    display = ""

    if token_data:

        display = str(
            token_data.get(
                "time_display",
                ""
            )
        )

    if not display:

        match = re.search(
            r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2}:\d{2})',
            text or ""
        )

        if match:

            display = match.group(0)

    if display:

        match = re.search(
            r'(\d{1,2}):(\d{2})\s*-',
            display
        )

        if match:

            minutes = int(
                match.group(1)
            )

            seconds = int(
                match.group(2)
            )

            return (
                now
                + minutes * 60
                + seconds
            )

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    return now + 180


# ============================================================
# LIVE LINK
# ============================================================

def get_live_link(
    username,
    room,
    openitok
):

    # Öncelik gerçek openitok
    if openitok:

        openitok = str(
            openitok
        ).strip()

        if (
            openitok.startswith(
                "https://www.tiktok.com/"
            )
            or
            openitok.startswith(
                "https://tiktok.com/"
            )
        ):

            return openitok

    # Room
    if room:

        return (
            "https://www.tiktok.com/"
            "share/live/"
            f"{room}"
        )

    # Username
    if username:

        return (
            "https://www.tiktok.com/"
            f"@{username}/live"
        )

    return None


# ============================================================
# MESAJI PARSE ET
# ============================================================

def parse_source_message(
    text,
    chat_title,
    token
):

    token_data = decode_token(
        token
    )

    if not token_data:

        print(
            "[SKIP] Token decode başarısız."
        )

        return None

    # --------------------------------------------------------
    # TOKEN VERİLERİ
    # --------------------------------------------------------

    username = token_data.get(
        "username"
    )

    room = token_data.get(
        "room"
    )

    openitok = token_data.get(
        "openitok"
    )

    is_goody_bag = bool(
        token_data.get(
            "is_goody_bag",
            False
        )
    )

    people = token_data.get(
        "people",
        0
    )

    ratio = token_data.get(
        "ratio",
        0
    )

    view = token_data.get(
        "view",
        0
    )

    matxem = token_data.get(
        "matxem",
        0
    )

    box_tag = token_data.get(
        "box_tag",
        ""
    )

    item_label = token_data.get(
        "item_label",
        ""
    )

    time_display = token_data.get(
        "time_display",
        ""
    )

    # --------------------------------------------------------
    # USERNAME
    # --------------------------------------------------------

    if not username:

        username = "Bilinmeyen_Yayinci"

    username = str(
        username
    ).strip().lstrip("@")

    # --------------------------------------------------------
    # COIN
    # --------------------------------------------------------

    coins = extract_coins(
        text,
        token_data
    )

    # --------------------------------------------------------
    # JOINED
    # --------------------------------------------------------

    joined = extract_joined(
        text
    )

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    live_link = get_live_link(
        username,
        room,
        openitok
    )

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    target_time = calculate_target_time(
        token_data,
        text
    )

    total_duration = max(
        1,
        int(
            target_time - now_ts()
        )
    )

    # --------------------------------------------------------
    # SONUÇ
    # --------------------------------------------------------

    return {

        "username": username,

        "coins": coins,

        "people": people,

        "can_open": people,

        "joined": joined,

        "ratio": ratio,

        "viewers": view,

        "matxem": matxem,

        "room": str(
            room or ""
        ),

        "live_link": live_link,

        "box_name": box_tag,

        "item_label": item_label,

        "is_goody": is_goody_bag,

        "is_gold": False,

        "target_time": target_time,

        "total_duration": total_duration,

        "time_display": time_display,

        "source_chat": str(
            chat_title or ""
        ),

        "detected_at": now_ts(),

        "token_data": token_data,
    }


# ============================================================
# RADAR'A EKLE
# ============================================================

def add_to_radar(data):

    if not data:
        return

    # --------------------------------------------------------
    # GOODY BAG
    # --------------------------------------------------------

    if data["is_goody"]:

        target = LIVE_GOODY_BAGS

        radar_name = "GOODY BAG"

    # --------------------------------------------------------
    # CHEST
    # --------------------------------------------------------

    else:

        target = LIVE_CHESTS

        radar_name = "CHEST"

    room = data.get(
        "room"
    )

    username = data.get(
        "username"
    )

    replaced = False

    # --------------------------------------------------------
    # ROOM'A GÖRE GÜNCELLE
    # --------------------------------------------------------

    if room:

        for index, old in enumerate(
            target
        ):

            if old.get("room") == room:

                target[index] = data

                replaced = True

                break

    # --------------------------------------------------------
    # USERNAME'A GÖRE GÜNCELLE
    # --------------------------------------------------------

    if (
        not replaced
        and username
    ):

        for index, old in enumerate(
            target
        ):

            if (
                old.get("username")
                == username
            ):

                target[index] = data

                replaced = True

                break

    # --------------------------------------------------------
    # YENİ
    # --------------------------------------------------------

    if not replaced:

        target.append(
            data
        )

    clean_expired()

    print(
        f"[RADAR] {radar_name} "
        f"{'güncellendi' if replaced else 'eklendi'}."
    )


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def build_telegram_message(data):

    username = data.get(
        "username",
        "Bilinmeyen_Yayinci"
    )

    coins = data.get(
        "coins",
        10
    )

    people = data.get(
        "people",
        0
    )

    joined = data.get(
        "joined",
        0
    )

    ratio = data.get(
        "ratio",
        0
    )

    viewers = data.get(
        "viewers",
        0
    )

    room = data.get(
        "room",
        ""
    )

    live_link = data.get(
        "live_link"
    )

    time_display = data.get(
        "time_display",
        ""
    )

    # --------------------------------------------------------
    # TÜR
    # --------------------------------------------------------

    if data.get("is_goody"):

        title = "🟪 GOODY BAG"

        item = (
            f"🧺 TÚI: **{coins}**"
        )

    else:

        title = "🟨 HAZİNE SANDIĞI"

        item = (
            f"📦 BOX: **{coins}**"
        )

    lines = [

        title,

        f"👤 **@{username}**",

        item,

        f"👥 Kişi: **{people}**",
    ]

    if joined:

        lines.append(
            f"👥 Join: **{joined}**"
        )

    if ratio:

        lines.append(
            f"📈 Rate: **{ratio}**"
        )

    if viewers:

        lines.append(
            f"👀 **{viewers}**"
        )

    if time_display:

        lines.append(
            f"⏳ **{time_display}**"
        )

    if room:

        lines.append(
            f"🏠 Room: `{room}`"
        )

    if live_link:

        lines.append(
            f"🔗 {live_link}"
        )

    return "\n".join(lines)


# ============================================================
# TELEGRAM GÖNDERİCİ
# ============================================================

async def telegram_sender():

    global last_telegram_send

    while True:

        text = await telegram_queue.get()

        try:

            current = now_ts()

            wait = (
                1
                - (
                    current
                    - last_telegram_send
                )
            )

            if wait > 0:

                await asyncio.sleep(
                    wait
                )

            url = (
                "https://api.telegram.org/"
                f"bot{BOT_TOKEN}/sendMessage"
            )

            payload = {

                "chat_id":
                    TARGET_CHAT_ID,

                "text":
                    text,

                "disable_web_page_preview":
                    True,
            }

            async with http_session.post(
                url,
                json=payload,
                timeout=20
            ) as response:

                result = await response.text()

                if response.status != 200:

                    print(
                        "[TELEGRAM] Hata:",
                        response.status,
                        result
                    )

                else:

                    print(
                        "[TELEGRAM] Bildirim gönderildi."
                    )

            last_telegram_send = now_ts()

        except Exception as e:

            print(
                "[TELEGRAM] Exception:",
                repr(e)
            )

        finally:

            telegram_queue.task_done()


async def send_telegram(text):

    await telegram_queue.put(
        text
    )


# ============================================================
# MESAJ İŞLE
# ============================================================

async def process_message(
    event,
    text,
    chat_title
):

    # --------------------------------------------------------
    # TOKEN
    # --------------------------------------------------------

    token = await extract_token_from_event(
        event
    )

    if not token:

        print(
            "[SKIP] t.php token bulunamadı."
        )

        return

    print(
        "[OK] t.php token bulundu."
    )

    # --------------------------------------------------------
    # TOKEN DECODE
    # --------------------------------------------------------

    token_data = decode_token(
        token
    )

    if not token_data:

        return

    # --------------------------------------------------------
    # TÜRÜ ÖNCEDEN GÖSTER
    # --------------------------------------------------------

    is_goody = bool(
        token_data.get(
            "is_goody_bag",
            False
        )
    )

    if is_goody:

        print(
            "[TYPE] 🟪 GOODY BAG"
        )

    else:

        print(
            "[TYPE] 🟨 CHEST"
        )

    # --------------------------------------------------------
    # PARSE
    # --------------------------------------------------------

    data = parse_source_message(
        text,
        chat_title,
        token
    )

    if not data:

        return

    # --------------------------------------------------------
    # RADAR
    # --------------------------------------------------------

    add_to_radar(
        data
    )

    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------

    print()
    print(
        "========================================"
    )

    print(
        "TYPE:",
        "GOODY BAG"
        if data["is_goody"]
        else "CHEST"
    )

    print(
        "USERNAME:",
        data["username"]
    )

    print(
        "COINS:",
        data["coins"]
    )

    print(
        "PEOPLE:",
        data["people"]
    )

    print(
        "JOINED:",
        data["joined"]
    )

    print(
        "RATE:",
        data["ratio"]
    )

    print(
        "VIEW:",
        data["viewers"]
    )

    print(
        "ROOM:",
        data["room"]
    )

    print(
        "LIVE:",
        data["live_link"]
    )

    print(
        "========================================"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_text = build_telegram_message(
        data
    )

    await send_telegram(
        telegram_text
    )


# ============================================================
# TELEGRAM EVENT
# ============================================================

async def message_listener(event):

    try:

        text = event.raw_text or ""

        # ----------------------------------------------------
        # CHAT
        # ----------------------------------------------------

        chat_title = ""

        try:

            if event.chat:

                chat_title = (
                    getattr(
                        event.chat,
                        "title",
                        ""
                    )
                    or ""
                )

        except Exception:
            pass

        # ----------------------------------------------------
        # DEBUG
        # ----------------------------------------------------

        print()
        print(
            "========== YENİ MESAJ =========="
        )

        print(
            "[CHAT]",
            chat_title
        )

        print(
            "[CHAT ID]",
            event.chat_id
        )

        print(
            "[MESSAGE ID]",
            getattr(
                event.message,
                "id",
                ""
            )
        )

        print(
            "[RAW]",
            repr(text)
        )

        print(
            "================================"
        )

        # ----------------------------------------------------
        # DUPLICATE
        # ----------------------------------------------------

        message_id = getattr(
            event.message,
            "id",
            None
        )

        unique_id = (
            event.chat_id,
            message_id
        )

        if unique_id in PROCESSED_MESSAGES:

            print(
                "[SKIP] Mesaj daha önce işlendi."
            )

            return

        PROCESSED_MESSAGES.add(
            unique_id
        )

        # Çok büyürse temizle
        if len(PROCESSED_MESSAGES) > 20000:

            PROCESSED_MESSAGES.clear()

        # ----------------------------------------------------
        # İŞLE
        # ----------------------------------------------------

        await process_message(
            event,
            text,
            chat_title
        )

    except Exception as e:

        print(
            "[LISTENER ERROR]",
            repr(e)
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global http_session
    global telegram_queue

    print(
        "[TELEGRAM] Başlatılıyor..."
    )

    # --------------------------------------------------------
    # ENV
    # --------------------------------------------------------

    if not API_HASH:

        raise ValueError(
            "API_HASH boş."
        )

    if not STRING_SESSION:

        raise ValueError(
            "STRING_SESSION boş."
        )

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN boş."
        )

    # --------------------------------------------------------
    # QUEUE
    # --------------------------------------------------------

    telegram_queue = asyncio.Queue()

    # --------------------------------------------------------
    # WEB
    # --------------------------------------------------------

    web_thread = threading.Thread(
        target=start_http_server,
        daemon=True
    )

    web_thread.start()

    # --------------------------------------------------------
    # HTTP SESSION
    # --------------------------------------------------------

    http_session = (
        aiohttp.ClientSession()
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    client = TelegramClient(
        StringSession(
            STRING_SESSION
        ),
        API_ID,
        API_HASH
    )

    # --------------------------------------------------------
    # CHEST + GOODY BAG
    #
    # İKİSİ DE AYNI ANDA DİNLENİYOR.
    # --------------------------------------------------------

    client.add_event_handler(
        message_listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        )
    )

    # --------------------------------------------------------
    # BAĞLAN
    # --------------------------------------------------------

    await client.start()

    print(
        "[TELEGRAM] Bağlantı başarılı."
    )

    print(
        "[TELEGRAM] Kaynak gruplar dinleniyor..."
    )

    print(
        "[TELEGRAM] 🟨 CHEST AKTİF"
    )

    print(
        "[TELEGRAM] 🟪 GOODY BAG AKTİF"
    )

    print(
        f"[TELEGRAM] {len(SOURCE_CHATS)} "
        "kaynak grup aktif."
    )

    # --------------------------------------------------------
    # SENDER
    # --------------------------------------------------------

    asyncio.create_task(
        telegram_sender()
    )

    # --------------------------------------------------------
    # DIALOG KONTROL
    # --------------------------------------------------------

    try:

        dialogs = await client.get_dialogs()

        print(
            f"[TELEGRAM] Toplam dialog: "
            f"{len(dialogs)}"
        )

    except Exception as e:

        print(
            "[DIALOG] Hata:",
            repr(e)
        )

    # --------------------------------------------------------
    # ÇALIŞ
    # --------------------------------------------------------

    await client.run_until_disconnected()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "[SYSTEM] Durduruldu."
        )

    except Exception as e:

        print(
            "[FATAL]",
            repr(e)
        )
