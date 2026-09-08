import os
import re
import json
import base64
import asyncio
import time
from urllib.parse import unquote

import aiohttp
from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession


# =========================================================
# AYARLAR
# =========================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ["STRING_SESSION"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

TARGET_CHAT_ID = -1004421946217

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445,
]

PORT = int(os.environ.get("PORT", "10000"))


# =========================================================
# VERİLER
# =========================================================

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}

processed_messages = set()

telegram_queue = asyncio.Queue()

http_session = None


# =========================================================
# YARDIMCI FONKSİYONLAR
# =========================================================

def safe_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def safe_float(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


# =========================================================
# TOKEN BUL
# =========================================================

def token_from_event(event):

    try:
        message = event.message
        text = message.raw_text or ""
    except Exception:
        return None

    patterns = [
        r'https?://[^ \n\]\)]+/t\.php\?token=([^&\s\]\)]+)',
        r'https?://[^ \n\]\)]+t\.php\?token=([^&\s\]\)]+)',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            return unquote(match.group(1))

    # Telegram URL entities
    try:

        for entity in message.entities or []:

            url = getattr(entity, "url", None)

            if url:

                match = re.search(
                    r't\.php\?token=([^&\s]+)',
                    url,
                    re.IGNORECASE
                )

                if match:
                    print("[TOKEN] Telegram entity üzerinden bulundu")
                    return unquote(match.group(1))

    except Exception as error:

        print(
            "[TOKEN ENTITY]",
            repr(error)
        )

    # Alternatif entity yöntemi
    try:

        for entity, _ in message.get_entities_text():

            url = getattr(entity, "url", None)

            if url:

                match = re.search(
                    r't\.php\?token=([^&\s]+)',
                    url,
                    re.IGNORECASE
                )

                if match:
                    print("[TOKEN] Telegram entity text üzerinden bulundu")
                    return unquote(match.group(1))

    except Exception as error:

        print(
            "[TOKEN ENTITY TEXT]",
            repr(error)
        )

    return None


# =========================================================
# TOKEN ÇÖZ
# =========================================================

def decode_token(token):

    if not token:
        return None

    try:

        value = unquote(str(token)).strip()

        decoded = base64.urlsafe_b64decode(
            value + "=" * (-len(value) % 4)
        )

        result = json.loads(
            decoded.decode(
                "utf-8",
                errors="ignore"
            )
        )

        print("[TOKEN] çözüldü")

        return result

    except Exception as error:

        print(
            "[TOKEN HATA]",
            repr(error)
        )

        return None


# =========================================================
# ROOM BUL
# =========================================================

def room_from_text(text):

    patterns = [
        r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)',
        r'https?://[^ \n]+/t/\?p=([A-Za-z0-9_\-+/=]+)',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text or "",
            re.IGNORECASE
        )

        if not match:
            continue

        try:

            value = match.group(1)

            decoded = base64.urlsafe_b64decode(
                value + "=" * (-len(value) % 4)
            ).decode(
                "utf-8",
                errors="ignore"
            ).strip()

            if decoded.isdigit():
                return decoded

        except Exception:
            pass

    return None


# =========================================================
# USERNAME
# =========================================================

def username_from_text(text):

    patterns = [
        r'^\s*##\s*T\d+\s*[›>:]\s*([^\s\n]+)',
        r'^\s*T\d+\s*[›>:]\s*([^\s\n]+)',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text or "",
            re.MULTILINE
        )

        if match:
            return match.group(1).strip()

    return None


# =========================================================
# COIN
# =========================================================

def extract_coins(text, data=None):

    patterns = [
        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
        r'BOX\s*:\s*(\d+)\s*/',
        r'(\d+)\s*/\s*(\d+)',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text or "",
            re.IGNORECASE
        )

        if match:
            return safe_int(match.group(1))

    if data:

        for key in [
            "coins",
            "coin",
            "gem",
            "diamond",
            "amount",
        ]:

            if key in data:
                return safe_int(data[key])

    return 0


# =========================================================
# KİŞİ
# =========================================================

def extract_people(text):

    patterns = [
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        r'(\d+)\s*/\s*(\d+)',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text or "",
            re.IGNORECASE
        )

        if not match:
            continue

        if (
            "TÚI" in pattern
            or "TUI" in pattern
            or "BOX" in pattern
        ):
            return safe_int(match.group(1))

        return safe_int(match.group(2))

    return 0


# =========================================================
# KATILAN
# =========================================================

def extract_joined(text):

    patterns = [
        r'Đã\s*join\s*:\s*(\d+)',
        r'joined\s*:\s*(\d+)',
        r'join\s*:\s*(\d+)',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text or "",
            re.IGNORECASE
        )

        if match:
            return safe_int(match.group(1))

    return 0


# =========================================================
# İZLENME
# =========================================================

def extract_viewers(text):

    match = re.search(
        r'👀\s*(\d+)',
        text or ""
    )

    if match:
        return safe_int(match.group(1))

    return 0


# =========================================================
# RATE
# =========================================================

def extract_rate(text, data=None):

    match = re.search(
        r'Rate\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        text or "",
        re.IGNORECASE
    )

    if match:
        return safe_float(match.group(1))

    if data:

        for key in [
            "ratio",
            "rate",
        ]:

            if key in data:
                return safe_float(data[key])

    return 0


# =========================================================
# GOODY / CHEST TESPİTİ
# =========================================================

def is_goody(text, data):

    upper = (text or "").upper()

    # GOODY
    if re.search(
        r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG',
        upper
    ):
        return True

    # CHEST
    if (
        re.search(
            r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
            upper
        )
        or "🟡" in (text or "")
    ):
        return False

    if data:

        if data.get("is_goody_bag") in [
            True,
            1,
            "1",
            "true",
            "True",
        ]:
            return True

        if data.get("is_goody_bag") in [
            False,
            0,
            "0",
            "false",
            "False",
        ]:
            return False

    return None


# =========================================================
# HEDEF ZAMAN
# =========================================================

def target_time(text, data=None):

    now = int(time.time())

    if data:

        for key in [
            "time",
            "target_time",
            "end_time",
            "endTime",
        ]:

            if key not in data:
                continue

            try:

                value = int(float(data[key]))

                if value > 10_000_000_000:
                    return value // 1000

                if value > 1_000_000_000:
                    return value

                if 0 < value < 86400:
                    return now + value

            except Exception:
                pass

    match = re.search(
        r'TIME\s*:\s*(\d+):(\d+)',
        text or "",
        re.IGNORECASE
    )

    if match:

        minutes = safe_int(match.group(1))
        seconds = safe_int(match.group(2))

        return now + minutes * 60 + seconds

    return now + 180


# =========================================================
# MESAJI PARSE ET
# =========================================================

def parse_event(event):

    text = event.message.raw_text or ""

    token = token_from_event(event)

    data = decode_token(token)

    goody = is_goody(
        text,
        data
    )

    if goody is None:
        return None

    # USERNAME
    username = None

    if data:

        for key in [
            "username",
            "user",
            "unique_id",
            "uniqueId",
        ]:

            if data.get(key):

                username = str(
                    data[key]
                )

                break

    username = (
        username
        or username_from_text(text)
        or "bilinmiyor"
    )

    # ROOM
    room = None

    if data:

        for key in [
            "room",
            "room_id",
            "roomid",
            "roomId",
            "roomID",
        ]:

            if data.get(key):

                room = str(
                    data[key]
                )

                break

    room = (
        room
        or room_from_text(text)
    )

    if not room:

        room = "msg:" + str(
            event.message.id
        )

    # KİŞİ
    people = extract_people(text)

    if not people and data:

        for key in [
            "people",
            "person",
            "count",
            "capacity",
        ]:

            if key in data:

                people = safe_int(
                    data[key]
                )

                if people:
                    break

    # KATILAN
    joined = extract_joined(text)

    if not joined and data:

        for key in [
            "joined",
            "join",
            "join_count",
            "joined_count",
        ]:

            if key in data:

                joined = safe_int(
                    data[key]
                )

                if joined:
                    break

    # VIEW
    viewers = extract_viewers(text)

    if not viewers and data:

        for key in [
            "view",
            "views",
            "viewer",
            "viewers",
        ]:

            if key in data:

                viewers = safe_int(
                    data[key]
                )

                if viewers:
                    break

    # LIVE URL
    live_url = ""

    if data:

        for key in [
            "openitok",
            "live",
            "live_url",
            "url",
        ]:

            value = data.get(key)

            if (
                value
                and str(value).startswith(
                    (
                        "http://",
                        "https://",
                    )
                )
            ):

                live_url = str(value)

                break

    if not live_url:

        live_url = (
            "https://www.tiktok.com/"
            f"share/live/{room}"
        )

    result = {

        "type":
            "GOODY BAG"
            if goody
            else
            "CHEST",

        "box_name":
            "Goody Bag"
            if goody
            else
            "Hazine Sandığı",

        "username":
            username,

        "coins":
            extract_coins(
                text,
                data
            ),

        "people":
            people,

        "joined":
            joined,

        "rate":
            extract_rate(
                text,
                data
            ),

        "view":
            viewers,

        "room":
            room,

        "live":
            live_url,

        "target_time":
            target_time(
                text,
                data
            ),

        "detected_at":
            int(time.time()),

        "source_message_id":
            event.message.id,
    }

    print()
    print("[PARSE SONUCU]")
    print("TÜR:", result["type"])
    print("KULLANICI:", result["username"])
    print("COIN:", result["coins"])
    print("KİŞİ:", result["people"])
    print("KATILAN:", result["joined"])
    print("ORAN:", result["rate"])
    print("İZLENME:", result["view"])
    print("ODA:", result["room"])
    print("HEDEF:", result["target_time"])
    print("ALGILANMA:", result["detected_at"])

    return result


# =========================================================
# RADARA EKLE
# =========================================================

def add_to_radar(data):

    if not data:
        return False

    if not data.get("room"):
        return False

    if data["type"] == "GOODY BAG":

        target = LIVE_GOODY_BAGS

    else:

        target = LIVE_CHESTS

    room = data["room"]

    # Çok kısa sürede aynı room tekrar gelirse alma
    if room in target:

        old_time = safe_int(
            target[room].get(
                "detected_at",
                0
            )
        )

        if (
            int(time.time()) - old_time
            < 5
        ):
            return False

    target[room] = data

    print(
        "[RADAR]",
        data["type"],
        data["username"],
        "EKLENDİ"
    )

    return True


# =========================================================
# TELEGRAM MESAJI
# =========================================================

async def send_telegram(data):

    global http_session

    if http_session is None:
        return

    if data["type"] == "GOODY BAG":

        title = "🟪 GOODY BAG"

    else:

        title = "🟨 HAZİNE SANDIĞI"

    text = (
        f"{title}\n\n"
        f"👤 Kullanıcı: {data['username']}\n"
        f"🪙 Coin: {data['coins']}\n"
        f"👥 Kişi: {data['people']}\n"
        f"🙋 Katılan: {data['joined']}\n"
        f"📈 Oran: {data['rate']}\n"
        f"👀 İzlenme: {data['view']}\n"
    )

    # Direkt URL
    if data.get("live"):

        text += (
            "\n🔴 "
            f"{data['live']}"
        )

    telegram_url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    for attempt in range(1, 9):

        try:

            payload = {
                "chat_id":
                    TARGET_CHAT_ID,

                "text":
                    text,

                "disable_web_page_preview":
                    True,
            }

            async with http_session.post(
                telegram_url,
                json=payload
            ) as response:

                response_text = (
                    await response.text()
                )

                if response.status == 200:

                    return

                # Rate limit
                if response.status == 429:

                    try:

                        wait = (
                            json.loads(
                                response_text
                            )
                            .get(
                                "parameters",
                                {}
                            )
                            .get(
                                "retry_after",
                                30
                            )
                        )

                    except Exception:

                        wait = 30

                    print(
                        "[TELEGRAM 429]"
                        f" {wait} saniye bekleniyor"
                    )

                    await asyncio.sleep(
                        max(
                            1,
                            safe_int(
                                wait,
                                30
                            )
                        )
                    )

                    continue

                # Geçici Telegram hataları
                if response.status in [
                    500,
                    502,
                    503,
                    504,
                ]:

                    await asyncio.sleep(
                        min(
                            5 * attempt,
                            30
                        )
                    )

                    continue

                print(
                    "[TELEGRAM HATA]",
                    response.status,
                    response_text
                )

                return

        except Exception as error:

            print(
                "[TELEGRAM]",
                repr(error)
            )

            await asyncio.sleep(
                min(
                    5 * attempt,
                    30
                )
            )


# =========================================================
# TELEGRAM QUEUE
# =========================================================

async def sender():

    while True:

        data = await telegram_queue.get()

        try:

            await send_telegram(data)

        finally:

            telegram_queue.task_done()


# =========================================================
# HTML
# =========================================================

RADAR_HTML = r"""
<!doctype html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>🏆 ÖDÜL AVCISI</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 7px;
    background: #05060c;
    color: #fff;
    font-family: Arial, sans-serif;
}

.wrap {
    width: 100%;
    max-width: 1200px;
    margin: auto;
}

.head {
    text-align: center;
    padding: 7px 4px 10px;
}

.title {
    font-size: clamp(25px, 7vw, 48px);
    font-weight: 1000;
    text-shadow:
        0 0 5px #fff,
        0 0 16px #8b55ff,
        0 0 35px #5d25ff;
}

.sub {
    font-size: 11px;
    color: #aeb5c8;
    font-weight: 800;
    margin-top: 6px;
}

.status {
    display: inline-block;
    margin-top: 7px;
    padding: 5px 10px;
    border-radius: 99px;
    background: #141428;
    border: 1px solid #9d5cff55;
    color: #74ff9a;
    font-size: 9px;
    font-weight: 900;
}


/* =====================================================
   HER ZAMAN 2 KOLON
   ===================================================== */

.last,
.grid {
    display: grid;
    grid-template-columns:
        repeat(2, minmax(0, 1fr));
    gap: 8px;
    width: 100%;
}

.last {
    margin-bottom: 9px;
}

.box {
    min-width: 0;
    background: #090c15ed;
    border: 1px solid #293448;
    border-radius: 13px;
    padding: 7px;
}

.g {
    border-color: #9d51ff99;
}

.c {
    border-color: #f1c84b88;
}

.lt {
    font-size: 10px;
    font-weight: 1000;
    margin-bottom: 5px;
}

.g .lt,
.g .pn {
    color: #d5a8ff;
}

.c .lt,
.c .pn {
    color: #ffe37b;
}

.lc,
.card {
    background: #171d2df5;
    border: 1px solid #293448;
    border-radius: 8px;
    padding: 6px;
}

.lc {
    border-left: 3px solid #9d51ff;
}

.c .lc {
    border-left-color: #f1c84b;
}

.lu,
.ur {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 4px;
    font-size: 9px;
    font-weight: 1000;
    margin-bottom: 5px;
}

.stats,
.ig {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 3px;
}

.st,
.info {
    background: #ffffff09;
    border-radius: 5px;
    padding: 3px;
    font-size: 5px;
    color: #818da1;
}

.st b,
.info b {
    display: block;
    color: #fff;
    font-size: 8px;
    margin-top: 1px;
}

.pnrow {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 2px 2px 6px;
}

.pn {
    font-size: 11px;
    font-weight: 1000;
}

.cnt {
    font-size: 7px;
    background: #ffffff12;
    border-radius: 99px;
    padding: 3px 5px;
}

.card {
    margin-bottom: 4px;
    background:
        linear-gradient(
            145deg,
            #171d2df9,
            #0a0e17f9
        );
}

.card:last-child {
    margin-bottom: 0;
}

.g .card {
    border-left: 3px solid #9d51ff;
}

.c .card {
    border-left: 3px solid #f1c84b;
}

.user {
    font-size: 8px;
    font-weight: 1000;
    word-break: break-word;
}

.rank {
    font-size: 6px;
    color: #77849a;
}

.new {
    animation: newCard .7s ease-out;
}

.badge {
    padding: 3px 5px;
    border-radius: 5px;
    font-size: 6px;
    font-weight: 1000;
    background: #8d35ff;
    color: #fff;
}

.c .badge {
    background: #f4d35e;
    color: #211700;
}


/* =====================================================
   DİREKT CANLI URL
   ===================================================== */

.live-link {
    display: block;
    margin-top: 6px;
    padding: 5px;
    border-radius: 6px;
    background: #ffffff08;
    border: 1px solid #ffffff12;
    color: #8fc7ff;
    text-decoration: none;
    font-size: 6px;
    font-weight: 900;
    word-break: break-all;
    line-height: 1.4;
}

.live-link:hover {
    background: #ffffff12;
    text-decoration: underline;
}


@keyframes newCard {

    0% {
        opacity: .3;
        transform:
            translateY(-7px)
            scale(.97);
    }

    40% {
        box-shadow:
            0 0 25px #9d51ff77;
    }

    100% {
        opacity: 1;
        transform: none;
    }

}


.empty {
    text-align: center;
    padding: 16px;
    color: #626e82;
    font-size: 7px;
}

.foot {
    text-align: center;
    color: #59647a;
    font-size: 6px;
    padding: 8px;
}


/* =====================================================
   MOBİLDE DE YAN YANA
   ===================================================== */

@media (max-width: 700px) {

    body {
        padding: 4px;
    }

    .title {
        font-size: 27px;
    }

    .sub {
        font-size: 8px;
    }

    .status {
        font-size: 7px;
    }

    .last,
    .grid {
        grid-template-columns:
            repeat(2, minmax(0, 1fr));
        gap: 4px;
    }

    .box {
        padding: 5px;
        border-radius: 9px;
    }

    .lt {
        font-size: 7px;
    }

    .pn {
        font-size: 8px;
    }

    .cnt {
        font-size: 6px;
    }

    .card,
    .lc {
        padding: 5px;
    }

    .user {
        font-size: 7px;
    }

    .info,
    .st {
        font-size: 4px;
    }

    .info b,
    .st b {
        font-size: 7px;
    }

    .badge {
        font-size: 5px;
        padding: 2px 4px;
    }

    .live-link {
        font-size: 5px;
        padding: 4px;
    }

}

</style>

</head>

<body>

<div class="wrap">

    <div class="head">

        <div class="title">
            🏆 ÖDÜL AVCISI
        </div>

        <div class="sub">
            🟪 GOODY BAG • 🟨 HAZİNE SANDIĞI
        </div>

        <div
            id="status"
            class="status"
        >
            🟡 RADAR BAĞLANIYOR...
        </div>

    </div>


    <!-- =================================================
         SON YAKALANAN
         ================================================= -->

    <div class="last">

        <div class="box g">

            <div class="lt">
                ⚡ SON GOODY BAG
            </div>

            <div id="lg"></div>

        </div>


        <div class="box c">

            <div class="lt">
                ⚡ SON HAZİNE SANDIĞI
            </div>

            <div id="lc"></div>

        </div>

    </div>


    <!-- =================================================
         ANA LİSTELER
         ================================================= -->

    <div class="grid">

        <div class="box g">

            <div class="pnrow">

                <div class="pn">
                    🟪 GOODY BAG
                </div>

                <div
                    id="gc"
                    class="cnt"
                >
                    0
                </div>

            </div>

            <div id="gs"></div>

        </div>


        <div class="box c">

            <div class="pnrow">

                <div class="pn">
                    🟨 HAZİNE SANDIĞI
                </div>

                <div
                    id="cc"
                    class="cnt"
                >
                    0
                </div>

            </div>

            <div id="cs"></div>

        </div>

    </div>


    <div class="foot">
        ⚡ ÖDÜL AVCISI • CANLI RADAR
    </div>

</div>


<script>


let data = {
    goody_bags: [],
    chests: []
};


let firstLoad = true;


/* İlk açılıştaki kayıtlar eski kabul edilir */
const seenGoody = new Set();
const seenChest = new Set();

/* Yeni gelen kayıtlar */
const newGoody = new Set();
const newChest = new Set();


/* =====================================================
   HTML ESCAPE
   ===================================================== */

function esc(value) {

    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");

}


/* =====================================================
   KAYIT KEY
   ===================================================== */

function recordKey(item) {

    return String(
        item.source_message_id
        ??
        item.room
        ??
        (
            (item.username ?? "")
            +
            "_"
            +
            (item.detected_at ?? "")
        )
    );

}


/* =====================================================
   ZAMAN
   ===================================================== */

function recordTime(item) {

    return Number(
        item.detected_at
        ||
        item.created_at
        ||
        item.timestamp
        ||
        0
    );

}


/* =====================================================
   SON 5
   ===================================================== */

function five(items) {

    if (!Array.isArray(items)) {
        return [];
    }

    return [...items]
        .sort(
            (a, b) =>
                recordTime(b)
                -
                recordTime(a)
        )
        .slice(0, 5);

}


/* =====================================================
   SON KAYIT
   ===================================================== */

function renderLast(item, elementId, icon, type) {

    const element =
        document.getElementById(elementId);


    if (!item) {

        element.innerHTML =
            '<div class="empty">⚡ Henüz veri yok.</div>';

        return;
    }


    const newSet =
        type === "G"
            ?
            newGoody
            :
            newChest;


    const key =
        recordKey(item);


    element.innerHTML = `

        <div class="lc">

            <div class="lu">

                <span>
                    ${icon}
                    ${esc(item.username)}
                </span>

                ${
                    newSet.has(key)
                    ?
                    '<span class="badge">⚡ YENİ</span>'
                    :
                    ''
                }

            </div>


            <div class="stats">

                <div class="st">
                    🪙 COIN
                    <b>
                        ${esc(item.coins)}
                    </b>
                </div>

                <div class="st">
                    👥 KİŞİ
                    <b>
                        ${esc(item.people)}
                    </b>
                </div>

                <div class="st">
                    🙋 KATILAN
                    <b>
                        ${esc(item.joined)}
                    </b>
                </div>

                <div class="st">
                    📈 ORAN
                    <b>
                        ${esc(item.rate)}
                    </b>
                </div>

                <div class="st">
                    👀 İZLENME
                    <b>
                        ${esc(item.view)}
                    </b>
                </div>

                <div class="st">
                    🏠 ODA
                    <b>
                        ${esc(item.room)}
                    </b>
                </div>

            </div>


            ${
                item.live
                ?
                `
                <a
                    class="live-link"
                    href="${esc(item.live)}"
                    target="_blank"
                    rel="noopener noreferrer"
                >
                    ${esc(item.live)}
                </a>
                `
                :
                ""
            }

        </div>

    `;

}


/* =====================================================
   LİSTE
   ===================================================== */

function renderList(
    items,
    elementId,
    countId,
    icon,
    type
) {

    const element =
        document.getElementById(
            elementId
        );


    const count =
        document.getElementById(
            countId
        );


    const array =
        five(items);


    count.textContent =
        array.length;


    if (!array.length) {

        element.innerHTML =
            '<div class="empty">⚡ Henüz veri yok.</div>';

        return;
    }


    const seenSet =
        type === "G"
            ?
            seenGoody
            :
            seenChest;


    const newSet =
        type === "G"
            ?
            newGoody
            :
            newChest;


    /* Yeni kayıtları tespit et */

    array.forEach(item => {

        const key =
            recordKey(item);


        if (firstLoad) {

            seenSet.add(key);

        }
        else if (!seenSet.has(key)) {

            seenSet.add(key);

            newSet.add(key);

        }

    });


    element.innerHTML =
        array
            .map(
                (item, index) => {

                    const key =
                        recordKey(item);


                    const isNew =
                        newSet.has(key);


                    return `

                    <div
                        class="card ${
                            isNew
                                ? "new"
                                : ""
                        }"
                    >

                        <div class="ur">

                            <div class="user">

                                ${icon}

                                ${esc(
                                    item.username
                                )}

                            </div>


                            ${
                                isNew
                                ?
                                '<div class="badge">⚡ YENİ</div>'
                                :
                                `<div class="rank">#${index + 1}</div>`
                            }

                        </div>


                        <div class="ig">


                            <div class="info">

                                🪙 COIN

                                <b>
                                    ${esc(
                                        item.coins
                                    )}
                                </b>

                            </div>


                            <div class="info">

                                👥 KİŞİ

                                <b>
                                    ${esc(
                                        item.people
                                    )}
                                </b>

                            </div>


                            <div class="info">

                                🙋 KATILAN

                                <b>
                                    ${esc(
                                        item.joined
                                    )}
                                </b>

                            </div>


                            <div class="info">

                                📈 ORAN

                                <b>
                                    ${esc(
                                        item.rate
                                    )}
                                </b>

                            </div>


                            <div class="info">

                                👀 İZLENME

                                <b>
                                    ${esc(
                                        item.view
                                    )}
                                </b>

                            </div>


                            <div class="info">

                                🏠 ODA

                                <b>
                                    ${esc(
                                        item.room
                                    )}
                                </b>

                            </div>


                        </div>


                        ${
                            item.live
                            ?
                            `
                            <a
                                class="live-link"
                                href="${esc(
                                    item.live
                                )}"
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                ${esc(
                                    item.live
                                )}
                            </a>
                            `
                            :
                            ""
                        }

                    </div>

                    `;

                }
            )
            .join("");

}


/* =====================================================
   RENDER
   ===================================================== */

function render() {

    const goody =
        five(
            data.goody_bags
        );


    const chest =
        five(
            data.chests
        );


    renderLast(
        goody[0],
        "lg",
        "🟪",
        "G"
    );


    renderLast(
        chest[0],
        "lc",
        "🟨",
        "C"
    );


    renderList(
        data.goody_bags,
        "gs",
        "gc",
        "🟪",
        "G"
    );


    renderList(
        data.chests,
        "cs",
        "cc",
        "🟨",
        "C"
    );

}


/* =====================================================
   API
   ===================================================== */

async function loadData() {

    try {

        const response =
            await fetch(
                "/api/all?t=" +
                Date.now(),
                {
                    cache: "no-store"
                }
            );


        if (!response.ok) {

            throw new Error(
                response.status
            );

        }


        const result =
            await response.json();


        data = {

            goody_bags:
                Array.isArray(
                    result.goody_bags
                )
                ?
                result.goody_bags
                :
                [],


            chests:
                Array.isArray(
                    result.chests
                )
                ?
                result.chests
                :
                []

        };


        const status =
            document.getElementById(
                "status"
            );


        status.className =
            "status";


        status.textContent =
            "🟢 RADAR AKTİF • CANLI VERİ";


        render();


        firstLoad = false;


    }
    catch (error) {

        const status =
            document.getElementById(
                "status"
            );


        status.className =
            "status error";


        status.textContent =
            "🔴 VERİ BAĞLANTISI HATASI";


        console.error(error);

    }

}


/* =====================================================
   2 SANİYEDE BİR GÜNCELLE
   ===================================================== */

setInterval(
    loadData,
    2000
);


/* İLK YÜKLEME */

loadData();


</script>

</body>

</html>
"""


# =========================================================
# RADAR SAYFASI
# =========================================================

async def radar_page(request):

    return web.Response(
        text=RADAR_HTML,
        content_type="text/html",
        charset="utf-8"
    )


# =========================================================
# CORS
# =========================================================

@web.middleware
async def cors(request, handler):

    if request.method == "OPTIONS":

        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods":
                    "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            }
        )

    response = await handler(request)

    response.headers[
        "Access-Control-Allow-Origin"
    ] = "*"

    response.headers[
        "Access-Control-Allow-Methods"
    ] = "GET, OPTIONS"

    response.headers[
        "Access-Control-Allow-Headers"
    ] = "*"

    return response


# =========================================================
# API ENDPOINTLERİ
# =========================================================

async def api_all(request):

    return web.json_response({

        "status":
            "online",

        "server_time":
            int(time.time()),

        "chests":
            list(
                LIVE_CHESTS.values()
            ),

        "goody_bags":
            list(
                LIVE_GOODY_BAGS.values()
            ),

    })


async def api_boxes(request):

    return web.json_response(
        list(
            LIVE_CHESTS.values()
        )
    )


async def api_goody(request):

    return web.json_response(
        list(
            LIVE_GOODY_BAGS.values()
        )
    )


async def api_status(request):

    return web.json_response({

        "status":
            "online",

        "chests":
            len(LIVE_CHESTS),

        "goody_bags":
            len(LIVE_GOODY_BAGS),

        "server_time":
            int(time.time()),

    })


# =========================================================
# HTTP SUNUCU
# =========================================================

async def start_http():

    app = web.Application(
        middlewares=[cors]
    )


    app.router.add_get(
        "/",
        radar_page
    )


    app.router.add_get(
        "/radar",
        radar_page
    )


    app.router.add_get(
        "/api/all",
        api_all
    )


    app.router.add_get(
        "/api/boxes",
        api_boxes
    )


    app.router.add_get(
        "/api/goody_bags",
        api_goody
    )


    app.router.add_get(
        "/api/status",
        api_status
    )


    for path in [
        "/api/all",
        "/api/boxes",
        "/api/goody_bags",
        "/api/status",
    ]:

        app.router.add_options(
            path,
            lambda request:
                web.Response(
                    status=204
                )
        )


    # ÖNEMLİ:
    # Bunlar ayrı ve geçerli Python satırlarıdır.

    runner = web.AppRunner(app)

    await runner.setup()


    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )


    await site.start()


    print(
        "[HTTP] Sunucu başladı:",
        PORT
    )


# =========================================================
# TELEGRAM LISTENER
# =========================================================

async def listener(event):

    try:

        message_key = (
            event.chat_id,
            event.message.id
        )


        if message_key in processed_messages:
            return


        processed_messages.add(
            message_key
        )


        if len(processed_messages) > 50000:

            processed_messages.clear()


        data = parse_event(event)


        if data:

            added = add_to_radar(
                data
            )


            if added:

                await telegram_queue.put(
                    data
                )


    except Exception as error:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(error)
        )


# =========================================================
# ANA PROGRAM
# =========================================================

async def main():

    global http_session


    print(
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
    )


    http_session = aiohttp.ClientSession()


    await start_http()


    # ÖNEMLİ:
    # client ataması tek satırda.

    client = TelegramClient(
        StringSession(
            STRING_SESSION
        ),
        API_ID,
        API_HASH
    )


    await client.start()


    print(
        "[TELEGRAM] İstemci bağlandı."
    )


    client.add_event_handler(
        listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        )
    )


    asyncio.create_task(
        sender()
    )


    print(
        "[HAZIR] Goody Bag + Hazine Sandığı aktif."
    )


    print(
        "[HAZIR] Son Goody + Son Chest üstte."
    )


    print(
        "[HAZIR] Yeni gelen HER kayıt ⚡ YENİ."
    )


    try:

        await client.run_until_disconnected()

    finally:

        if http_session:

            await http_session.close()


# =========================================================
# BAŞLAT
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Kapatıldı."
        )

    except Exception as error:

        print(
            "[KRİTİK HATA]",
            repr(error)
        )
