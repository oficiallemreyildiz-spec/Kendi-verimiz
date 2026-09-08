# main.py
# ÖDÜL AVCISI
# GOODY BAG + HAZİNE SANDIĞI
# HER EKRANDA YAN YANA

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
# TOKEN
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
# ODA
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
            return safe_int(
                m.group(1)
            )

        m = re.search(
            r'BOX\s*:\s*(\d+)\s*/',
            text,
            re.I
        )

        if m:
            return safe_int(
                m.group(1)
            )

        m = re.search(
            r'(\d+)\s*/\s*(\d+)',
            text
        )

        if m:
            return safe_int(
                m.group(1)
            )

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
        return safe_int(
            m.group(1)
        )

    m = re.search(
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        text,
        re.I
    )

    if m:
        return safe_int(
            m.group(1)
        )

    m = re.search(
        r'(\d+)\s*/\s*(\d+)',
        text
    )

    if m:
        return safe_int(
            m.group(2)
        )

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
            return safe_int(
                m.group(1)
            )

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
        return safe_int(
            m.group(1)
        )

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
            return safe_float(
                m.group(1)
            )

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
# TÜR
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
# ZAMAN
# =========================================================

def calculate_target_time(
    text,
    token_data=None
):

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
                    return int(
                        value / 1000
                    )

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

            duration = (
                safe_int(m.group(1)) * 60
                + safe_int(m.group(2))
            )

            if duration > 0:
                return now + duration

    return now + 180


# =========================================================
# LIVE LINK
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

                if value.startswith(
                    (
                        "http://",
                        "https://"
                    )
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
# PARSE
# =========================================================

def parse_source_message(event):

    text = (
        event.message.raw_text
        or ""
    )

    print("\n" + "=" * 70)

    print(
        "[YENİ KAYNAK MESAJI]"
    )

    print(text)

    print("=" * 70)

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
            + str(
                event.message.id
            )
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

        "type": (
            "GOODY BAG"
            if is_goody
            else "CHEST"
        ),

        "box_name": (
            "Goody Bag"
            if is_goody
            else "Hazine Sandığı"
        ),

        "username": username,

        "coins": coins,

        "people": people,

        "joined": joined,

        "rate": rate,

        "view": viewers,

        "room": room,

        "live": live_link,

        "target_time": target_time,

        "detected_at": detected_at,

        "source_message_id":
            event.message.id,
    }

    print("\n[PARSE SONUCU]")

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

    return result


# =========================================================
# RADAR
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

        if now - old_detected < 5:

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

    print("\n" + "=" * 70)

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

    print("=" * 70)

    return True


# =========================================================
# TELEGRAM BİLDİRİM
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
            f"{data['live']}"
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

        "disable_web_page_preview":
            True,
    }

    for attempt in range(1, 9):

        try:

            async with http_session.post(
                url,
                json=payload
            ) as response:

                result_text = (
                    await response.text()
                )

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

                    await asyncio.sleep(
                        max(
                            1,
                            safe_int(
                                retry_after,
                                30
                            )
                        )
                    )

                    continue

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
# RADAR HTML
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

<title>🏆 ÖDÜL AVCISI</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    padding: 20px;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    background:
        linear-gradient(
            135deg,
            #080b14,
            #111827
        );

    color: white;
}

.container {

    width: 100%;

    max-width: 1400px;

    margin: auto;
}

.title {

    text-align: center;

    font-size: 34px;

    font-weight: 900;

    margin-bottom: 5px;
}

.subtitle {

    text-align: center;

    color: #cbd5e1;

    font-size: 15px;

    margin-bottom: 12px;
}

.status {

    text-align: center;

    background: #172033;

    border: 1px solid #26334d;

    border-radius: 12px;

    padding: 10px;

    margin-bottom: 20px;

    font-size: 14px;
}


/* =====================================================
   HER ZAMAN YAN YANA
   ===================================================== */

.last-grid {

    display: grid;

    grid-template-columns:
        minmax(0, 1fr)
        minmax(0, 1fr);

    gap: 16px;

    margin-bottom: 20px;
}

.grid {

    display: grid;

    grid-template-columns:
        minmax(0, 1fr)
        minmax(0, 1fr);

    gap: 16px;
}


/* =====================================================
   PANELLER
   ===================================================== */

.last-panel {

    min-width: 0;

    background: #101827;

    border-radius: 18px;

    padding: 16px;

    border: 1px solid #26334d;
}

.panel {

    min-width: 0;

    background: #101827;

    border-radius: 18px;

    padding: 16px;

    border: 1px solid #26334d;
}


/* =====================================================
   BAŞLIKLAR
   ===================================================== */

.last-title {

    font-weight: 900;

    font-size: 18px;

    margin-bottom: 12px;
}

.panel-title {

    display: flex;

    justify-content: space-between;

    align-items: center;

    margin-bottom: 14px;
}

.panel-name {

    font-size: 20px;

    font-weight: 900;
}

.panel-count {

    min-width: 34px;

    text-align: center;

    padding: 5px 9px;

    border-radius: 20px;

    background: #25324a;

    color: #dbeafe;

    font-weight: 800;
}


/* =====================================================
   KART
   ===================================================== */

.last-card {

    min-width: 0;

    background: #182235;

    border-radius: 14px;

    padding: 15px;

    border: 1px solid #30405d;
}

.card {

    min-width: 0;

    background: #182235;

    border-radius: 14px;

    padding: 14px;

    margin-bottom: 10px;

    border: 1px solid #30405d;

    position: relative;

    transition:
        transform .25s ease,
        box-shadow .25s ease;
}

.card:hover {

    transform: translateY(-2px);
}

.card.new {

    animation:
        newCard .7s ease;
}

@keyframes newCard {

    0% {

        transform: scale(.96);

        opacity: .3;
    }

    60% {

        transform: scale(1.02);

        opacity: 1;
    }

    100% {

        transform: scale(1);

    }
}


/* =====================================================
   YENİ
   ===================================================== */

.new-badge {

    position: absolute;

    top: 10px;

    right: 10px;

    background: #facc15;

    color: #111827;

    font-size: 12px;

    font-weight: 900;

    padding: 5px 8px;

    border-radius: 8px;
}


/* =====================================================
   KULLANICI
   ===================================================== */

.user {

    font-size: 17px;

    font-weight: 900;

    padding-right: 65px;

    margin-bottom: 9px;

    overflow: hidden;

    text-overflow: ellipsis;

    white-space: nowrap;
}


/* =====================================================
   BİLGİLER
   ===================================================== */

.row {

    display: grid;

    grid-template-columns:
        minmax(0, 1fr)
        minmax(0, 1fr);

    gap: 6px;

    color: #d1d5db;

    font-size: 14px;

    margin-bottom: 8px;
}

.row div {

    min-width: 0;

    overflow: hidden;

    text-overflow: ellipsis;

    white-space: nowrap;
}


/* =====================================================
   CANLI LİNK
   ===================================================== */

.live-link {

    display: block;

    margin-top: 10px;

    padding-top: 10px;

    border-top:
        1px solid #2d3a53;

    color: #ff5b5b;

    text-decoration: none;

    font-weight: 800;

    font-size: 13px;

    word-break: break-all;
}

.live-link:hover {

    text-decoration: underline;
}


/* =====================================================
   BOŞ
   ===================================================== */

.empty {

    text-align: center;

    color: #64748b;

    padding: 25px 10px;
}


/* =====================================================
   FOOTER
   ===================================================== */

.footer {

    text-align: center;

    color: #64748b;

    font-size: 12px;

    margin-top: 20px;
}


/* =====================================================
   TELEFONDA DA YAN YANA
   ===================================================== */

@media (max-width: 800px) {

    .last-grid {

        grid-template-columns:
            minmax(0, 1fr)
            minmax(0, 1fr);

        gap: 8px;
    }

    .grid {

        grid-template-columns:
            minmax(0, 1fr)
            minmax(0, 1fr);

        gap: 8px;
    }

    body {

        padding: 8px;
    }

    .title {

        font-size: 25px;
    }

    .subtitle {

        font-size: 12px;
    }

    .last-panel,
    .panel {

        padding: 9px;

        border-radius: 12px;
    }

    .last-title {

        font-size: 13px;

        margin-bottom: 8px;
    }

    .panel-name {

        font-size: 14px;
    }

    .panel-count {

        min-width: 25px;

        padding: 3px 6px;

        font-size: 12px;
    }

    .card,
    .last-card {

        padding: 9px;

        border-radius: 10px;
    }

    .user {

        font-size: 13px;

        padding-right: 45px;

        margin-bottom: 7px;
    }

    .row {

        grid-template-columns:
            1fr;

        gap: 3px;

        font-size: 11px;

        margin-bottom: 5px;
    }

    .new-badge {

        top: 6px;

        right: 6px;

        font-size: 9px;

        padding: 3px 5px;
    }

    .live-link {

        font-size: 10px;

        margin-top: 6px;

        padding-top: 6px;
    }

}


/* =====================================================
   ÇOK DAR TELEFONLARDA BİLE YAN YANA
   ===================================================== */

@media (max-width: 380px) {

    .last-grid,
    .grid {

        gap: 5px;
    }

    .last-panel,
    .panel {

        padding: 6px;
    }

    .panel-name {

        font-size: 12px;
    }

    .last-title {

        font-size: 11px;
    }

    .card,
    .last-card {

        padding: 7px;
    }

    .user {

        font-size: 11px;

        padding-right: 35px;
    }

    .row {

        font-size: 9px;
    }

    .live-link {

        font-size: 8px;
    }

}

</style>

</head>


<body>

<div class="container">


    <div class="title">
        🏆 ÖDÜL AVCISI
    </div>


    <div class="subtitle">
        🟪 GOODY BAG • 🟨 HAZİNE SANDIĞI
    </div>


    <div
        id="status"
        class="status"
    >
        🟡 RADAR BAĞLANIYOR...
    </div>


    <!-- =================================================
         SON GOODY / SON CHEST
         ================================================= -->

    <div class="last-grid">


        <div class="last-panel">

            <div class="last-title">
                ⚡ SON GOODY BAG
            </div>

            <div id="lastGoody">

                <div class="empty">
                    Henüz kayıt yok
                </div>

            </div>

        </div>


        <div class="last-panel">

            <div class="last-title">
                ⚡ SON HAZİNE SANDIĞI
            </div>

            <div id="lastChest">

                <div class="empty">
                    Henüz kayıt yok
                </div>

            </div>

        </div>


    </div>


    <!-- =================================================
         ALT LİSTELER
         ================================================= -->

    <div class="grid">


        <div class="panel">

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


        <div class="panel">

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
        ⚡ ÖDÜL AVCISI • CANLI RADAR
    </div>


</div>


<script>

let firstLoad = true;

const newGoody = new Set();

const newChest = new Set();

let knownGoody = new Set();

let knownChest = new Set();


function esc(value) {

    return String(
        value ?? ""
    )
    .replaceAll(
        "&",
        "&amp;"
    )
    .replaceAll(
        "<",
        "&lt;"
    )
    .replaceAll(
        ">",
        "&gt;"
    )
    .replaceAll(
        '"',
        "&quot;"
    )
    .replaceAll(
        "'",
        "&#039;"
    );
}


function formatTime(timestamp) {

    if (!timestamp) {
        return "";
    }

    const date = new Date(
        Number(timestamp) * 1000
    );

    return date.toLocaleTimeString(
        "tr-TR",
        {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        }
    );
}


function card(item, isNew) {

    return `

        <div class="card ${
            isNew ? "new" : ""
        }">


            ${
                isNew
                ?
                `<div class="new-badge">
                    ⚡ YENİ
                </div>`
                :
                ""
            }


            <div class="user">

                👤 ${esc(
                    item.username
                )}

            </div>


            <div class="row">

                <div>
                    🪙 Coin:
                    <b>${esc(
                        item.coins
                    )}</b>
                </div>


                <div>
                    👥 Kişi:
                    <b>${esc(
                        item.people
                    )}</b>
                </div>

            </div>


            <div class="row">

                <div>
                    🙋 Katılan:
                    <b>${esc(
                        item.joined
                    )}</b>
                </div>


                <div>
                    📈 Oran:
                    <b>${esc(
                        item.rate
                    )}</b>
                </div>

            </div>


            <div class="row">

                <div>
                    👀 İzlenme:
                    <b>${esc(
                        item.view
                    )}</b>
                </div>


                <div>
                    🕐 ${esc(
                        formatTime(
                            item.detected_at
                        )
                    )}
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


function lastCard(item, isNew) {

    if (!item) {

        return `
            <div class="empty">
                Henüz kayıt yok
            </div>
        `;
    }

    return card(
        item,
        isNew
    );
}


function sortNewest(items) {

    return [...items]
        .sort(
            (a, b) =>
                Number(
                    b.detected_at || 0
                )
                -
                Number(
                    a.detected_at || 0
                )
        );
}


function renderList(
    items,
    elementId,
    newSet
) {

    const element =
        document.getElementById(
            elementId
        );


    if (!items.length) {

        element.innerHTML = `
            <div class="empty">
                Henüz kayıt yok
            </div>
        `;

        return;
    }


    const sorted =
        sortNewest(
            items
        ).slice(
            0,
            5
        );


    element.innerHTML =
        sorted
        .map(
            item =>
                card(
                    item,
                    newSet.has(
                        String(
                            item.room
                        )
                    )
                )
        )
        .join("");
}


function renderLast(
    items,
    elementId,
    newSet
) {

    const element =
        document.getElementById(
            elementId
        );


    const sorted =
        sortNewest(
            items
        );


    if (!sorted.length) {

        element.innerHTML = `
            <div class="empty">
                Henüz kayıt yok
            </div>
        `;

        return;
    }


    const item = sorted[0];


    element.innerHTML =
        lastCard(
            item,
            newSet.has(
                String(
                    item.room
                )
            )
        );
}


async function loadRadar() {

    try {

        const response =
            await fetch(
                "/api/all",
                {
                    cache:
                        "no-store"
                }
            );


        const data =
            await response.json();


        const goody =
            sortNewest(
                data.goody_bags || []
            );


        const chest =
            sortNewest(
                data.chests || []
            );


        /*
         * İlk açılışta mevcut kayıtlar
         * YENİ değildir.
         */

        if (firstLoad) {

            knownGoody =
                new Set(
                    goody.map(
                        item =>
                            String(
                                item.room
                            )
                    )
                );


            knownChest =
                new Set(
                    chest.map(
                        item =>
                            String(
                                item.room
                            )
                    )
                );


            firstLoad = false;


        } else {


            /*
             * Sonradan gelen her yeni kayıt
             * YENİ olarak gösterilir.
             */

            for (
                const item of goody
            ) {

                const key =
                    String(
                        item.room
                    );


                if (
                    !knownGoody.has(
                        key
                    )
                ) {

                    newGoody.add(
                        key
                    );

                    knownGoody.add(
                        key
                    );

                }

            }


            for (
                const item of chest
            ) {

                const key =
                    String(
                        item.room
                    );


                if (
                    !knownChest.has(
                        key
                    )
                ) {

                    newChest.add(
                        key
                    );

                    knownChest.add(
                        key
                    );

                }

            }

        }


        renderList(
            goody,
            "bags",
            newGoody
        );


        renderList(
            chest,
            "chests",
            newChest
        );


        renderLast(
            goody,
            "lastGoody",
            newGoody
        );


        renderLast(
            chest,
            "lastChest",
            newChest
        );


        document.getElementById(
            "bagCounter"
        ).textContent =
            Math.min(
                goody.length,
                5
            );


        document.getElementById(
            "chestCounter"
        ).textContent =
            Math.min(
                chest.length,
                5
            );


        document.getElementById(
            "status"
        ).innerHTML =

            "🟢 RADAR AKTİF • " +

            "Goody: " +

            Math.min(
                goody.length,
                5
            ) +

            " • Chest: " +

            Math.min(
                chest.length,
                5
            );


    } catch (error) {

        console.error(
            error
        );


        document.getElementById(
            "status"
        ).innerHTML =
            "🔴 RADAR BAĞLANTI HATASI";
    }
}


loadRadar();


setInterval(
    loadRadar,
    2000
);

</script>

</body>

</html>
"""


# =========================================================
# HTTP
# =========================================================

async def radar_page(request):

    return web.Response(
        text=RADAR_HTML,
        content_type="text/html",
        charset="utf-8"
    )


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


async def api_boxes(request):

    return web.json_response(
        list(
            LIVE_CHESTS.values()
        )
    )


async def api_goody_bags(request):

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
# TELEGRAM DİNLEYİCİ
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
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
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
        "[HAZIR] Yeni gelen HER kayıt ⚡ YENİ."
    )

    print(
        "[HAZIR] Goody ve Chest ayrı."
    )

    print(
        "[HAZIR] Liderler bölümü kaldırıldı."
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
# ÇALIŞTIR
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
