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

# Süresi biten kayıtların sitede tutulacağı süre
RECENT_VISIBLE_SECONDS = 120


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


    # -----------------------------------------------------
    # RAW TEXT
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # TELEGRAM URL ENTITY
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # ENTITY TEXT
    # -----------------------------------------------------

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


        decoded = base64.urlsafe_b64decode(
            token + padding
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

def extract_coins(
    text,
    token_data=None
):

    if text:

        # GOODY

        m = re.search(
            r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
            text,
            re.I
        )


        if m:

            return safe_int(
                m.group(1)
            )


        # BOX

        m = re.search(
            r'BOX\s*:\s*(\d+)\s*/',
            text,
            re.I
        )


        if m:

            return safe_int(
                m.group(1)
            )


        # GENEL

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


    # TÚI

    m = re.search(
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        text,
        re.I
    )


    if m:

        return safe_int(
            m.group(1)
        )


    # BOX

    m = re.search(
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        text,
        re.I
    )


    if m:

        return safe_int(
            m.group(1)
        )


    # RƯƠNG

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
# TÜR TESPİTİ
# =========================================================

def detect_type(
    text,
    token_data
):

    text_upper = (
        text or ""
    ).upper()


    # =====================================================
    # 1. TÚI = KESİN GOODY BAG
    # =====================================================

    if re.search(
        r'TÚI|TUI',
        text_upper
    ):

        print(
            "[TÜR] TÚI -> GOODY BAG"
        )

        return True


    # =====================================================
    # 2. GOODY BAG YAZISI
    # =====================================================

    if re.search(
        r'GOODY\s*BAG|REWARD\s*BAG',
        text_upper
    ):

        print(
            "[TÜR] GOODY BAG -> GOODY BAG"
        )

        return True


    # =====================================================
    # 3. CHEST
    # =====================================================

    if re.search(
        r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
        text_upper
    ):

        print(
            "[TÜR] BOX/RƯƠNG/TREO -> CHEST"
        )

        return False


    # =====================================================
    # 4. SARI İŞARET
    # =====================================================

    if "🟡" in text:

        print(
            "[TÜR] 🟡 -> CHEST"
        )

        return False


    # =====================================================
    # 5. TOKEN
    # =====================================================

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

def calculate_target_time(
    text,
    token_data=None
):

    now = int(
        time.time()
    )


    # =====================================================
    # TOKEN
    # =====================================================

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


                # Milisaniye

                if value > 10_000_000_000:

                    return int(
                        value / 1000
                    )


                # Saniye epoch

                if value > 1_000_000_000:

                    return value


                # Süre

                if 0 < value < 86_400:

                    return now + value


            except Exception:

                pass


    # =====================================================
    # MESAJ TIME
    # =====================================================

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


    # =====================================================
    # FALLBACK
    # =====================================================

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

            value = token_data.get(
                key
            )


            if value:

                value = str(
                    value
                )


                if (

                    value.startswith(
                        "http://"
                    )

                    or

                    value.startswith(
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
        "\n"
        + "=" * 70
    )

    print(
        "[YENİ KAYNAK MESAJI]"
    )

    print(
        text
    )

    print(
        "=" * 70
    )


    # =====================================================
    # TOKEN
    # =====================================================

    token = extract_token_from_event(
        event
    )


    token_data = decode_token(
        token
    )


    # =====================================================
    # TÜR
    # =====================================================

    is_goody = detect_type(
        text,
        token_data
    )


    if is_goody is None:

        print(
            "[ATLANDI] Tür belirlenemedi"
        )

        return None


    # =====================================================
    # USERNAME
    # =====================================================

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

        username = extract_username_from_text(
            text
        )


    if not username:

        username = "bilinmiyor"


    # =====================================================
    # ROOM
    # =====================================================

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


    # =====================================================
    # ROOM YOKSA MESAJ ID FALLBACK
    # =====================================================

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


    # =====================================================
    # COIN
    # =====================================================

    coins = extract_coins(
        text,
        token_data
    )


    # =====================================================
    # PEOPLE
    # =====================================================

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


    # =====================================================
    # JOINED
    # =====================================================

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


    # =====================================================
    # RATE
    # =====================================================

    rate = extract_rate(
        text,
        token_data
    )


    # =====================================================
    # VIEW
    # =====================================================

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


    # =====================================================
    # TARGET
    # =====================================================

    target_time = calculate_target_time(
        text,
        token_data
    )


    # =====================================================
    # DETECTED
    # =====================================================

    detected_at = int(
        time.time()
    )


    # =====================================================
    # LIVE
    # =====================================================

    live_link = get_live_link(
        username,
        room,
        token_data
    )


    # =====================================================
    # RESULT
    # =====================================================

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
# RADAR TEMİZLİĞİ
# =========================================================

def cleanup_expired():

    now = int(
        time.time()
    )


    removed_chests = 0
    removed_bags = 0


    # =====================================================
    # CHESTS
    # =====================================================

    for room, data in list(
        LIVE_CHESTS.items()
    ):

        target_time = safe_int(
            data.get(
                "target_time",
                0
            )
        )


        # Süre bitince 2 dakika daha tut

        if target_time < (
            now
            -
            RECENT_VISIBLE_SECONDS
        ):

            del LIVE_CHESTS[room]

            removed_chests += 1


    # =====================================================
    # GOODY
    # =====================================================

    for room, data in list(
        LIVE_GOODY_BAGS.items()
    ):

        target_time = safe_int(
            data.get(
                "target_time",
                0
            )
        )


        if target_time < (
            now
            -
            RECENT_VISIBLE_SECONDS
        ):

            del LIVE_GOODY_BAGS[room]

            removed_bags += 1


    if (
        removed_chests
        or removed_bags
    ):

        print(
            "[TEMİZLİK]",
            "Sandık:",
            removed_chests,
            "Goody:",
            removed_bags
        )


# =========================================================
# RADARA EKLE
# =========================================================

def add_to_radar(data):

    cleanup_expired()


    room = data.get(
        "room"
    )


    if not room:

        print(
            "[ATLANDI] Room yok"
        )

        return False


    # =====================================================
    # HEDEF
    # =====================================================

    if data["type"] == "GOODY BAG":

        target = LIVE_GOODY_BAGS

    else:

        target = LIVE_CHESTS


    # =====================================================
    # AYNI ROOM KONTROLÜ
    # =====================================================

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


        # Çok yakın zamanda aynı oda tekrar geldiyse
        # duplicate kabul et

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


        # Yeni mesaj geldiyse güncelle

        print(
            "[GÜNCELLEME] Aynı oda yeniden geldi:",
            room
        )


    # =====================================================
    # EKLE / GÜNCELLE
    # =====================================================

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
        "HEDEF:",
        data["target_time"]
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


                # -----------------------------------------
                # 429
                # -----------------------------------------

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

                        "[TELEGRAM] "
                        f"{retry_after} saniye bekleniyor..."

                    )


                    await asyncio.sleep(
                        retry_after
                    )


                    continue


                # -----------------------------------------
                # 5xx
                # -----------------------------------------

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

                        "[TELEGRAM] "
                        f"Sunucu hatası: {wait}s"

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

<title>Treasure Alert</title>


<style>

* {
    box-sizing: border-box;
}

html,
body {

    margin: 0;
    padding: 0;

    background: #080b12;
    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

body {
    padding: 14px;
}

.header {
    text-align: center;
    margin-bottom: 20px;
}

.header h1 {

    margin: 0 0 8px;

    font-size: 25px;
}

.status {

    display: inline-block;

    padding:
        7px 14px;

    border-radius: 20px;

    background: #111827;

    font-size: 13px;

    font-weight: bold;
}

.status.online {
    color: #72ff8a;
}

.status.error {
    color: #ff7070;
}

.section {
    margin-bottom: 28px;
}

.section-title {

    display: flex;

    justify-content:
        space-between;

    align-items:
        center;

    margin-bottom: 11px;
}

.section-title h2 {

    margin: 0;

    font-size: 20px;
}

.counter {

    min-width: 34px;

    text-align: center;

    padding:
        6px 10px;

    border-radius: 18px;

    background: #171d2a;

    font-weight: bold;

    font-size: 13px;
}

.card {

    background: #121824;

    border:
        1px solid
        #202938;

    border-radius: 15px;

    padding: 15px;

    margin-bottom: 11px;

    box-shadow:
        0 4px 15px
        rgba(0,0,0,.35);
}

.card.expired {

    opacity: .65;

}

.card.urgent {

    animation:
        pulse
        .8s
        infinite;
}

@keyframes pulse {

    50% {

        transform:
            scale(1.015);

    }

}

.user {

    font-size: 18px;

    font-weight: bold;

    word-break:
        break-word;

    margin-bottom: 8px;
}

.countdown {

    font-size: 27px;

    font-weight: bold;

    margin:
        7px 0 12px;
}

.expired-text {

    color: #ff9c9c;

    font-size: 15px;

    font-weight: bold;

    margin-bottom: 8px;
}

.info {

    font-size: 14px;

    line-height: 1.85;
}

.info b {

    font-size: 15px;
}

.live-button {

    display: block;

    text-align: center;

    text-decoration: none;

    color: white;

    background: #e91e4d;

    padding: 11px;

    margin-top: 13px;

    border-radius: 10px;

    font-weight: bold;
}

.empty {

    text-align: center;

    padding: 22px 10px;

    border-radius: 12px;

    background: #10151f;

    color: #7f8999;
}

.footer {

    text-align: center;

    color: #687386;

    font-size: 11px;

    padding:
        10px 0 20px;
}

</style>

</head>


<body>


<div class="header">

    <h1>
        🚨 TREASURE ALERT
    </h1>


    <div
        id="status"
        class="status"
    >
        🟡 SUNUCU BAĞLANIYOR...
    </div>

</div>


<!-- =====================================================
     CHEST
     ===================================================== -->

<div class="section">

    <div class="section-title">

        <h2>
            📦 Hazine Sandığı Radarı
        </h2>

        <div
            id="chestCounter"
            class="counter"
        >
            0
        </div>

    </div>


    <div id="chests"></div>

</div>


<!-- =====================================================
     GOODY
     ===================================================== -->

<div class="section">

    <div class="section-title">

        <h2>
            🎒 Goody Bag Radarı
        </h2>

        <div
            id="bagCounter"
            class="counter"
        >
            0
        </div>

    </div>


    <div id="bags"></div>

</div>


<div class="footer">

    Treasure Alert • Canlı Radar

</div>


<script>

let radarData = {

    chests: [],

    goody_bags: []

};


const RECENT_VISIBLE_SECONDS = 120;


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


function formatTime(seconds) {

    seconds = Math.max(

        0,

        Math.floor(
            Number(seconds) || 0
        )

    );


    const minutes =
        Math.floor(
            seconds / 60
        );


    const secs =
        seconds % 60;


    return (

        String(minutes)
        .padStart(2, "0")

        +

        ":"

        +

        String(secs)
        .padStart(2, "0")

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


    const now =
        Math.floor(
            Date.now() / 1000
        );


    const allItems =

        (
            Array.isArray(items)
                ? items
                : []
        )

        .map(item => {

            const target =
                Number(
                    item.target_time || 0
                );


            const remaining =
                target - now;


            const recent =
                remaining >=
                -RECENT_VISIBLE_SECONDS;


            return {

                ...item,

                remaining:
                    remaining,

                recent:
                    recent

            };

        })

        .filter(
            item =>
                item.recent
        )

        .sort(
            (a, b) => {

                const aActive =
                    a.remaining > 0;

                const bActive =
                    b.remaining > 0;


                if (
                    aActive
                    &&
                    !bActive
                ) {

                    return -1;

                }


                if (
                    !aActive
                    &&
                    bActive
                ) {

                    return 1;

                }


                return (
                    a.remaining
                    -
                    b.remaining
                );

            }
        );


    counter.textContent =
        allItems.length;


    if (!allItems.length) {

        container.innerHTML =

            '<div class="empty">' +

            'Şu anda aktif veri yok.' +

            '</div>';

        return;

    }


    container.innerHTML =

        allItems.map(item => {


            const active =
                item.remaining > 0;


            const urgent =

                active
                &&
                item.remaining <= 15

                    ? "urgent"

                    : "";


            const expired =

                !active

                    ? "expired"

                    : "";


            const live =
                item.live || "";


            let timeHtml;


            if (active) {

                timeHtml =

                    `

                    <div class="countdown">

                        ⏳

                        ${formatTime(
                            item.remaining
                        )}

                    </div>

                    `;

            }

            else {

                timeHtml =

                    `

                    <div class="expired-text">

                        ⚫ SÜRESİ DOLDU

                    </div>

                    `;

            }


            return `

            <div
                class="card ${urgent} ${expired}"
            >

                <div class="user">

                    ${icon}

                    ${escapeHtml(
                        item.username
                    )}

                </div>


                ${timeHtml}


                <div class="info">

                    🪙 Coin:

                    <b>
                        ${escapeHtml(
                            item.coins
                        )}
                    </b>

                    <br>


                    👥 Kişi:

                    <b>
                        ${escapeHtml(
                            item.people
                        )}
                    </b>

                    <br>


                    🙋 Katılan:

                    <b>
                        ${escapeHtml(
                            item.joined
                        )}
                    </b>

                    <br>


                    📈 Oran:

                    <b>
                        ${escapeHtml(
                            item.rate
                        )}
                    </b>

                    <br>


                    👀 İzlenme:

                    <b>
                        ${escapeHtml(
                            item.view
                        )}
                    </b>

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

                        🔴 TIKTOK CANLI YAYIN

                    </a>

                    `

                    :

                    ""

                }

            </div>

            `;

        }).join("");

}


function renderRadar() {

    renderItems(

        radarData.chests || [],

        "chests",

        "chestCounter",

        "🟨"

    );


    renderItems(

        radarData.goody_bags || [],

        "bags",

        "bagCounter",

        "🟪"

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
            "status online";


        status.textContent =
            "🟢 SUNUCU ONLINE";


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


// Her saniye geri sayım

setInterval(

    renderRadar,

    1000

);


// Her 2 saniyede API

setInterval(

    loadRadar,

    2000

);


// İlk yükleme

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

    cleanup_expired()


    return web.json_response(

        list(
            LIVE_CHESTS.values()
        )

    )


# =========================================================
# API GOODY
# =========================================================

async def api_goody_bags(request):

    cleanup_expired()


    return web.json_response(

        list(
            LIVE_GOODY_BAGS.values()
        )

    )


# =========================================================
# STATUS
# =========================================================

async def api_status(request):

    cleanup_expired()


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

    cleanup_expired()


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


    # =====================================================
    # RADAR
    # =====================================================

    app.router.add_get(

        "/radar",

        radar_page

    )


    # =====================================================
    # API
    # =====================================================

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


    # =====================================================
    # OPTIONS
    # =====================================================

    app.router.add_options(

        "/api/boxes",

        lambda request:

            web.Response(
                status=204
            )

    )


    app.router.add_options(

        "/api/goody_bags",

        lambda request:

            web.Response(
                status=204
            )

    )


    app.router.add_options(

        "/api/status",

        lambda request:

            web.Response(
                status=204
            )

    )


    app.router.add_options(

        "/api/all",

        lambda request:

            web.Response(
                status=204
            )

    )


    # =====================================================
    # ANA SAYFA
    # =====================================================

    app.router.add_get(

        "/",

        radar_page

    )


    # =====================================================
    # SERVER
    # =====================================================

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
        "[HTTP] Radar:",
        "/radar"
    )


    print(
        "[HTTP] API:",
        "/api/all"
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


        print(
            text
        )


        print(
            "=" * 70
        )


        # =================================================
        # PARSE
        # =================================================

        data = parse_source_message(
            event
        )


        if not data:

            return


        # =================================================
        # RADAR
        # =================================================

        added = add_to_radar(
            data
        )


        # =================================================
        # TELEGRAM
        # =================================================

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
        "🚨 TREASURE ALERT BAŞLIYOR"
    )


    print(
        "=" * 70
    )


    # =====================================================
    # HTTP SESSION
    # =====================================================

    http_session = (
        aiohttp.ClientSession()
    )


    # =====================================================
    # HTTP SERVER
    # =====================================================

    await start_http_server()


    # =====================================================
    # TELEGRAM
    # =====================================================

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


    # =====================================================
    # EVENT
    # =====================================================

    client.add_event_handler(

        message_listener,

        events.NewMessage(
            chats=SOURCE_CHATS
        )

    )


    # =====================================================
    # TELEGRAM SENDER
    # =====================================================

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
        "[HAZIR] Süresi biten kayıtlar 120 saniye görünür."
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
