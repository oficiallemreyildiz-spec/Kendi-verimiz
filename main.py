# ============================================================
# 🏆 ÖDÜL AVCISI
# TELEGRAM 429 KORUMALI + VIP + MINI APP
# ============================================================

import os
import re
import json
import base64
import asyncio
import time
import sqlite3
import secrets
import hashlib
import hmac

from urllib.parse import unquote, parse_qsl

import aiohttp
from aiohttp import web

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    Update,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# AYARLAR
# ============================================================

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

DATABASE_PATH = os.environ.get(
    "DATABASE_PATH",
    "radar.db"
)

ADMIN_USER_ID = int(
    os.environ.get(
        "ADMIN_USER_ID",
        "0"
    )
)

ADMIN_CHAT_ID = int(
    os.environ.get(
        "ADMIN_CHAT_ID",
        "0"
    )
)

BOT_USERNAME = os.environ.get(
    "BOT_USERNAME",
    "YeniBirAirdropBot"
)

BASE_URL = os.environ.get(
    "BASE_URL",
    "https://kendi-verimiz.onrender.com"
)

MINI_APP_URL = (
    f"{BASE_URL}/miniapp"
)

VIP_DAYS = int(
    os.environ.get(
        "VIP_DAYS",
        "30"
    )
)

INVITE_EXPIRE_MINUTES = int(
    os.environ.get(
        "INVITE_EXPIRE_MINUTES",
        "60"
    )
)


# ============================================================
# ALARM
# ============================================================

COIN_ALARM_LIMIT = 100
PEOPLE_ALARM_LIMIT = 5


# ============================================================
# RAM
# ============================================================

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}

processed_messages = set()
processed_alarm_keys = set()


# ============================================================
# TELEGRAM KUYRUĞU
# ============================================================

telegram_queue = asyncio.PriorityQueue()

queue_seq = 0

http_session = None
client = None
bot_application = None


# ============================================================
# 429 RATE LIMIT KORUMASI
# ============================================================

send_lock = asyncio.Lock()

last_send_at = {}

retry_until = {}


# ============================================================
# SQLITE
# ============================================================

db = sqlite3.connect(
    DATABASE_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row


db.executescript(
    """
    CREATE TABLE IF NOT EXISTS radar_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        username TEXT,
        coins INTEGER,
        people INTEGER,
        joined INTEGER,
        rate REAL,
        view INTEGER,
        room TEXT,
        live TEXT,
        target_time INTEGER,
        detected_at INTEGER,
        source_message_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS alarm_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alarm_key TEXT UNIQUE,
        username TEXT,
        coins INTEGER,
        people INTEGER,
        created_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS vip_users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        expires_at INTEGER,
        created_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS invite_tokens (
        token TEXT PRIMARY KEY,
        created_at INTEGER,
        expires_at INTEGER,
        used_by INTEGER,
        used_at INTEGER
    );
    """
)

db.commit()


# ============================================================
# YARDIMCI
# ============================================================

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


# ============================================================
# TOKEN ÇIKAR
# ============================================================

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
            return unquote(
                m.group(1)
            )

    try:

        for entity in message.entities or []:

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


# ============================================================
# TOKEN DECODE
# ============================================================

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

        data = json.loads(
            decoded.decode(
                "utf-8",
                errors="ignore"
            ).strip()
        )

        return (
            data
            if isinstance(data, dict)
            else None
        )

    except Exception:
        return None


# ============================================================
# ROOM ID
# ============================================================

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

        try:

            encoded = m.group(1)

            padding = "=" * (
                -len(encoded) % 4
            )

            room = base64.urlsafe_b64decode(
                encoded + padding
            ).decode(
                "utf-8",
                errors="ignore"
            ).strip()

            if room.isdigit():
                return room

        except Exception:
            pass

    return None


# ============================================================
# USERNAME
# ============================================================

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


# ============================================================
# COIN
# ============================================================

def extract_coins(text, token_data=None):

    patterns = [
        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
        r'BOX\s*:\s*(\d+)\s*/',
        r'(\d+)\s*/\s*(\d+)',
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text or "",
            re.I
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

            if key in token_data:

                value = safe_int(
                    token_data.get(key),
                    -1
                )

                if value >= 0:
                    return value

    return 0


# ============================================================
# KİŞİ
# ============================================================

def extract_people_from_message(text):

    if not text:
        return 0

    patterns = [
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        r'(\d+)\s*/\s*(\d+)',
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text,
            re.I
        )

        if not m:
            continue

        if (
            "TÚI" in pattern
            or "TUI" in pattern
            or "BOX" in pattern
        ):
            return safe_int(
                m.group(1)
            )

        return safe_int(
            m.group(2)
        )

    return 0


# ============================================================
# JOINED
# ============================================================

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


# ============================================================
# VIEWERS
# ============================================================

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


# ============================================================
# RATE
# ============================================================

def extract_rate(
    text,
    token_data=None
):

    m = re.search(
        r'Rate\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        text or "",
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
                    token_data.get(key)
                )

    return 0


# ============================================================
# TİP
# ============================================================

def detect_type(
    text,
    token_data
):

    text_upper = (
        text or ""
    ).upper()

    if re.search(
        r'TÚI|TUI',
        text_upper
    ):
        return True

    if re.search(
        r'GOODY\s*BAG|REWARD\s*BAG',
        text_upper
    ):
        return True

    if re.search(
        r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
        text_upper
    ):
        return False

    if "🟡" in text:
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
            return True

        if value in [
            False,
            0,
            "0",
            "false",
            "False",
        ]:
            return False

    return None


# ============================================================
# HEDEF ZAMAN
# ============================================================

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
                    return value // 1000

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


# ============================================================
# PARSE
# ============================================================

def parse_source_message(event):

    text = (
        event.message.raw_text
        or ""
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
        return None

    username = None

    if token_data:

        for key in [
            "username",
            "user",
            "unique_id",
            "uniqueId",
        ]:

            if token_data.get(key):

                username = str(
                    token_data[key]
                )

                break

    username = (
        username
        or extract_username_from_text(text)
        or "bilinmiyor"
    )

    room = None

    if token_data:

        for key in [
            "room",
            "room_id",
            "roomid",
            "roomId",
            "roomID",
        ]:

            if token_data.get(key):

                room = str(
                    token_data[key]
                )

                break

    room = (
        room
        or extract_p_room(text)
        or f"msg:{event.message.id}"
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
                    token_data.get(key)
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
                    token_data.get(key)
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
                    token_data.get(key)
                )

                if viewers:
                    break

    live = ""

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

            if value and str(value).startswith(
                (
                    "http://",
                    "https://",
                )
            ):

                live = str(value)

                break

    if not live and username != "bilinmiyor":

        live = (
            f"https://www.tiktok.com/"
            f"@{username}/live"
        )

    now = int(
        time.time()
    )

    return {
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
        "coins": extract_coins(
            text,
            token_data
        ),
        "people": people,
        "joined": joined,
        "rate": rate,
        "view": viewers,
        "room": room,
        "live": live,
        "target_time": calculate_target_time(
            text,
            token_data
        ),
        "detected_at": now,
        "source_message_id": event.message.id,
    }


# ============================================================
# RADARA EKLE
# ============================================================

def add_to_radar(data):

    if not data:
        return False

    if not data.get("room"):
        return False

    target = (
        LIVE_GOODY_BAGS
        if data["type"] == "GOODY BAG"
        else LIVE_CHESTS
    )

    room = data["room"]

    if room in target:

        old_time = safe_int(
            target[room].get(
                "detected_at"
            )
        )

        if (
            int(time.time())
            - old_time
            < 5
        ):
            return False

    target[room] = data

    db.execute(
        """
        INSERT INTO radar_history (
            type,
            username,
            coins,
            people,
            joined,
            rate,
            view,
            room,
            live,
            target_time,
            detected_at,
            source_message_id
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data["type"],
            data["username"],
            data["coins"],
            data["people"],
            data["joined"],
            data["rate"],
            data["view"],
            data["room"],
            data["live"],
            data["target_time"],
            data["detected_at"],
            data["source_message_id"],
        )
    )

    db.commit()

    print(
        "[RADAR]",
        data["type"],
        "|",
        data["username"],
        "| coin",
        data["coins"],
        "| kişi",
        data["people"],
    )

    return True


# ============================================================
# ALARM
# ============================================================

def make_alarm(data):

    if (
        data["coins"]
        < COIN_ALARM_LIMIT
    ):
        return None

    if (
        data["people"]
        <= 0
    ):
        return None

    if (
        data["people"]
        > PEOPLE_ALARM_LIMIT
    ):
        return None

    key = (
        f'{data["type"]}:'
        f'{data["room"]}:'
        f'{data["coins"]}:'
        f'{data["people"]}'
    )

    if key in processed_alarm_keys:
        return None

    processed_alarm_keys.add(
        key
    )

    try:

        db.execute(
            """
            INSERT INTO alarm_history (
                alarm_key,
                username,
                coins,
                people,
                created_at
            )
            VALUES (?,?,?,?,?)
            """,
            (
                key,
                data["username"],
                data["coins"],
                data["people"],
                int(time.time()),
            )
        )

        db.commit()

    except sqlite3.IntegrityError:

        return None

    return key


# ============================================================
# TELEGRAM API
#
# EN ÖNEMLİ BÖLÜM:
# 1.25 saniye minimum aralık
# 429 retry_after desteği
# tek lock
# ============================================================

async def telegram_api(
    method,
    payload,
    target_chat=None
):

    global http_session

    if not http_session:
        return False

    chat_id = (
        target_chat
        if target_chat is not None
        else TARGET_CHAT_ID
    )

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    # Bütün Telegram gönderimlerini
    # tek tek sıraya sokuyoruz.
    async with send_lock:

        for attempt in range(
            1,
            13
        ):

            # Telegram daha önce 429 verdiyse
            # belirlenen zamana kadar bekle.
            wait = max(
                0,
                retry_until.get(
                    chat_id,
                    0
                )
                - time.monotonic()
            )

            if wait > 0:

                print(
                    f"[TELEGRAM] "
                    f"{chat_id} için "
                    f"{wait:.1f} sn bekleniyor..."
                )

                await asyncio.sleep(
                    wait
                )

            # Aynı chat'e çok hızlı
            # mesaj göndermeyi engelle.
            gap = (
                1.25
                - (
                    time.monotonic()
                    - last_send_at.get(
                        chat_id,
                        0
                    )
                )
            )

            if gap > 0:
                await asyncio.sleep(
                    gap
                )

            try:

                async with http_session.post(
                    url,
                    json={
                        **payload,
                        "chat_id": chat_id,
                    },
                    timeout=aiohttp.ClientTimeout(
                        total=45
                    ),
                ) as response:

                    body = await response.text()

                    # ------------------------------------------------
                    # BAŞARILI
                    # ------------------------------------------------

                    if response.status == 200:

                        last_send_at[
                            chat_id
                        ] = time.monotonic()

                        return True

                    # ------------------------------------------------
                    # TELEGRAM 429
                    # ------------------------------------------------

                    if response.status == 429:

                        try:

                            retry_after = safe_int(
                                json.loads(
                                    body
                                )
                                .get(
                                    "parameters",
                                    {}
                                )
                                .get(
                                    "retry_after",
                                    15
                                ),
                                15
                            )

                        except Exception:

                            retry_after = 15

                        retry_after = max(
                            retry_after,
                            1
                        )

                        retry_until[
                            chat_id
                        ] = (
                            time.monotonic()
                            + retry_after
                        )

                        print(
                            f"[TELEGRAM 429] "
                            f"retry_after="
                            f"{retry_after} sn"
                        )

                        continue

                    # ------------------------------------------------
                    # TELEGRAM SUNUCU HATALARI
                    # ------------------------------------------------

                    if response.status in (
                        500,
                        502,
                        503,
                        504,
                    ):

                        wait_seconds = min(
                            5 * attempt,
                            30
                        )

                        print(
                            "[TELEGRAM]",
                            response.status,
                            f"{wait_seconds} sn bekleniyor"
                        )

                        await asyncio.sleep(
                            wait_seconds
                        )

                        continue

                    # ------------------------------------------------
                    # DİĞER HATA
                    # ------------------------------------------------

                    print(
                        "[TELEGRAM HATA]",
                        response.status,
                        body
                    )

                    return False

            except asyncio.CancelledError:

                raise

            except Exception as e:

                print(
                    "[TELEGRAM]",
                    repr(e)
                )

                await asyncio.sleep(
                    min(
                        5 * attempt,
                        30
                    )
                )

        print(
            "[TELEGRAM] "
            "Gönderim yeniden deneme limiti doldu."
        )

        return False


# ============================================================
# NORMAL TELEGRAM MESAJI
# ============================================================

async def send_radar(data):

    title = (
        "🟪 GOODY BAG"
        if data["type"] == "GOODY BAG"
        else "🟨 HAZİNE SANDIĞI"
    )

    text = (
        f"{title}\n\n"
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
            f'\n🔴 <a href="'
            f'{data["live"]}'
            f'">TIKTOK CANLI YAYIN</a>'
        )

    return await telegram_api(
        "sendMessage",
        {
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        TARGET_CHAT_ID
    )


# ============================================================
# ALARM MESAJI
# ============================================================

async def send_alarm(data):

    if data["type"] == "GOODY BAG":

        title = (
            "🟣 MOR ZARF / GOODY BAG"
        )

    else:

        title = (
            "🚨 HAZİNE SANDIĞI ALARMI"
        )

    text = (
        f"{title}\n\n"
        f"👤 {data['username']}\n"
        f"🪙 Coin: {data['coins']}\n"
        f"👥 Kişi: {data['people']}\n"
        f"🙋 Katılan: {data['joined']}\n"
        f"📈 Oran: {data['rate']}\n"
        f"👀 İzlenme: {data['view']}\n\n"
        f"⚡ Hazine yüksek, "
        f"dağıtılan kişi düşük!"
    )

    if data.get("live"):

        text += (
            f'\n\n🔴 <a href="'
            f'{data["live"]}'
            f'">CANLI YAYINA GİT</a>'
        )

    return await telegram_api(
        "sendMessage",
        {
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        TARGET_CHAT_ID
    )


# ============================================================
# ADMİNE ÖZEL MESAJ
# ============================================================

async def send_admin(text):

    if not ADMIN_CHAT_ID:
        return False

    return await telegram_api(
        "sendMessage",
        {
            "text": text,
            "disable_web_page_preview": True,
        },
        ADMIN_CHAT_ID
    )


# ============================================================
# KUYRUĞA EKLE
# ============================================================

async def enqueue(
    kind,
    data,
    priority=10
):

    global queue_seq

    queue_seq += 1

    await telegram_queue.put(
        (
            priority,
            queue_seq,
            kind,
            data,
        )
    )


# ============================================================
# TELEGRAM SENDER
#
# priority 0 = ALARM
# priority 10 = NORMAL
# ============================================================

async def sender():

    while True:

        priority, seq, kind, data = (
            await telegram_queue.get()
        )

        try:

            if kind == "alarm":

                await send_alarm(
                    data
                )

            else:

                await send_radar(
                    data
                )

        except asyncio.CancelledError:

            raise

        except Exception as e:

            print(
                "[KUYRUK HATASI]",
                repr(e)
            )

        finally:

            telegram_queue.task_done()


# ============================================================
# VIP DAVET
# ============================================================

def create_invite():

    token = secrets.token_urlsafe(
        18
    )

    now = int(
        time.time()
    )

    expires = (
        now
        + INVITE_EXPIRE_MINUTES * 60
    )

    db.execute(
        """
        INSERT INTO invite_tokens (
            token,
            created_at,
            expires_at,
            used_by,
            used_at
        )
        VALUES (?,?,?,?,?)
        """,
        (
            token,
            now,
            expires,
            None,
            None,
        )
    )

    db.commit()

    return token, expires


# ============================================================
# DAVET KULLAN
# ============================================================

def use_invite(
    token,
    user
):

    now = int(
        time.time()
    )

    row = db.execute(
        """
        SELECT *
        FROM invite_tokens
        WHERE token=?
        """,
        (
            token,
        )
    ).fetchone()

    if not row:

        return (
            False,
            "Geçersiz davet."
        )

    if row["used_by"]:

        return (
            False,
            "Bu davet daha önce kullanılmış."
        )

    if (
        row["expires_at"]
        < now
    ):

        return (
            False,
            "Davetin süresi dolmuş."
        )

    expires = (
        now
        + VIP_DAYS * 86400
    )

    db.execute(
        """
        UPDATE invite_tokens
        SET used_by=?,
            used_at=?
        WHERE token=?
        """,
        (
            user.id,
            now,
            token,
        )
    )

    db.execute(
        """
        INSERT INTO vip_users (
            user_id,
            username,
            first_name,
            expires_at,
            created_at
        )
        VALUES (?,?,?,?,?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            expires_at=excluded.expires_at
        """,
        (
            user.id,
            user.username or "",
            user.first_name or "",
            expires,
            now,
        )
    )

    db.commit()

    return (
        True,
        expires
    )


# ============================================================
# VIP KONTROL
# ============================================================

def is_vip(user_id):

    row = db.execute(
        """
        SELECT expires_at
        FROM vip_users
        WHERE user_id=?
        """,
        (
            safe_int(user_id),
        )
    ).fetchone()

    if not row:
        return False

    return (
        row["expires_at"]
        > int(time.time())
    )


# ============================================================
# TELEGRAM MINI APP INITDATA DOĞRULA
# ============================================================

def validate_telegram_init_data(
    init_data
):

    if not init_data:
        return None

    try:

        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = pairs.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={pairs[key]}"
            for key in sorted(pairs)
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        auth_date = safe_int(
            pairs.get(
                "auth_date"
            )
        )

        if (
            auth_date
            and int(time.time())
            - auth_date
            > 86400
        ):
            return None

        user_json = pairs.get(
            "user"
        )

        if not user_json:
            return None

        return json.loads(
            user_json
        )

    except Exception:

        return None


# ============================================================
# /START
# ============================================================

async def start_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    # Zaten VIP
    if is_vip(user.id):

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏆 RADARI AÇ",
                        web_app=WebAppInfo(
                            url=MINI_APP_URL
                        )
                    )
                ]
            ]
        )

        await message.reply_text(
            "✅ VIP erişimin aktif.",
            reply_markup=keyboard
        )

        return

    args = (
        context.args
        or []
    )

    # DAVET
    if (
        args
        and args[0].startswith(
            "invite_"
        )
    ):

        token = args[0][7:]

        ok, info = use_invite(
            token,
            user
        )

        if not ok:

            await message.reply_text(
                f"❌ {info}"
            )

            return

        await message.reply_text(
            f"✅ VIP erişim açıldı.\n"
            f"⏳ Süre: {VIP_DAYS} gün"
        )

        await send_admin(
            "🆕 YENİ VIP\n\n"
            f"👤 @{user.username or 'yok'}\n"
            f"🆔 {user.id}\n"
            f"👤 {user.first_name or ''}\n"
            f"⏳ {VIP_DAYS} gün"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏆 RADARI AÇ",
                        web_app=WebAppInfo(
                            url=MINI_APP_URL
                        )
                    )
                ]
            ]
        )

        await message.reply_text(
            "Hazır. Aşağıdaki "
            "butondan radarı açabilirsin.",
            reply_markup=keyboard
        )

        return

    await message.reply_text(
        "🔒 Bu bot davet/VIP sistemiyle çalışıyor.\n\n"
        "Geçerli davet bağlantın varsa "
        "o bağlantı üzerinden /start yap."
    )


# ============================================================
# ADMİN KONTROL
# ============================================================

def admin_only(update):

    user = update.effective_user

    if not user:
        return False

    return (
        ADMIN_USER_ID
        and user.id
        == ADMIN_USER_ID
    )


# ============================================================
# /DAVET
# ============================================================

async def davet_cmd(
    update,
    context
):

    if not admin_only(update):
        return

    token, expires = create_invite()

    link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=invite_{token}"
    )

    await update.effective_message.reply_text(
        "🎟 DAVET LİNKİ\n\n"
        f"{link}\n\n"
        f"⏳ Geçerlilik: "
        f"{INVITE_EXPIRE_MINUTES} dakika\n"
        "♻️ Tek kullanımlık."
    )


# ============================================================
# /UYELER
# ============================================================

async def uyeler_cmd(
    update,
    context
):

    if not admin_only(update):
        return

    rows = db.execute(
        """
        SELECT *
        FROM vip_users
        ORDER BY expires_at DESC
        """
    ).fetchall()

    if not rows:

        await update.effective_message.reply_text(
            "VIP üye yok."
        )

        return

    lines = [
        "👥 VIP ÜYELER\n"
    ]

    now = int(
        time.time()
    )

    for row in rows:

        status = (
            "✅"
            if row["expires_at"] > now
            else "❌"
        )

        date = time.strftime(
            "%Y-%m-%d",
            time.localtime(
                row["expires_at"]
            )
        )

        lines.append(
            f'{status} '
            f'{row["user_id"]} '
            f'@{row["username"] or "yok"} '
            f'• {date}'
        )

    await update.effective_message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /SILVIP
# ============================================================

async def silvip_cmd(
    update,
    context
):

    if not admin_only(update):
        return

    if not context.args:

        await update.effective_message.reply_text(
            "Kullanım:\n"
            "/silvip 123456789"
        )

        return

    user_id = safe_int(
        context.args[0],
        0
    )

    if not user_id:

        await update.effective_message.reply_text(
            "❌ ID geçersiz."
        )

        return

    db.execute(
        """
        DELETE FROM vip_users
        WHERE user_id=?
        """,
        (
            user_id,
        )
    )

    db.commit()

    await update.effective_message.reply_text(
        "🗑 VIP erişim silindi."
    )


# ============================================================
# /ID
# ============================================================

async def id_cmd(
    update,
    context
):

    if (
        update.effective_message
        and update.effective_user
    ):

        await update.effective_message.reply_text(
            f"🆔 Telegram ID: "
            f"{update.effective_user.id}"
        )


# ============================================================
# MINI APP HTML
# ============================================================

MINI_APP_HTML = r"""
<!doctype html>
<html lang="tr">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>ÖDÜL AVCISI</title>

<style>

*{
    box-sizing:border-box
}

body{
    margin:0;
    padding:6px;
    background:#070910;
    color:#fff;
    font-family:Arial,sans-serif
}

.wrap{
    max-width:1100px;
    margin:auto
}

.head{
    text-align:center;
    padding:5px 3px 8px
}

.title{
    font-size:clamp(
        25px,
        7vw,
        44px
    );
    font-weight:1000;
    text-shadow:
        0 0 6px #fff,
        0 0 18px #9d51ff,
        0 0 35px #6425ff
}

.sub{
    font-size:8px;
    color:#9da6bb;
    font-weight:900;
    margin-top:5px
}

.status{
    display:inline-block;
    margin-top:6px;
    padding:5px 9px;
    border-radius:999px;
    background:#111522;
    border:1px solid #30394d;
    color:#69ff9a;
    font-size:7px;
    font-weight:900
}

.grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:5px
}

.box{
    min-width:0;
    padding:6px;
    border-radius:12px;
    background:#0d111c;
    border:1px solid #293246
}

.g{
    border-color:#9d51ff77
}

.c{
    border-color:#f1c84b66
}

.pnrow{
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding:1px 1px 6px
}

.pn{
    font-size:9px;
    font-weight:1000
}

.g .pn{
    color:#d8adff
}

.c .pn{
    color:#ffe47b
}

.cnt{
    font-size:6px;
    background:#fff1;
    border-radius:99px;
    padding:3px 5px
}

.card{
    margin-bottom:4px;
    padding:6px;
    border-radius:9px;
    background:
        linear-gradient(
            145deg,
            #171d2d,
            #0b0f18
        );
    border:1px solid #293246
}

.g .card{
    border-left:3px solid #9d51ff
}

.c .card{
    border-left:3px solid #f1c84b
}

.ur{
    display:flex;
    justify-content:space-between;
    gap:4px;
    align-items:center;
    margin-bottom:5px
}

.user{
    font-size:8px;
    font-weight:1000;
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap
}

.badge{
    padding:3px 5px;
    border-radius:5px;
    font-size:6px;
    font-weight:1000;
    background:#e52c59;
    flex-shrink:0
}

.info{
    background:#ffffff09;
    border-radius:5px;
    padding:4px;
    font-size:5px;
    color:#818da1
}

.info b{
    display:block;
    color:#fff;
    font-size:8px;
    margin-top:1px
}

.ig{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:3px
}

.go{
    display:block;
    text-align:center;
    color:#fff;
    text-decoration:none;
    background:#df1650;
    padding:5px;
    margin-top:5px;
    border-radius:6px;
    font-size:6px;
    font-weight:1000
}

.empty{
    text-align:center;
    padding:16px;
    color:#657087;
    font-size:7px
}

@keyframes newCard{

    0%{
        opacity:.25;
        transform:
            translateY(-8px)
            scale(.97)
    }

    50%{
        box-shadow:
            0 0 25px
            rgba(157,81,255,.55)
    }

    100%{
        opacity:1;
        transform:none
    }

}

.card.new-card{
    animation:
        newCard
        .8s
        ease-out
}

@media(max-width:700px){

    .grid{
        gap:4px
    }

    .box{
        padding:5px
    }

    .pn{
        font-size:8px
    }

    .user{
        font-size:7px
    }

    .info{
        font-size:4px
    }

    .info b{
        font-size:7px
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
🟡 BAĞLANIYOR...
</div>

</div>


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

</div>


<script>

let firstLoad = true;

let seenGoody =
    new Set();

let seenChest =
    new Set();

let newGoody =
    new Set();

let newChest =
    new Set();


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


function itemKey(item){

    return String(
        item.source_message_id
        ??
        item.room
        ??
        (
            String(
                item.username
                ?? ""
            )
            +
            "_"
            +
            String(
                item.detected_at
                ?? ""
            )
        )
    );

}


function itemTime(item){

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


function latestFive(items){

    if(
        !Array.isArray(items)
    ){
        return [];
    }

    return [
        ...items
    ]
    .sort(
        (a,b)=>
            itemTime(b)
            -
            itemTime(a)
    )
    .slice(
        0,
        5
    );

}


function renderList(
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

    const arr =
        latestFive(items);

    counter.textContent =
        arr.length;

    if(!arr.length){

        container.innerHTML =
            '<div class="empty">⚡ Veri yok.</div>';

        return;

    }


    const seen =
        type === "GOODY"
        ? seenGoody
        : seenChest;

    const fresh =
        type === "GOODY"
        ? newGoody
        : newChest;


    arr.forEach(
        item => {

            const key =
                itemKey(item);

            if(firstLoad){

                seen.add(
                    key
                );

            }else{

                if(
                    !seen.has(key)
                ){

                    seen.add(
                        key
                    );

                    fresh.add(
                        key
                    );

                }

            }

        }
    );


    container.innerHTML =
        arr.map(
            item => {

                const key =
                    itemKey(item);

                const isNew =
                    fresh.has(key);

                return `

<div class="card ${
    isNew
    ? "new-card"
    : ""
}">

<div class="ur">

<div class="user">
${icon}
${escapeHtml(
    item.username
)}
</div>

${
    isNew
    ?
    '<div class="badge">⚡ YENİ</div>'
    :
    ''
}

</div>


<div class="ig">

<div class="info">
🪙
<b>
${escapeHtml(
    item.coins
)}
</b>
</div>


<div class="info">
👥
<b>
${escapeHtml(
    item.people
)}
</b>
</div>


<div class="info">
🙋
<b>
${escapeHtml(
    item.joined
)}
</b>
</div>


<div class="info">
📈
<b>
${escapeHtml(
    item.rate
)}
</b>
</div>


<div class="info">
👀
<b>
${escapeHtml(
    item.view
)}
</b>
</div>


<div class="info">
🏠
<b>
${escapeHtml(
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
    class="go"
    href="${escapeHtml(
        item.live
    )}"
    target="_blank"
    rel="noopener"
>
🔴 CANLIYA GİT
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


async function loadRadar(){

    try{

        const initData =
            window.Telegram
            ?.WebApp
            ?.initData
            ||
            "";

        let url;

        if(initData){

            url =
                "/api/miniapp-data"
                +
                "?initData="
                +
                encodeURIComponent(
                    initData
                );

        }else{

            url =
                "/api/all";

        }


        const response =
            await fetch(
                url,
                {
                    cache:
                        "no-store"
                }
            );


        if(
            !response.ok
        ){

            throw new Error(
                "HTTP "
                +
                response.status
            );

        }


        const data =
            await response.json();


        renderList(
            data.goody_bags,
            "gs",
            "gc",
            "🟪",
            "GOODY"
        );


        renderList(
            data.chests,
            "cs",
            "cc",
            "🟨",
            "CHEST"
        );


        const status =
            document.getElementById(
                "status"
            );


        status.textContent =
            "🟢 RADAR AKTİF • CANLI VERİ";


        if(!firstLoad){

            setTimeout(
                () => {

                    newGoody.clear();
                    newChest.clear();

                    loadRadar();

                },
                4000
            );

        }


        firstLoad = false;


    }catch(error){

        console.error(
            "Radar hatası:",
            error
        );


        const status =
            document.getElementById(
                "status"
            );

        status.textContent =
            "🔴 ERİŞİM / VERİ HATASI";

    }

}


if(
    window.Telegram
    ?.WebApp
){

    Telegram.WebApp.ready();

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


# ============================================================
# RADAR HTML
# ============================================================

RADAR_HTML = MINI_APP_HTML


# ============================================================
# RADAR SAYFASI
# ============================================================

async def radar_page(request):

    return web.Response(
        text=RADAR_HTML,
        content_type="text/html",
        charset="utf-8"
    )


# ============================================================
# MINI APP
# ============================================================

async def miniapp_page(request):

    init_data = (
        request.query.get(
            "initData",
            ""
        )
    )

    if not init_data:

        return web.Response(
            status=401,
            text=(
                "Telegram Mini App gerekli."
            )
        )

    user = validate_telegram_init_data(
        init_data
    )

    if not user:

        return web.Response(
            status=403,
            text=(
                "Telegram doğrulaması başarısız."
            )
        )

    if not is_vip(
        user.get("id")
    ):

        return web.Response(
            status=403,
            text=(
                "VIP erişim gerekli."
            )
        )

    return web.Response(
        text=MINI_APP_HTML,
        content_type="text/html",
        charset="utf-8"
    )


# ============================================================
# API
# ============================================================

async def api_all(request):

    return web.json_response(
        {
            "status": "online",
            "server_time": int(
                time.time()
            ),
            "chests": list(
                LIVE_CHESTS.values()
            ),
            "goody_bags": list(
                LIVE_GOODY_BAGS.values()
            ),
        }
    )


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

    return web.json_response(
        {
            "status": "online",
            "chests": len(
                LIVE_CHESTS
            ),
            "goody_bags": len(
                LIVE_GOODY_BAGS
            ),
            "server_time": int(
                time.time()
            ),
            "telegram_queue": (
                telegram_queue.qsize()
            ),
        }
    )


# ============================================================
# MINI APP API
# ============================================================

async def api_miniapp_data(request):

    init_data = (
        request.query.get(
            "initData",
            ""
        )
    )

    user = validate_telegram_init_data(
        init_data
    )

    if not user:

        return web.json_response(
            {
                "error":
                    "Telegram doğrulaması başarısız"
            },
            status=403
        )

    if not is_vip(
        user.get("id")
    ):

        return web.json_response(
            {
                "error":
                    "VIP erişim gerekli"
            },
            status=403
        )

    return await api_all(
        request
    )


# ============================================================
# CORS
# ============================================================

@web.middleware
async def cors(
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


# ============================================================
# HTTP SERVER
# ============================================================

async def start_http():

    app = web.Application(
        middlewares=[
            cors
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
        "/miniapp",
        miniapp_page
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
        api_goody_bags
    )

    app.router.add_get(
        "/api/status",
        api_status
    )

    app.router.add_get(
        "/api/miniapp-data",
        api_miniapp_data
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
        "[HTTP] Sunucu başladı:",
        PORT
    )

    print(
        "[HTTP] Radar aktif."
    )

    print(
        "[HTTP] Mini App aktif."
    )


# ============================================================
# TELEGRAM KAYNAK DİNLEYİCİ
# ============================================================

async def listener(event):

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

        if (
            len(processed_messages)
            > 50000
        ):

            processed_messages.clear()

        data = parse_source_message(
            event
        )

        if not data:
            return

        if add_to_radar(
            data
        ):

            # Alarm varsa priority 0
            alarm_key = make_alarm(
                data
            )

            if alarm_key:

                print(
                    "[ALARM]",
                    data["username"],
                    data["coins"],
                    data["people"]
                )

                await enqueue(
                    "alarm",
                    data,
                    0
                )

            # Normal radar mesajı
            await enqueue(
                "normal",
                data,
                10
            )

    except Exception as e:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(e)
        )


# ============================================================
# BOTU BAŞLAT
# ============================================================

async def start_bot():

    global bot_application

    bot_application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    bot_application.add_handler(
        CommandHandler(
            "start",
            start_cmd
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "davet",
            davet_cmd
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "uyeler",
            uyeler_cmd
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "silvip",
            silvip_cmd
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "id",
            id_cmd
        )
    )

    await bot_application.initialize()

    await bot_application.start()

    if bot_application.updater:

        await bot_application.updater.start_polling()

    print(
        "[BOT] Telegram bot başladı."
    )


# ============================================================
# ANA
# ============================================================

async def main():

    global http_session
    global client

    print(
        "=" * 70
    )

    print(
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
    )

    print(
        "=" * 70
    )

    # HTTP
    http_session = aiohttp.ClientSession()

    # Web server
    await start_http()

    # Bot
    await start_bot()

    # TEK Telegram sender
    asyncio.create_task(
        sender()
    )

    # Telethon
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

    # Kaynak mesajları dinle
    client.add_event_handler(
        listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        )
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
        "[HAZIR] VIP sistemi aktif."
    )

    print(
        "[HAZIR] Telegram 429 koruması aktif."
    )

    print(
        "[HAZIR] Mesajlar minimum "
        "1.25 saniye aralıkla gönderilecek."
    )

    try:

        await client.run_until_disconnected()

    finally:

        if bot_application:

            try:

                if bot_application.updater:

                    await bot_application.updater.stop()

                await bot_application.stop()

                await bot_application.shutdown()

            except Exception as e:

                print(
                    "[BOT KAPATMA]",
                    repr(e)
                )

        if http_session:

            await http_session.close()

        try:
            db.close()
        except Exception:
            pass

        print(
            "[DURDU] Sistem kapandı."
        )


# ============================================================
# ÇALIŞTIR
# ============================================================

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
