# ============================================================
# main.py
# ÖDÜL AVCISI
#
# GOODY BAG + HAZİNE SANDIĞI
#
# EKLENENLER:
# - Akıllı alarm / yüksek coin + düşük kişi
# - Aynı olayın tekrar bildirilmesini engelleme
# - Kişisel alarm sistemi
# - Yayıncı takip sistemi
# - Sessize alma sistemi
# - VIP süre uzatma
# - VIP bilgi
# - VIP silinince kullanıcıya bildirim
# - Telegram 429 koruması
# - VIP / davet sistemi
# - Telegram Mini App
# - Mini App initData doğrulaması
# - Arama
# - Filtreler
# - 2 sütun mobil görünüm
# - Büyük yazılar
# - ⚡ YENİ sistemi
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
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# ENV
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ["STRING_SESSION"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

PORT = int(os.environ.get("PORT", "10000"))

BASE_URL = os.environ.get(
    "BASE_URL",
    "https://kendi-verimiz.onrender.com"
).rstrip("/")

BOT_USERNAME = os.environ.get(
    "BOT_USERNAME",
    "YeniBirAirdropBot"
)

ADMIN_USER_ID = int(
    os.environ.get("ADMIN_USER_ID", "0")
)

ADMIN_CHAT_ID = int(
    os.environ.get(
        "ADMIN_CHAT_ID",
        str(ADMIN_USER_ID or 0)
    )
)

VIP_DAYS = int(
    os.environ.get("VIP_DAYS", "30")
)

TARGET_CHAT_ID = -1004421946217


# ============================================================
# KAYNAKLAR
# ============================================================

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445,
]


# ============================================================
# ALARM AYARLARI
# ============================================================

# Genel akıllı alarm:
# Coin bu değere eşit/yüksek
# VE kişi bu değere eşit/düşük ise alarm.
COIN_ALARM_LIMIT = 100
PEOPLE_ALARM_LIMIT = 5

# Aynı oda için tekrar alarm gönderme süresi.
DUPLICATE_COOLDOWN = 60


# ============================================================
# RAM
# ============================================================

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}

processed_messages = set()

# Aynı olay için son Telegram bildirimi
last_event_notification = {}

# Kullanıcıya özel ayarlar RAM'de hızlı erişim için
USER_SETTINGS_CACHE = {}


# ============================================================
# GLOBAL
# ============================================================

http_session = None

telegram_queue = asyncio.PriorityQueue()

telegram_send_lock = asyncio.Lock()

last_telegram_send = 0.0
telegram_retry_until = 0.0

queue_counter = 0


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "radar.db"


def db():
    return sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )


def init_db():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS vip_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invite_tokens (
            token TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER DEFAULT 0,
            used_by INTEGER DEFAULT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS radar_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT,
            type TEXT,
            username TEXT,
            coins INTEGER,
            people INTEGER,
            joined INTEGER,
            rate REAL,
            view INTEGER,
            live TEXT,
            detected_at INTEGER
        )
    """)

    # Kullanıcı ayarları
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            alarm_coins INTEGER DEFAULT 0,
            alarm_people INTEGER DEFAULT 0,
            mute_goody INTEGER DEFAULT 0,
            mute_chest INTEGER DEFAULT 0
        )
    """)

    # Takip edilen yayıncılar
    cur.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, username)
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# VIP
# ============================================================

def add_vip(
    user_id,
    username="",
    first_name=""
):

    now = int(time.time())
    expires = now + VIP_DAYS * 86400

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO vip_users
        (user_id, username, first_name, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            expires_at=excluded.expires_at
    """, (
        user_id,
        username or "",
        first_name or "",
        expires,
        now
    ))

    conn.commit()
    conn.close()

    return expires


def get_vip(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, username, first_name, expires_at
        FROM vip_users
        WHERE user_id=?
    """, (user_id,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    if int(row[3]) <= int(time.time()):

        conn = db()

        conn.execute(
            "DELETE FROM vip_users WHERE user_id=?",
            (user_id,)
        )

        conn.commit()
        conn.close()

        return None

    return {
        "user_id": row[0],
        "username": row[1],
        "first_name": row[2],
        "expires_at": row[3],
    }


def remove_vip(user_id):

    conn = db()

    cur = conn.cursor()

    cur.execute(
        "DELETE FROM vip_users WHERE user_id=?",
        (user_id,)
    )

    removed = cur.rowcount > 0

    conn.commit()
    conn.close()

    return removed


def extend_vip(user_id, days):

    days = safe_int(days)

    if days <= 0:
        return None

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT expires_at
        FROM vip_users
        WHERE user_id=?
    """, (user_id,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    now = int(time.time())

    old_expire = int(row[0])

    base = max(
        now,
        old_expire
    )

    new_expire = (
        base +
        days * 86400
    )

    cur.execute("""
        UPDATE vip_users
        SET expires_at=?
        WHERE user_id=?
    """, (
        new_expire,
        user_id
    ))

    conn.commit()
    conn.close()

    return new_expire


def list_vips():

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, username, first_name, expires_at
        FROM vip_users
        WHERE expires_at > ?
        ORDER BY expires_at ASC
    """, (int(time.time()),))

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# DAVET
# ============================================================

def create_invite():

    token = secrets.token_urlsafe(24)

    now = int(time.time())
    expires = now + 24 * 3600

    conn = db()

    conn.execute("""
        INSERT INTO invite_tokens
        (token, created_at, expires_at, used)
        VALUES (?, ?, ?, 0)
    """, (
        token,
        now,
        expires
    ))

    conn.commit()
    conn.close()

    return token


def use_invite(token, user):

    if not token:
        return False

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT token, expires_at, used
        FROM invite_tokens
        WHERE token=?
    """, (token,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return False

    if row[2]:
        conn.close()
        return False

    if int(row[1]) <= int(time.time()):
        conn.close()
        return False

    cur.execute("""
        UPDATE invite_tokens
        SET used=1, used_by=?
        WHERE token=? AND used=0
    """, (
        user.id,
        token
    ))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False

    now = int(time.time())
    expires = now + VIP_DAYS * 86400

    cur.execute("""
        INSERT INTO vip_users
        (user_id, username, first_name, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            expires_at=excluded.expires_at
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        expires,
        now
    ))

    conn.commit()
    conn.close()

    return True


# ============================================================
# YARDIMCI
# ============================================================

def safe_int(value, default=0):

    try:

        if value is None:
            return default

        return int(float(value))

    except Exception:

        return default


def safe_float(value, default=0):

    try:
        return float(value)

    except Exception:

        return default


def normalize_username(username):

    if not username:
        return ""

    username = str(username).strip()

    if username.startswith("@"):
        username = username[1:]

    return username.lower()


# ============================================================
# KULLANICI AYARLARI
# ============================================================

def get_user_settings(user_id):

    if user_id in USER_SETTINGS_CACHE:
        return USER_SETTINGS_CACHE[user_id]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            alarm_coins,
            alarm_people,
            mute_goody,
            mute_chest
        FROM user_settings
        WHERE user_id=?
    """, (user_id,))

    row = cur.fetchone()
    conn.close()

    if row:

        result = {
            "alarm_coins": safe_int(row[0]),
            "alarm_people": safe_int(row[1]),
            "mute_goody": bool(row[2]),
            "mute_chest": bool(row[3]),
        }

    else:

        result = {
            "alarm_coins": 0,
            "alarm_people": 0,
            "mute_goody": False,
            "mute_chest": False,
        }

    USER_SETTINGS_CACHE[user_id] = result

    return result


def save_user_settings(
    user_id,
    **kwargs
):

    current = get_user_settings(
        user_id
    )

    current.update(kwargs)

    conn = db()

    conn.execute("""
        INSERT INTO user_settings
        (
            user_id,
            alarm_coins,
            alarm_people,
            mute_goody,
            mute_chest
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            alarm_coins=excluded.alarm_coins,
            alarm_people=excluded.alarm_people,
            mute_goody=excluded.mute_goody,
            mute_chest=excluded.mute_chest
    """, (
        user_id,
        current["alarm_coins"],
        current["alarm_people"],
        int(current["mute_goody"]),
        int(current["mute_chest"]),
    ))

    conn.commit()
    conn.close()

    USER_SETTINGS_CACHE[user_id] = current

    return current


# ============================================================
# TAKİP
# ============================================================

def add_follow(
    user_id,
    username
):

    username = normalize_username(
        username
    )

    if not username:
        return False

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO follows
        (user_id, username, created_at)
        VALUES (?, ?, ?)
    """, (
        user_id,
        username,
        int(time.time())
    ))

    changed = cur.rowcount > 0

    conn.commit()
    conn.close()

    return changed


def remove_follow(
    user_id,
    username
):

    username = normalize_username(
        username
    )

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        DELETE FROM follows
        WHERE user_id=? AND username=?
    """, (
        user_id,
        username
    ))

    changed = cur.rowcount > 0

    conn.commit()
    conn.close()

    return changed


def get_follows(user_id):

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT username
        FROM follows
        WHERE user_id=?
        ORDER BY username ASC
    """, (user_id,))

    rows = [
        row[0]
        for row in cur.fetchall()
    ]

    conn.close()

    return rows


def get_followers(username):

    username = normalize_username(
        username
    )

    if not username:
        return []

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT user_id
        FROM follows
        WHERE username=?
    """, (username,))

    rows = [
        row[0]
        for row in cur.fetchall()
    ]

    conn.close()

    return rows


# ============================================================
# EVENT TOKEN
# ============================================================

def extract_token_from_event(event):

    try:
        text = event.message.raw_text or ""
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

        for entity in event.message.entities or []:

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

    try:

        for entity, _ in event.message.get_entities_text():

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

        return data if isinstance(
            data,
            dict
        ) else None

    except Exception:

        return None


# ============================================================
# ROOM
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

    if text:

        patterns = [
            r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
            r'BOX\s*:\s*(\d+)\s*/',
            r'(\d+)\s*/\s*(\d+)',
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

    if token_data:

        for key in [
            "coins",
            "coin",
            "gem",
            "diamond",
            "amount"
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
# PEOPLE
# ============================================================

def extract_people(text):

    if not text:
        return 0

    patterns = [
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
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

    m = re.search(
        r'(\d+)\s*/\s*(\d+)',
        text
    )

    if m:
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

    return (
        safe_int(m.group(1))
        if m else 0
    )


# ============================================================
# RATE
# ============================================================

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
            "rate"
        ]:

            if key in token_data:
                return safe_float(
                    token_data.get(key)
                )

    return 0


# ============================================================
# TYPE
# ============================================================

def detect_type(text, token_data):

    upper = (
        text or ""
    ).upper()

    if re.search(
        r'TÚI|TUI',
        upper
    ):
        return True

    if re.search(
        r'GOODY\s*BAG|REWARD\s*BAG',
        upper
    ):
        return True

    if re.search(
        r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
        upper
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
            "True"
        ]:
            return True

        if value in [
            False,
            0,
            "0",
            "false",
            "False"
        ]:
            return False

    return None


# ============================================================
# TARGET TIME
# ============================================================

def calculate_target_time(
    text,
    token_data=None
):

    now = int(time.time())

    if token_data:

        for key in [
            "time",
            "target_time",
            "end_time",
            "endTime"
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

                if 0 < value < 86400:
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
# LIVE LINK
# ============================================================

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
            "url"
        ]:

            value = token_data.get(
                key
            )

            if value and str(value).startswith(
                ("http://", "https://")
            ):
                return str(value)

    if username:

        return (
            f"https://www.tiktok.com/"
            f"@{username}/live"
        )

    return ""


# ============================================================
# PARSER
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
            "uniqueId"
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
            "roomID"
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

    coins = extract_coins(
        text,
        token_data
    )

    people = extract_people(
        text
    )

    if not people and token_data:

        for key in [
            "people",
            "person",
            "count",
            "capacity"
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
            "joined_count"
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
            "viewers"
        ]:

            if key in token_data:

                viewers = safe_int(
                    token_data.get(key)
                )

                if viewers:
                    break

    now = int(time.time())

    return {

        "type":
            "GOODY BAG"
            if is_goody
            else "CHEST",

        "box_name":
            "Goody Bag"
            if is_goody
            else "Hazine Sandığı",

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
            get_live_link(
                username,
                room,
                token_data
            ),

        "target_time":
            calculate_target_time(
                text,
                token_data
            ),

        "detected_at":
            now,

        "source_message_id":
            event.message.id,
    }


# ============================================================
# AKILLI ALARM
# ============================================================

def is_smart_alarm(data):

    coins = safe_int(
        data.get("coins")
    )

    people = safe_int(
        data.get("people")
    )

    return (
        coins >= COIN_ALARM_LIMIT
        and
        people > 0
        and
        people <= PEOPLE_ALARM_LIMIT
    )


def alarm_reason(data):

    return (
        f"🪙 {data.get('coins', 0)} coin"
        f" / "
        f"👥 {data.get('people', 0)} kişi"
    )


# ============================================================
# RADAR
# ============================================================

def add_to_radar(data):

    target = (
        LIVE_GOODY_BAGS
        if data["type"] == "GOODY BAG"
        else LIVE_CHESTS
    )

    room = data.get("room")

    if not room:
        return False

    if room in target:

        old_time = safe_int(
            target[room].get(
                "detected_at"
            )
        )

        if int(time.time()) - old_time < 5:
            return False

    target[room] = data

    try:

        conn = db()

        conn.execute("""
            INSERT INTO radar_history
            (
                room,
                type,
                username,
                coins,
                people,
                joined,
                rate,
                view,
                live,
                detected_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["room"],
            data["type"],
            data["username"],
            data["coins"],
            data["people"],
            data["joined"],
            data["rate"],
            data["view"],
            data["live"],
            data["detected_at"]
        ))

        conn.commit()
        conn.close()

    except Exception as e:

        print(
            "[DB HATA]",
            repr(e)
        )

    return True


# ============================================================
# TELEGRAM MESAJ KUYRUĞU
# ============================================================

async def telegram_api(
    method,
    payload=None
):

    global http_session
    global last_telegram_send
    global telegram_retry_until

    if not http_session:
        return False, None

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    payload = payload or {}

    async with telegram_send_lock:

        for attempt in range(1, 10):

            now = time.monotonic()

            wait_until = max(
                telegram_retry_until,
                last_telegram_send + 1.25
            )

            if wait_until > now:

                await asyncio.sleep(
                    wait_until - now
                )

            try:

                async with http_session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(
                        total=90
                    )
                ) as response:

                    text = await response.text()

                    last_telegram_send = (
                        time.monotonic()
                    )

                    if response.status == 200:

                        try:
                            result = json.loads(
                                text
                            )
                        except Exception:
                            result = None

                        return True, result

                    if response.status == 429:

                        try:

                            result = json.loads(
                                text
                            )

                            retry_after = safe_int(
                                result.get(
                                    "parameters",
                                    {}
                                ).get(
                                    "retry_after",
                                    15
                                ),
                                15
                            )

                        except Exception:

                            retry_after = 15

                        telegram_retry_until = (
                            time.monotonic()
                            + max(
                                1,
                                retry_after
                            )
                        )

                        print(
                            f"[TELEGRAM 429] "
                            f"{retry_after}s bekleniyor."
                        )

                        continue

                    if response.status in [
                        500,
                        502,
                        503,
                        504
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
                        text
                    )

                    return False, None

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

    return False, None


# ============================================================
# NORMAL RADAR MESAJI
# ============================================================

async def send_telegram_message(data):

    title = (
        "🟪 GOODY BAG"
        if data["type"] == "GOODY BAG"
        else "🟨 HAZİNE SANDIĞI"
    )

    alarm = is_smart_alarm(data)

    if alarm:

        title = (
            "🚨 AKILLI ALARM\n"
            + title
        )

    text = (
        f"{title}\n\n"
        f"👤 Kullanıcı: {data['username']}\n"
        f"🪙 Coin: {data['coins']}\n"
        f"👥 Kişi: {data['people']}\n"
        f"🙋 Katılan: {data['joined']}\n"
        f"📈 Oran: {data['rate']}\n"
        f"👀 İzlenme: {data['view']}\n"
    )

    if alarm:

        text += (
            "\n🚨 "
            f"{alarm_reason(data)}"
            "\n⚡ YÜKSEK ÖDÜL / AZ KİŞİ"
        )

    if data.get("live"):

        text += (
            "\n\n🔴 "
            f'<a href="{data["live"]}">'
            "TIKTOK CANLI YAYIN"
            "</a>"
        )

    ok, _ = await telegram_api(
        "sendMessage",
        {
            "chat_id":
                TARGET_CHAT_ID,

            "text":
                text,

            "parse_mode":
                "HTML",

            "disable_web_page_preview":
                True,
        }
    )

    return ok


# ============================================================
# KİŞİSEL ALARM
# ============================================================

async def send_personal_alarm(
    user_id,
    data,
    settings
):

    coin_limit = safe_int(
        settings.get("alarm_coins")
    )

    people_limit = safe_int(
        settings.get("alarm_people")
    )

    if coin_limit <= 0:
        return False

    if people_limit <= 0:
        return False

    coins = safe_int(
        data.get("coins")
    )

    people = safe_int(
        data.get("people")
    )

    if coins < coin_limit:
        return False

    if people <= 0 or people > people_limit:
        return False

    text = (
        "🎯 KİŞİSEL ALARM\n\n"
        f"👤 Kullanıcı: {data['username']}\n"
        f"🪙 Coin: {data['coins']}\n"
        f"👥 Kişi: {data['people']}\n"
        f"🙋 Katılan: {data['joined']}\n"
        f"📈 Oran: {data['rate']}\n\n"
        "🚨 Ayarladığın alarma uyuyor!"
    )

    if data.get("live"):

        text += (
            "\n\n🔴 "
            f'<a href="{data["live"]}">'
            "TIKTOK CANLI YAYIN"
            "</a>"
        )

    ok, _ = await telegram_api(
        "sendMessage",
        {
            "chat_id": user_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    )

    return ok


# ============================================================
# TAKİP BİLDİRİMİ
# ============================================================

async def send_follow_notifications(data):

    username = normalize_username(
        data.get("username")
    )

    if not username:
        return

    followers = get_followers(
        username
    )

    for user_id in followers:

        settings = get_user_settings(
            user_id
        )

        if data["type"] == "GOODY BAG":

            if settings["mute_goody"]:
                continue

        if data["type"] == "CHEST":

            if settings["mute_chest"]:
                continue

        text = (
            "👤 TAKİP ETTİĞİN YAYINCI\n\n"
            f"👤 @{username}\n"
            f"🎁 {data['box_name']}\n"
            f"🪙 Coin: {data['coins']}\n"
            f"👥 Kişi: {data['people']}\n"
        )

        if is_smart_alarm(data):

            text += (
                "\n🚨 AKILLI ALARM\n"
                "⚡ YÜKSEK ÖDÜL / AZ KİŞİ\n"
            )

        if data.get("live"):

            text += (
                "\n🔴 "
                f'<a href="{data["live"]}">'
                "TIKTOK CANLI YAYIN"
                "</a>"
            )

        await telegram_api(
            "sendMessage",
            {
                "chat_id": user_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        )


# ============================================================
# YENİ OLAY BİLDİRİMİ
# ============================================================

async def notify_event(data):

    room = str(
        data.get("room", "")
    )

    now = time.time()

    if room:

        previous = last_event_notification.get(
            room,
            0
        )

        if (
            now - previous
            <
            DUPLICATE_COOLDOWN
        ):

            print(
                "[TEKRAR ENGELLENDİ]",
                room
            )

            return

        last_event_notification[
            room
        ] = now

    await send_telegram_message(
        data
    )

    # Takipçiler
    try:

        await send_follow_notifications(
            data
        )

    except Exception as e:

        print(
            "[TAKİP BİLDİRİM HATASI]",
            repr(e)
        )


# ============================================================
# QUEUE
# ============================================================

async def telegram_sender():

    while True:

        priority, sequence, data = (
            await telegram_queue.get()
        )

        try:

            await notify_event(
                data
            )

        except Exception as e:

            print(
                "[KUYRUK HATASI]",
                repr(e)
            )

        finally:

            telegram_queue.task_done()


# ============================================================
# ADMIN TELEGRAM
# ============================================================

async def send_admin(text):

    if not ADMIN_CHAT_ID:
        return False

    ok, _ = await telegram_api(
        "sendMessage",
        {
            "chat_id":
                ADMIN_CHAT_ID,

            "text":
                text,

            "disable_web_page_preview":
                True,
        }
    )

    return ok


# ============================================================
# VIP SİLİNDİ BİLDİRİMİ
# ============================================================

async def notify_vip_removed(user_id):

    ok, _ = await telegram_api(
        "sendMessage",
        {
            "chat_id":
                user_id,

            "text":
                (
                    "🔒 VIP erişimin sonlandırıldı.\n\n"
                    "Ödül Avcısı VIP radarına "
                    "erişimin kapatıldı."
                ),

            "disable_web_page_preview":
                True,
        }
    )

    return ok


# ============================================================
# MINI APP AUTH
# ============================================================

def validate_telegram_init_data(
    init_data
):

    if not init_data:
        return None

    try:

        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = data.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        auth_date = safe_int(
            data.get(
                "auth_date"
            )
        )

        if not auth_date:
            return None

        if (
            int(time.time())
            - auth_date
            > 86400
        ):
            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
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

        user_json = data.get(
            "user"
        )

        if not user_json:
            return None

        user = json.loads(
            user_json
        )

        return user

    except Exception:

        return None


# ============================================================
# MINI APP HTML
# ============================================================

MINI_APP_HTML = r"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
 name="viewport"
 content="width=device-width,
 maximum-scale=1.0,
 user-scalable=no"
>

<title>ÖDÜL AVCISI</title>

<script src="https://telegram.org/js/telegram-web-app.js"></script>

<style>

*{
 box-sizing:border-box;
}

html,body{
 margin:0;
 padding:0;
 min-height:100%;
 background:#080a12;
 color:#fff;
 font-family:Arial,Helvetica,sans-serif;
 -webkit-text-size-adjust:100%;
}

body{
 padding:8px;
 overflow-x:hidden;
}

.wrapper{
 width:100%;
 max-width:1100px;
 margin:auto;
}

.header{
 text-align:center;
 padding:8px 4px 14px;
}

.title{
 font-size:clamp(30px,8vw,48px);
 font-weight:1000;
 line-height:1;
 text-shadow:
 0 0 7px #fff,
 0 0 18px #9d51ff,
 0 0 35px #6425ff;
}

.subtitle{
 margin-top:9px;
 font-size:13px;
 color:#aeb7ca;
 font-weight:900;
}

.status{
 display:inline-flex;
 margin-top:10px;
 padding:8px 14px;
 border-radius:999px;
 background:#111522;
 border:1px solid #30394d;
 color:#69ff9a;
 font-size:12px;
 font-weight:1000;
}

.status.error{
 color:#ff6b6b;
}

.latest-box{
 margin-bottom:10px;
 padding:11px;
 border-radius:18px;
 background:linear-gradient(145deg,#151927,#0b0e18);
 border:1px solid #353d51;
}

.latest-title{
 font-size:17px;
 font-weight:1000;
 margin-bottom:9px;
}

.latest-card{
 display:flex;
 align-items:center;
 gap:10px;
 padding:10px;
 border-radius:15px;
 background:#111521;
 border:1px solid #343b50;
}

.latest-card.goody{
 border-color:#6f35a8;
}

.latest-card.chest{
 border-color:#806d29;
}

.latest-card.alarm{
 box-shadow:
 0 0 15px rgba(255,70,70,.35);
 border-color:#ff4545;
}

.latest-icon{
 width:50px;
 height:50px;
 display:flex;
 align-items:center;
 justify-content:center;
 border-radius:13px;
 background:#1c2030;
 font-size:27px;
 flex-shrink:0;
}

.latest-main{
 flex:1;
 min-width:0;
}

.latest-user{
 font-size:16px;
 font-weight:1000;
 overflow:hidden;
 text-overflow:ellipsis;
 white-space:nowrap;
}

.latest-info{
 display:flex;
 flex-wrap:wrap;
 gap:7px;
 margin-top:6px;
 color:#c0c8d9;
 font-size:12px;
 font-weight:900;
}

.latest-live{
 flex-shrink:0;
 text-decoration:none;
 color:#fff;
 background:#df1650;
 padding:10px 13px;
 border-radius:10px;
 font-size:11px;
 font-weight:1000;
}

.search{
 width:100%;
 padding:13px 15px;
 margin-bottom:9px;
 border-radius:14px;
 border:1px solid #293044;
 outline:none;
 background:#101420;
 color:#fff;
 font-size:15px;
 font-weight:700;
}

.search::placeholder{
 color:#7d879d;
}

.filters{
 display:flex;
 gap:7px;
 overflow-x:auto;
 padding-bottom:9px;
 scrollbar-width:none;
}

.filters::-webkit-scrollbar{
 display:none;
}

.filter{
 flex-shrink:0;
 padding:10px 14px;
 border-radius:12px;
 border:1px solid #2b3347;
 background:#101420;
 color:#aeb7ca;
 font-size:12px;
 font-weight:1000;
}

.filter.active{
 color:#fff;
 background:#20283b;
 border-color:#68748e;
}

.radar-grid{
 display:grid;
 grid-template-columns:1fr 1fr;
 gap:8px;
 align-items:start;
}

.panel{
 min-width:0;
 padding:8px;
 border-radius:16px;
 background:#0d111c;
 border:1px solid #293246;
}

.panel.goody{
 border-color:rgba(157,81,255,.55);
}

.panel.chest{
 border-color:rgba(241,200,75,.45);
}

.panel-title{
 display:flex;
 align-items:center;
 justify-content:space-between;
 padding:3px 3px 9px;
}

.panel-name{
 font-size:14px;
 font-weight:1000;
}

.goody .panel-name{
 color:#d8adff;
}

.chest .panel-name{
 color:#ffe47b;
}

.panel-count{
 min-width:28px;
 padding:5px 8px;
 border-radius:999px;
 background:#272d3d;
 text-align:center;
 font-size:11px;
 font-weight:1000;
}

.card{
 position:relative;
 overflow:hidden;
 margin-bottom:7px;
 padding:9px;
 border-radius:13px;
 background:linear-gradient(145deg,#171c29,#0e121d);
 border:1px solid #293246;
}

.card:last-child{
 margin-bottom:0;
}

.goody .card{
 border-left:4px solid #9d51ff;
}

.chest .card{
 border-left:4px solid #f1c84b;
}

.card.alarm{
 border-color:#ff4545;
 box-shadow:0 0 14px rgba(255,60,60,.22);
}

.card.new-card{
 animation:newCard .8s ease-out;
}

@keyframes newCard{

 0%{
  opacity:.35;
  transform:translateY(-7px);
 }

 50%{
  box-shadow:0 0 24px rgba(160,80,255,.38);
 }

 100%{
  opacity:1;
  transform:translateY(0);
  box-shadow:none;
 }

}

.user-row{
 display:flex;
 align-items:center;
 justify-content:space-between;
 gap:7px;
 margin-bottom:8px;
}

.user{
 min-width:0;
 font-size:14px;
 font-weight:1000;
 overflow:hidden;
 text-overflow:ellipsis;
 white-space:nowrap;
}

.new-badge{
 flex-shrink:0;
 padding:5px 7px;
 border-radius:7px;
 background:#e52c59;
 color:#fff;
 font-size:10px;
 font-weight:1000;
}

.alarm-badge{
 flex-shrink:0;
 padding:5px 7px;
 border-radius:7px;
 background:#d51f3c;
 color:#fff;
 font-size:10px;
 font-weight:1000;
}

.info-grid{
 display:grid;
 grid-template-columns:1fr 1fr;
 gap:5px;
}

.info{
 min-width:0;
 padding:7px;
 border-radius:8px;
 background:#151a27;
 color:#a3aec2;
 font-size:11px;
 font-weight:900;
 line-height:1.15;
}

.info b{
 display:block;
 margin-top:3px;
 color:#fff;
 font-size:14px;
 font-weight:1000;
 overflow:hidden;
 text-overflow:ellipsis;
 white-space:nowrap;
}

.live-button{
 display:block;
 margin-top:8px;
 padding:10px 5px;
 border-radius:9px;
 text-align:center;
 text-decoration:none;
 color:#fff;
 background:#e31850;
 font-size:11px;
 font-weight:1000;
}

.empty{
 padding:22px 5px;
 text-align:center;
 color:#7d879d;
 font-size:12px;
 font-weight:700;
}

.footer{
 text-align:center;
 color:#68748b;
 font-size:10px;
 padding:15px 0;
}

@media(max-width:700px){

 body{
  padding:7px;
 }

 .title{
  font-size:32px;
 }

 .radar-grid{
  grid-template-columns:1fr 1fr;
  gap:6px;
 }

 .panel{
  padding:7px;
 }

 .panel-name{
  font-size:12px;
 }

 .card{
  padding:8px;
 }

 .user{
  font-size:13px;
 }

 .info{
  padding:6px;
  font-size:10px;
 }

 .info b{
  font-size:13px;
 }

 .live-button{
  font-size:10px;
  padding:9px 3px;
 }

}

@media(max-width:390px){

 .panel-name{
  font-size:11px;
 }

 .user{
  font-size:12px;
 }

 .info{
  font-size:9px;
 }

 .info b{
  font-size:12px;
 }

 .live-button{
  font-size:9px;
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

 <div id="status" class="status">
  🟡 RADAR BAĞLANIYOR...
 </div>

</div>


<div class="latest-box">

 <div class="latest-title">
  🔥 SON YAKALANAN
 </div>

 <div id="latest"></div>

</div>


<input
 id="search"
 class="search"
 type="text"
 placeholder="🔎 Kullanıcı ara..."
>


<div class="filters">

 <button class="filter active" data-filter="ALL">
  📡 TÜMÜ
 </button>

 <button class="filter" data-filter="GOODY">
  🟪 GOODY
 </button>

 <button class="filter" data-filter="CHEST">
  🟨 CHEST
 </button>

 <button class="filter" data-filter="COIN100">
  🪙 100+ COIN
 </button>

 <button class="filter" data-filter="PEOPLE50">
  👥 50+
 </button>

 <button class="filter" data-filter="ALARM">
  🚨 ALARM
 </button>

</div>


<div class="radar-grid">


<div class="panel goody">

 <div class="panel-title">

  <div class="panel-name">
   🟪 GOODY BAG
  </div>

  <div id="bagCounter" class="panel-count">
   0
  </div>

 </div>

 <div id="bags"></div>

</div>


<div class="panel chest">

 <div class="panel-title">

  <div class="panel-name">
   🟨 HAZİNE SANDIĞI
  </div>

  <div id="chestCounter" class="panel-count">
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

const tg =
 window.Telegram &&
 window.Telegram.WebApp
 ? window.Telegram.WebApp
 : null;

if(tg){
 tg.ready();
 tg.expand();
}

let radarData = {
 chests:[],
 goody_bags:[]
};

let firstLoad = true;
let activeFilter = "ALL";
let searchText = "";

const seenGoody = new Set();
const seenChest = new Set();

const newGoody = new Set();
const newChest = new Set();

let latestKey = null;


function escapeHtml(value){

 return String(value ?? "")
 .replace(/&/g,"&amp;")
 .replace(/</g,"&lt;")
 .replace(/>/g,"&gt;")
 .replace(/"/g,"&quot;")
 .replace(/'/g,"&#039;");

}


function numberValue(value){

 const n = Number(value);

 return Number.isFinite(n)
  ? n
  : 0;

}


function timestamp(item){

 return Number(
  item.detected_at ||
  item.created_at ||
  item.timestamp ||
  0
 );

}


function itemKey(item){

 return String(
  item.source_message_id ??
  item.room ??
  (
   String(item.username ?? "")
   +
   "_"
   +
   String(item.detected_at ?? "")
  )
 );

}


function latestFive(items){

 if(!Array.isArray(items))
  return [];

 return [...items]
  .sort(
   (a,b)=>
    timestamp(b)-timestamp(a)
  )
  .slice(0,5);

}


function isAlarm(item){

 const coins =
  numberValue(item.coins);

 const people =
  numberValue(item.people);

 return (
  coins >= 100 &&
  people > 0 &&
  people <= 5
 );

}


function filterItems(
 items,
 type
){

 let result = latestFive(items);

 return result.filter(item=>{

  const username =
   String(
    item.username ?? ""
   ).toLowerCase();

  if(
   searchText &&
   !username.includes(searchText)
  )
   return false;

  if(
   activeFilter === "GOODY" &&
   type !== "GOODY"
  )
   return false;

  if(
   activeFilter === "CHEST" &&
   type !== "CHEST"
  )
   return false;

  if(
   activeFilter === "COIN100" &&
   numberValue(item.coins) < 100
  )
   return false;

  if(
   activeFilter === "PEOPLE50" &&
   numberValue(item.people) < 50
  )
   return false;

  if(
   activeFilter === "ALARM" &&
   !isAlarm(item)
  )
   return false;

  return true;

 });

}


function detectNewItems(
 items,
 seenSet,
 newSet
){

 if(!Array.isArray(items))
  return;

 items.forEach(item=>{

  const key = itemKey(item);

  if(!key)
   return;

  if(firstLoad){

   seenSet.add(key);
   return;

  }

  if(!seenSet.has(key)){

   seenSet.add(key);
   newSet.add(key);

  }

 });

}


function renderLatest(){

 const container =
  document.getElementById("latest");

 const all = [

  ...radarData.goody_bags.map(
   x=>({...x,_type:"GOODY"})
  ),

  ...radarData.chests.map(
   x=>({...x,_type:"CHEST"})
  )

 ];

 all.sort(
  (a,b)=>
   timestamp(b)-timestamp(a)
 );

 if(!all.length){

  container.innerHTML =
   '<div class="empty">Henüz kayıt yok.</div>';

  return;

 }

 const item = all[0];

 const key = itemKey(item);

 const changed =
  latestKey !== null &&
  key !== latestKey;

 latestKey = key;

 const isGoody =
  item._type === "GOODY";

 const icon =
  isGoody ? "🟪" : "🟨";

 const cls =
  isGoody ? "goody" : "chest";

 const alarm =
  isAlarm(item);

 container.innerHTML = `

  <div class="
   latest-card
   ${cls}
   ${alarm ? "alarm" : ""}
  ">

   <div class="latest-icon">
    ${icon}
   </div>

   <div class="latest-main">

    <div class="latest-user">
     ${escapeHtml(item.username)}
    </div>

    <div class="latest-info">

     <span>🪙 ${escapeHtml(item.coins)}</span>
     <span>•</span>
     <span>👥 ${escapeHtml(item.people)}</span>
     <span>•</span>
     <span>📈 ${escapeHtml(item.rate)}</span>
     <span>•</span>
     <span>👀 ${escapeHtml(item.view)}</span>

    </div>

   </div>

   ${
    alarm
    ?
    `<div class="alarm-badge">🚨</div>`
    :
    ""
   }

   ${
    item.live
    ?
    `
    <a
     class="latest-live"
     href="${escapeHtml(item.live)}"
     target="_blank"
     rel="noopener"
    >
     🔴 GİT
    </a>
    `
    :
    ""
   }

  </div>

 `;

}


function renderItems(
 originalItems,
 elementId,
 counterId,
 icon,
 type
){

 const container =
  document.getElementById(elementId);

 const counter =
  document.getElementById(counterId);

 const items =
  filterItems(
   originalItems,
   type
  );

 counter.textContent =
  items.length;

 if(!items.length){

  container.innerHTML =
   '<div class="empty">⚡ Veri yok.</div>';

  return;

 }

 const newSet =
  type === "GOODY"
  ? newGoody
  : newChest;

 container.innerHTML =

  items.map(item=>{

   const key =
    itemKey(item);

   const isNew =
    newSet.has(key);

   const alarm =
    isAlarm(item);

   return `

    <div class="
     card
     ${isNew ? "new-card" : ""}
     ${alarm ? "alarm" : ""}
    ">

     <div class="user-row">

      <div class="user">

       ${icon}

       ${escapeHtml(item.username)}

      </div>

      <div style="
       display:flex;
       gap:4px;
      ">

       ${
        alarm
        ?
        `
        <div class="alarm-badge">
         🚨
        </div>
        `
        :
        ""
       }

       ${
        isNew
        ?
        `
        <div class="new-badge">
         ⚡ YENİ
        </div>
        `
        :
        ""
       }

      </div>

     </div>


     <div class="info-grid">

      <div class="info">
       🪙 COIN
       <b>${escapeHtml(item.coins)}</b>
      </div>

      <div class="info">
       👥 KİŞİ
       <b>${escapeHtml(item.people)}</b>
      </div>

      <div class="info">
       🙋 KATILAN
       <b>${escapeHtml(item.joined)}</b>
      </div>

      <div class="info">
       📈 ORAN
       <b>${escapeHtml(item.rate)}</b>
      </div>

      <div class="info">
       👀 İZLENME
       <b>${escapeHtml(item.view)}</b>
      </div>

      <div class="info">
       🏠 ODA
       <b title="${escapeHtml(item.room)}">
        ${escapeHtml(item.room)}
       </b>
      </div>

     </div>


     ${
      alarm
      ?
      `
      <div style="
       margin-top:7px;
       padding:7px;
       border-radius:8px;
       background:#32151a;
       color:#ff7373;
       text-align:center;
       font-size:10px;
       font-weight:1000;
      ">
       🚨 YÜKSEK ÖDÜL / AZ KİŞİ
      </div>
      `
      :
      ""
     }


     ${
      item.live
      ?
      `
      <a
       class="live-button"
       href="${escapeHtml(item.live)}"
       target="_blank"
       rel="noopener"
      >
       🔴 TIKTOK CANLI
      </a>
      `
      :
      ""
     }

    </div>

   `;

  }).join("");

}


function renderRadar(){

 detectNewItems(
  radarData.goody_bags,
  seenGoody,
  newGoody
 );

 detectNewItems(
  radarData.chests,
  seenChest,
  newChest
 );

 renderLatest();

 renderItems(
  radarData.goody_bags,
  "bags",
  "bagCounter",
  "🟪",
  "GOODY"
 );

 renderItems(
  radarData.chests,
  "chests",
  "chestCounter",
  "🟨",
  "CHEST"
 );

}


async function loadRadar(){

 try{

  let url =
   "/api/all?t="
   + Date.now();

  const headers = {};

  if(
   tg &&
   tg.initData
  ){

   url =
    "/api/miniapp-data?t="
    + Date.now();

   headers[
    "X-Telegram-Init-Data"
   ] =
    tg.initData;

  }

  const response =
   await fetch(
    url,
    {
     cache:"no-store",
     headers:headers
    }
   );

  if(!response.ok){

   if(response.status === 401){

    throw new Error(
     "VIP erişimi gerekli"
    );

   }

   throw new Error(
    "HTTP " + response.status
   );

  }

  const data =
   await response.json();

  radarData = {

   chests:
    Array.isArray(data.chests)
    ? data.chests
    : [],

   goody_bags:
    Array.isArray(data.goody_bags)
    ? data.goody_bags
    : []

  };

  const status =
   document.getElementById("status");

  status.className =
   "status";

  status.textContent =
   "🟢 RADAR AKTİF • CANLI VERİ";

  renderRadar();

  firstLoad = false;

 }
 catch(error){

  console.error(
   "Radar hatası:",
   error
  );

  const status =
   document.getElementById("status");

  status.className =
   "status error";

  status.textContent =
   "🔴 " + error.message;

 }

}


document
 .getElementById("search")
 .addEventListener(
  "input",
  function(){

   searchText =
    this.value
     .trim()
     .toLowerCase();

   renderRadar();

  }
 );


document
 .querySelectorAll(".filter")
 .forEach(button=>{

  button.addEventListener(
   "click",
   function(){

    document
     .querySelectorAll(".filter")
     .forEach(x=>
      x.classList.remove("active")
     );

    this.classList.add("active");

    activeFilter =
     this.dataset.filter;

    renderRadar();

   }
  );

 });


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
# WEB
# ============================================================

async def radar_page(request):

    return web.Response(
        text=MINI_APP_HTML,
        content_type="text/html",
        charset="utf-8"
    )


async def miniapp_page(request):

    return web.Response(
        text=MINI_APP_HTML,
        content_type="text/html",
        charset="utf-8"
    )


# ============================================================
# CORS
# ============================================================

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
# API
# ============================================================

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
            len(LIVE_CHESTS),

        "goody_bags":
            len(LIVE_GOODY_BAGS),

        "server_time":
            int(time.time()),

    })


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


# ============================================================
# VIP MINI APP API
# ============================================================

async def api_miniapp_data(request):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user = validate_telegram_init_data(
        init_data
    )

    if not user:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "Geçersiz Telegram erişimi"
            },
            status=401
        )

    user_id = safe_int(
        user.get("id")
    )

    vip = get_vip(
        user_id
    )

    if not vip:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "VIP erişimi gerekli"
            },
            status=401
        )

    return web.json_response({

        "ok":
            True,

        "status":
            "online",

        "user":
            {
                "id":
                    user_id,

                "username":
                    user.get(
                        "username",
                        ""
                    )
            },

        "expires_at":
            vip["expires_at"],

        "chests":
            list(
                LIVE_CHESTS.values()
            ),

        "goody_bags":
            list(
                LIVE_GOODY_BAGS.values()
            ),

        "server_time":
            int(time.time()),

    })


# ============================================================
# HTTP SERVER
# ============================================================

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
        "/miniapp",
        miniapp_page
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
        f"[HTTP] Sunucu başladı: {PORT}"
    )

    print(
        "[HTTP] Mini App:",
        f"{BASE_URL}/miniapp"
    )


# ============================================================
# VIP KEYBOARD
# ============================================================

def vip_keyboard():

    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "🌐 VIP RADARI AÇ",
                web_app=WebAppInfo(
                    url=
                        f"{BASE_URL}/miniapp"
                )
            )
        ]]
    )


# ============================================================
# START
# ============================================================

async def start_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    vip = get_vip(
        user.id
    )

    if vip:

        await update.message.reply_text(

            "✅ VIP erişimin aktif.\n\n"
            "🏆 Ödül Avcısı radarını "
            "açmak için aşağıdaki butona bas:",

            reply_markup=
                vip_keyboard()

        )

        return

    args = (
        context.args
        if context.args
        else []
    )

    invite_token = None

    if args:

        value = args[0]

        if value.startswith(
            "invite_"
        ):

            invite_token = value[
                len("invite_"):
            ]

    if invite_token:

        success = use_invite(
            invite_token,
            user
        )

        if success:

            await update.message.reply_text(

                "✅ VIP erişim açıldı!\n\n"
                f"⏳ Süre: {VIP_DAYS} gün\n\n"
                "🏆 Radarı açmak için "
                "aşağıdaki butona bas:",

                reply_markup=
                    vip_keyboard()

            )

            await send_admin(

                "🎟 YENİ VIP ÜYE\n\n"
                f"👤 "
                f"{user.first_name or ''}\n"
                f"🆔 {user.id}\n"
                f"👤 @{user.username or 'yok'}\n"
                f"⏳ {VIP_DAYS} gün"

            )

            return

    await update.message.reply_text(

        "🔒 Bu bot davet/VIP sistemiyle "
        "çalışıyor.\n\n"
        "VIP erişimin yok.\n"
        "Yönetici tarafından gönderilen "
        "davet bağlantısıyla giriş yapabilirsin."

    )


# ============================================================
# DAVET
# ============================================================

async def davet_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "❌ Yetkin yok."
        )

        return

    token = create_invite()

    link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=invite_{token}"
    )

    await update.message.reply_text(

        "🎟 VIP DAVET LİNKİ\n\n"
        f"⏳ Süre: {VIP_DAYS} gün\n"
        "🔐 Kullanım: Tek kişi\n"
        "⏰ Link geçerliliği: 24 saat\n\n"
        f"{link}"

    )


# ============================================================
# ÜYELER
# ============================================================

async def uyeler_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "❌ Yetkin yok."
        )

        return

    rows = list_vips()

    if not rows:

        await update.message.reply_text(
            "📭 Aktif VIP üye yok."
        )

        return

    lines = [
        "👑 AKTİF VIP ÜYELER",
        ""
    ]

    for row in rows:

        user_id = row[0]
        username = row[1]
        first_name = row[2]
        expires = row[3]

        remaining = max(
            0,
            expires -
            int(time.time())
        )

        days = remaining // 86400

        name = (
            first_name
            or username
            or "Bilinmiyor"
        )

        lines.append(
            f"👤 {name}\n"
            f"🆔 {user_id}\n"
            f"📅 {days} gün kaldı\n"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# SIL VIP
# ============================================================

async def silvip_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "❌ Yetkin yok."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Kullanım:\n"
            "/silvip 123456789"
        )

        return

    user_id = safe_int(
        context.args[0]
    )

    if not user_id:

        await update.message.reply_text(
            "❌ Geçersiz kullanıcı ID."
        )

        return

    removed = remove_vip(
        user_id
    )

    if removed:

        notified = await notify_vip_removed(
            user_id
        )

        if notified:

            await update.message.reply_text(
                "✅ VIP erişim silindi.\n"
                "📩 Kullanıcıya bildirim gönderildi."
            )

        else:

            await update.message.reply_text(
                "✅ VIP erişim silindi.\n"
                "⚠️ Kullanıcıya bildirim gönderilemedi."
            )

    else:

        await update.message.reply_text(
            "❌ Bu kullanıcı VIP değil."
        )


# ============================================================
# VIP UZAT
# ============================================================

async def uzatvip_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "❌ Yetkin yok."
        )

        return

    if len(context.args) < 2:

        await update.message.reply_text(
            "Kullanım:\n"
            "/uzatvip 123456789 30"
        )

        return

    user_id = safe_int(
        context.args[0]
    )

    days = safe_int(
        context.args[1]
    )

    if not user_id or days <= 0:

        await update.message.reply_text(
            "❌ Geçersiz ID veya gün."
        )

        return

    new_expire = extend_vip(
        user_id,
        days
    )

    if not new_expire:

        await update.message.reply_text(
            "❌ Bu kullanıcı VIP değil."
        )

        return

    remaining = max(
        0,
        new_expire -
        int(time.time())
    )

    await update.message.reply_text(

        "✅ VIP süresi uzatıldı.\n\n"
        f"🆔 {user_id}\n"
        f"➕ {days} gün\n"
        f"📅 Kalan yaklaşık: "
        f"{remaining // 86400} gün"

    )

    await telegram_api(
        "sendMessage",
        {
            "chat_id": user_id,
            "text":
                (
                    "👑 VIP süren uzatıldı!\n\n"
                    f"➕ {days} gün eklendi.\n"
                    f"📅 Yeni kalan süre: "
                    f"{remaining // 86400} gün"
                )
        }
    )


# ============================================================
# VIP BILGI
# ============================================================

async def vipbilgi_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "❌ Yetkin yok."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Kullanım:\n"
            "/vipbilgi 123456789"
        )

        return

    user_id = safe_int(
        context.args[0]
    )

    vip = get_vip(
        user_id
    )

    if not vip:

        await update.message.reply_text(
            "❌ Aktif VIP bulunamadı."
        )

        return

    remaining = max(
        0,
        vip["expires_at"]
        -
        int(time.time())
    )

    await update.message.reply_text(

        "👑 VIP BİLGİ\n\n"
        f"🆔 {vip['user_id']}\n"
        f"👤 {vip['first_name'] or '-'}\n"
        f"📱 @{vip['username'] or 'yok'}\n"
        f"⏳ {remaining // 86400} gün kaldı"

    )


# ============================================================
# ID
# ============================================================

async def id_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    await update.message.reply_text(

        "🆔 Telegram ID:\n"
        f"{user.id}"

    )


# ============================================================
# ALARM KOMUTU
# ============================================================

async def alarm_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not get_vip(user.id):

        await update.message.reply_text(
            "🔒 Bu özellik VIP kullanıcılar içindir."
        )

        return

    if len(context.args) < 2:

        settings = get_user_settings(
            user.id
        )

        if settings["alarm_coins"]:

            await update.message.reply_text(

                "🎯 MEVCUT ALARM\n\n"
                f"🪙 Coin: "
                f"{settings['alarm_coins']}+\n"
                f"👥 Kişi: "
                f"{settings['alarm_people']} veya daha az\n\n"
                "Kapatmak için:\n"
                "/alarm kapat"

            )

        else:

            await update.message.reply_text(

                "🎯 KİŞİSEL ALARM\n\n"
                "Örnek:\n"
                "/alarm 200 5\n\n"
                "Anlamı:\n"
                "🪙 200+ coin\n"
                "👥 5 veya daha az kişi\n\n"
                "Kapatmak:\n"
                "/alarm kapat"

            )

        return

    if context.args[0].lower() == "kapat":

        save_user_settings(
            user.id,
            alarm_coins=0,
            alarm_people=0
        )

        await update.message.reply_text(
            "🔕 Kişisel alarm kapatıldı."
        )

        return

    coins = safe_int(
        context.args[0]
    )

    people = safe_int(
        context.args[1]
    )

    if coins <= 0 or people <= 0:

        await update.message.reply_text(
            "❌ Örnek:\n/alarm 200 5"
        )

        return

    save_user_settings(
        user.id,
        alarm_coins=coins,
        alarm_people=people
    )

    await update.message.reply_text(

        "🎯 KİŞİSEL ALARM AKTİF\n\n"
        f"🪙 {coins}+ coin\n"
        f"👥 {people} veya daha az kişi\n\n"
        "Uygun hazine geldiğinde "
        "sana özel bildirim gönderilecek."

    )


# ============================================================
# TAKİP KOMUTU
# ============================================================

async def takip_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not get_vip(user.id):

        await update.message.reply_text(
            "🔒 Bu özellik VIP kullanıcılar içindir."
        )

        return

    if not context.args:

        follows = get_follows(
            user.id
        )

        if not follows:

            await update.message.reply_text(
                "📭 Takip ettiğin yayıncı yok."
            )

        else:

            await update.message.reply_text(
                "👤 TAKİP LİSTEN\n\n"
                +
                "\n".join(
                    f"• @{x}"
                    for x in follows
                )
            )

        return

    username = normalize_username(
        context.args[0]
    )

    if add_follow(
        user.id,
        username
    ):

        await update.message.reply_text(
            f"✅ @{username} takip listesine eklendi."
        )

    else:

        await update.message.reply_text(
            f"ℹ️ @{username} zaten takip ediliyor."
        )


# ============================================================
# TAKİPLER
# ============================================================

async def takipler_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not get_vip(user.id):

        await update.message.reply_text(
            "🔒 Bu özellik VIP kullanıcılar içindir."
        )

        return

    follows = get_follows(
        user.id
    )

    if not follows:

        await update.message.reply_text(
            "📭 Takip listen boş."
        )

        return

    await update.message.reply_text(

        "👤 TAKİP LİSTEN\n\n"
        +
        "\n".join(
            f"• @{x}"
            for x in follows
        )
        +
        "\n\n❌ Silmek:\n"
        "/takipsil kullanıcı"

    )


# ============================================================
# TAKİP SİL
# ============================================================

async def takipsil_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not get_vip(user.id):

        await update.message.reply_text(
            "🔒 Bu özellik VIP kullanıcılar içindir."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Kullanım:\n"
            "/takipsil kullanıcı"
        )

        return

    username = normalize_username(
        context.args[0]
    )

    if remove_follow(
        user.id,
        username
    ):

        await update.message.reply_text(
            f"❌ @{username} takipten çıkarıldı."
        )

    else:

        await update.message.reply_text(
            f"ℹ️ @{username} takip listende yok."
        )


# ============================================================
# SESSİZ
# ============================================================

async def sessiz_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not get_vip(user.id):

        await update.message.reply_text(
            "🔒 Bu özellik VIP kullanıcılar içindir."
        )

        return

    if not context.args:

        settings = get_user_settings(
            user.id
        )

        await update.message.reply_text(

            "🔕 SESSİZE ALMA\n\n"
            f"🟪 Goody: "
            f"{'KAPALI' if settings['mute_goody'] else 'AÇIK'}\n"
            f"🟨 Chest: "
            f"{'KAPALI' if settings['mute_chest'] else 'AÇIK'}\n\n"
            "Kullanım:\n"
            "/sessiz goody\n"
            "/sessiz chest\n"
            "/sessiz kapat"

        )

        return

    value = context.args[0].lower()

    if value == "goody":

        save_user_settings(
            user.id,
            mute_goody=True
        )

        await update.message.reply_text(
            "🔕 Goody Bag bildirimleri sessize alındı."
        )

        return

    if value == "chest":

        save_user_settings(
            user.id,
            mute_chest=True
        )

        await update.message.reply_text(
            "🔕 Hazine Sandığı bildirimleri sessize alındı."
        )

        return

    if value == "kapat":

        save_user_settings(
            user.id,
            mute_goody=False,
            mute_chest=False
        )

        await update.message.reply_text(
            "🔔 Tüm bildirimler tekrar açıldı."
        )

        return

    await update.message.reply_text(

        "Kullanım:\n"
        "/sessiz goody\n"
        "/sessiz chest\n"
        "/sessiz kapat"

    )


# ============================================================
# YARDIM
# ============================================================

async def yardim_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "🏆 ÖDÜL AVCISI\n\n"

        "🎯 KİŞİSEL ALARM\n"
        "/alarm 200 5\n"
        "/alarm kapat\n\n"

        "👤 TAKİP\n"
        "/takip kullanici\n"
        "/takipler\n"
        "/takipsil kullanici\n\n"

        "🔕 SESSİZ\n"
        "/sessiz goody\n"
        "/sessiz chest\n"
        "/sessiz kapat\n\n"

        "🌐 Radar\n"
        "/start"

    )


# ============================================================
# TELETHON LISTENER
# ============================================================

async def message_listener(event):

    global queue_counter

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

        if add_to_radar(data):

            # Alarm öncelikli
            priority = (
                0
                if is_smart_alarm(data)
                else 10
            )

            queue_counter += 1

            await telegram_queue.put(
                (
                    priority,
                    queue_counter,
                    data
                )
            )

            print(
                "[RADAR]",
                data["type"],
                "|",
                data["username"],
                "| COIN",
                data["coins"],
                "| KİŞİ",
                data["people"],
                "| ALARM",
                is_smart_alarm(data)
            )

    except Exception as e:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(e)
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global http_session

    print("=" * 70)

    print(
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
    )

    print("=" * 70)

    init_db()

    http_session = (
        aiohttp.ClientSession()
    )

    await start_http_server()

    # --------------------------------------------------------
    # BOT
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "davet",
            davet_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "uyeler",
            uyeler_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "silvip",
            silvip_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "uzatvip",
            uzatvip_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "vipbilgi",
            vipbilgi_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "id",
            id_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "alarm",
            alarm_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "takip",
            takip_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "takipler",
            takipler_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "takipsil",
            takipsil_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "sessiz",
            sessiz_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "yardim",
            yardim_cmd
        )
    )

    await application.initialize()

    await application.start()

    if application.updater:

        await application.updater.start_polling()

    print(
        "[BOT] Telegram bot başladı."
    )

    # --------------------------------------------------------
    # TELETHON
    # --------------------------------------------------------

    client = TelegramClient(

        StringSession(
            STRING_SESSION
        ),

        API_ID,

        API_HASH,

    )

    await client.start()

    print(
        "[TELEGRAM] İstemci bağlandı."
    )

    client.add_event_handler(

        message_listener,

        events.NewMessage(
            chats=SOURCE_CHATS
        )

    )

    # --------------------------------------------------------
    # QUEUE
    # --------------------------------------------------------

    asyncio.create_task(
        telegram_sender()
    )

    print(
        "[HAZIR] Goody Bag aktif."
    )

    print(
        "[HAZIR] Hazine Sandığı aktif."
    )

    print(
        "[HAZIR] Akıllı alarm aktif."
    )

    print(
        "[HAZIR] Kişisel alarm aktif."
    )

    print(
        "[HAZIR] Yayıncı takip sistemi aktif."
    )

    print(
        "[HAZIR] Sessize alma aktif."
    )

    print(
        "[HAZIR] VIP sistemi aktif."
    )

    print(
        "[HAZIR] Mini App aktif."
    )

    print(
        "[HAZIR] Büyük yazı modu aktif."
    )

    print(
        "[HAZIR] Telegram Queue aktif."
    )

    try:

        await client.run_until_disconnected()

    finally:

        try:

            if application.updater:

                await application.updater.stop()

            await application.stop()

            await application.shutdown()

        except Exception:
            pass

        try:

            await client.disconnect()

        except Exception:
            pass

        if http_session:

            await http_session.close()

        print(
            "[DURDU] Sistem kapandı."
        )


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
            "Kapatıldı."
        )

    except Exception as e:

        print(
            "[KRİTİK HATA]",
            repr(e)
        )
