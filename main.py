import os
import re
import json
import base64
import asyncio
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
        return int(value)
    except Exception:
        return default


def safe_float(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


# =========================================================
# TELEGRAM T.PHP TOKEN BUL
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
            return unquote(m.group(1))

    # -----------------------------------------------------
    # TELEGRAM URL ENTITY
    # -----------------------------------------------------

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
                return unquote(
                    m.group(1)
                )

    except Exception:
        pass

    # -----------------------------------------------------
    # GET ENTITIES TEXT
    # -----------------------------------------------------

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
                return unquote(
                    m.group(1)
                )

    except Exception:
        pass

    return None


# =========================================================
# TOKEN ÇÖZ
# =========================================================

def decode_token(token):

    if not token:
        return None

    try:

        token = unquote(
            token
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
        )

        return json.loads(text)

    except Exception as e:

        print(
            "[TOKEN ERROR]",
            repr(e)
        )

        return None


# =========================================================
# P= BASE64 ODA ID
# =========================================================

def extract_p_room(text):

    if not text:
        return None

    m = re.search(
        r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)',
        text,
        re.I
    )

    if not m:
        return None

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
            return room

    except Exception as e:

        print(
            "[P ROOM ERROR]",
            repr(e)
        )

    return None


# =========================================================
# KULLANICI ADI
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

        # GOODY BAG
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

        # GENEL 100/25
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
            "gem_type",
            "item_label",
        ]:

            value = token_data.get(key)

            if isinstance(
                value,
                (int, float)
            ):
                return int(value)

    return 0


# =========================================================
# KİŞİ SAYISI
# =========================================================

def extract_people_from_message(text):

    if not text:
        return 0

    # TÚI: 20/20
    m = re.search(
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        text,
        re.I
    )

    if m:
        return safe_int(
            m.group(1)
        )

    # BOX: 100/25
    m = re.search(
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        text,
        re.I
    )

    if m:
        return safe_int(
            m.group(1)
        )

    # RƯƠNG / genel sayı
    m = re.search(
        r'(?:RƯƠNG|TREO).*?(\d+)\s*/\s*(\d+)',
        text,
        re.I
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

    # ÖRNEK:
    # 👀 86
    # 👀 75 | 13

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

        return safe_float(
            token_data.get(
                "ratio",
                0
            )
        )

    return 0


# =========================================================
# TÜR BELİRLE
# =========================================================

def detect_type(text, token_data):

    # Önce token
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
            return True

        if value in [
            False,
            0,
            "0",
            "false",
            "False",
        ]:
            return False

    # Token yoksa mesajdan
    text_upper = (
        text or ""
    ).upper()

    # GOODY
    if re.search(
        r'TÚI|TUI|GOODY\s*BAG',
        text_upper
    ):
        return True

    # CHEST
    if re.search(
        r'BOX|RƯƠNG|TREO',
        text_upper
    ):
        return False

    if "🟡" in text:
        return False

    return None


# =========================================================
# HEDEF ZAMAN
# =========================================================

def calculate_target_time(
    text,
    token_data=None
):

    if token_data:

        value = token_data.get(
            "time"
        )

        if value is not None:

            try:

                value = int(value)

                if value > 0:
                    return value

            except Exception:
                pass

    if text:

        m = re.search(
            r'TIME\s*:\s*(\d+):(\d+)\s*-\s*(\d+):(\d+):(\d+)',
            text,
            re.I
        )

        if m:

            mm = safe_int(
                m.group(1)
            )

            ss = safe_int(
                m.group(2)
            )

            return (
                mm * 60
                + ss
            )

    return 180


# =========================================================
# TIKTOK CANLI LINK
# =========================================================

def get_live_link(
    username,
    room,
    token_data=None
):

    if token_data:

        openitok = token_data.get(
            "openitok"
        )

        if openitok:
            return str(
                openitok
            )

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
# MESAJI PARÇALA
# =========================================================

def parse_source_message(event):

    text = (
        event.message.raw_text
        or ""
    )

    # TOKEN
    token = extract_token_from_event(
        event
    )

    token_data = decode_token(
        token
    )

    if token_data:

        print(
            "[TOKEN] çözüldü"
        )

    else:

        print(
            "[TOKEN] yok -> mesaj yedeği"
        )

    # TÜR
    is_goody = detect_type(
        text,
        token_data
    )

    if is_goody is None:

        print(
            "[ATLANDI] tür belirlenemedi"
        )

        return None

    # KULLANICI
    username = None

    if token_data:

        username = token_data.get(
            "username"
        )

    if not username:

        username = extract_username_from_text(
            text
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
        ]:

            value = token_data.get(
                key
            )

            if value:

                room = str(
                    value
                )

                break

    # TOKEN YOKSA p=
    if not room:

        room = extract_p_room(
            text
        )

    # VERİLER
    coins = extract_coins(
        text,
        token_data
    )

    message_people = (
        extract_people_from_message(
            text
        )
    )

    if message_people:

        people = message_people

    elif token_data:

        people = safe_int(
            token_data.get(
                "people",
                0
            )
        )

    else:

        people = 0

    joined = extract_joined(
        text
    )

    rate = extract_rate(
        text,
        token_data
    )

    message_view = extract_viewers(
        text
    )

    if message_view:

        viewers = message_view

    elif token_data:

        viewers = safe_int(
            token_data.get(
                "view",
                0
            )
        )

    else:

        viewers = 0

    # EK BİLGİ
    matxem = 0
    box_tag = ""
    item_label = ""
    time_display = ""

    if token_data:

        matxem = safe_int(
            token_data.get(
                "matxem",
                0
            )
        )

        box_tag = str(
            token_data.get(
                "box_tag",
                ""
            )
        )

        item_label = str(
            token_data.get(
                "item_label",
                ""
            )
        )

        time_display = str(
            token_data.get(
                "time_display",
                ""
            )
        )

    target_time = calculate_target_time(
        text,
        token_data
    )

    live_link = get_live_link(
        username,
        room,
        token_data
    )

    return {
        "type": (
            "GOODY BAG"
            if is_goody
            else "CHEST"
        ),
        "username": username,
        "coins": coins,
        "people": people,
        "joined": joined,
        "rate": rate,
        "view": viewers,
        "room": room or "",
        "live": live_link,
        "target_time": target_time,
        "matxem": matxem,
        "box_tag": box_tag,
        "item_label": item_label,
        "time_display": time_display,
        "source_message_id": event.message.id,
    }


# =========================================================
# RADARA EKLE
# =========================================================

def add_to_radar(data):

    room = data.get(
        "room"
    )

    username = data.get(
        "username"
    )

    if not room:

        print(
            "[ATLANDI] oda ID bulunamadı:",
            username
        )

        return False

    target = (
        LIVE_GOODY_BAGS
        if data["type"] == "GOODY BAG"
        else LIVE_CHESTS
    )

    if room in target:

        print(
            "[TEKRAR] Bu oda zaten radarda:",
            room
        )

        return False

    target[room] = data

    print(
        f"[RADAR] {data['type']} eklendi."
    )

    print(
        "TÜR:",
        data["type"]
    )

    print(
        "KULLANICI:",
        username
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
        room
    )

    print(
        "CANLI:",
        data["live"]
    )

    return True


# =========================================================
# TELEGRAM MESAJI
# =========================================================

async def send_telegram_message(data):

    global http_session

    if not http_session:
        return

    if data["type"] == "GOODY BAG":

        baslik = "🟪 ÖDÜL ÇANTASI"

    else:

        baslik = "🟨 HAZİNE SANDIĞI"

    text = (
        f"{baslik}\n\n"
        f"👤 Kullanıcı: {data['username']}\n"
        f"🪙 Coin: {data['coins']}\n"
        f"👥 Kişi: {data['people']}\n"
        f"🙋 Katılan: {data['joined']}\n"
        f"📈 Oran: {data['rate']}\n"
        f"👀 İzlenme: {data['view']}\n"
    )

    if data.get("live"):

        text += (
            "\n🔴 "
            f"<a href=\"{data['live']}\">"
            "TIKTOK CANLI YAYIN"
            "</a>"
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TARGET_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    # -----------------------------------------------------
    # 429 İÇİN OTOMATİK TEKRAR
    # -----------------------------------------------------

    max_attempts = 5

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

                # BAŞARILI
                if response.status == 200:

                    print(
                        "[TELEGRAM] Bildirim gönderildi:",
                        data["username"]
                    )

                    return True

                # 429
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
                        f"[TELEGRAM] Hız sınırı. "
                        f"{retry_after} saniye bekleniyor..."
                    )

                    await asyncio.sleep(
                        retry_after
                    )

                    continue

                # Diğer hata
                print(
                    "[TELEGRAM HATA]",
                    response.status,
                    result_text
                )

                # Geçici sunucu hataları
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

    print(
        "[TELEGRAM] Maksimum tekrar sayısına ulaşıldı."
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
# HTTP API
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
        "status": "online",
        "chests": len(
            LIVE_CHESTS
        ),
        "goody_bags": len(
            LIVE_GOODY_BAGS
        ),
    })


async def api_all(request):

    return web.json_response({
        "chests": list(
            LIVE_CHESTS.values()
        ),
        "goody_bags": list(
            LIVE_GOODY_BAGS.values()
        ),
    })


async def start_http_server():

    app = web.Application()

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
        api_status
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


# =========================================================
# YENİ MESAJ
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
            "\n" + "=" * 70
        )

        print(
            "[YENİ KAYNAK MESAJI]"
        )

        print(text)

        print(
            "=" * 70
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
        "TREASURE ALERT BAŞLIYOR"
    )

    print(
        "=" * 70
    )

    http_session = aiohttp.ClientSession()

    # HTTP
    await start_http_server()

    # TELEGRAM
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
        "[TAKİP EDİLEN KANALLAR]",
        SOURCE_CHATS
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

    try:

        await client.run_until_disconnected()

    finally:

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
