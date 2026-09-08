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
# RADAR HAFIZASI
# =========================================================

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}

processed_messages = set()

telegram_queue = asyncio.Queue()

http_session = None


# =========================================================
# YARDIMCI
# =========================================================

def safe_int(value, default=0):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return int(value)

        return int(float(value))

    except Exception:
        return default


def safe_float(value, default=0):
    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


# =========================================================
# TOKEN BUL
# =========================================================

def extract_token_from_event(event):

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

        m = re.search(
            pattern,
            text,
            re.I
        )

        if m:

            token = unquote(
                m.group(1)
            )

            print(
                "[TOKEN] raw text üzerinden bulundu"
            )

            return token

    try:

        entities = message.entities or []

        for entity in entities:

            url = getattr(
                entity,
                "url",
                None
            )

            if not url:
                continue

            m = re.search(
                r't\.php\?token=([^&\s]+)',
                url,
                re.I
            )

            if m:

                token = unquote(
                    m.group(1)
                )

                print(
                    "[TOKEN] Telegram entity üzerinden bulundu"
                )

                return token

    except Exception as e:

        print(
            "[TOKEN ENTITY HATASI]",
            repr(e)
        )

    try:

        for entity, entity_text in message.get_entities_text():

            url = getattr(
                entity,
                "url",
                None
            )

            if not url:
                continue

            m = re.search(
                r't\.php\?token=([^&\s]+)',
                url,
                re.I
            )

            if m:

                token = unquote(
                    m.group(1)
                )

                print(
                    "[TOKEN] entity text üzerinden bulundu"
                )

                return token

    except Exception as e:

        print(
            "[TOKEN ENTITY TEXT HATASI]",
            repr(e)
        )

    print(
        "[TOKEN] bulunamadı"
    )

    return None


# =========================================================
# TOKEN ÇÖZ
# =========================================================

def decode_token(token):

    if not token:
        return None

    try:

        token = unquote(
            str(token)
        ).strip()

        padding = "=" * (
            -len(token) % 4
        )

        decoded = base64.urlsafe_b64decode(
            token + padding
        )

        text = decoded.decode(
            "utf-8",
            errors="ignore"
        ).strip()

        data = json.loads(text)

        if isinstance(data, dict):

            print(
                "[TOKEN] çözüldü"
            )

            return data

    except Exception as e:

        print(
            "[TOKEN HATASI]",
            repr(e)
        )

    return None


# =========================================================
# P= BASE64 ODA ID
# =========================================================

def extract_p_room(text):

    if not text:
        return None

    patterns = [
        r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)',
        r'https?://[^ \n]+/t/\?p=([A-Za-z0-9_\-+/=]+)',
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text,
            re.I
        )

        if not m:
            continue

        encoded = m.group(1)

        try:

            padding = "=" * (
                -len(encoded) % 4
            )

            decoded = base64.urlsafe_b64decode(
                encoded + padding
            )

            room = decoded.decode(
                "utf-8",
                errors="ignore"
            ).strip()

            if room.isdigit():

                print(
                    "[ODA ID] bulundu:",
                    room
                )

                return room

        except Exception as e:

            print(
                "[ODA ID HATASI]",
                repr(e)
            )

    return None


# =========================================================
# USERNAME
# =========================================================

def extract_username_from_text(text):

    if not text:
        return None

    patterns = [
        r'^\s*##\s*T\d+\s*[›>:]\s*([^\s\n]+)',
        r'^\s*T\d+\s*[›>:]\s*([^\s\n]+)',
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text,
            re.M
        )

        if m:
            return m.group(1).strip()

    return None


# =========================================================
# COIN
# =========================================================

def extract_coins(text, token_data=None):

    if text:

        m = re.search(
            r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
            text,
            re.I
        )

        if m:
            return safe_int(m.group(1))

        m = re.search(
            r'BOX\s*:\s*(\d+)\s*/',
            text,
            re.I
        )

        if m:
            return safe_int(m.group(1))

        m = re.search(
            r'(\d+)\s*/\s*(\d+)',
            text
        )

        if m:
            return safe_int(m.group(1))

    if token_data:

        for key in [
            "coins",
            "coin",
            "gem",
            "diamond",
            "amount",
        ]:

            value = token_data.get(key)

            if value is not None:

                number = safe_int(
                    value,
                    -1
                )

                if number >= 0:
                    return number

    return 0


# =========================================================
# KİŞİ
# =========================================================

def extract_people_from_message(text):

    if not text:
        return 0

    m = re.search(
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        text,
        re.I
    )

    if m:
        return safe_int(m.group(1))

    m = re.search(
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        text,
        re.I
    )

    if m:
        return safe_int(m.group(1))

    m = re.search(
        r'(\d+)\s*/\s*(\d+)',
        text
    )

    if m:
        return safe_int(m.group(2))

    return 0


# =========================================================
# KATILAN
# =========================================================

def extract_joined(text):

    if not text:
        return 0

    patterns = [
        r'Đã\s*join\s*:\s*(\d+)',
        r'joined\s*:\s*(\d+)',
        r'join\s*:\s*(\d+)',
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text,
            re.I
        )

        if m:
            return safe_int(m.group(1))

    return 0


# =========================================================
# İZLENME
# =========================================================

def extract_viewers(text):

    if not text:
        return 0

    m = re.search(
        r'👀\s*(\d+)',
        text
    )

    if m:
        return safe_int(m.group(1))

    return 0


# =========================================================
# ORAN
# =========================================================

def extract_rate(text, token_data=None):

    if text:

        m = re.search(
            r'Rate\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            text,
            re.I
        )

        if m:
            return safe_float(m.group(1))

    if token_data:

        for key in [
            "ratio",
            "rate",
        ]:

            if key in token_data:

                return safe_float(
                    token_data.get(
                        key,
                        0
                    )
                )

    return 0


# =========================================================
# TÜR TESPİTİ
# =========================================================

def detect_type(text, token_data):

    text_upper = (
        text or ""
    ).upper()

    if re.search(
        r'TÚI|TUI',
        text_upper
    ):

        print(
            "[TÜR] TÚI -> GOODY BAG"
        )

        return True

    if re.search(
        r'GOODY\s*BAG|REWARD\s*BAG',
        text_upper
    ):

        print(
            "[TÜR] GOODY BAG -> GOODY BAG"
        )

        return True

    if re.search(
        r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
        text_upper
    ):

        print(
            "[TÜR] BOX/RƯƠNG/TREO -> CHEST"
        )

        return False

    if "🟡" in text:

        print(
            "[TÜR] 🟡 -> CHEST"
        )

        return False

    if token_data:

        value = token_data.get(
            "is_goody_bag"
        )

        if value in [
            True,
            1,
            "1",
            "true",
            "True",
        ]:

            print(
                "[TÜR] TOKEN -> GOODY BAG"
            )

            return True

        if value in [
            False,
            0,
            "0",
            "false",
            "False",
        ]:

            print(
                "[TÜR] TOKEN -> CHEST"
            )

            return False

    print(
        "[TÜR] belirlenemedi"
    )

    return None


# =========================================================
# HEDEF ZAMAN
# =========================================================

def calculate_target_time(text, token_data=None):

    now = int(
        time.time()
    )

    if token_data:

        for key in [
            "time",
            "target_time",
            "end_time",
            "endTime",
        ]:

            value = token_data.get(key)

            if value is None:
                continue

            try:

                value = int(
                    float(value)
                )

                if value > 10_000_000_000:
                    return int(value / 1000)

                if value > 1_000_000_000:
                    return value

                if 0 < value < 86_400:
                    return now + value

            except Exception:
                pass

    if text:

        m = re.search(
            r'TIME\s*:\s*(\d+):(\d+)',
            text,
            re.I
        )

        if m:

            minutes = safe_int(
                m.group(1)
            )

            seconds = safe_int(
                m.group(2)
            )

            duration = (
                minutes * 60
                + seconds
            )

            if duration > 0:
                return now + duration

    return now + 180


# =========================================================
# TIKTOK LINK
# =========================================================

def get_live_link(
    username,
    room,
    token_data=None
):

    if token_data:

        for key in [
            "openitok",
            "live",
            "live_url",
            "url",
        ]:

            value = token_data.get(key)

            if value:

                value = str(value)

                if (
                    value.startswith("http://")
                    or
                    value.startswith("https://")
                ):

                    return value

    if room:

        return (
            "https://www.tiktok.com/"
            f"share/live/{room}"
        )

    if username:

        return (
            "https://www.tiktok.com/"
            f"@{username}/live"
        )

    return ""


# =========================================================
# MESAJ PARSE
# =========================================================

def parse_source_message(event):

    text = (
        event.message.raw_text
        or ""
    )

    print(
        "\n"
        + "=" * 70
    )

    print(
        "[YENİ KAYNAK MESAJI]"
    )

    print(text)

    print(
        "=" * 70
    )

    token = extract_token_from_event(
        event
    )

    token_data = decode_token(
        token
    )

    is_goody = detect_type(
        text,
        token_data
    )

    if is_goody is None:

        print(
            "[ATLANDI] Tür belirlenemedi"
        )

        return None

    username = None

    if token_data:

        for key in [
            "username",
            "user",
            "unique_id",
            "uniqueId",
        ]:

            value = token_data.get(key)

            if value:

                username = str(value)

                break

    if not username:

        username = extract_username_from_text(
            text
        )

    if not username:
        username = "bilinmiyor"

    room = None

    if token_data:

        for key in [
            "room",
            "room_id",
            "roomid",
            "roomId",
            "roomID",
        ]:

            value = token_data.get(key)

            if value:

                room = str(value)

                break

    if not room:

        room = extract_p_room(
            text
        )

    if not room:

        room = (
            "msg:"
            +
            str(event.message.id)
        )

        print(
            "[ROOM YOK] Mesaj ID kullanılıyor:",
            room
        )

    coins = extract_coins(
        text,
        token_data
    )

    people = extract_people_from_message(
        text
    )

    if not people and token_data:

        for key in [
            "people",
            "person",
            "count",
            "capacity",
        ]:

            if key in token_data:

                people = safe_int(
                    token_data.get(
                        key,
                        0
                    )
                )

                if people:
                    break

    joined = extract_joined(
        text
    )

    if not joined and token_data:

        for key in [
            "joined",
            "join",
            "join_count",
            "joined_count",
        ]:

            if key in token_data:

                joined = safe_int(
                    token_data.get(
                        key,
                        0
                    )
                )

                if joined:
                    break

    rate = extract_rate(
        text,
        token_data
    )

    viewers = extract_viewers(
        text
    )

    if not viewers and token_data:

        for key in [
            "view",
            "views",
            "viewer",
            "viewers",
        ]:

            if key in token_data:

                viewers = safe_int(
                    token_data.get(
                        key,
                        0
                    )
                )

                if viewers:
                    break

    target_time = calculate_target_time(
        text,
        token_data
    )

    detected_at = int(
        time.time()
    )

    live_link = get_live_link(
        username,
        room,
        token_data
    )

    result = {

        "type":
            (
                "GOODY BAG"
                if is_goody
                else "CHEST"
            ),

        "box_name":
            (
                "Goody Bag"
                if is_goody
                else "Hazine Sandığı"
            ),

        "username":
            username,

        "coins":
            coins,

        "people":
            people,

        "joined":
            joined,

        "rate":
            rate,

        "view":
            viewers,

        "room":
            room,

        "live":
            live_link,

        "target_time":
            target_time,

        "detected_at":
            detected_at,

        "source_message_id":
            event.message.id,

    }

    print(
        "\n[PARSE SONUCU]"
    )

    print(
        "TÜR:",
        result["type"]
    )

    print(
        "KULLANICI:",
        result["username"]
    )

    print(
        "COIN:",
        result["coins"]
    )

    print(
        "KİŞİ:",
        result["people"]
    )

    print(
        "KATILAN:",
        result["joined"]
    )

    print(
        "ORAN:",
        result["rate"]
    )

    print(
        "İZLENME:",
        result["view"]
    )

    print(
        "ODA:",
        result["room"]
    )

    print(
        "HEDEF:",
        result["target_time"]
    )

    print(
        "ALGILANMA:",
        result["detected_at"]
    )

    return result


# =========================================================
# RADARA EKLE
#
# SÜREYE GÖRE SİLME YOK
# =========================================================

def add_to_radar(data):

    room = data.get(
        "room"
    )

    if not room:

        print(
            "[ATLANDI] Room yok"
        )

        return False

    if data["type"] == "GOODY BAG":

        target = LIVE_GOODY_BAGS

    else:

        target = LIVE_CHESTS

    if room in target:

        old = target[room]

        old_detected = safe_int(
            old.get(
                "detected_at",
                0
            )
        )

        now = int(
            time.time()
        )

        if (
            now
            -
            old_detected
            <
            5
        ):

            print(
                "[TEKRAR] Aynı kayıt:",
                room
            )

            return False

        print(
            "[GÜNCELLEME] Aynı oda yeniden geldi:",
            room
        )

    target[room] = data

    print(
        "\n"
        + "=" * 70
    )

    print(
        f"[RADAR] {data['type']} EKLENDİ"
    )

    print(
        "KULLANICI:",
        data["username"]
    )

    print(
        "COIN:",
        data["coins"]
    )

    print(
        "KİŞİ:",
        data["people"]
    )

    print(
        "KATILAN:",
        data["joined"]
    )

    print(
        "ORAN:",
        data["rate"]
    )

    print(
        "İZLENME:",
        data["view"]
    )

    print(
        "ODA:",
        data["room"]
    )

    print(
        "=" * 70
    )

    return True


# =========================================================
# TELEGRAM
# =========================================================

async def send_telegram_message(data):

    global http_session

    if not http_session:
        return False

    if data["type"] == "GOODY BAG":

        baslik = "🟪 GOODY BAG"

    else:

        baslik = "🟨 HAZİNE SANDIĞI"

    text = (

        f"{baslik}\n\n"

        f"👤 Kullanıcı: "
        f"{data['username']}\n"

        f"🪙 Coin: "
        f"{data['coins']}\n"

        f"👥 Kişi: "
        f"{data['people']}\n"

        f"🙋 Katılan: "
        f"{data['joined']}\n"

        f"📈 Oran: "
        f"{data['rate']}\n"

        f"👀 İzlenme: "
        f"{data['view']}\n"

    )

    if data.get("live"):

        text += (

            "\n🔴 "

            f"<a href=\"{data['live']}\">"

            "TIKTOK CANLI YAYIN"

            "</a>"

        )

    url = (

        "https://api.telegram.org/bot"

        f"{BOT_TOKEN}/sendMessage"

    )

    payload = {

        "chat_id":
            TARGET_CHAT_ID,

        "text":
            text,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True,

    }

    max_attempts = 8

    for attempt in range(
        1,
        max_attempts + 1
    ):

        try:

            async with http_session.post(
                url,
                json=payload
            ) as response:

                result_text = await response.text()

                if response.status == 200:

                    print(
                        "[TELEGRAM] Bildirim gönderildi:",
                        data["type"],
                        data["username"]
                    )

                    return True

                if response.status == 429:

                    try:

                        result_json = json.loads(
                            result_text
                        )

                        retry_after = (
                            result_json
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

                        retry_after = 30

                    retry_after = max(
                        1,
                        safe_int(
                            retry_after,
                            30
                        )
                    )

                    print(
                        "[TELEGRAM]",
                        retry_after,
                        "saniye bekleniyor..."
                    )

                    await asyncio.sleep(
                        retry_after
                    )

                    continue

                if response.status in [
                    500,
                    502,
                    503,
                    504,
                ]:

                    wait = min(
                        5 * attempt,
                        30
                    )

                    print(
                        "[TELEGRAM] Sunucu hatası:",
                        wait,
                        "s"
                    )

                    await asyncio.sleep(
                        wait
                    )

                    continue

                print(
                    "[TELEGRAM HATA]",
                    response.status,
                    result_text
                )

                return False

        except Exception as e:

            print(
                "[TELEGRAM GÖNDERME HATASI]",
                repr(e)
            )

            await asyncio.sleep(
                min(
                    5 * attempt,
                    30
                )
            )

    return False


# =========================================================
# TELEGRAM KUYRUK
# =========================================================

async def telegram_sender():

    while True:

        data = await telegram_queue.get()

        try:

            await send_telegram_message(
                data
            )

        except Exception as e:

            print(
                "[KUYRUK HATASI]",
                repr(e)
            )

        finally:

            telegram_queue.task_done()


# =========================================================
# NEON JİMİN RADAR HTML
# =========================================================

RADAR_HTML = r"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>⚡ JİMİN • ÖDÜL AVCISI</title>

<style>

* {
    box-sizing: border-box;
}

html,
body {

    margin: 0;

    padding: 0;

    min-height: 100%;

    background:
        #05060c;

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

body {

    padding: 12px;

    overflow-x: hidden;

    background:
        radial-gradient(
            circle at 50% -10%,
            rgba(106, 52, 255, .32),
            transparent 34%
        ),
        radial-gradient(
            circle at 0% 50%,
            rgba(0, 174, 255, .08),
            transparent 30%
        ),
        radial-gradient(
            circle at 100% 50%,
            rgba(180, 0, 255, .10),
            transparent 30%
        ),
        #05060c;
}

.wrapper {

    max-width: 1200px;

    margin: 0 auto;
}


/* =====================================================
   HEADER
   ===================================================== */

.header {

    text-align: center;

    padding:
        12px 5px 18px;

    position: relative;
}


/* =====================================================
   NEON JİMİN
   ===================================================== */

.jimin-wrap {

    position: relative;

    display: inline-block;

    padding:
        10px 25px 8px;

    margin-bottom: 4px;
}

.jimin {

    position: relative;

    font-size:
        clamp(42px, 11vw, 82px);

    font-weight: 1000;

    letter-spacing:
        clamp(2px, 1vw, 9px);

    line-height: 1;

    color: #fff;

    text-transform: uppercase;

    text-shadow:

        0 0 3px #fff,

        0 0 8px #d9c5ff,

        0 0 18px #9d5cff,

        0 0 32px #713cff,

        0 0 55px #5c2bff,

        0 0 85px #3e16ff;

    animation:
        neonPulse 2.2s ease-in-out infinite;
}

@keyframes neonPulse {

    0%,
    100% {

        filter:
            brightness(1);

        text-shadow:

            0 0 3px #fff,

            0 0 8px #d9c5ff,

            0 0 18px #9d5cff,

            0 0 32px #713cff,

            0 0 55px #5c2bff;

    }

    50% {

        filter:
            brightness(1.28);

        text-shadow:

            0 0 4px #fff,

            0 0 12px #fff,

            0 0 24px #c084ff,

            0 0 45px #8b45ff,

            0 0 75px #662cff,

            0 0 105px #3d12ff;

    }
}


/* =====================================================
   YILDIRIMLAR
   ===================================================== */

.bolt {

    position: absolute;

    color: #fff;

    font-size:
        clamp(25px, 6vw, 48px);

    filter:

        drop-shadow(
            0 0 5px #fff
        )

        drop-shadow(
            0 0 15px #6f4cff
        )

        drop-shadow(
            0 0 30px #7c35ff
        );

    animation:
        boltFlash 1.8s infinite;
}

.bolt.left {

    left:
        -8px;

    top:
        0;

    transform:
        rotate(-12deg);
}

.bolt.right {

    right:
        -8px;

    bottom:
        0;

    transform:
        rotate(12deg);

    animation-delay:
        .7s;
}

@keyframes boltFlash {

    0%,
    100% {

        opacity:
            .65;

        transform:
            scale(1);
    }

    8% {

        opacity:
            1;

        filter:

            drop-shadow(
                0 0 7px #fff
            )

            drop-shadow(
                0 0 25px #a66cff
            )

            drop-shadow(
                0 0 45px #703cff
            );
    }

    14% {

        opacity:
            .4;
    }

    20% {

        opacity:
            1;
    }

    40% {

        opacity:
            .7;
    }
}


/* =====================================================
   ALT BAŞLIK
   ===================================================== */

.subtitle {

    color:
        #b8b1cf;

    font-size:
        12px;

    font-weight:
        800;

    letter-spacing:
        1.2px;

    margin-top:
        8px;

    text-shadow:
        0 0 10px
        rgba(150,100,255,.5);
}

.status {

    display:
        inline-flex;

    align-items:
        center;

    justify-content:
        center;

    margin-top:
        11px;

    padding:
        7px 14px;

    border-radius:
        999px;

    background:
        rgba(20,20,40,.8);

    border:
        1px solid
        rgba(157,92,255,.4);

    color:
        #74ff9a;

    font-size:
        11px;

    font-weight:
        900;

    box-shadow:
        0 0 18px
        rgba(111,76,255,.18);
}

.status.error {

    color:
        #ff7474;

    border-color:
        rgba(255,80,80,.45);
}


/* =====================================================
   RADAR GRID
   ===================================================== */

.radar-grid {

    display:
        grid;

    grid-template-columns:
        1fr 1fr;

    gap:
        14px;

    align-items:
        start;
}


/* =====================================================
   PANELS
   ===================================================== */

.panel {

    min-width:
        0;

    background:
        rgba(9,12,21,.88);

    border:
        1px solid
        #273146;

    border-radius:
        18px;

    padding:
        12px;

    box-shadow:
        0 12px 38px
        rgba(0,0,0,.42);

    backdrop-filter:
        blur(10px);
}

.panel.goody {

    border-color:
        rgba(157,81,255,.65);

    box-shadow:
        0 0 25px
        rgba(157,81,255,.10);
}

.panel.chest {

    border-color:
        rgba(241,200,75,.55);

    box-shadow:
        0 0 25px
        rgba(241,200,75,.07);
}

.panel-title {

    display:
        flex;

    align-items:
        center;

    justify-content:
        space-between;

    gap:
        8px;

    padding:
        3px 2px 12px;
}

.panel-name {

    font-size:
        17px;

    font-weight:
        950;

    white-space:
        nowrap;

    overflow:
        hidden;

    text-overflow:
        ellipsis;
}

.goody .panel-name {

    color:
        #d5a8ff;

    text-shadow:
        0 0 13px
        rgba(157,81,255,.65);
}

.chest .panel-name {

    color:
        #ffe37b;

    text-shadow:
        0 0 13px
        rgba(241,200,75,.55);
}

.panel-count {

    flex-shrink:
        0;

    min-width:
        28px;

    padding:
        4px 8px;

    border-radius:
        999px;

    background:
        rgba(255,255,255,.07);

    color:
        #fff;

    text-align:
        center;

    font-size:
        10px;

    font-weight:
        900;
}


/* =====================================================
   CARD
   ===================================================== */

.card {

    position:
        relative;

    overflow:
        hidden;

    background:
        linear-gradient(
            145deg,
            rgba(23,29,45,.98),
            rgba(10,14,23,.98)
        );

    border:
        1px solid
        #293448;

    border-radius:
        13px;

    padding:
        11px;

    margin-bottom:
        9px;

    transition:
        transform .2s,
        box-shadow .2s,
        border-color .2s;
}

.card:hover {

    transform:
        translateY(-2px);

    border-color:
        #53627c;
}

.goody .card {

    border-left:
        3px solid
        #9d51ff;
}

.chest .card {

    border-left:
        3px solid
        #f1c84b;
}


/* =====================================================
   YENİ KART
   ===================================================== */

.new-card {

    animation:
        cardIn .65s ease-out;
}

@keyframes cardIn {

    0% {

        opacity:
            0;

        transform:
            translateY(-8px)
            scale(.97);
    }

    100% {

        opacity:
            1;

        transform:
            translateY(0)
            scale(1);
    }
}

.new-card::after {

    content:
        "";

    position:
        absolute;

    top:
        0;

    left:
        -100%;

    width:
        60%;

    height:
        100%;

    background:
        linear-gradient(
            90deg,
            transparent,
            rgba(255,255,255,.08),
            transparent
        );

    animation:
        shine 1.5s ease-out;
}

@keyframes shine {

    from {
        left:
            -100%;
    }

    to {
        left:
            150%;
    }
}


/* =====================================================
   KULLANICI
   ===================================================== */

.user-row {

    display:
        flex;

    align-items:
        center;

    justify-content:
        space-between;

    gap:
        6px;

    margin-bottom:
        9px;
}

.user {

    font-size:
        15px;

    font-weight:
        950;

    word-break:
        break-word;
}

.badge {

    flex-shrink:
        0;

    font-size:
        8px;

    font-weight:
        950;

    padding:
        4px 6px;

    border-radius:
        6px;

    background:
        rgba(255,255,255,.07);

    color:
        #aeb9cc;
}

.goody .badge {

    color:
        #d2a7ff;

    background:
        rgba(157,81,255,.13);
}

.chest .badge {

    color:
        #ffe17a;

    background:
        rgba(241,200,75,.10);
}


/* =====================================================
   BİLGİLER
   ===================================================== */

.info-grid {

    display:
        grid;

    grid-template-columns:
        1fr 1fr;

    gap:
        5px;
}

.info {

    background:
        rgba(255,255,255,.035);

    border-radius:
        7px;

    padding:
        6px;

    font-size:
        9px;

    color:
        #818da1;
}

.info b {

    display:
        block;

    color:
        #fff;

    font-size:
        12px;

    margin-top:
        2px;
}


/* =====================================================
   CANLI BUTON
   ===================================================== */

.live-button {

    display:
        block;

    text-align:
        center;

    text-decoration:
        none;

    color:
        white;

    background:
        linear-gradient(
            135deg,
            #f12657,
            #a90d38
        );

    padding:
        9px;

    margin-top:
        9px;

    border-radius:
        8px;

    font-size:
        10px;

    font-weight:
        950;

    box-shadow:
        0 5px 18px
        rgba(225,25,75,.2);

    transition:
        transform .15s,
        filter .15s;
}

.live-button:hover {

    filter:
        brightness(1.15);

    transform:
        translateY(-1px);
}


/* =====================================================
   BOŞ
   ===================================================== */

.empty {

    text-align:
        center;

    padding:
        25px 8px;

    border-radius:
        11px;

    background:
        rgba(255,255,255,.025);

    color:
        #626e82;

    font-size:
        11px;
}


/* =====================================================
   FOOTER
   ===================================================== */

.footer {

    text-align:
        center;

    color:
        #59647a;

    font-size:
        9px;

    padding:
        16px 0 5px;

    letter-spacing:
        .4px;
}


/* =====================================================
   MOBİL
   ===================================================== */

@media (max-width: 700px) {

    body {
        padding: 7px;
    }

    .header {
        padding-top: 7px;
    }

    .jimin-wrap {
        padding-left: 21px;
        padding-right: 21px;
    }

    .subtitle {
        font-size: 9px;
        letter-spacing: .7px;
    }

    .status {
        font-size: 9px;
        padding: 6px 10px;
    }

    .radar-grid {

        grid-template-columns:
            1fr 1fr;

        gap:
            6px;
    }

    .panel {

        padding:
            7px;

        border-radius:
            13px;
    }

    .panel-title {

        padding-bottom:
            8px;
    }

    .panel-name {

        font-size:
            11px;
    }

    .panel-count {

        min-width:
            23px;

        padding:
            3px 5px;

        font-size:
            9px;
    }

    .card {

        padding:
            8px;

        border-radius:
            9px;

        margin-bottom:
            6px;
    }

    .user {

        font-size:
            11px;
    }

    .badge {

        display:
            none;
    }

    .info-grid {

        gap:
            3px;
    }

    .info {

        padding:
            4px;

        font-size:
            7px;
    }

    .info b {

        font-size:
            10px;
    }

    .live-button {

        padding:
            7px 3px;

        font-size:
            8px;

        margin-top:
            6px;
    }

    .empty {

        padding:
            20px 4px;

        font-size:
            9px;
    }

}

</style>

</head>


<body>

<div class="wrapper">


    <!-- =================================================
         HEADER
         ================================================= -->

    <div class="header">

        <div class="jimin-wrap">

            <div class="bolt left">
                ⚡
            </div>

            <div class="jimin">
                JİMİN
            </div>

            <div class="bolt right">
                ⚡
            </div>

        </div>


        <div class="subtitle">

            🏆 ÖDÜL AVCISI
            •
            🟪 GOODY BAG
            •
            🟨 HAZİNE SANDIĞI

        </div>


        <div
            id="status"
            class="status"
        >

            🟡 RADAR BAĞLANIYOR...

        </div>

    </div>


    <!-- =================================================
         RADAR
         ================================================= -->

    <div class="radar-grid">


        <!-- GOODY -->

        <div class="panel goody">

            <div class="panel-title">

                <div class="panel-name">

                    🟪 GOODY BAG

                </div>

                <div
                    id="bagCounter"
                    class="panel-count"
                >
                    0
                </div>

            </div>


            <div id="bags"></div>

        </div>


        <!-- CHEST -->

        <div class="panel chest">

            <div class="panel-title">

                <div class="panel-name">

                    🟨 HAZİNE SANDIĞI

                </div>

                <div
                    id="chestCounter"
                    class="panel-count"
                >
                    0
                </div>

            </div>


            <div id="chests"></div>

        </div>


    </div>


    <div class="footer">

        ⚡ JİMİN • ÖDÜL AVCISI • CANLI RADAR ⚡

    </div>


</div>


<script>

let radarData = {

    chests: [],

    goody_bags: []

};


function escapeHtml(value) {

    return String(
        value ?? ""
    )

    .replace(
        /&/g,
        "&amp;"
    )

    .replace(
        /</g,
        "&lt;"
    )

    .replace(
        />/g,
        "&gt;"
    )

    .replace(
        /"/g,
        "&quot;"
    )

    .replace(
        /'/g,
        "&#039;"
    );

}


function timestamp(item) {

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


function latestFive(items) {

    if (!Array.isArray(items)) {

        return [];

    }


    return [...items]

        .sort(

            (a, b) =>

                timestamp(b)
                -
                timestamp(a)

        )

        .slice(
            0,
            5
        );

}


function renderItems(

    items,
    elementId,
    counterId,
    icon

) {

    const container =
        document.getElementById(
            elementId
        );


    const counter =
        document.getElementById(
            counterId
        );


    const latest =
        latestFive(items);


    counter.textContent =
        latest.length;


    if (!latest.length) {

        container.innerHTML =

            '<div class="empty">' +

            '⚡ Henüz veri yok.' +

            '</div>';

        return;

    }


    container.innerHTML =

        latest.map(

            (item, index) => {

                const live =
                    item.live || "";


                const badge =

                    index === 0

                    ?

                    "⚡ YENİ"

                    :

                    "#" + (
                        index + 1
                    );


                return `

                <div
                    class="card ${
                        index === 0
                            ? "new-card"
                            : ""
                    }"
                >

                    <div class="user-row">

                        <div class="user">

                            ${icon}

                            ${escapeHtml(
                                item.username
                            )}

                        </div>


                        <div class="badge">

                            ${badge}

                        </div>

                    </div>


                    <div class="info-grid">


                        <div class="info">

                            🪙 COIN

                            <b>
                                ${escapeHtml(
                                    item.coins
                                )}
                            </b>

                        </div>


                        <div class="info">

                            👥 KİŞİ

                            <b>
                                ${escapeHtml(
                                    item.people
                                )}
                            </b>

                        </div>


                        <div class="info">

                            🙋 KATILAN

                            <b>
                                ${escapeHtml(
                                    item.joined
                                )}
                            </b>

                        </div>


                        <div class="info">

                            📈 ORAN

                            <b>
                                ${escapeHtml(
                                    item.rate
                                )}
                            </b>

                        </div>


                        <div class="info">

                            👀 İZLENME

                            <b>
                                ${escapeHtml(
                                    item.view
                                )}
                            </b>

                        </div>


                        <div class="info">

                            🏠 ODA

                            <b>
                                ${escapeHtml(
                                    item.room
                                )}
                            </b>

                        </div>


                    </div>


                    ${
                        live

                        ?

                        `

                        <a

                            class="live-button"

                            href="${escapeHtml(
                                live
                            )}"

                            target="_blank"

                            rel="noopener"

                        >

                            🔴 CANLI YAYINA GİT

                        </a>

                        `

                        :

                        ""

                    }

                </div>

                `;

            }

        ).join("");

}


function renderRadar() {

    const bags =
        latestFive(
            radarData.goody_bags
        );


    const chests =
        latestFive(
            radarData.chests
        );


    renderItems(

        bags,

        "bags",

        "bagCounter",

        "🟪"

    );


    renderItems(

        chests,

        "chests",

        "chestCounter",

        "🟨"

    );

}


async function loadRadar() {

    try {

        const response =

            await fetch(

                "/api/all?t="
                +
                Date.now(),

                {
                    cache:
                        "no-store"
                }

            );


        if (!response.ok) {

            throw new Error(

                "HTTP "
                +
                response.status

            );

        }


        const data =
            await response.json();


        radarData = {

            chests:

                Array.isArray(
                    data.chests
                )

                ?

                data.chests

                :

                [],


            goody_bags:

                Array.isArray(
                    data.goody_bags
                )

                ?

                data.goody_bags

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


        renderRadar();


    }

    catch (error) {

        console.error(
            "Radar hatası:",
            error
        );


        const status =

            document.getElementById(
                "status"
            );


        status.className =
            "status error";


        status.textContent =
            "🔴 VERİ BAĞLANTISI HATASI";

    }

}


setInterval(

    loadRadar,

    2000

);


loadRadar();

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
async def cors_middleware(
    request,
    handler
):

    if request.method == "OPTIONS":

        return web.Response(

            status=204,

            headers={

                "Access-Control-Allow-Origin":
                    "*",

                "Access-Control-Allow-Methods":
                    "GET, OPTIONS",

                "Access-Control-Allow-Headers":
                    "*",

                "Access-Control-Max-Age":
                    "86400",

            }

        )


    response = await handler(
        request
    )


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
# API BOXES
# =========================================================

async def api_boxes(request):

    return web.json_response(

        list(
            LIVE_CHESTS.values()
        )

    )


# =========================================================
# API GOODY
# =========================================================

async def api_goody_bags(request):

    return web.json_response(

        list(
            LIVE_GOODY_BAGS.values()
        )

    )


# =========================================================
# STATUS
# =========================================================

async def api_status(request):

    return web.json_response({

        "status":
            "online",

        "chests":
            len(
                LIVE_CHESTS
            ),

        "goody_bags":
            len(
                LIVE_GOODY_BAGS
            ),

        "server_time":
            int(
                time.time()
            ),

    })


# =========================================================
# ALL
# =========================================================

async def api_all(request):

    return web.json_response({

        "status":
            "online",

        "server_time":
            int(
                time.time()
            ),

        "chests":
            list(
                LIVE_CHESTS.values()
            ),

        "goody_bags":
            list(
                LIVE_GOODY_BAGS.values()
            ),

    })


# =========================================================
# HTTP SUNUCU
# =========================================================

async def start_http_server():

    app = web.Application(

        middlewares=[
            cors_middleware
        ]

    )


    app.router.add_get(
        "/radar",
        radar_page
    )


    app.router.add_get(
        "/api/boxes",
        api_boxes
    )


    app.router.add_get(
        "/api/goody_bags",
        api_goody_bags
    )


    app.router.add_get(
        "/api/status",
        api_status
    )


    app.router.add_get(
        "/api/all",
        api_all
    )


    app.router.add_options(
        "/api/boxes",
        lambda request:
            web.Response(status=204)
    )


    app.router.add_options(
        "/api/goody_bags",
        lambda request:
            web.Response(status=204)
    )


    app.router.add_options(
        "/api/status",
        lambda request:
            web.Response(status=204)
    )


    app.router.add_options(
        "/api/all",
        lambda request:
            web.Response(status=204)
    )


    app.router.add_get(
        "/",
        radar_page
    )


    runner = web.AppRunner(
        app
    )


    await runner.setup()


    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )


    await site.start()


    print(
        f"[HTTP] Sunucu başladı: {PORT}"
    )

    print(
        "[HTTP] Ana sayfa: /"
    )

    print(
        "[HTTP] Radar: /radar"
    )

    print(
        "[HTTP] API: /api/all"
    )


# =========================================================
# TELEGRAM MESAJ DİNLEYİCİ
# =========================================================

async def message_listener(event):

    try:

        key = (
            event.chat_id,
            event.message.id
        )


        if key in processed_messages:
            return


        processed_messages.add(
            key
        )


        if len(
            processed_messages
        ) > 50000:

            processed_messages.clear()


        data = parse_source_message(
            event
        )


        if not data:
            return


        added = add_to_radar(
            data
        )


        if added:

            await telegram_queue.put(
                data
            )


    except Exception as e:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(e)
        )


# =========================================================
# ANA
# =========================================================

async def main():

    global http_session


    print(
        "=" * 70
    )

    print(
        "⚡ JİMİN • ÖDÜL AVCISI BAŞLIYOR"
    )

    print(
        "=" * 70
    )


    http_session = (
        aiohttp.ClientSession()
    )


    await start_http_server()


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


    print(
        "[TAKİP EDİLEN KANALLAR]"
    )


    for chat in SOURCE_CHATS:

        print(
            " -",
            chat
        )


    client.add_event_handler(

        message_listener,

        events.NewMessage(
            chats=SOURCE_CHATS
        )

    )


    asyncio.create_task(
        telegram_sender()
    )


    print(
        "[HAZIR] Radar çalışıyor."
    )

    print(
        "[HAZIR] Goody Bag algılama aktif."
    )

    print(
        "[HAZIR] Hazine Sandığı algılama aktif."
    )

    print(
        "[HAZIR] Süreye göre kayıt silme KAPALI."
    )

    print(
        "[HAZIR] Her bölümde son 5 kayıt gösteriliyor."
    )

    print(
        "[HAZIR] ⚡ JİMİN NEON AKTİF."
    )


    try:

        await client.run_until_disconnected()


    finally:

        if http_session:

            await http_session.close()


        print(
            "[DURDU] Sistem kapandı."
        )


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


    except Exception as e:

        print(
            "[KRİTİK HATA]",
            repr(e)
        )
