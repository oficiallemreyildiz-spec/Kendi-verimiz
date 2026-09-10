# ============================================================
# main.py
# Ãƒâ€“DÃƒÅ“L AVCISI
#
# GOODY BAG + HAZÃ„Â°NE SANDIÃ„ÂI
#
# EK SÃ„Â°STEMLER:
# - /davet 7
# - /davet 20 gÃƒÂ¼n
# - VIP otomatik sÃƒÂ¼re sonlandÃ„Â±rma
# - VIP sÃƒÂ¼resi bitince otomatik bildirim
# - /uyeler iÃƒÂ§inde SÃ„Â°L butonu
# - Her gÃƒÂ¼n 09:00 TÃƒÂ¼rkiye saati VIP raporu
# - GÃƒÂ¼nlÃƒÂ¼k raporda sadece kalan VIP sÃƒÂ¼resi
# - VIP otomatik yetki raporu
# - KiÃ…Å¸isel alarm
# - YayÃ„Â±ncÃ„Â± takip
# - Sessize alma
# - Telegram Queue
# - 429 korumasÃ„Â±
# - Mini App
# - Mini App initData doÃ„Å¸rulama
# - Arama / filtre
# - KullanÃ„Â±cÃ„Â± adÃ„Â± kopyalama
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

from datetime import datetime, timedelta
from urllib.parse import unquote, parse_qsl
from zoneinfo import ZoneInfo

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
    CallbackQueryHandler,
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
    os.environ.get(
        "ADMIN_USER_ID",
        "0"
    )
)

ADMIN_CHAT_ID = int(
    os.environ.get(
        "ADMIN_CHAT_ID",
        str(ADMIN_USER_ID or 0)
    )
)

# /davet yazÃ„Â±ldÃ„Â±Ã„Å¸Ã„Â±nda kullanÃ„Â±lacak varsayÃ„Â±lan sÃƒÂ¼re
VIP_DAYS = int(
    os.environ.get(
        "VIP_DAYS",
        "30"
    )
)

TARGET_CHAT_ID = -1004421946217

# TÃƒÂ¼rkiye saati
TURKEY_TZ = ZoneInfo("Europe/Istanbul")

# GÃƒÂ¼nlÃƒÂ¼k VIP rapor saati
VIP_REPORT_HOUR = 9
VIP_REPORT_MINUTE = 0


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
# ALARM
# ============================================================

COIN_ALARM_LIMIT = 100
PEOPLE_ALARM_LIMIT = 5
DUPLICATE_COOLDOWN = 60


# ============================================================
# RAM
# ============================================================

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}

processed_messages = set()
last_event_notification = {}
USER_SETTINGS_CACHE = {}

telegram_queue = asyncio.PriorityQueue()
telegram_send_lock = asyncio.Lock()

last_telegram_send = 0.0
telegram_retry_until = 0.0

queue_counter = 0

http_session = None


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
            used_by INTEGER DEFAULT NULL,
            duration_days INTEGER DEFAULT 30
        )
    """)

    # Eski radar.db kullanÃ„Â±lÃ„Â±yorsa duration_days sÃƒÂ¼tununu ekle
    cur.execute(
        "PRAGMA table_info(invite_tokens)"
    )

    columns = [
        row[1]
        for row in cur.fetchall()
    ]

    if "duration_days" not in columns:
        try:
            cur.execute("""
                ALTER TABLE invite_tokens
                ADD COLUMN duration_days INTEGER DEFAULT 30
            """)
        except Exception:
            pass

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            alarm_coins INTEGER DEFAULT 0,
            alarm_people INTEGER DEFAULT 0,
            mute_goody INTEGER DEFAULT 0,
            mute_chest INTEGER DEFAULT 0
        )
    """)

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
# VIP
# ============================================================

def add_vip(
    user_id,
    username="",
    first_name="",
    days=None
):

    if days is None:
        days = VIP_DAYS

    days = safe_int(days)

    if days <= 0:
        days = VIP_DAYS

    now = int(time.time())
    expires = now + days * 86400

    conn = db()

    conn.execute("""
        INSERT INTO vip_users
        (
            user_id,
            username,
            first_name,
            expires_at,
            created_at
        )
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
        SELECT
            user_id,
            username,
            first_name,
            expires_at
        FROM vip_users
        WHERE user_id=?
    """, (user_id,))

    row = cur.fetchone()

    conn.close()

    if not row:
        return None

    # SÃƒÂ¼resi geÃƒÂ§miÃ…Å¸se VIP olarak kabul edilmez.
    # Silme iÃ…Å¸lemini background cleanup yapar.
    if int(row[3]) <= int(time.time()):
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
        base
        +
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
        SELECT
            user_id,
            username,
            first_name,
            expires_at
        FROM vip_users
        WHERE expires_at > ?
        ORDER BY expires_at ASC
    """, (
        int(time.time()),
    ))

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# VIP SÃƒÅ“RE
# ============================================================

def format_remaining(expires_at):

    remaining = max(
        0,
        safe_int(expires_at)
        -
        int(time.time())
    )

    days = remaining // 86400
    hours = (
        remaining % 86400
    ) // 3600

    minutes = (
        remaining % 3600
    ) // 60

    return (
        f"{days} gÃƒÂ¼n "
        f"{hours} saat "
        f"{minutes} dakika"
    )


def format_remaining_days(expires_at):

    remaining = max(
        0,
        safe_int(expires_at)
        -
        int(time.time())
    )

    days = remaining // 86400

    return f"{days} gÃƒÂ¼n"


# ============================================================
# VIP SÃƒÅ“RESÃ„Â° BÃ„Â°TENLER
# ============================================================

async def notify_vip_removed(user_id):

    ok, _ = await telegram_api(
        "sendMessage",
        {
            "chat_id":
                user_id,

            "text":
                (
                    "ÄŸÅ¸â€â€™ VIP ERÃ„Â°Ã…ÂÃ„Â°MÃ„Â°N SONA ERDÃ„Â°.\n\n"
                    "Ã¢ÂÂ° VIP sÃƒÂ¼ren doldu.\n"
                    "Ã¢ÂÅ’ Ãƒâ€“dÃƒÂ¼l AvcÃ„Â±sÃ„Â± VIP radarÃ„Â±na "
                    "eriÃ…Å¸imin otomatik olarak kapatÃ„Â±ldÃ„Â±."
                ),

            "disable_web_page_preview":
                True
        }
    )

    return ok


def get_expired_vips():

    now = int(time.time())

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            user_id,
            username,
            first_name,
            expires_at
        FROM vip_users
        WHERE expires_at <= ?
        ORDER BY expires_at ASC
    """, (now,))

    rows = cur.fetchall()

    conn.close()

    return rows


async def cleanup_expired_vips():

    expired = get_expired_vips()

    if not expired:
        return

    for row in expired:

        user_id = row[0]

        try:

            removed = remove_vip(
                user_id
            )

            if removed:

                print(
                    "[VIP SÃƒÅ“RESÃ„Â° BÃ„Â°TTÃ„Â°]",
                    user_id
                )

                try:

                    await notify_vip_removed(
                        user_id
                    )

                except Exception as e:

                    print(
                        "[VIP BÃ„Â°TÃ„Â°Ã…Â BÃ„Â°LDÃ„Â°RÃ„Â°M HATASI]",
                        user_id,
                        repr(e)
                    )

                try:

                    await send_admin(
                        "Ã¢ÂÂ° VIP SÃƒÅ“RESÃ„Â° BÃ„Â°TTÃ„Â°\n"
                        "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â\n\n"
                        f"ÄŸÅ¸â€˜Â¤ {row[2] or '-'}\n"
                        f"ÄŸÅ¸â€œÂ± @{row[1] or 'yok'}\n"
                        f"ÄŸÅ¸â€ â€ {user_id}\n\n"
                        "Ã¢ÂÅ’ VIP eriÃ…Å¸imi otomatik olarak "
                        "kapatÃ„Â±ldÃ„Â±."
                    )

                except Exception as e:

                    print(
                        "[ADMIN VIP BÃ„Â°TÃ„Â°Ã…Â HATASI]",
                        repr(e)
                    )

        except Exception as e:

            print(
                "[VIP TEMÃ„Â°ZLEME HATASI]",
                user_id,
                repr(e)
            )


async def vip_expiry_loop():

    while True:

        try:

            await cleanup_expired_vips()

        except Exception as e:

            print(
                "[VIP EXPIRY LOOP HATASI]",
                repr(e)
            )

        # Her dakika kontrol
        await asyncio.sleep(60)


# ============================================================
# DAVET
# ============================================================

def create_invite(days=None):

    if days is None:
        days = VIP_DAYS

    days = safe_int(days)

    if days <= 0:
        days = VIP_DAYS

    token = secrets.token_urlsafe(24)

    now = int(time.time())

    # Davet linkinin kendisi 24 saat geÃƒÂ§erli
    expires = now + 24 * 3600

    conn = db()

    conn.execute("""
        INSERT INTO invite_tokens
        (
            token,
            created_at,
            expires_at,
            used,
            duration_days
        )
        VALUES (?, ?, ?, 0, ?)
    """, (
        token,
        now,
        expires,
        days
    ))

    conn.commit()
    conn.close()

    return token, days


def use_invite(token, user):

    if not token:
        return False

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            token,
            expires_at,
            used,
            duration_days
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

    duration_days = safe_int(
        row[3],
        VIP_DAYS
    )

    if duration_days <= 0:
        duration_days = VIP_DAYS

    cur.execute("""
        UPDATE invite_tokens
        SET
            used=1,
            used_by=?
        WHERE
            token=?
            AND used=0
    """, (
        user.id,
        token
    ))

    if cur.rowcount != 1:

        conn.rollback()
        conn.close()

        return False

    now = int(time.time())

    expires = (
        now
        +
        duration_days * 86400
    )

    cur.execute("""
        INSERT INTO vip_users
        (
            user_id,
            username,
            first_name,
            expires_at,
            created_at
        )
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

    return True, duration_days


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
            "alarm_coins":
                safe_int(row[0]),

            "alarm_people":
                safe_int(row[1]),

            "mute_goody":
                bool(row[2]),

            "mute_chest":
                bool(row[3]),
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


def get_personal_alarm_users():

    now = int(time.time())

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            v.user_id,
            s.alarm_coins,
            s.alarm_people
        FROM vip_users v
        JOIN user_settings s
            ON s.user_id = v.user_id
        WHERE
            v.expires_at > ?
            AND s.alarm_coins > 0
            AND s.alarm_people > 0
    """, (now,))

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# TAKÃ„Â°P
# ============================================================

def add_follow(user_id, username):

    username = normalize_username(
        username
    )

    if not username:
        return False

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO follows
        (
            user_id,
            username,
            created_at
        )
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


def remove_follow(user_id, username):

    username = normalize_username(
        username
    )

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        DELETE FROM follows
        WHERE
            user_id=?
            AND username=?
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

        for entity in (
            event.message.entities
            or []
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
                return unquote(
                    m.group(1)
                )

    except Exception:
        pass

    try:

        for entity, _ in (
            event.message.get_entities_text()
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

        return (
            data
            if isinstance(data, dict)
            else None
        )

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
        r'^\s*##\s*T\d+\s*[Ã¢â‚¬Âº>:]\s*([^\s\n]+)',
        r'^\s*T\d+\s*[Ã¢â‚¬Âº>:]\s*([^\s\n]+)',
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

def extract_coins(
    text,
    token_data=None
):

    if text:

        patterns = [
            r'(?:TÃƒÅ¡I|TUI)\s*:\s*(\d+)\s*/',
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
        r'(?:TÃƒÅ¡I|TUI)\s*:\s*\d+\s*/\s*(\d+)',
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
        r'Ã„ÂÃƒÂ£\s*join\s*:\s*(\d+)',
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
        r'ÄŸÅ¸â€˜â‚¬\s*(\d+)',
        text
    )

    return (
        safe_int(m.group(1))
        if m
        else 0
    )


# ============================================================
# RATE
# ============================================================

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

def detect_type(
    text,
    token_data
):

    upper = (
        text or ""
    ).upper()

    if re.search(
        r'TÃƒÅ¡I|TUI',
        upper
    ):
        return True

    if re.search(
        r'GOODY\s*BAG|REWARD\s*BAG',
        upper
    ):
        return True

    if re.search(
        r'\bBOX\b|RÃ†Â¯Ã†Â NG|TREO|HAZÃ„Â°NE',
        upper
    ):
        return False

    if "ÄŸÅ¸Å¸Â¡" in text:
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

                if (
                    0
                    <
                    value
                    <
                    86400
                ):
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
                safe_int(m.group(1))
                *
                60
                +
                safe_int(m.group(2))
            )

            if duration > 0:
                return now + duration

    return now + 180


# ============================================================
# LIVE LINK
# ============================================================

def get_live_link(
    username,
    room=None,
    token_data=None
):

    username = str(
        username or ""
    ).strip().lstrip("@").strip()

    if not username:
        return ""

    return (
        "https://www.tiktok.com/"
        f"@{username}/live"
    )


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
        or extract_username_from_text(
            text
        )
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
            else "Hazine SandÃ„Â±Ã„Å¸Ã„Â±",

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
        f"ÄŸÅ¸Âªâ„¢ {data.get('coins', 0)} coin"
        f" / "
        f"ÄŸÅ¸â€˜Â¥ {data.get('people', 0)} kiÃ…Å¸i"
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

        if (
            int(time.time())
            -
            old_time
            <
            5
        ):
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
# TELEGRAM API
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
        "https://api.telegram.org/"
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
                            +
                            max(
                                1,
                                retry_after
                            )
                        )

                        print(
                            "[TELEGRAM 429]",
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
                    "[TELEGRAM GÃƒâ€“NDERME HATASI]",
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
# NORMAL RADAR
# ============================================================

async def send_telegram_message(data):

    title = (
        "ÄŸÅ¸Å¸Âª GOODY BAG"
        if data["type"] == "GOODY BAG"
        else "ÄŸÅ¸Å¸Â¨ HAZÃ„Â°NE SANDIÃ„ÂI"
    )

    alarm = is_smart_alarm(
        data
    )

    if alarm:

        title = (
            "ÄŸÅ¸Å¡Â¨ AKILLI ALARM\n"
            +
            title
        )

    text = (
        f"{title}\n\n"
        f"ÄŸÅ¸â€˜Â¤ KullanÃ„Â±cÃ„Â±: {data['username']}\n"
        f"ÄŸÅ¸Âªâ„¢ Coin: {data['coins']}\n"
        f"ÄŸÅ¸â€˜Â¥ KiÃ…Å¸i: {data['people']}\n"
        f"ÄŸÅ¸â„¢â€¹ KatÃ„Â±lan: {data['joined']}\n"
        f"ÄŸÅ¸â€œË† Oran: {data['rate']}\n"
        f"ÄŸÅ¸â€˜â‚¬ Ã„Â°zlenme: {data['view']}\n"
    )

    if alarm:

        text += (
            "\nÄŸÅ¸Å¡Â¨ "
            f"{alarm_reason(data)}"
            "\nÃ¢Å¡Â¡ YÃƒÅ“KSEK Ãƒâ€“DÃƒÅ“L / AZ KÃ„Â°Ã…ÂÃ„Â°"
        )

    if data.get("live"):

        text += (
            "\n\nÄŸÅ¸â€Â´ "
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
# KÃ„Â°Ã…ÂÃ„Â°SEL ALARM
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

    if (
        people <= 0
        or
        people > people_limit
    ):
        return False

    text = (
        "ÄŸÅ¸ÂÂ¯ KÃ„Â°Ã…ÂÃ„Â°SEL ALARM\n\n"
        f"ÄŸÅ¸â€˜Â¤ KullanÃ„Â±cÃ„Â±: "
        f"{data['username']}\n"
        f"ÄŸÅ¸ÂÂ TÃƒÂ¼r: "
        f"{data['box_name']}\n"
        f"ÄŸÅ¸Âªâ„¢ Coin: "
        f"{data['coins']}\n"
        f"ÄŸÅ¸â€˜Â¥ KiÃ…Å¸i: "
        f"{data['people']}\n"
        f"ÄŸÅ¸â„¢â€¹ KatÃ„Â±lan: "
        f"{data['joined']}\n"
        f"ÄŸÅ¸â€œË† Oran: "
        f"{data['rate']}\n"
        f"ÄŸÅ¸â€˜â‚¬ Ã„Â°zlenme: "
        f"{data['view']}\n"
        "\nÄŸÅ¸Å¡Â¨ AyarladÃ„Â±Ã„Å¸Ã„Â±n alarma uyuyor!"
    )

    if data.get("live"):

        text += (
            "\n\nÄŸÅ¸â€Â´ "
            f'<a href="{data["live"]}">'
            "TIKTOK CANLI YAYIN"
            "</a>"
        )

    ok, _ = await telegram_api(
        "sendMessage",
        {
            "chat_id":
                user_id,

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
# TAKÃ„Â°P BÃ„Â°LDÃ„Â°RÃ„Â°MÃ„Â°
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

        if not get_vip(user_id):
            continue

        settings = get_user_settings(
            user_id
        )

        if (
            data["type"] == "GOODY BAG"
            and
            settings["mute_goody"]
        ):
            continue

        if (
            data["type"] == "CHEST"
            and
            settings["mute_chest"]
        ):
            continue

        text = (
            "ÄŸÅ¸â€˜Â¤ TAKÃ„Â°P ETTÃ„Â°Ã„ÂÃ„Â°N YAYINCI\n\n"
            f"ÄŸÅ¸â€˜Â¤ @{username}\n"
            f"ÄŸÅ¸ÂÂ {data['box_name']}\n"
            f"ÄŸÅ¸Âªâ„¢ Coin: {data['coins']}\n"
            f"ÄŸÅ¸â€˜Â¥ KiÃ…Å¸i: {data['people']}\n"
            f"ÄŸÅ¸â„¢â€¹ KatÃ„Â±lan: {data['joined']}\n"
            f"ÄŸÅ¸â€œË† Oran: {data['rate']}\n"
        )

        if is_smart_alarm(data):

            text += (
                "\nÄŸÅ¸Å¡Â¨ AKILLI ALARM\n"
                "Ã¢Å¡Â¡ YÃƒÅ“KSEK Ãƒâ€“DÃƒÅ“L / AZ KÃ„Â°Ã…ÂÃ„Â°\n"
            )

        if data.get("live"):

            text += (
                "\nÄŸÅ¸â€Â´ "
                f'<a href="{data["live"]}">'
                "TIKTOK CANLI YAYIN"
                "</a>"
            )

        await telegram_api(
            "sendMessage",
            {
                "chat_id":
                    user_id,

                "text":
                    text,

                "parse_mode":
                    "HTML",

                "disable_web_page_preview":
                    True,
            }
        )


# ============================================================
# OLAY BÃ„Â°LDÃ„Â°RÃ„Â°MÃ„Â°
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
                "[TEKRAR ENGELLENDÃ„Â°]",
                room
            )

            return

        last_event_notification[room] = now

    await send_telegram_message(
        data
    )

    try:

        personal_users = (
            get_personal_alarm_users()
        )

        for (
            user_id,
            alarm_coins,
            alarm_people
        ) in personal_users:

            settings = {
                "alarm_coins":
                    alarm_coins,

                "alarm_people":
                    alarm_people,
            }

            try:

                await send_personal_alarm(
                    user_id,
                    data,
                    settings
                )

            except Exception as e:

                print(
                    "[KÃ„Â°Ã…ÂÃ„Â°SEL ALARM HATASI]",
                    user_id,
                    repr(e)
                )

    except Exception as e:

        print(
            "[KÃ„Â°Ã…ÂÃ„Â°SEL ALARM LÃ„Â°STESÃ„Â° HATASI]",
            repr(e)
        )

    try:

        await send_follow_notifications(
            data
        )

    except Exception as e:

        print(
            "[TAKÃ„Â°P BÃ„Â°LDÃ„Â°RÃ„Â°M HATASI]",
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
# ADMIN
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
# ADMIN VIP YETKÃ„Â° RAPORU
# ============================================================

def build_vip_admin_report(
    vip,
    action="VIP AKTÃ„Â°F"
):

    if not vip:
        return ""

    return (

        f"ÄŸÅ¸â€˜â€˜ {action}\n"
        "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â\n\n"

        "ÄŸÅ¸â€˜Â¤ VIP ÃƒÅ“YE BÃ„Â°LGÃ„Â°LERÃ„Â°\n"
        f"Ã¢â‚¬Â¢ Ad: "
        f"{vip.get('first_name') or '-'}\n"

        f"Ã¢â‚¬Â¢ KullanÃ„Â±cÃ„Â± adÃ„Â±: "
        f"@{vip.get('username') or 'yok'}\n"

        f"Ã¢â‚¬Â¢ Telegram ID: "
        f"{vip.get('user_id')}\n"

        f"Ã¢â‚¬Â¢ Kalan VIP: "
        f"{format_remaining(vip['expires_at'])}\n\n"

        "ÄŸÅ¸â€˜â€˜ ADMIN YETKÃ„Â°LERÃ„Â°\n"
        "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â\n"

        "ÄŸÅ¸â€˜â€˜ VIP YÃƒâ€“NETÃ„Â°MÃ„Â°\n"
        "Ã¢â‚¬Â¢ /davet Ã¢â‚¬â€ VIP davet oluÃ…Å¸turma\n"
        "Ã¢â‚¬Â¢ /uyeler Ã¢â‚¬â€ VIP ÃƒÂ¼yeleri gÃƒÂ¶rÃƒÂ¼ntÃƒÂ¼leme\n"
        "Ã¢â‚¬Â¢ /vipbilgi ID Ã¢â‚¬â€ VIP bilgisi\n"
        "Ã¢â‚¬Â¢ /uzatvip ID gÃƒÂ¼n Ã¢â‚¬â€ VIP uzatma\n"
        "Ã¢â‚¬Â¢ /silvip ID Ã¢â‚¬â€ VIP silme\n\n"

        "ÄŸÅ¸Å’Â RADAR YÃƒâ€“NETÃ„Â°MÃ„Â°\n"
        "Ã¢â‚¬Â¢ ÄŸÅ¸Å¸Âª Goody Bag radarÃ„Â±nÃ„Â± yÃƒÂ¶netme\n"
        "Ã¢â‚¬Â¢ ÄŸÅ¸Å¸Â¨ Hazine SandÃ„Â±Ã„Å¸Ã„Â± radarÃ„Â±nÃ„Â± yÃƒÂ¶netme\n"
        "Ã¢â‚¬Â¢ ÄŸÅ¸â€Â Radar verilerini gÃƒÂ¶rÃƒÂ¼ntÃƒÂ¼leme\n"
        "Ã¢â‚¬Â¢ ÄŸÅ¸Ââ€º Arama ve filtreler\n\n"

        "ÄŸÅ¸â€˜Â¥ VIP SÃ„Â°STEMÃ„Â°\n"
        "Ã¢â‚¬Â¢ VIP ÃƒÂ¼yeleri yÃƒÂ¶netme\n"
        "Ã¢â‚¬Â¢ VIP sÃƒÂ¼relerini deÃ„Å¸iÃ…Å¸tirme\n"
        "Ã¢â‚¬Â¢ VIP eriÃ…Å¸imini aÃƒÂ§ma/kapatma\n"
        "Ã¢â‚¬Â¢ VIP durumlarÃ„Â±nÃ„Â± gÃƒÂ¶rÃƒÂ¼ntÃƒÂ¼leme\n\n"

        "ÄŸÅ¸â€œÂ¢ BÃ„Â°LDÃ„Â°RÃ„Â°M YÃƒâ€“NETÃ„Â°MÃ„Â°\n"
        "Ã¢â‚¬Â¢ VIP aktivasyon bildirimleri\n"
        "Ã¢â‚¬Â¢ VIP sÃƒÂ¼re uzatma bildirimleri\n"
        "Ã¢â‚¬Â¢ VIP silme bildirimleri\n"
        "Ã¢â‚¬Â¢ GÃƒÂ¼nlÃƒÂ¼k VIP raporlarÃ„Â±\n\n"

        "ÄŸÅ¸â€Â ADMIN KOMUTLARI\n"
        "Ã¢â‚¬Â¢ /davet\n"
        "Ã¢â‚¬Â¢ /uyeler\n"
        "Ã¢â‚¬Â¢ /vipbilgi\n"
        "Ã¢â‚¬Â¢ /uzatvip\n"
        "Ã¢â‚¬Â¢ /silvip\n"
        "Ã¢â‚¬Â¢ /yardim\n\n"

        "Ã¢Å“â€¦ Bu bÃƒÂ¶lÃƒÂ¼m ADMIN yetkilerini gÃƒÂ¶sterir.\n"
        "Ã¢ÂÅ’ VIP kullanÃ„Â±cÃ„Â±nÃ„Â±n kiÃ…Å¸isel yetkileri "
        "bu raporda gÃƒÂ¶sterilmez."

    )


async def notify_admin_vip(
    vip,
    action="VIP AKTÃ„Â°F"
):

    report = build_vip_admin_report(
        vip,
        action
    )

    if not report:
        return False

    return await send_admin(
        report
    )


# ============================================================
# GÃƒÅ“NLÃƒÅ“K VIP RAPORU
#
# SADECE KALAN GÃƒÅ“N GÃƒâ€“STERÃ„Â°LÃ„Â°R.
# Alarm / takip / sessiz ayarlarÃ„Â± YOK.
# ============================================================

def build_daily_vip_report():

    rows = list_vips()

    if not rows:

        return (
            "ÄŸÅ¸â€˜â€˜ GÃƒÅ“NLÃƒÅ“K VIP RAPORU\n"
            "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â\n\n"
            "ÄŸÅ¸â€œÂ­ Aktif VIP ÃƒÂ¼ye yok."
        )

    lines = [
        "ÄŸÅ¸â€˜â€˜ GÃƒÅ“NLÃƒÅ“K VIP RAPORU",
        "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â",
        ""
    ]

    for row in rows:

        user_id = row[0]
        username = row[1]
        first_name = row[2]
        expires_at = row[3]

        name = (
            first_name
            or username
            or "Bilinmiyor"
        )

        # GÃƒÂ¼nlÃƒÂ¼k raporda SADECE gÃƒÂ¼n bilgisi
        remaining = format_remaining_days(
            expires_at
        )

        lines.extend([
            f"ÄŸÅ¸â€˜Â¤ {name}",
            f"ÄŸÅ¸â€œÂ± @{username or 'yok'}",
            f"ÄŸÅ¸â€ â€ {user_id}",
            f"Ã¢ÂÂ³ Kalan: {remaining}",
            ""
        ])

    return "\n".join(lines)


async def send_daily_vip_report():

    try:

        # Ãƒâ€“nce sÃƒÂ¼resi bitenleri temizle
        await cleanup_expired_vips()

        report = build_daily_vip_report()

        await send_admin(
            report
        )

        print(
            "[VIP RAPOR] GÃƒÂ¼nlÃƒÂ¼k VIP raporu gÃƒÂ¶nderildi."
        )

    except Exception as e:

        print(
            "[GÃƒÅ“NLÃƒÅ“K VIP RAPOR HATASI]",
            repr(e)
        )


async def daily_vip_report_loop():

    while True:

        try:

            now = datetime.now(
                TURKEY_TZ
            )

            target = now.replace(
                hour=VIP_REPORT_HOUR,
                minute=VIP_REPORT_MINUTE,
                second=0,
                microsecond=0
            )

            if target <= now:

                target += timedelta(
                    days=1
                )

            wait_seconds = (
                target - now
            ).total_seconds()

            print(
                "[VIP RAPOR]",
                f"Sonraki rapor: {target.isoformat()}",
                f"| {int(wait_seconds)} saniye"
            )

            await asyncio.sleep(
                max(
                    1,
                    wait_seconds
                )
            )

            await send_daily_vip_report()

        except Exception as e:

            print(
                "[GÃƒÅ“NLÃƒÅ“K RAPOR LOOP HATASI]",
                repr(e)
            )

            # Hata olursa loop tamamen ÃƒÂ¶lmesin
            await asyncio.sleep(
                60
            )


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
            data.get("auth_date")
        )

        if not auth_date:
            return None

        if (
            int(time.time())
            -
            auth_date
            >
            86400
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
# MINI APP
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

<title>Ãƒâ€“DÃƒÅ“L AVCISI</title>

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
 box-shadow:0 0 15px rgba(255,70,70,.35);
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

.copy-user{
 min-width:0;
 max-width:78%;
 overflow:hidden;
 text-overflow:ellipsis;
 white-space:nowrap;
 border:0;
 background:transparent;
 color:#fff;
 padding:3px 5px;
 margin:0;
 border-radius:8px;
 font-size:14px;
 font-weight:1000;
 text-align:left;
 cursor:pointer;
}

.copy-user:active{
 transform:scale(.97);
 background:#242a3b;
}

.copy-user.copied{
 color:#69ff9a;
 background:#14251c;
}

.copy-icon{
 margin-left:4px;
 font-size:12px;
 opacity:.8;
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

 .copy-user{
  font-size:13px;
  max-width:76%;
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

 .copy-user{
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
  ÄŸÅ¸Ââ€  Ãƒâ€“DÃƒÅ“L AVCISI
 </div>

 <div class="subtitle">
  ÄŸÅ¸Å¸Âª GOODY BAG Ã¢â‚¬Â¢ ÄŸÅ¸Å¸Â¨ HAZÃ„Â°NE SANDIÃ„ÂI
 </div>

 <div id="status" class="status">
  ÄŸÅ¸Å¸Â¡ RADAR BAÃ„ÂLANIYOR...
 </div>

</div>

<div class="latest-box">

 <div class="latest-title">
  ÄŸÅ¸â€Â¥ SON YAKALANAN
 </div>

 <div id="latest"></div>

</div>

<input
 id="search"
 class="search"
 type="text"
 placeholder="ÄŸÅ¸â€Â KullanÃ„Â±cÃ„Â± ara..."
>

<div class="filters">

 <button class="filter active" data-filter="ALL">
  ÄŸÅ¸â€œÂ¡ TÃƒÅ“MÃƒÅ“
 </button>

 <button class="filter" data-filter="GOODY">
  ÄŸÅ¸Å¸Âª GOODY
 </button>

 <button class="filter" data-filter="CHEST">
  ÄŸÅ¸Å¸Â¨ CHEST
 </button>

 <button class="filter" data-filter="COIN100">
  ÄŸÅ¸Âªâ„¢ 100+ COIN
 </button>

 <button class="filter" data-filter="PEOPLE50">
  ÄŸÅ¸â€˜Â¥ 50+
 </button>

 <button class="filter" data-filter="ALARM">
  ÄŸÅ¸Å¡Â¨ ALARM
 </button>

</div>

<div class="radar-grid">

<div class="panel goody">

 <div class="panel-title">

  <div class="panel-name">
   ÄŸÅ¸Å¸Âª GOODY BAG
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
   ÄŸÅ¸Å¸Â¨ HAZÃ„Â°NE SANDIÃ„ÂI
  </div>

  <div id="chestCounter" class="panel-count">
   0
  </div>

 </div>

 <div id="chests"></div>

</div>

</div>

<div class="footer">
 Ã¢Å¡Â¡ Ãƒâ€“DÃƒÅ“L AVCISI Ã¢â‚¬Â¢ CANLI RADAR
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


/* =========================================================
   KULLANICI ADI KOPYALAMA
   @username kopyalar
   ========================================================= */

async function copyUsername(username, el){

 const cleanName =
  String(username ?? "")
   .replace(/^@+/,"")
   .trim();

 if(!cleanName)
  return;

 const value =
  "@" + cleanName;

 const oldHtml =
  el.innerHTML;

 try{

  if(
   navigator.clipboard &&
   window.isSecureContext
  ){

   await navigator.clipboard.writeText(
    value
   );

  }
  else{

   const textarea =
    document.createElement("textarea");

   textarea.value =
    value;

   textarea.style.position =
    "fixed";

   textarea.style.left =
    "-9999px";

   textarea.style.top =
    "0";

   textarea.style.opacity =
    "0";

   document.body.appendChild(
    textarea
   );

   textarea.focus();
   textarea.select();

   document.execCommand(
    "copy"
   );

   textarea.remove();

  }

  el.classList.add(
   "copied"
  );

  el.innerHTML =
   "Ã¢Å“â€¦ KOPYALANDI";

  setTimeout(
   ()=>{
    el.classList.remove(
     "copied"
    );

    el.innerHTML =
     oldHtml;
   },
   1200
  );

 }
 catch(error){

  console.error(
   "Kopyalama hatasÃ„Â±:",
   error
  );

 }

}


function filterItems(
 items,
 type
){

 let result =
  latestFive(items);

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

  const key =
   itemKey(item);

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
  document.getElementById(
   "latest"
  );

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
   '<div class="empty">HenÃƒÂ¼z kayÃ„Â±t yok.</div>';

  return;

 }

 const item =
  all[0];

 const isGoody =
  item._type === "GOODY";

 const icon =
  isGoody
  ? "ÄŸÅ¸Å¸Âª"
  : "ÄŸÅ¸Å¸Â¨";

 const cls =
  isGoody
  ? "goody"
  : "chest";

 const alarm =
  isAlarm(item);

 const username =
  String(
   item.username ?? ""
  )
   .replace(/^@+/,"");

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

    <button
     type="button"
     class="copy-user latest-user"
     onclick='copyUsername(${JSON.stringify(username)}, this)'
    >
     @${escapeHtml(username)}
     <span class="copy-icon">ÄŸÅ¸â€œâ€¹</span>
    </button>

    <div class="latest-info">

     <span>ÄŸÅ¸Âªâ„¢ ${escapeHtml(item.coins)}</span>
     <span>Ã¢â‚¬Â¢</span>
     <span>ÄŸÅ¸â€˜Â¥ ${escapeHtml(item.people)}</span>
     <span>Ã¢â‚¬Â¢</span>
     <span>ÄŸÅ¸â€œË† ${escapeHtml(item.rate)}</span>
     <span>Ã¢â‚¬Â¢</span>
     <span>ÄŸÅ¸â€˜â‚¬ ${escapeHtml(item.view)}</span>

    </div>

   </div>

   ${
    alarm
    ?
    `<div class="alarm-badge">ÄŸÅ¸Å¡Â¨</div>`
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
     ÄŸÅ¸â€Â´ GÃ„Â°T
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
  document.getElementById(
   elementId
  );

 const counter =
  document.getElementById(
   counterId
  );

 const items =
  filterItems(
   originalItems,
   type
  );

 counter.textContent =
  items.length;

 if(!items.length){

  container.innerHTML =
   '<div class="empty">Ã¢Å¡Â¡ Veri yok.</div>';

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

   const username =
    String(
     item.username ?? ""
    )
     .replace(/^@+/,"");

   return `

    <div class="
     card
     ${isNew ? "new-card" : ""}
     ${alarm ? "alarm" : ""}
    ">

     <div class="user-row">

      <button
       type="button"
       class="copy-user"
       onclick='copyUsername(${JSON.stringify(username)}, this)'
      >

       ${icon}

       @${escapeHtml(username)}

       <span class="copy-icon">
        ÄŸÅ¸â€œâ€¹
       </span>

      </button>

      <div style="
       display:flex;
       gap:4px;
      ">

       ${
        alarm
        ?
        `
        <div class="alarm-badge">
         ÄŸÅ¸Å¡Â¨
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
         Ã¢Å¡Â¡ YENÃ„Â°
        </div>
        `
        :
        ""
       }

      </div>

     </div>

     <div class="info-grid">

      <div class="info">
       ÄŸÅ¸Âªâ„¢ COIN
       <b>${escapeHtml(item.coins)}</b>
      </div>

      <div class="info">
       ÄŸÅ¸â€˜Â¥ KÃ„Â°Ã…ÂÃ„Â°
       <b>${escapeHtml(item.people)}</b>
      </div>

      <div class="info">
       ÄŸÅ¸â„¢â€¹ KATILAN
       <b>${escapeHtml(item.joined)}</b>
      </div>

      <div class="info">
       ÄŸÅ¸â€œË† ORAN
       <b>${escapeHtml(item.rate)}</b>
      </div>

      <div class="info">
       ÄŸÅ¸â€˜â‚¬ Ã„Â°ZLENME
       <b>${escapeHtml(item.view)}</b>
      </div>

      <div class="info">
       ÄŸÅ¸ÂÂ  ODA
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
       ÄŸÅ¸Å¡Â¨ YÃƒÅ“KSEK Ãƒâ€“DÃƒÅ“L / AZ KÃ„Â°Ã…ÂÃ„Â°
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
       ÄŸÅ¸â€Â´ TIKTOK CANLI
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
  "ÄŸÅ¸Å¸Âª",
  "GOODY"
 );

 renderItems(
  radarData.chests,
  "chests",
  "chestCounter",
  "ÄŸÅ¸Å¸Â¨",
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

   if(
    response.status === 401
   ){

    throw new Error(
     "VIP eriÃ…Å¸imi gerekli"
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
   document.getElementById(
    "status"
   );

  status.className =
   "status";

  status.textContent =
   "ÄŸÅ¸Å¸Â¢ RADAR AKTÃ„Â°F Ã¢â‚¬Â¢ CANLI VERÃ„Â°";

  renderRadar();

  firstLoad = false;

 }
 catch(error){

  console.error(
   "Radar hatasÃ„Â±:",
   error
  );

  const status =
   document.getElementById(
    "status"
   );

  status.className =
   "status error";

  status.textContent =
   "ÄŸÅ¸â€Â´ " + error.message;

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
      x.classList.remove(
       "active"
      )
     );

    this.classList.add(
     "active"
    );

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
                    "GeÃƒÂ§ersiz Telegram eriÃ…Å¸imi"
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
                    "VIP eriÃ…Å¸imi gerekli"
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
        f"[HTTP] Sunucu baÃ…Å¸ladÃ„Â±: {PORT}"
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
                "ÄŸÅ¸Å’Â VIP RADARI AÃƒâ€¡",
                web_app=WebAppInfo(
                    url=f"{BASE_URL}/miniapp"
                )
            )
        ]]
    )


# ============================================================
# VIP YETKÃ„Â°LERÃ„Â°
# ============================================================

def vip_permissions_text(vip):

    remaining = format_remaining(
        vip["expires_at"]
    )

    settings = get_user_settings(
        vip["user_id"]
    )

    follows = get_follows(
        vip["user_id"]
    )

    if settings["alarm_coins"] > 0:

        alarm_text = (
            f"ÄŸÅ¸Å¸Â¢ "
            f"{settings['alarm_coins']}+ coin / "
            f"{settings['alarm_people']} "
            f"veya daha az kiÃ…Å¸i"
        )

    else:

        alarm_text = (
            "ÄŸÅ¸â€Â´ KiÃ…Å¸isel alarm kapalÃ„Â±"
        )

    return (

        "ÄŸÅ¸â€˜â€˜ VIP ERÃ„Â°Ã…ÂÃ„Â°MÃ„Â°N AKTÃ„Â°F\n\n"

        f"Ã¢ÂÂ³ Kalan sÃƒÂ¼re: "
        f"{remaining}\n\n"

        "ÄŸÅ¸Å’Â VIP RADAR\n"
        "Ã¢â‚¬Â¢ CanlÃ„Â± Goody Bag radarÃ„Â±\n"
        "Ã¢â‚¬Â¢ CanlÃ„Â± Hazine SandÃ„Â±Ã„Å¸Ã„Â± radarÃ„Â±\n"
        "Ã¢â‚¬Â¢ Arama ve filtreler\n\n"

        "ÄŸÅ¸ÂÂ¯ KÃ„Â°Ã…ÂÃ„Â°SEL ALARM\n"
        f"Ã¢â‚¬Â¢ {alarm_text}\n\n"

        "ÄŸÅ¸â€˜Â¤ YAYINCI TAKÃ„Â°P\n"
        f"Ã¢â‚¬Â¢ Takip edilen: "
        f"{len(follows)} yayÃ„Â±ncÃ„Â±\n"
        "Ã¢â‚¬Â¢ Takip ettiÃ„Å¸in yayÃ„Â±ncÃ„Â± yakalanÃ„Â±nca "
        "ÃƒÂ¶zel mesaj alÃ„Â±rsÃ„Â±n.\n\n"

        "ÄŸÅ¸â€â€¢ SESSÃ„Â°ZE ALMA\n"
        "Ã¢â‚¬Â¢ Goody Bag\n"
        "Ã¢â‚¬Â¢ Hazine SandÃ„Â±Ã„Å¸Ã„Â±\n\n"

        "ÄŸÅ¸â€œÅ’ KOMUTLAR\n"
        "/alarm\n"
        "/takip\n"
        "/takipler\n"
        "/takipsil\n"
        "/sessiz\n"
        "/yardim"

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

    # Ãƒâ€“nce mevcut VIP kontrolÃƒÂ¼
    vip = get_vip(
        user.id
    )

    if vip:

        await update.message.reply_text(
            vip_permissions_text(vip),
            reply_markup=vip_keyboard()
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

        result = use_invite(
            invite_token,
            user
        )

        if result:

            _, duration_days = result

            vip = get_vip(
                user.id
            )

            await update.message.reply_text(

                "ÄŸÅ¸Ââ€° HOÃ…Â GELDÃ„Â°N!\n\n"

                f"Ã¢Å“â€¦ VIP eriÃ…Å¸imin aÃƒÂ§Ã„Â±ldÃ„Â±.\n"
                f"Ã¢ÂÂ³ VIP sÃƒÂ¼resi: "
                f"{duration_days} gÃƒÂ¼n\n\n"

                +
                vip_permissions_text(
                    vip
                ),

                reply_markup=vip_keyboard()

            )

            try:

                await notify_admin_vip(
                    vip,
                    f"ÄŸÅ¸ÂÅ¸ YENÃ„Â° VIP ÃƒÅ“YE "
                    f"({duration_days} GÃƒÅ“N)"
                )

            except Exception as e:

                print(
                    "[ADMIN VIP RAPOR HATASI]",
                    repr(e)
                )

            return

    await update.message.reply_text(

        "ÄŸÅ¸â€â€™ Bu bot davet/VIP sistemiyle "
        "ÃƒÂ§alÃ„Â±Ã…Å¸Ã„Â±yor.\n\n"

        "VIP eriÃ…Å¸imin yok.\n"

        "YÃƒÂ¶netici tarafÃ„Â±ndan gÃƒÂ¶nderilen "
        "davet baÃ„Å¸lantÃ„Â±sÃ„Â±yla giriÃ…Å¸ yapabilirsin."

    )


# ============================================================
# DAVET
#
# /davet
# /davet 7
# /davet 20 gÃƒÂ¼n
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
            "Ã¢ÂÅ’ Yetkin yok."
        )

        return

    days = VIP_DAYS

    if context.args:

        # Ã„Â°lk sayÃ„Â± gÃƒÂ¼n olarak alÃ„Â±nÃ„Â±r.
        # /davet 20 gÃƒÂ¼n -> 20
        # /davet 7 -> 7
        match = re.search(
            r"\d+",
            context.args[0]
        )

        if not match:

            await update.message.reply_text(
                "Ã¢ÂÅ’ GeÃƒÂ§ersiz gÃƒÂ¼n.\n\n"
                "Ãƒâ€“rnek:\n"
                "/davet 7\n"
                "/davet 20 gÃƒÂ¼n"
            )

            return

        days = safe_int(
            match.group(0)
        )

        if days <= 0:

            await update.message.reply_text(
                "Ã¢ÂÅ’ GÃƒÂ¼n sayÃ„Â±sÃ„Â± 0'dan bÃƒÂ¼yÃƒÂ¼k olmalÃ„Â±."
            )

            return

    token, days = create_invite(
        days
    )

    link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=invite_{token}"
    )

    await update.message.reply_text(

        "ÄŸÅ¸ÂÅ¸ VIP DAVET LÃ„Â°NKÃ„Â°\n\n"

        f"Ã¢ÂÂ³ VIP eriÃ…Å¸im sÃƒÂ¼resi: "
        f"{days} gÃƒÂ¼n\n"

        "ÄŸÅ¸â€Â KullanÃ„Â±m: Tek kiÃ…Å¸i\n"

        "Ã¢ÂÂ° Link geÃƒÂ§erliliÃ„Å¸i: 24 saat\n\n"

        "ÄŸÅ¸â€â€” DAVET LÃ„Â°NKÃ„Â°:\n"
        f"{link}"

    )


# ============================================================
# ÃƒÅ“YELER
# ============================================================

def vip_delete_keyboard(user_id):

    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "ÄŸÅ¸â€â€™ VIP SÃ„Â°L",
                callback_data=f"silvip:{user_id}"
            )
        ]]
    )


async def uyeler_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "Ã¢ÂÅ’ Yetkin yok."
        )

        return

    # Ãƒâ€“nce sÃƒÂ¼resi bitenleri temizle
    await cleanup_expired_vips()

    rows = list_vips()

    if not rows:

        await update.message.reply_text(
            "ÄŸÅ¸â€œÂ­ Aktif VIP ÃƒÂ¼ye yok."
        )

        return

    await update.message.reply_text(
        "ÄŸÅ¸â€˜â€˜ AKTÃ„Â°F VIP ÃƒÅ“YELER\n"
        "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â"
    )

    for row in rows:

        user_id = row[0]
        username = row[1]
        first_name = row[2]
        expires = row[3]

        remaining = format_remaining(
            expires
        )

        name = (
            first_name
            or username
            or "Bilinmiyor"
        )

        text = (

            f"ÄŸÅ¸â€˜Â¤ {name}\n"

            f"ÄŸÅ¸â€œÂ± @{username or 'yok'}\n"

            f"ÄŸÅ¸â€ â€ {user_id}\n"

            f"Ã¢ÂÂ³ Kalan: {remaining}"

        )

        await update.message.reply_text(
            text,
            reply_markup=vip_delete_keyboard(
                user_id
            )
        )


# ============================================================
# VIP CALLBACK
#
# /uyeler ekranÃ„Â±ndaki SÃ„Â°L butonu
# ============================================================

async def vip_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = query.from_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await query.answer(
            "Ã¢ÂÅ’ Yetkin yok.",
            show_alert=True
        )

        return

    data = query.data or ""

    if not data.startswith(
        "silvip:"
    ):
        return

    user_id = safe_int(
        data.split(
            ":",
            1
        )[1]
    )

    if not user_id:

        await query.answer(
            "Ã¢ÂÅ’ GeÃƒÂ§ersiz kullanÃ„Â±cÃ„Â±.",
            show_alert=True
        )

        return

    old_vip = get_vip(
        user_id
    )

    if not old_vip:

        await query.edit_message_text(
            "Ã¢â€Â¹Ã¯Â¸Â Bu VIP ÃƒÂ¼yelik zaten aktif deÃ„Å¸il."
        )

        return

    removed = remove_vip(
        user_id
    )

    if not removed:

        await query.edit_message_text(
            "Ã¢ÂÅ’ VIP silinemedi."
        )

        return

    notified = False

    try:

        notified = await notify_vip_removed(
            user_id
        )

    except Exception as e:

        print(
            "[VIP SIL BÃ„Â°LDÃ„Â°RÃ„Â°M HATASI]",
            repr(e)
        )

    try:

        await send_admin(

            "ÄŸÅ¸â€â€™ VIP ÃƒÅ“YE SÃ„Â°LÃ„Â°NDÃ„Â°\n"
            "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â\n\n"

            f"ÄŸÅ¸â€˜Â¤ "
            f"{old_vip.get('first_name') or '-'}\n"

            f"ÄŸÅ¸â€œÂ± @"
            f"{old_vip.get('username') or 'yok'}\n"

            f"ÄŸÅ¸â€ â€ "
            f"{old_vip.get('user_id')}\n\n"

            "Ã¢ÂÅ’ VIP eriÃ…Å¸imi kapatÃ„Â±ldÃ„Â±."

        )

    except Exception as e:

        print(
            "[ADMIN VIP SILME HATASI]",
            repr(e)
        )

    await query.edit_message_text(

        "ÄŸÅ¸â€â€™ VIP ÃƒÅ“YE SÃ„Â°LÃ„Â°NDÃ„Â°\n"
        "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â\n\n"

        f"ÄŸÅ¸â€˜Â¤ "
        f"{old_vip.get('first_name') or '-'}\n"

        f"ÄŸÅ¸â€œÂ± @"
        f"{old_vip.get('username') or 'yok'}\n"

        f"ÄŸÅ¸â€ â€ "
        f"{old_vip.get('user_id')}\n\n"

        "Ã¢ÂÅ’ VIP eriÃ…Å¸imi kapatÃ„Â±ldÃ„Â±.\n"

        +
        (
            "ÄŸÅ¸â€œÂ© KullanÃ„Â±cÃ„Â±ya bildirim gÃƒÂ¶nderildi."
            if notified
            else
            "Ã¢Å¡Â Ã¯Â¸Â KullanÃ„Â±cÃ„Â±ya bildirim gÃƒÂ¶nderilemedi."
        )

    )


# ============================================================
# SIL VIP
#
# Eski /silvip ID komutu da ÃƒÂ§alÃ„Â±Ã…Å¸Ã„Â±r.
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
            "Ã¢ÂÅ’ Yetkin yok."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "KullanÃ„Â±m:\n"
            "/silvip 123456789\n\n"
            "Alternatif:\n"
            "/uyeler komutundaki "
            "ÄŸÅ¸â€â€™ VIP SÃ„Â°L butonunu kullanabilirsin."
        )

        return

    user_id = safe_int(
        context.args[0]
    )

    if not user_id:

        await update.message.reply_text(
            "Ã¢ÂÅ’ GeÃƒÂ§ersiz kullanÃ„Â±cÃ„Â± ID."
        )

        return

    old_vip = get_vip(
        user_id
    )

    removed = remove_vip(
        user_id
    )

    if not removed:

        await update.message.reply_text(
            "Ã¢ÂÅ’ Bu kullanÃ„Â±cÃ„Â± VIP deÃ„Å¸il."
        )

        return

    notified = False

    try:

        notified = await notify_vip_removed(
            user_id
        )

    except Exception as e:

        print(
            "[VIP SIL BÃ„Â°LDÃ„Â°RÃ„Â°M HATASI]",
            repr(e)
        )

    try:

        if old_vip:

            await send_admin(

                "ÄŸÅ¸â€â€™ VIP ÃƒÅ“YE SÃ„Â°LÃ„Â°NDÃ„Â°\n"
                "Ã¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€ÂÃ¢â€Â\n\n"

                f"ÄŸÅ¸â€˜Â¤ "
                f"{old_vip.get('first_name') or '-'}\n"

                f"ÄŸÅ¸â€œÂ± @"
                f"{old_vip.get('username') or 'yok'}\n"

                f"ÄŸÅ¸â€ â€ "
                f"{old_vip.get('user_id')}\n\n"

                "Ã¢ÂÅ’ VIP eriÃ…Å¸imi kapatÃ„Â±ldÃ„Â±."

            )

        else:

            await send_admin(

                "ÄŸÅ¸â€â€™ VIP ÃƒÅ“YE SÃ„Â°LÃ„Â°NDÃ„Â°\n\n"
                f"ÄŸÅ¸â€ â€ {user_id}\n\n"
                "Ã¢ÂÅ’ VIP eriÃ…Å¸imi kapatÃ„Â±ldÃ„Â±."

            )

    except Exception as e:

        print(
            "[ADMIN VIP SILME HATASI]",
            repr(e)
        )

    await update.message.reply_text(

        "Ã¢Å“â€¦ VIP eriÃ…Å¸im silindi.\n"
        +
        (
            "ÄŸÅ¸â€œÂ© KullanÃ„Â±cÃ„Â±ya bildirim gÃƒÂ¶nderildi."
            if notified
            else
            "Ã¢Å¡Â Ã¯Â¸Â KullanÃ„Â±cÃ„Â±ya bildirim gÃƒÂ¶nderilemedi."
        )

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
            "Ã¢ÂÅ’ Yetkin yok."
        )

        return

    if len(context.args) < 2:

        await update.message.reply_text(
            "KullanÃ„Â±m:\n"
            "/uzatvip 123456789 30"
        )

        return

    user_id = safe_int(
        context.args[0]
    )

    days = safe_int(
        context.args[1]
    )

    if (
        not user_id
        or
        days <= 0
    ):

        await update.message.reply_text(
            "Ã¢ÂÅ’ GeÃƒÂ§ersiz ID veya gÃƒÂ¼n."
        )

        return

    new_expire = extend_vip(
        user_id,
        days
    )

    if not new_expire:

        await update.message.reply_text(
            "Ã¢ÂÅ’ Bu kullanÃ„Â±cÃ„Â± VIP deÃ„Å¸il."
        )

        return

    remaining_text = format_remaining(
        new_expire
    )

    await update.message.reply_text(

        "Ã¢Å“â€¦ VIP sÃƒÂ¼resi uzatÃ„Â±ldÃ„Â±.\n\n"

        f"ÄŸÅ¸â€ â€ {user_id}\n"

        f"Ã¢Ââ€¢ {days} gÃƒÂ¼n\n"

        f"ÄŸÅ¸â€œâ€¦ Yeni kalan sÃƒÂ¼re: "
        f"{remaining_text}"

    )

    await telegram_api(
        "sendMessage",
        {
            "chat_id":
                user_id,

            "text":
                (
                    "ÄŸÅ¸â€˜â€˜ VIP SÃƒÅ“REN UZATILDI!\n\n"

                    f"Ã¢Ââ€¢ {days} gÃƒÂ¼n eklendi.\n"

                    f"Ã¢ÂÂ³ Yeni kalan sÃƒÂ¼re:\n"
                    f"{remaining_text}"
                )
        }
    )

    try:

        updated_vip = get_vip(
            user_id
        )

        if updated_vip:

            await notify_admin_vip(
                updated_vip,
                f"Ã¢Ââ€¢ VIP SÃƒÅ“RESÃ„Â° UZATILDI "
                f"(+{days} GÃƒÅ“N)"
            )

    except Exception as e:

        print(
            "[ADMIN VIP UZATMA RAPOR HATASI]",
            repr(e)
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
            "Ã¢ÂÅ’ Yetkin yok."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "KullanÃ„Â±m:\n"
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
            "Ã¢ÂÅ’ Aktif VIP bulunamadÃ„Â±."
        )

        return

    remaining_text = format_remaining(
        vip["expires_at"]
    )

    settings = get_user_settings(
        user_id
    )

    follows = get_follows(
        user_id
    )

    await update.message.reply_text(

        "ÄŸÅ¸â€˜â€˜ VIP BÃ„Â°LGÃ„Â°\n\n"

        f"ÄŸÅ¸â€ â€ {vip['user_id']}\n"

        f"ÄŸÅ¸â€˜Â¤ {vip['first_name'] or '-'}\n"

        f"ÄŸÅ¸â€œÂ± @{vip['username'] or 'yok'}\n"

        f"Ã¢ÂÂ³ Kalan: {remaining_text}\n\n"

        "ÄŸÅ¸ÂÂ¯ KiÃ…Å¸isel alarm:\n"

        f"ÄŸÅ¸Âªâ„¢ {settings['alarm_coins']}+\n"

        f"ÄŸÅ¸â€˜Â¥ {settings['alarm_people']} "
        f"veya daha az\n\n"

        f"ÄŸÅ¸â€˜Â¤ Takip edilen yayÃ„Â±ncÃ„Â±: "
        f"{len(follows)}"

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

        "ÄŸÅ¸â€ â€ Telegram ID:\n"
        f"{user.id}"

    )


# ============================================================
# ALARM
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
            "ÄŸÅ¸â€â€™ Bu ÃƒÂ¶zellik VIP kullanÃ„Â±cÃ„Â±lar iÃƒÂ§indir."
        )

        return

    if len(context.args) < 2:

        settings = get_user_settings(
            user.id
        )

        if settings["alarm_coins"]:

            await update.message.reply_text(

                "ÄŸÅ¸ÂÂ¯ MEVCUT ALARM\n\n"

                f"ÄŸÅ¸Âªâ„¢ Coin: "
                f"{settings['alarm_coins']}+\n"

                f"ÄŸÅ¸â€˜Â¥ KiÃ…Å¸i: "
                f"{settings['alarm_people']} "
                f"veya daha az\n\n"

                "Kapatmak iÃƒÂ§in:\n"
                "/alarm kapat"

            )

        else:

            await update.message.reply_text(

                "ÄŸÅ¸ÂÂ¯ KÃ„Â°Ã…ÂÃ„Â°SEL ALARM\n\n"

                "Ãƒâ€“rnek:\n"
                "/alarm 200 5\n\n"

                "AnlamÃ„Â±:\n"
                "ÄŸÅ¸Âªâ„¢ 200+ coin\n"
                "ÄŸÅ¸â€˜Â¥ 5 veya daha az kiÃ…Å¸i\n\n"

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
            "ÄŸÅ¸â€â€¢ KiÃ…Å¸isel alarm kapatÃ„Â±ldÃ„Â±."
        )

        return

    coins = safe_int(
        context.args[0]
    )

    people = safe_int(
        context.args[1]
    )

    if (
        coins <= 0
        or
        people <= 0
    ):

        await update.message.reply_text(
            "Ã¢ÂÅ’ Ãƒâ€“rnek:\n/alarm 200 5"
        )

        return

    save_user_settings(
        user.id,
        alarm_coins=coins,
        alarm_people=people
    )

    await update.message.reply_text(

        "ÄŸÅ¸ÂÂ¯ KÃ„Â°Ã…ÂÃ„Â°SEL ALARM AKTÃ„Â°F\n\n"

        f"ÄŸÅ¸Âªâ„¢ {coins}+ coin\n"

        f"ÄŸÅ¸â€˜Â¥ {people} veya daha az kiÃ…Å¸i\n\n"

        "Uygun hazine geldiÃ„Å¸inde "
        "sana ÃƒÂ¶zel bildirim gÃƒÂ¶nderilecek."

    )


# ============================================================
# TAKÃ„Â°P
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
            "ÄŸÅ¸â€â€™ Bu ÃƒÂ¶zellik VIP kullanÃ„Â±cÃ„Â±lar iÃƒÂ§indir."
        )

        return

    if not context.args:

        follows = get_follows(
            user.id
        )

        if not follows:

            await update.message.reply_text(
                "ÄŸÅ¸â€œÂ­ Takip ettiÃ„Å¸in yayÃ„Â±ncÃ„Â± yok."
            )

        else:

            await update.message.reply_text(
                "ÄŸÅ¸â€˜Â¤ TAKÃ„Â°P LÃ„Â°STEN\n\n"
                +
                "\n".join(
                    f"Ã¢â‚¬Â¢ @{x}"
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
            f"Ã¢Å“â€¦ @{username} takip listesine eklendi.\n\n"
            "Bu yayÃ„Â±ncÃ„Â±dan uygun bir kayÃ„Â±t geldiÃ„Å¸inde "
            "bildirim doÃ„Å¸rudan sana gÃƒÂ¶nderilecek."
        )

    else:

        await update.message.reply_text(
            f"Ã¢â€Â¹Ã¯Â¸Â @{username} zaten takip ediliyor."
        )


# ============================================================
# TAKÃ„Â°PLER
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
            "ÄŸÅ¸â€â€™ Bu ÃƒÂ¶zellik VIP kullanÃ„Â±cÃ„Â±lar iÃƒÂ§indir."
        )

        return

    follows = get_follows(
        user.id
    )

    if not follows:

        await update.message.reply_text(
            "ÄŸÅ¸â€œÂ­ Takip listen boÃ…Å¸."
        )

        return

    await update.message.reply_text(

        "ÄŸÅ¸â€˜Â¤ TAKÃ„Â°P LÃ„Â°STEN\n\n"

        +
        "\n".join(
            f"Ã¢â‚¬Â¢ @{x}"
            for x in follows
        )

        +
        "\n\nÃ¢ÂÅ’ Silmek:\n"
        "/takipsil kullanÃ„Â±cÃ„Â±"

    )


# ============================================================
# TAKÃ„Â°P SÃ„Â°L
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
            "ÄŸÅ¸â€â€™ Bu ÃƒÂ¶zellik VIP kullanÃ„Â±cÃ„Â±lar iÃƒÂ§indir."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "KullanÃ„Â±m:\n"
            "/takipsil kullanÃ„Â±cÃ„Â±"
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
            f"Ã¢ÂÅ’ @{username} takipten ÃƒÂ§Ã„Â±karÃ„Â±ldÃ„Â±."
        )

    else:

        await update.message.reply_text(
            f"Ã¢â€Â¹Ã¯Â¸Â @{username} takip listende yok."
        )


# ============================================================
# SESSÃ„Â°Z
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
            "ÄŸÅ¸â€â€™ Bu ÃƒÂ¶zellik VIP kullanÃ„Â±cÃ„Â±lar iÃƒÂ§indir."
        )

        return

    if not context.args:

        settings = get_user_settings(
            user.id
        )

        await update.message.reply_text(

            "ÄŸÅ¸â€â€¢ SESSÃ„Â°ZE ALMA\n\n"

            f"ÄŸÅ¸Å¸Âª Goody: "
            f"{'KAPALI' if settings['mute_goody'] else 'AÃƒâ€¡IK'}\n"

            f"ÄŸÅ¸Å¸Â¨ Chest: "
            f"{'KAPALI' if settings['mute_chest'] else 'AÃƒâ€¡IK'}\n\n"

            "KullanÃ„Â±m:\n"
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
            "ÄŸÅ¸â€â€¢ Goody Bag bildirimleri sessize alÃ„Â±ndÃ„Â±."
        )

        return

    if value == "chest":

        save_user_settings(
            user.id,
            mute_chest=True
        )

        await update.message.reply_text(
            "ÄŸÅ¸â€â€¢ Hazine SandÃ„Â±Ã„Å¸Ã„Â± bildirimleri sessize alÃ„Â±ndÃ„Â±."
        )

        return

    if value == "kapat":

        save_user_settings(
            user.id,
            mute_goody=False,
            mute_chest=False
        )

        await update.message.reply_text(
            "ÄŸÅ¸â€â€ TÃƒÂ¼m bildirimler tekrar aÃƒÂ§Ã„Â±ldÃ„Â±."
        )

        return

    await update.message.reply_text(

        "KullanÃ„Â±m:\n"
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

    user = update.effective_user

    text = (
        "ÄŸÅ¸Ââ€  Ãƒâ€“DÃƒÅ“L AVCISI\n\n"

        "ÄŸÅ¸ÂÂ¯ KÃ„Â°Ã…ÂÃ„Â°SEL ALARM\n"
        "/alarm 200 5\n"
        "/alarm kapat\n\n"

        "ÄŸÅ¸â€˜Â¤ TAKÃ„Â°P\n"
        "/takip kullanici\n"
        "/takipler\n"
        "/takipsil kullanici\n\n"

        "ÄŸÅ¸â€â€¢ SESSÃ„Â°Z\n"
        "/sessiz goody\n"
        "/sessiz chest\n"
        "/sessiz kapat\n\n"

        "ÄŸÅ¸Å’Â VIP RADAR\n"
        "/start"
    )

    if (
        user
        and
        user.id == ADMIN_USER_ID
    ):

        text += (

            "\n\nÄŸÅ¸â€˜â€˜ ADMÃ„Â°N\n\n"

            "ÄŸÅ¸ÂÅ¸ VIP DAVET\n"
            "/davet\n"
            "/davet 7\n"
            "/davet 20 gÃƒÂ¼n\n\n"

            "ÄŸÅ¸â€˜Â¥ VIP YÃƒâ€“NETÃ„Â°MÃ„Â°\n"
            "/uyeler\n"
            "/vipbilgi ID\n"
            "/uzatvip ID gÃƒÂ¼n\n"
            "/silvip ID\n\n"

            "Ã¢â€Â¹Ã¯Â¸Â /uyeler iÃƒÂ§inde "
            "ÄŸÅ¸â€â€™ VIP SÃ„Â°L butonu da var."

        )

    await update.message.reply_text(
        text
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
                "| KÃ„Â°Ã…ÂÃ„Â°",
                data["people"],
                "| ALARM",
                is_smart_alarm(data)
            )

    except Exception as e:

        print(
            "[DÃ„Â°NLEYÃ„Â°CÃ„Â° HATASI]",
            repr(e)
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global http_session

    print("=" * 70)

    print(
        "ÄŸÅ¸Ââ€  Ãƒâ€“DÃƒÅ“L AVCISI BAÃ…ÂLIYOR"
    )

    print("=" * 70)

    init_db()

    http_session = (
        aiohttp.ClientSession()
    )

    await start_http_server()

    # ========================================================
    # BOT
    # ========================================================

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

    # /uyeler iÃƒÂ§indeki ÄŸÅ¸â€â€™ VIP SÃ„Â°L
    application.add_handler(
        CallbackQueryHandler(
            vip_callback,
            pattern=r"^silvip:"
        )
    )

    await application.initialize()

    await application.start()

    if application.updater:

        await application.updater.start_polling()

    print(
        "[BOT] Telegram bot baÃ…Å¸ladÃ„Â±."
    )

    # ========================================================
    # TELETHON
    # ========================================================

    client = TelegramClient(
        StringSession(
            STRING_SESSION
        ),
        API_ID,
        API_HASH,
    )

    await client.start()

    print(
        "[TELEGRAM] Ã„Â°stemci baÃ„Å¸landÃ„Â±."
    )

    client.add_event_handler(
        message_listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        )
    )

    # ========================================================
    # QUEUE
    # ========================================================

    asyncio.create_task(
        telegram_sender()
    )

    # ========================================================
    # VIP SÃƒÅ“RE KONTROLÃƒÅ“
    # ========================================================

    asyncio.create_task(
        vip_expiry_loop()
    )

    # ========================================================
    # GÃƒÅ“NLÃƒÅ“K VIP RAPORU
    # ========================================================

    asyncio.create_task(
        daily_vip_report_loop()
    )

    print(
        "[HAZIR] Goody Bag aktif."
    )

    print(
        "[HAZIR] Hazine SandÃ„Â±Ã„Å¸Ã„Â± aktif."
    )

    print(
        "[HAZIR] AkÃ„Â±llÃ„Â± alarm aktif."
    )

    print(
        "[HAZIR] KiÃ…Å¸isel alarm aktif."
    )

    print(
        "[HAZIR] YayÃ„Â±ncÃ„Â± takip sistemi aktif."
    )

    print(
        "[HAZIR] Sessize alma aktif."
    )

    print(
        "[HAZIR] VIP sistemi aktif."
    )

    print(
        "[HAZIR] Ãƒâ€“zel sÃƒÂ¼reli davet sistemi aktif."
    )

    print(
        "[HAZIR] VIP otomatik sÃƒÂ¼re sonlandÃ„Â±rma aktif."
    )

    print(
        "[HAZIR] GÃƒÂ¼nlÃƒÂ¼k 09:00 VIP raporu aktif."
    )

    print(
        "[HAZIR] /uyeler VIP silme butonu aktif."
    )

    print(
        "[HAZIR] Mini App aktif."
    )

    print(
        "[HAZIR] KullanÃ„Â±cÃ„Â± adÃ„Â± kopyalama aktif."
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
            "[DURDU] Sistem kapandÃ„Â±."
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
            "KapatÃ„Â±ldÃ„Â±."
        )

    except Exception as e:

        print(
            "[KRÃ„Â°TÃ„Â°K HATA]",
            repr(e)
        )
