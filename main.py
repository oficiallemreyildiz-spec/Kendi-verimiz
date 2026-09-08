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
# AYARLAR
# ============================================================

API_ID = int(os.getenv("API_ID", "36135300"))
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")

TARGET_CHAT_ID = -1004421946217

# Hem CHEST hem GOODY BAG kaynakları
SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445,
]

PORT = int(os.getenv("PORT", "10000"))


# ============================================================
# GLOBAL
# ============================================================

LIVE_CHESTS = []
LIVE_GOODY_BAGS = []

# Aynı Telegram mesajını tekrar işlememek için
PROCESSED_MESSAGES = set()

telegram_queue = None
http_session = None
last_telegram_send = 0


# ============================================================
# HTTP RADAR
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
                "processed_messages": len(
                    PROCESSED_MESSAGES
                ),
            })

            return

        self.send_json({
            "status": "ok",
            "chests": len(LIVE_CHESTS),
            "goody_bags": len(LIVE_GOODY_BAGS),
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
# SÜRESİ DOLANLARI TEMİZLE
# ============================================================

def clean_expired():

    now = time.time()

    global LIVE_CHESTS
    global LIVE_GOODY_BAGS

    LIVE_CHESTS = [
        x
        for x in LIVE_CHESTS
        if x.get("target_time", now + 1) > now
    ]

    LIVE_GOODY_BAGS = [
        x
        for x in LIVE_GOODY_BAGS
        if x.get("target_time", now + 1) > now
    ]


# ============================================================
# TOKEN URL'DEN ÇIKAR
# ============================================================

def extract_token_from_text(text):

    if not text:
        return None

    patterns = [

        # Tam t.php URL
        r'https?://[^)\s]+/tiktok/t\.php\?token=([A-Za-z0-9_-]+={0,2})',

        # Host fark etmeksizin
        r'(?:https?://)?[^)\s]+/tiktok/t\.php\?token=([A-Za-z0-9_-]+={0,2})',

        # Sadece token=
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
# TELEGRAM ENTITY İÇİNDEN TOKEN BUL
# ============================================================

def extract_token_from_event(event):

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
    # 2. TELEGRAM LINK ENTITY
    #
    # Örnek:
    #
    # [01:30 - 10:21:28](https://.../t.php?token=XXXX)
    #
    # raw_text sadece:
    #
    # 01:30 - 10:21:28
    #
    # gösterebilir.
    #
    # Gerçek URL entity.url içindedir.
    # --------------------------------------------------------

    try:

        message = event.message

        for entity, entity_text in (
            message.get_entities_text()
        ):

            url = getattr(
                entity,
                "url",
                None
            )

            if not url:
                continue

            if (
                "/tiktok/t.php?token="
                not in url
            ):
                continue

            print(
                "[TOKEN] Telegram link entity bulundu."
            )

            token = extract_token_from_text(
                url
            )

            if token:

                return token

    except Exception as e:

        print(
            "[TOKEN] Entity okuma hatası:",
            repr(e)
        )

    # --------------------------------------------------------
    # 3. DIRECT ENTITY
    # --------------------------------------------------------

    try:

        entities = (
            event.message.entities
            or []
        )

        for entity in entities:

            url = getattr(
                entity,
                "url",
                None
            )

            if not url:
                continue

            if (
                "/tiktok/t.php?token="
                not in url
            ):
                continue

            print(
                "[TOKEN] Direct entity URL bulundu."
            )

            token = extract_token_from_text(
                url
            )

            if token:

                return token

    except Exception as e:

        print(
            "[TOKEN] Direct entity hatası:",
            repr(e)
        )

    return None


# ============================================================
# TOKEN DECODE
# ============================================================

def decode_token(token):

    try:

        token = unquote(
            token.strip()
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
# COIN
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

            except:
                pass

    # Token içindeki değerler
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

                except:
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

            except:
                pass

    return 0


# ============================================================
# TIME
# ============================================================

def calculate_target_time(
    token_data,
    text
):

    now = time.time()

    # --------------------------------------------------------
    # Token UNIX timestamp
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

                if token_time > now:

                    return token_time

            except:
                pass

    # --------------------------------------------------------
    # time_display
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

    # Fallback
    return now + 180


# ============================================================
# SOURCE MESAJ PARSE
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
    # LIVE LINK
    # --------------------------------------------------------

    live_link = None

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

            live_link = openitok

    # --------------------------------------------------------
    # ROOM FALLBACK
    # --------------------------------------------------------

    if not live_link and room:

        live_link = (
            "https://www.tiktok.com/"
            "share/live/"
            f"{room}"
        )

    # --------------------------------------------------------
    # USER FALLBACK
    # --------------------------------------------------------

    if not live_link:

        live_link = (
            "https://www.tiktok.com/"
            f"@{username}/live"
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
            target_time
            - time.time()
        )
    )

    # --------------------------------------------------------
    # DATA
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

        "detected_at": time.time(),

        "token_data": token_data,
    }


# ============================================================
# RADAR'A EKLE
# ============================================================

def add_to_radar(data):

    if not data:
        return

    if data.get("is_goody"):

        target = LIVE_GOODY_BAGS

        radar_type = "GOODY BAG"

    else:

        target = LIVE_CHESTS

        radar_type = "CHEST"

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

        for i, old in enumerate(target):

            if old.get("room") == room:

                target[i] = data

                replaced = True

                break

    # --------------------------------------------------------
    # USERNAME'A GÖRE GÜNCELLE
    # --------------------------------------------------------

    if (
        not replaced
        and username
    ):

        for i, old in enumerate(target):

            if (
                old.get("username")
                == username
            ):

                target[i] = data

                replaced = True

                break

    # --------------------------------------------------------
    # YENİ KAYIT
    # --------------------------------------------------------

    if not replaced:

        target.append(data)

    clean_expired()

    print(
        f"[RADAR] {radar_type} "
        f"eklendi/güncellendi."
    )


# ============================================================
# TELEGRAM MESAJ
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

    is_goody = data.get(
        "is_goody",
        False
    )

    time_display = data.get(
        "time_display",
        ""
    )

    # --------------------------------------------------------
    # TÜR
    # --------------------------------------------------------

    if is_goody:

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
# TELEGRAM SENDER
# ============================================================

async def telegram_sender():

    global last_telegram_send

    while True:

        text = await telegram_queue.get()

        try:

            now = time.time()

            wait = (
                1
                - (now - last_telegram_send)
            )

            if wait > 0:

                await asyncio.sleep(
                    wait
                )

            url = (
                f"https://api.telegram.org/"
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

            last_telegram_send = time.time()

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
# ANA MESAJ İŞLEYİCİ
# ============================================================

async def process_message(
    event,
    text,
    chat_title
):

    # --------------------------------------------------------
    # TOKEN BUL
    # --------------------------------------------------------

    token = extract_token_from_event(
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

        print(
            "[SKIP] Token decode edilemedi."
        )

        return

    # --------------------------------------------------------
    # TOKEN DEBUG
    # --------------------------------------------------------

    print(
        "[TOKEN DATA]"
    )

    print(
        json.dumps(
            token_data,
            ensure_ascii=False
        )
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
        "================================"
    )

    if data["is_goody"]:

        print(
            "🟪 GOODY BAG YAKALANDI"
        )

    else:

        print(
            "🟨 CHEST YAKALANDI"
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
        "LINK:",
        data["live_link"]
    )

    print(
        "================================"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_text = (
        build_telegram_message(
            data
        )
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

        except:

            pass

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
            getattr(
                event,
                "chat_id",
                ""
            )
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
        # AYNI MESAJI TEKRAR İŞLEME
        # ----------------------------------------------------

        message_id = getattr(
            event.message,
            "id",
            None
        )

        chat_id = getattr(
            event,
            "chat_id",
            None
        )

        unique_id = (
            chat_id,
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

        # Çok büyümesini önle
        if len(PROCESSED_MESSAGES) > 10000:

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
    # ENV KONTROL
    # --------------------------------------------------------

    if not
