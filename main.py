# main.py
# =========================================================
# 🏆 ÖDÜL AVCISI
#
# - GOODY BAG + HAZİNE SANDIĞI
# - Goody / Chest yan yana
# - Her bölüm son 5 kayıt
# - Yeni kayıt en üstte
# - Yeni kayıt ⚡ YENİ
# - Kayıtlar süre dolunca silinmez
# - En Çok Kişi kaldırıldı
# - En Yüksek Rate kaldırıldı
# - Sadece En Yüksek Coin
# - En Yüksek Coin son 5 + son 5 üzerinden hesaplanır
# - En Yüksek Coin canlı yayın linklidir
# - Telegram canlı yayın butonu gerçek inline butondur
# =========================================================

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

PORT = int(
    os.environ.get(
        "PORT",
        "10000"
    )
)


# =========================================================
# RADAR VERİLERİ
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

        return int(
            float(value)
        )

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

        text = (
            message.raw_text
            or ""
        )

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

        entities = (
            message.entities
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

        decoded = (
            base64.urlsafe_b64decode(
                token + padding
            )
        )

        text = decoded.decode(
            "utf-8",
            errors="ignore"
        ).strip()

        data = json.loads(
            text
        )

        if isinstance(
            data,
            dict
        ):

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
# ROOM / ODA
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

            decoded = (
                base64.urlsafe_b64decode(
                    encoded + padding
                )
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

def extract_coins(
    text,
    token_data=None
):

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

            value = token_data.get(
                key
            )

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
# RATE
# =========================================================

def extract_rate(
    text,
    token_data=None
):

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

def detect_type(
    text,
    token_data
):

    text_upper = (
        text or ""
    ).upper()


    # GOODY

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


    # CHEST

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


    # TOKEN

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

            value = token_data.get(
                key
            )

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

                    return (
                        now + value
                    )


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

                safe_int(
                    m.group(1)
                )
                * 60

                +

                safe_int(
                    m.group(2)
                )

            )

            if duration > 0:

                return (
                    now + duration
                )


    return (
        now + 180
    )


# =========================================================
# CANLI LINK
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

            value = token_data.get(
                key
            )

            if value:

                value = str(
                    value
                )


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
# MESAJ PARSE
# =========================================================

def parse_source_message(event):

    text = (
        event.message.raw_text
        or ""
    )


    print(
        "\n" + "=" * 70
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


    # USERNAME

    username = None


    if token_data:

        for key in [

            "username",
            "user",
            "unique_id",
            "uniqueId",

        ]:

            value = token_data.get(
                key
            )

            if value:

                username = str(
                    value
                )

                break


    if not username:

        username = (
            extract_username_from_text(
                text
            )
        )


    if not username:

        username = "bilinmiyor"


    # ROOM

    room = None


    if token_data:

        for key in [

            "room",
            "room_id",
            "roomid",
            "roomId",
            "roomID",

        ]:

            value = token_data.get(
                key
            )

            if value:

                room = str(
                    value
                )

                break


    if not room:

        room = extract_p_room(
            text
        )


    if not room:

        room = (
            "msg:"
            +
            str(
                event.message.id
            )
        )

        print(
            "[ROOM YOK] Mesaj ID kullanılıyor:",
            room
        )


    # COIN

    coins = extract_coins(
        text,
        token_data
    )


    # PEOPLE

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


    # JOINED

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


    # RATE

    rate = extract_rate(
        text,
        token_data
    )


    # VIEW

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
        "ALGILANMA:",
        result["detected_at"]
    )


    return result


# =========================================================
# RADARA EKLE
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
        "\n" + "=" * 70
    )

    print(
        "[RADAR]",
        data["type"],
        "EKLENDİ"
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


    # =====================================================
    # GERÇEK TELEGRAM BUTONU
    # =====================================================

    reply_markup = None


    if data.get("live"):

        reply_markup = {

            "inline_keyboard": [

                [

                    {

                        "text":
                            "🔴 CANLI YAYINA GİT",

                        "url":
                            str(
                                data["live"]
                            ),

                    }

                ]

            ]

        }


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


    if reply_markup:

        payload[
            "reply_markup"
        ] = reply_markup


    for attempt in range(
        1,
        9
    ):

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
                        "[TELEGRAM] "
                        "Butonlu bildirim gönderildi:",
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
# HTML
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

*{
    box-sizing:border-box;
}

html,
body{

    margin:0;

    min-height:100%;

    background:#05060c;

    color:white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

body{

    padding:8px;

    overflow-x:hidden;

    background:

        radial-gradient(
            circle at 50% -10%,
            rgba(106,52,255,.25),
            transparent 34%
        ),

        radial-gradient(
            circle at 0% 50%,
            rgba(0,174,255,.07),
            transparent 30%
        ),

        radial-gradient(
            circle at 100% 50%,
            rgba(180,0,255,.08),
            transparent 30%
        ),

        #05060c;
}

.wrapper{

    max-width:1200px;

    margin:auto;
}


/* =====================================================
   HEADER
   ===================================================== */

.header{

    text-align:center;

    padding:
        8px 4px 12px;
}

.title{

    font-size:
        clamp(
            27px,
            7vw,
            48px
        );

    font-weight:1000;

    line-height:1;

    letter-spacing:1px;

    color:#fff;

    text-shadow:

        0 0 5px #fff,

        0 0 16px #8b55ff,

        0 0 35px #5d25ff;
}

.subtitle{

    margin-top:7px;

    color:#aeb5c8;

    font-size:11px;

    font-weight:800;

    letter-spacing:.8px;
}

.status{

    display:inline-flex;

    margin-top:9px;

    padding:6px 12px;

    border-radius:999px;

    background:
        rgba(20,20,40,.8);

    border:
        1px solid
        rgba(157,92,255,.35);

    color:#74ff9a;

    font-size:10px;

    font-weight:900;
}

.status.error{

    color:#ff7474;

    border-color:
        rgba(255,80,80,.45);
}


/* =====================================================
   EN YÜKSEK COIN
   ===================================================== */

.coin-box{

    margin-bottom:12px;

    padding:10px;

    border-radius:15px;

    background:

        linear-gradient(
            145deg,
            rgba(25,21,45,.98),
            rgba(9,11,20,.98)
        );

    border:
        1px solid
        rgba(157,81,255,.6);

    box-shadow:
        0 0 25px
        rgba(157,81,255,.10);
}

.coin-title{

    color:#bca0ff;

    font-size:10px;

    font-weight:900;

    margin-bottom:4px;
}

.coin-content{

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:10px;
}

.coin-number{

    font-size:28px;

    font-weight:1000;
}

.coin-user{

    margin-top:2px;

    color:#aeb9cc;

    font-size:10px;

    overflow:hidden;

    text-overflow:ellipsis;

    white-space:nowrap;
}

.coin-live{

    flex-shrink:0;

    display:block;

    padding:9px 13px;

    border-radius:9px;

    background:
        linear-gradient(
            135deg,
            #f12657,
            #a90d38
        );

    color:white;

    text-decoration:none;

    font-size:9px;

    font-weight:1000;
}

.coin-live.disabled{

    opacity:.35;

    pointer-events:none;
}


/* =====================================================
   RADAR
   ===================================================== */

.radar-grid{

    display:grid;

    grid-template-columns:
        1fr 1fr;

    gap:10px;

    align-items:start;
}

.panel{

    min-width:0;

    background:
        rgba(9,12,21,.92);

    border:
        1px solid
        #273146;

    border-radius:16px;

    padding:9px;

    box-shadow:
        0 12px 38px
        rgba(0,0,0,.42);

    backdrop-filter:
        blur(10px);
}

.panel.goody{

    border-color:
        rgba(157,81,255,.65);
}

.panel.chest{

    border-color:
        rgba(241,200,75,.55);
}

.panel-title{

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:7px;

    padding:
        2px 2px 9px;
}

.panel-name{

    font-size:14px;

    font-weight:950;
}

.goody .panel-name{

    color:#d5a8ff;

    text-shadow:
        0 0 13px
        rgba(157,81,255,.65);
}

.chest .panel-name{

    color:#ffe37b;

    text-shadow:
        0 0 13px
        rgba(241,200,75,.55);
}

.panel-count{

    min-width:24px;

    padding:3px 7px;

    border-radius:999px;

    background:
        rgba(255,255,255,.08);

    text-align:center;

    font-size:9px;

    font-weight:900;
}


/* =====================================================
   CARD
   ===================================================== */

.card{

    position:relative;

    overflow:hidden;

    background:

        linear-gradient(
            145deg,
            rgba(23,29,45,.98),
            rgba(10,14,23,.98)
        );

    border:
        1px solid
        #293448;

    border-radius:11px;

    padding:8px;

    margin-bottom:6px;
}

.card:last-child{

    margin-bottom:0;
}

.goody .card{

    border-left:
        3px solid
        #9d51ff;
}

.chest .card{

    border-left:
        3px solid
        #f1c84b;
}


/* =====================================================
   YENİ
   ===================================================== */

.newest{

    animation:
        newestIn .7s ease-out;
}

@keyframes newestIn{

    0%{

        opacity:.35;

        transform:
            translateY(-8px)
            scale(.97);

        box-shadow:
            0 0 0
            rgba(157,81,255,0);
    }

    35%{

        box-shadow:
            0 0 30px
            rgba(157,81,255,.5);
    }

    100%{

        opacity:1;

        transform:
            translateY(0)
            scale(1);

        box-shadow:none;
    }
}

.new-badge{

    flex-shrink:0;

    padding:4px 7px;

    border-radius:6px;

    font-size:8px;

    font-weight:1000;

    color:#fff;

    background:

        linear-gradient(
            135deg,
            #8d35ff,
            #c05cff
        );

    box-shadow:
        0 0 13px
        rgba(157,81,255,.55);
}

.chest .new-badge{

    background:

        linear-gradient(
            135deg,
            #d89b14,
            #f4d35e
        );

    color:#211700;

    box-shadow:
        0 0 13px
        rgba(241,200,75,.45);
}


/* =====================================================
   USER
   ===================================================== */

.user-row{

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:5px;

    margin-bottom:7px;
}

.user{

    font-size:12px;

    font-weight:950;

    word-break:break-word;

    min-width:0;
}

.rank{

    flex-shrink:0;

    font-size:8px;

    color:#77849a;

    font-weight:900;
}


/* =====================================================
   BİLGİ
   ===================================================== */

.info-grid{

    display:grid;

    grid-template-columns:
        1fr 1fr;

    gap:4px;
}

.info{

    background:
        rgba(255,255,255,.035);

    border-radius:7px;

    padding:5px;

    font-size:7px;

    color:#818da1;
}

.info b{

    display:block;

    color:#fff;

    font-size:10px;

    margin-top:2px;

    overflow:hidden;

    text-overflow:ellipsis;
}


/* =====================================================
   CANLI BUTON
   ===================================================== */

.live-button{

    display:block;

    text-align:center;

    text-decoration:none;

    color:white;

    background:

        linear-gradient(
            135deg,
            #f12657,
            #a90d38
        );

    padding:7px;

    margin-top:7px;

    border-radius:8px;

    font-size:8px;

    font-weight:950;
}


/* =====================================================
   EMPTY
   ===================================================== */

.empty{

    text-align:center;

    padding:20px 4px;

    border-radius:9px;

    background:
        rgba(255,255,255,.025);

    color:#626e82;

    font-size:9px;
}


/* =====================================================
   FOOTER
   ===================================================== */

.footer{

    text-align:center;

    color:#59647a;

    font-size:8px;

    padding:12px 0 3px;
}


/* =====================================================
   MOBİL
   ===================================================== */

@media(max-width:700px){

    body{

        padding:5px;
    }

    .header{

        padding-top:6px;
    }

    .title{

        font-size:27px;
    }

    .subtitle{

        font-size:8px;
    }

    .status{

        font-size:8px;

        padding:5px 8px;
    }

    .coin-box{

        padding:8px;

        margin-bottom:7px;
    }

    .coin-title{

        font-size:8px;
    }

    .coin-number{

        font-size:21px;
    }

    .coin-user{

        font-size:8px;
    }

    .coin-live{

        padding:7px 8px;

        font-size:7px;
    }

    .radar-grid{

        grid-template-columns:
            1fr 1fr;

        gap:5px;
    }

    .panel{

        padding:5px;

        border-radius:11px;
    }

    .panel-name{

        font-size:9px;
    }

    .panel-count{

        min-width:20px;

        padding:3px 5px;

        font-size:7px;
    }

    .card{

        padding:6px;

        border-radius:8px;

        margin-bottom:4px;
    }

    .user{

        font-size:9px;
    }

    .new-badge{

        font-size:6px;

        padding:3px 5px;
    }

    .rank{

        font-size:6px;
    }

    .info{

        padding:4px;

        font-size:6px;
    }

    .info b{

        font-size:8px;
    }

    .live-button{

        padding:6px 2px;

        font-size:6px;

        margin-top:4px;
    }

    .empty{

        padding:17px 2px;

        font-size:7px;
    }
}

</style>

</head>


<body>

<div class="wrapper">


<div class="header">

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

</div>


<!-- =====================================================
     EN YÜKSEK COIN
     ===================================================== -->

<div class="coin-box">

    <div class="coin-title">
        🪙 EN YÜKSEK COIN
    </div>

    <div class="coin-content">

        <div>

            <div
                id="maxCoin"
                class="coin-number"
            >
                0
            </div>

            <div
                id="maxCoinUser"
                class="coin-user"
            >
                -
            </div>

        </div>


        <a
            id="maxCoinLink"
            class="coin-live disabled"
            href="#"
            target="_blank"
            rel="noopener"
        >
            🔴 CANLIYA GİT
        </a>

    </div>

</div>


<!-- =====================================================
     RADAR
     ===================================================== -->

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
    ⚡ ÖDÜL AVCISI • CANLI RADAR
</div>


</div>


<script>


let radarData = {

    chests: [],

    goody_bags: []

};


let firstLoad = true;


let lastNewestGoody = "";

let lastNewestChest = "";


/* =====================================================
   HTML ESCAPE
   ===================================================== */

function escapeHtml(value){

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


/* =====================================================
   TIMESTAMP
   ===================================================== */

function timestamp(item){

    return Number(

        item.detected_at ||

        item.created_at ||

        item.timestamp ||

        0

    );

}


/* =====================================================
   SON 5
   ===================================================== */

function latestFive(items){

    if(!Array.isArray(items)){

        return [];

    }


    return [...items]

        .sort(

            (a,b) =>

                timestamp(b)

                -

                timestamp(a)

        )

        .slice(
            0,
            5
        );

}


/* =====================================================
   KEY
   ===================================================== */

function newestKey(item){

    return String(

        item.source_message_id ??

        item.room ??

        item.detected_at ??

        ""

    );

}


/* =====================================================
   KART
   ===================================================== */

function renderItems(

    items,

    elementId,

    counterId,

    icon,

    type

){

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


    if(!latest.length){

        container.innerHTML =

            '<div class="empty">' +

            '⚡ Henüz veri yok.' +

            '</div>';

        return;

    }


    const newest =
        newestKey(
            latest[0]
        );


    const previousNewest =

        type === "GOODY BAG"

            ? lastNewestGoody

            : lastNewestChest;


    const isNew =

        !firstLoad &&

        newest &&

        newest !== previousNewest;


    container.innerHTML =

        latest.map(

            (item,index) => {


                const live =
                    item.live || "";


                const isNewest =
                    index === 0;


                return `

                <div class="card ${
                    isNew && isNewest
                        ? "newest"
                        : ""
                }">

                    <div class="user-row">

                        <div class="user">

                            ${icon}

                            ${escapeHtml(
                                item.username
                            )}

                        </div>


                        ${
                            isNewest

                            ?

                            `

                            <div class="new-badge">

                                ⚡ YENİ

                            </div>

                            `

                            :

                            `

                            <div class="rank">

                                #${index + 1}

                            </div>

                            `

                        }

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


    if(
        type === "GOODY BAG"
    ){

        lastNewestGoody =
            newest;

    }else{

        lastNewestChest =
            newest;

    }

}


/* =====================================================
   EN YÜKSEK COIN
   ===================================================== */

function updateMaxCoin(){

    /*
     * SADECE GÖSTERİLEN SON 5 GOODY
     * VE SON 5 CHEST KULLANILIYOR.
     *
     * Eski 2000 kayıt sonsuza kadar
     * lider kalamaz.
     */

    const all = [

        ...latestFive(
            radarData.goody_bags
        ),

        ...latestFive(
            radarData.chests
        )

    ];


    const number =
        document.getElementById(
            "maxCoin"
        );


    const user =
        document.getElementById(
            "maxCoinUser"
        );


    const link =
        document.getElementById(
            "maxCoinLink"
        );


    if(!all.length){

        number.textContent =
            "0";

        user.textContent =
            "-";

        link.href =
            "#";

        link.classList.add(
            "disabled"
        );

        return;

    }


    /*
     * Coin değeri ne olursa olsun
     * kayıtlar listeye girer.
     *
     * Burada sadece lider hesaplanıyor.
     */

    const coin =

        [...all].sort(

            (a,b) =>

                Number(
                    b.coins || 0
                )

                -

                Number(
                    a.coins || 0
                )

        )[0];


    number.textContent =

        Number(
            coin.coins || 0
        );


    user.textContent =

        `${

            coin.type === "GOODY BAG"

                ? "🟪"

                : "🟨"

        } ${coin.username || "-"}`;


    /*
     * EN YÜKSEK COIN DE LİNKLİ
     */

    if(coin.live){

        link.href =
            coin.live;

        link.classList.remove(
            "disabled"
        );

    }else{

        link.href =
            "#";

        link.classList.add(
            "disabled"
        );

    }

}


/* =====================================================
   RADAR RENDER
   ===================================================== */

function renderRadar(){

    renderItems(

        radarData.goody_bags,

        "bags",

        "bagCounter",

        "🟪",

        "GOODY BAG"

    );


    renderItems(

        radarData.chests,

        "chests",

        "chestCounter",

        "🟨",

        "CHEST"

    );


    updateMaxCoin();

}


/* =====================================================
   VERİ ÇEK
   ===================================================== */

async function loadRadar(){

    try{

        const response =

            await fetch(

                "/api/all?t=" +
                Date.now(),

                {

                    cache:
                        "no-store"

                }

            );


        if(!response.ok){

            throw new Error(

                "HTTP " +
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


        firstLoad =
            false;


    }

    catch(error){

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


/* =====================================================
   2 SANİYEDE BİR
   ===================================================== */

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
# HTTP SAYFA
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
# API
# =========================================================

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


# =========================================================
# HTTP SERVER
# =========================================================

async def start_http_server():

    app = web.Application(

        middlewares=[
            cors_middleware
        ]

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


        print(
            "[MESAJ] Kaynak mesaj alındı:",
            event.chat_id,
            event.message.id
        )


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
        "[HAZIR] Goody Bag aktif."
    )

    print(
        "[HAZIR] Hazine Sandığı aktif."
    )

    print(
        "[HAZIR] Son 5 kayıt gösteriliyor."
    )

    print(
        "[HAZIR] Kayıtlar silinmiyor."
    )

    print(
        "[HAZIR] En Yüksek Coin aktif."
    )

    print(
        "[HAZIR] En Yüksek Coin canlı linkli."
    )

    print(
        "[HAZIR] Telegram canlı yayın butonu aktif."
    )

    print(
        "[HAZIR] En Çok Kişi kaldırıldı."
    )

    print(
        "[HAZIR] En Yüksek Rate kaldırıldı."
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
# START
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
