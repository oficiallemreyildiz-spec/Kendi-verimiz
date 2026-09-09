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
        "10000",
    )
)

DATABASE_PATH = os.environ.get(
    "DATABASE_PATH",
    "radar.db",
)

DB_PATH = DATABASE_PATH


# =========================================================
# ADMIN
# =========================================================

ADMIN_USER_ID = int(
    os.environ.get(
        "ADMIN_USER_ID",
        "0",
    )
)

ADMIN_CHAT_ID = int(
    os.environ.get(
        "ADMIN_CHAT_ID",
        "0",
    )
)


# =========================================================
# VIP
# =========================================================

BOT_USERNAME = "YeniBirAirdropBot"

MINI_APP_URL = (
    "https://kendi-verimiz.onrender.com/miniapp"
)

VERIFY_URL = (
    "https://kendi-verimiz.onrender.com/verify"
)

VIP_DAYS = int(
    os.environ.get(
        "VIP_DAYS",
        "30",
    )
)

INVITE_EXPIRE_MINUTES = int(
    os.environ.get(
        "INVITE_EXPIRE_MINUTES",
        "60",
    )
)


# =========================================================
# ALARM
# =========================================================

COIN_ALARM_LIMIT = 100
PEOPLE_ALARM_LIMIT = 5


# =========================================================
# RAM
# =========================================================

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}

processed_messages = set()
processed_signatures = set()

telegram_queue = asyncio.Queue()

http_session = None
client = None
bot_application = None


# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False,
)

db.row_factory = sqlite3.Row


db.execute(
    """
    CREATE TABLE IF NOT EXISTS radar_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT UNIQUE,
        event_type TEXT NOT NULL,
        username TEXT,
        coins INTEGER DEFAULT 0,
        people INTEGER DEFAULT 0,
        joined INTEGER DEFAULT 0,
        rate REAL DEFAULT 0,
        viewers INTEGER DEFAULT 0,
        room TEXT,
        live TEXT,
        target_time INTEGER DEFAULT 0,
        detected_at INTEGER DEFAULT 0,
        source_message_id INTEGER DEFAULT 0,
        source_chat_id INTEGER DEFAULT 0
    )
    """
)


db.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_radar_detected
    ON radar_history(detected_at)
    """
)


db.execute(
    """
    CREATE TABLE IF NOT EXISTS alarm_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alarm_key TEXT UNIQUE,
        event_key TEXT,
        alarm_type TEXT,
        created_at INTEGER
    )
    """
)


# =========================================================
# VIP USERS
# =========================================================

db.execute(
    """
    CREATE TABLE IF NOT EXISTS vip_users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        verified_at INTEGER DEFAULT 0,
        expires_at INTEGER DEFAULT 0,
        invite_token TEXT DEFAULT ''
    )
    """
)


# =========================================================
# INVITE TOKENS
# =========================================================

db.execute(
    """
    CREATE TABLE IF NOT EXISTS invite_tokens (
        token TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        used INTEGER DEFAULT 0,
        used_by INTEGER DEFAULT 0,
        used_at INTEGER DEFAULT 0
    )
    """
)


db.commit()


# =========================================================
# HELPERS
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
# VIP DAVET TOKEN
# =========================================================

def create_invite_token():

    token = secrets.token_urlsafe(32)

    now = int(time.time())

    expires_at = (
        now
        +
        INVITE_EXPIRE_MINUTES * 60
    )

    db.execute(
        """
        INSERT INTO invite_tokens
        (
            token,
            created_at,
            expires_at,
            used,
            used_by,
            used_at
        )
        VALUES (?, ?, ?, 0, 0, 0)
        """,
        (
            token,
            now,
            expires_at,
        ),
    )

    db.commit()

    return token


def make_invite_link(token):

    return (
        "https://t.me/"
        +
        BOT_USERNAME
        +
        "?start=invite_"
        +
        token
    )


# =========================================================
# VIP TOKEN KULLAN
# =========================================================

def use_invite_token(
    token,
    telegram_id,
    username="",
    first_name="",
):

    if not token:
        return False, "Davet tokeni bulunamadı."

    now = int(time.time())

    row = db.execute(
        """
        SELECT *
        FROM invite_tokens
        WHERE token=?
        LIMIT 1
        """,
        (token,),
    ).fetchone()

    if not row:
        return False, "Davet bağlantısı geçersiz."

    if safe_int(row["used"]) == 1:
        return False, "Bu davet bağlantısı daha önce kullanılmış."

    if safe_int(row["expires_at"]) < now:
        return False, "Bu davet bağlantısının süresi dolmuş."

    # -----------------------------------------------------
    # TEK KULLANIM
    # -----------------------------------------------------

    db.execute(
        """
        UPDATE invite_tokens
        SET
            used=1,
            used_by=?,
            used_at=?
        WHERE token=?
        """,
        (
            safe_int(telegram_id),
            now,
            token,
        ),
    )

    # -----------------------------------------------------
    # VIP SÜRESİ
    # -----------------------------------------------------

    expires_at = (
        now
        +
        VIP_DAYS * 24 * 60 * 60
    )

    db.execute(
        """
        INSERT INTO vip_users
        (
            telegram_id,
            username,
            first_name,
            verified_at,
            expires_at,
            invite_token
        )
        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            verified_at=excluded.verified_at,
            expires_at=excluded.expires_at,
            invite_token=excluded.invite_token
        """,
        (
            safe_int(telegram_id),
            str(username or ""),
            str(first_name or ""),
            now,
            expires_at,
            token,
        ),
    )

    db.commit()

    return True, "VIP aktif."


# =========================================================
# VIP KONTROL
# =========================================================

def is_vip(telegram_id):

    if not telegram_id:
        return False

    row = db.execute(
        """
        SELECT expires_at
        FROM vip_users
        WHERE telegram_id=?
        LIMIT 1
        """,
        (
            safe_int(telegram_id),
        ),
    ).fetchone()

    if not row:
        return False

    expires_at = safe_int(
        row["expires_at"]
    )

    return (
        expires_at
        >
        int(time.time())
    )


def get_vip_user(telegram_id):

    return db.execute(
        """
        SELECT *
        FROM vip_users
        WHERE telegram_id=?
        LIMIT 1
        """,
        (
            safe_int(telegram_id),
        ),
    ).fetchone()


# =========================================================
# RADAR DATABASE
# =========================================================

def db_exists(event_key):

    try:

        row = db.execute(
            """
            SELECT 1
            FROM radar_history
            WHERE event_key=?
            LIMIT 1
            """,
            (
                event_key,
            ),
        ).fetchone()

        return row is not None

    except Exception:

        return False


def db_save(d, event_key):

    try:

        db.execute(
            """
            INSERT OR IGNORE INTO radar_history
            (
                event_key,
                event_type,
                username,
                coins,
                people,
                joined,
                rate,
                viewers,
                room,
                live,
                target_time,
                detected_at,
                source_message_id,
                source_chat_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_key,
                d["type"],
                d["username"],
                safe_int(d["coins"]),
                safe_int(d["people"]),
                safe_int(d["joined"]),
                safe_float(d["rate"]),
                safe_int(d["view"]),
                str(d["room"]),
                d.get("live", ""),
                safe_int(d["target_time"]),
                safe_int(d["detected_at"]),
                safe_int(
                    d.get(
                        "source_message_id",
                        0,
                    )
                ),
                safe_int(
                    d.get(
                        "source_chat_id",
                        0,
                    )
                ),
            ),
        )

        db.commit()

        return True

    except Exception as e:

        print(
            "[SQLITE KAYIT HATASI]",
            repr(e),
        )

        return False


def db_stats():

    try:

        total = db.execute(
            """
            SELECT COUNT(*)
            FROM radar_history
            """
        ).fetchone()[0]

        total_coins = db.execute(
            """
            SELECT COALESCE(SUM(coins),0)
            FROM radar_history
            """
        ).fetchone()[0]

        goody = db.execute(
            """
            SELECT COUNT(*)
            FROM radar_history
            WHERE event_type='GOODY BAG'
            """
        ).fetchone()[0]

        chest = db.execute(
            """
            SELECT COUNT(*)
            FROM radar_history
            WHERE event_type='CHEST'
            """
        ).fetchone()[0]

        return {
            "total": total,
            "total_coins": total_coins,
            "goody": goody,
            "chest": chest,
        }

    except Exception:

        return {
            "total": 0,
            "total_coins": 0,
            "goody": 0,
            "chest": 0,
        }


def db_load_recent():

    try:

        rows = db.execute(
            """
            SELECT *
            FROM radar_history
            ORDER BY detected_at DESC
            LIMIT 500
            """
        ).fetchall()

        for row in reversed(rows):

            d = {
                "type": row["event_type"],
                "box_name": (
                    "Goody Bag"
                    if row["event_type"]
                    == "GOODY BAG"
                    else
                    "Hazine Sandığı"
                ),
                "username": row["username"],
                "coins": row["coins"],
                "people": row["people"],
                "joined": row["joined"],
                "rate": row["rate"],
                "view": row["viewers"],
                "room": row["room"],
                "live": row["live"],
                "target_time": row["target_time"],
                "detected_at": row["detected_at"],
                "source_message_id":
                    row["source_message_id"],
                "source_chat_id":
                    row["source_chat_id"],
            }

            target = (
                LIVE_GOODY_BAGS
                if d["type"] == "GOODY BAG"
                else
                LIVE_CHESTS
            )

            if d["room"]:
                target[d["room"]] = d

        print(
            "[SQLITE] Geçmiş yüklendi:",
            len(rows),
        )

    except Exception as e:

        print(
            "[SQLITE YÜKLEME]",
            repr(e),
        )


# =========================================================
# TELEGRAM MINI APP INIT DATA DOĞRULAMA
# =========================================================

def validate_telegram_init_data(init_data):

    if not init_data:
        return None

    try:

        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )

        received_hash = pairs.pop(
            "hash",
            None,
        )

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value
            in sorted(
                pairs.items()
            )
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):
            return None

        auth_date = safe_int(
            pairs.get(
                "auth_date"
            )
        )

        if not auth_date:
            return None

        # 24 saatten eski initData kabul edilmez.
        if (
            int(time.time())
            -
            auth_date
            >
            86400
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

    except Exception as e:

        print(
            "[MINI APP AUTH]",
            repr(e),
        )

        return None


# =========================================================
# TOKEN ÇIKAR
# =========================================================

def token_from_event(event):

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

        match = re.search(
            pattern,
            text,
            re.I,
        )

        if match:

            return unquote(
                match.group(1)
            )

    try:

        for entity in (
            message.entities
            or []
        ):

            url = getattr(
                entity,
                "url",
                None,
            )

            if url:

                match = re.search(
                    r't\.php\?token=([^&\s]+)',
                    url,
                    re.I,
                )

                if match:

                    return unquote(
                        match.group(1)
                    )

    except Exception:

        pass

    return None


def decode_token(token):

    if not token:
        return None

    try:

        s = unquote(
            str(token)
        ).strip()

        raw = base64.urlsafe_b64decode(
            s
            +
            "="
            *
            (
                -len(s) % 4
            )
        )

        return json.loads(
            raw.decode(
                "utf-8",
                errors="ignore",
            )
        )

    except Exception:

        return None


# =========================================================
# ROOM
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
            re.I,
        )

        if match:

            try:

                encoded = match.group(1)

                room = base64.urlsafe_b64decode(
                    encoded
                    +
                    "="
                    *
                    (
                        -len(encoded) % 4
                    )
                ).decode(
                    "utf-8",
                    errors="ignore",
                ).strip()

                if room.isdigit():
                    return room

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
            re.M,
        )

        if match:

            return match.group(
                1
            ).strip()

    return None


# =========================================================
# COIN
# =========================================================

def extract_coins(text, data=None):

    patterns = [

        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',

        r'BOX\s*:\s*(\d+)\s*/',

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text or "",
            re.I,
        )

        if match:

            return safe_int(
                match.group(1)
            )

    if data:

        for key in [

            "coins",
            "coin",
            "amount",
            "diamond",
            "diamonds",

        ]:

            if key in data:

                value = safe_int(
                    data[key]
                )

                if value:
                    return value

    return 0


# =========================================================
# PEOPLE
# =========================================================

def extract_people(text):

    patterns = [

        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',

        r'BOX\s*:\s*\d+\s*/\s*(\d+)',

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text or "",
            re.I,
        )

        if match:

            return safe_int(
                match.group(1)
            )

    return 0


# =========================================================
# JOINED
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
            re.I,
        )

        if match:

            return safe_int(
                match.group(1)
            )

    return 0


# =========================================================
# VIEWERS
# =========================================================

def extract_viewers(text):

    match = re.search(
        r'👀\s*(\d+)',
        text or "",
    )

    if match:

        return safe_int(
            match.group(1)
        )

    return 0


# =========================================================
# RATE
# =========================================================

def extract_rate(text, data=None):

    match = re.search(
        r'Rate\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        text or "",
        re.I,
    )

    if match:

        return safe_float(
            match.group(1)
        )

    if data:

        for key in [
            "rate",
            "ratio",
        ]:

            if key in data:

                return safe_float(
                    data[key]
                )

    return 0


# =========================================================
# GOODY / CHEST
# =========================================================

def detect_type(text, data):

    upper = (
        text or ""
    ).upper()

    if re.search(
        r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG',
        upper,
    ):

        return "GOODY BAG"

    if re.search(
        r'\bBOX\b|RƯƠNG|TREO|HAZİNE|HAZINE',
        upper,
    ):

        return "CHEST"

    if data:

        value = str(
            data.get(
                "type",
                ""
            )
        ).lower()

        if (
            "goody" in value
            or
            "bag" in value
        ):

            return "GOODY BAG"

        if (
            "chest" in value
            or
            "box" in value
        ):

            return "CHEST"

    return None


# =========================================================
# TARGET TIME
# =========================================================

def extract_target_time(
    text,
    data=None,
):

    now = int(
        time.time()
    )

    if data:

        for key in [

            "target_time",
            "targetTime",
            "end_time",
            "endTime",
            "time",

        ]:

            if key in data:

                try:

                    value = int(
                        float(
                            data[key]
                        )
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

    match = re.search(
        r'TIME\s*:\s*(\d+):(\d+)',
        text or "",
        re.I,
    )

    if match:

        minutes = safe_int(
            match.group(1)
        )

        seconds = safe_int(
            match.group(2)
        )

        return (
            now
            +
            minutes * 60
            +
            seconds
        )

    return now + 180


# =========================================================
# PARSE
# =========================================================

def parse_event(event):

    text = (
        event.message.raw_text
        or ""
    )

    token = token_from_event(
        event
    )

    data = decode_token(
        token
    )

    event_type = detect_type(
        text,
        data
    )

    if not event_type:

        return None

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
        or
        username_from_text(
            text
        )
        or
        "bilinmiyor"
    )

    room = None

    if data:

        for key in [

            "room",
            "room_id",
            "roomid",
            "roomId",

        ]:

            if data.get(key):

                room = str(
                    data[key]
                )

                break

    room = (
        room
        or
        room_from_text(
            text
        )
        or
        "msg:"
        +
        str(
            event.message.id
        )
    )

    people = extract_people(
        text
    )

    joined = extract_joined(
        text
    )

    viewers = extract_viewers(
        text
    )

    if data:

        if not people:

            for key in [
                "people",
                "personCount",
                "peopleCount",
                "participantCount",
                "count",
            ]:

                if key in data:

                    people = safe_int(
                        data[key]
                    )

                    if people:
                        break

        if not joined:

            for key in [
                "joined",
                "join",
                "joinCount",
                "joinedCount",
            ]:

                if key in data:

                    joined = safe_int(
                        data[key]
                    )

                    if joined:
                        break

        if not viewers:

            for key in [
                "viewers",
                "viewerCount",
                "views",
                "view",
            ]:

                if key in data:

                    viewers = safe_int(
                        data[key]
                    )

                    if viewers:
                        break

    live = ""

    if data:

        for key in [
            "live",
            "live_url",
            "liveUrl",
            "url",
        ]:

            value = data.get(
                key
            )

            if (
                value
                and
                str(value).startswith(
                    (
                        "http://",
                        "https://",
                    )
                )
            ):

                live = str(
                    value
                )

                break

    if not live:

        live = (
            "https://www.tiktok.com/@"
            +
            username
            +
            "/live"
        )

    return {

        "type":
            event_type,

        "box_name":
            (
                "Goody Bag"
                if event_type
                == "GOODY BAG"
                else
                "Hazine Sandığı"
            ),

        "username":
            username,

        "coins":
            extract_coins(
                text,
                data,
            ),

        "people":
            people,

        "joined":
            joined,

        "rate":
            extract_rate(
                text,
                data,
            ),

        "view":
            viewers,

        "room":
            room,

        "live":
            live,

        "target_time":
            extract_target_time(
                text,
                data,
            ),

        "detected_at":
            int(
                time.time()
            ),

        "source_message_id":
            event.message.id,

        "source_chat_id":
            safe_int(
                event.chat_id
            ),
    }


# =========================================================
# EVENT KEY
# =========================================================

def make_event_key(d):

    return "|".join(
        [

            d["type"],

            str(
                d.get(
                    "room",
                    "",
                )
            ),

            str(
                d.get(
                    "username",
                    "",
                )
            ).lower(),

            str(
                d.get(
                    "coins",
                    0,
                )
            ),

            str(
                d.get(
                    "people",
                    0,
                )
            ),

            str(
                d.get(
                    "source_message_id",
                    0,
                )
            ),

        ]
    )


# =========================================================
# RADAR'A EKLE
# =========================================================

def add_to_radar(d):

    if not d:
        return False

    room = d.get(
        "room"
    )

    if not room:
        return False

    event_key = make_event_key(
        d
    )

    if event_key in processed_signatures:

        return False

    if db_exists(
        event_key
    ):

        processed_signatures.add(
            event_key
        )

        return False

    target = (

        LIVE_GOODY_BAGS

        if d["type"]
        ==
        "GOODY BAG"

        else

        LIVE_CHESTS
    )

    target[
        room
    ] = d

    db_save(
        d,
        event_key
    )

    processed_signatures.add(
        event_key
    )

    print(
        "[RADAR]",
        d["type"],
        "|",
        d["username"],
        "| COIN:",
        d["coins"],
        "| KİŞİ:",
        d["people"],
    )

    return True


# =========================================================
# ALARM
# =========================================================

def alarm_exists(
    alarm_key
):

    try:

        row = db.execute(
            """
            SELECT 1
            FROM alarm_history
            WHERE alarm_key=?
            LIMIT 1
            """,
            (
                alarm_key,
            ),
        ).fetchone()

        return row is not None

    except Exception:

        return False


def save_alarm(
    alarm_key,
    event_key,
    alarm_type,
):

    try:

        db.execute(
            """
            INSERT OR IGNORE INTO alarm_history
            (
                alarm_key,
                event_key,
                alarm_type,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                alarm_key,
                event_key,
                alarm_type,
                int(time.time()),
            ),
        )

        db.commit()

        return True

    except Exception:

        return False


def get_alarms(d):

    alarms = []

    event_key = make_event_key(
        d
    )

    coin = safe_int(
        d.get(
            "coins"
        )
    )

    people = safe_int(
        d.get(
            "people"
        )
    )

    if (
        COIN_ALARM_LIMIT > 0
        and
        coin >= COIN_ALARM_LIMIT
    ):

        alarms.append(
            {
                "type":
                    "COIN",

                "title":
                    "🚨 COIN ALARMI",

                "key":
                    (
                        event_key
                        +
                        "|COIN|"
                        +
                        str(
                            COIN_ALARM_LIMIT
                        )
                    ),
            }
        )

    if (
        PEOPLE_ALARM_LIMIT > 0
        and
        people > 0
        and
        people <= PEOPLE_ALARM_LIMIT
    ):

        alarms.append(
            {
                "type":
                    "PEOPLE",

                "title":
                    "⚠️ DÜŞÜK KİŞİ ALARMI",

                "key":
                    (
                        event_key
                        +
                        "|PEOPLE|"
                        +
                        str(
                            PEOPLE_ALARM_LIMIT
                        )
                    ),
            }
        )

    return alarms


# =========================================================
# TELEGRAM NORMAL MESAJ
# =========================================================

async def send_tg(d):

    global http_session

    if not http_session:
        return

    title = (
        "🟪 GOODY BAG"
        if d["type"]
        ==
        "GOODY BAG"
        else
        "🟨 HAZİNE SANDIĞI"
    )

    text = (
        f"{title}\n\n"

        f"👤 Kullanıcı: "
        f"{d['username']}\n"

        f"🪙 Coin: "
        f"{d['coins']}\n"

        f"👥 Kişi: "
        f"{d['people']}\n"

        f"🙋 Katılan: "
        f"{d['joined']}\n"

        f"📈 Oran: "
        f"{d['rate']}\n"

        f"👀 İzlenme: "
        f"{d['view']}\n"

        f"🏠 Oda: "
        f"{d['room']}\n"
    )

    if d.get("live"):

        text += (
            "\n🔴 "
            +
            d["live"]
        )

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:

        async with http_session.post(
            url,
            json={
                "chat_id":
                    TARGET_CHAT_ID,

                "text":
                    text,

                "disable_web_page_preview":
                    True,
            },
        ) as response:

            if response.status != 200:

                print(
                    "[TELEGRAM HATA]",
                    response.status,
                    await response.text(),
                )

    except Exception as e:

        print(
            "[TELEGRAM]",
            repr(e),
        )


# =========================================================
# ALARM MESAJ
# =========================================================

async def send_alarm(
    d,
    alarm,
):

    global http_session

    if not http_session:
        return

    if alarm["type"] == "COIN":

        title = "🚨 COIN ALARMI"

        reason = (
            f"🪙 COIN: {d['coins']}\n"
            f"🎯 LİMİT: {COIN_ALARM_LIMIT}"
        )

    else:

        title = "⚠️ DÜŞÜK KİŞİ ALARMI"

        reason = (
            f"👥 KİŞİ: {d['people']}\n"
            f"🎯 LİMİT: {PEOPLE_ALARM_LIMIT}"
        )

    box = (
        "🟪 GOODY BAG"
        if d["type"]
        ==
        "GOODY BAG"
        else
        "🟨 HAZİNE SANDIĞI"
    )

    text = (
        f"{title}\n\n"

        f"{box}\n"

        f"👤 Kullanıcı: "
        f"{d['username']}\n"

        f"{reason}\n"

        f"🙋 Katılan: "
        f"{d['joined']}\n"

        f"📈 Oran: "
        f"{d['rate']}\n"

        f"👀 İzlenme: "
        f"{d['view']}\n"

        f"🏠 Oda: "
        f"{d['room']}\n"
    )

    if d.get("live"):

        text += (
            "\n🔴 "
            +
            d["live"]
        )

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:

        async with http_session.post(
            url,
            json={
                "chat_id":
                    TARGET_CHAT_ID,

                "text":
                    text,

                "disable_web_page_preview":
                    True,
            },
        ) as response:

            if response.status != 200:

                print(
                    "[ALARM]",
                    response.status,
                )

    except Exception as e:

        print(
            "[ALARM]",
            repr(e),
        )


# =========================================================
# YENİ VIP ÜYE BİLDİRİMİ
# =========================================================

async def notify_new_vip(
    user,
    expires_at,
):

    global http_session

    if not http_session:
        return

    if not ADMIN_CHAT_ID:

        print(
            "[VIP] ADMIN_CHAT_ID ayarlı değil."
        )

        return

    username = (
        "@"
        +
        user.username
        if user.username
        else
        "Kullanıcı adı yok"
    )

    date_text = time.strftime(
        "%d.%m.%Y %H:%M",
        time.localtime(
            expires_at
        ),
    )

    text = (
        "🆕 YENİ VIP ÜYE\n\n"

        f"👤 İsim: "
        f"{user.first_name or '-'}\n"

        f"🔹 Kullanıcı adı: "
        f"{username}\n"

        f"🆔 Telegram ID: "
        f"{user.id}\n\n"

        "✅ VIP erişimi verildi\n"

        f"📅 Bitiş: "
        f"{date_text}\n\n"

        "🌐 Mini App erişimi aktif."
    )

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:

        async with http_session.post(
            url,
            json={
                "chat_id":
                    ADMIN_CHAT_ID,

                "text":
                    text,

                "disable_web_page_preview":
                    True,
            },
        ) as response:

            if response.status != 200:

                print(
                    "[ADMIN BİLDİRİM]",
                    response.status,
                    await response.text(),
                )

            else:

                print(
                    "[VIP] Admin bildirimi gönderildi."
                )

    except Exception as e:

        print(
            "[ADMIN BİLDİRİM]",
            repr(e),
        )


# =========================================================
# TELEGRAM QUEUE
# =========================================================

async def sender():

    while True:

        job = await telegram_queue.get()

        try:

            if job["kind"] == "normal":

                await send_tg(
                    job["data"]
                )

            elif job["kind"] == "alarm":

                await send_alarm(
                    job["data"],
                    job["alarm"],
                )

        except Exception as e:

            print(
                "[SENDER]",
                repr(e),
            )

        finally:

            telegram_queue.task_done()


# =========================================================
# /ID
# =========================================================

async def bot_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    username = (
        "@"
        +
        user.username
        if user.username
        else
        "Yok"
    )

    await update.message.reply_text(
        (
            "🆔 TELEGRAM BİLGİLERİN\n\n"
            f"ID: {user.id}\n"
            f"Username: {username}"
        )
    )


# =========================================================
# /DAVET
# =========================================================

async def bot_davet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    if not ADMIN_USER_ID:

        await update.message.reply_text(
            "⚠️ ADMIN_USER_ID ayarlanmamış."
        )

        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "⛔ Bu komut sadece yöneticiye açıktır."
        )

        return

    token = create_invite_token()

    link = make_invite_link(
        token
    )

    await update.message.reply_text(
        (
            "🎟️ YENİ VIP DAVET LİNKİ\n\n"

            f"⏰ Geçerlilik: "
            f"{INVITE_EXPIRE_MINUTES} dakika\n"

            "👤 Kullanım: 1 kişi\n\n"

            "👇 Müşteriye bu linki gönder:\n\n"

            f"{link}"
        ),
        disable_web_page_preview=True,
    )

    print(
        "[DAVET] Yeni VIP davet oluşturuldu."
    )


# =========================================================
# /UYELER
# =========================================================

async def bot_uyeler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "⛔ Yetkiniz yok."
        )

        return

    rows = db.execute(
        """
        SELECT *
        FROM vip_users
        ORDER BY verified_at DESC
        LIMIT 50
        """
    ).fetchall()

    if not rows:

        await update.message.reply_text(
            "📭 Henüz VIP üye yok."
        )

        return

    text = "👑 VIP ÜYELER\n\n"

    now = int(
        time.time()
    )

    for index, row in enumerate(
        rows,
        1,
    ):

        remaining = (
            safe_int(
                row["expires_at"]
            )
            -
            now
        )

        days = max(
            0,
            remaining // 86400
        )

        username = (
            "@"
            +
            row["username"]
            if row["username"]
            else
            "-"
        )

        text += (
            f"{index}. "
            f"{row['first_name'] or '-'} "
            f"{username}\n"

            f"🆔 {row['telegram_id']}\n"

            f"⏳ {days} gün\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# /SILVIP
# =========================================================

async def bot_silvip(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    if user.id != ADMIN_USER_ID:

        await update.message.reply_text(
            "⛔ Yetkiniz yok."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Kullanım:\n/silvip TELEGRAM_ID"
        )

        return

    telegram_id = safe_int(
        context.args[0]
    )

    if not telegram_id:

        await update.message.reply_text(
            "❌ Geçersiz Telegram ID."
        )

        return

    db.execute(
        """
        DELETE FROM vip_users
        WHERE telegram_id=?
        """,
        (
            telegram_id,
        ),
    )

    db.commit()

    await update.message.reply_text(
        (
            "✅ VIP üyelik kaldırıldı.\n\n"
            f"🆔 {telegram_id}"
        )
    )


# =========================================================
# /START
# =========================================================

async def bot_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    args = context.args

    # =====================================================
    # ÖZEL VIP DAVETİ
    # =====================================================

    if (
        args
        and
        args[0].startswith(
            "invite_"
        )
    ):

        token = args[0][7:]

        ok, message = use_invite_token(
            token,
            user.id,
            user.username or "",
            user.first_name or "",
        )

        if not ok:

            await update.message.reply_text(
                (
                    "❌ ERİŞİM VERİLEMEDİ\n\n"
                    f"{message}\n\n"
                    "Yeni bir VIP davet bağlantısı "
                    "almanız gerekiyor."
                )
            )

            return

        vip = get_vip_user(
            user.id
        )

        expires_at = safe_int(
            vip["expires_at"]
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🌐 VIP RADARI AÇ",
                    web_app=WebAppInfo(
                        url=MINI_APP_URL
                    ),
                )
            ]
        ]

        await update.message.reply_text(
            (
                f"✅ Doğrulama Başarılı, "
                f"{user.first_name or 'VIP Üye'}!\n\n"

                "👑 VIP erişiminiz aktif.\n\n"

                "👇 Radarı açmak için:"
            ),
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

        await notify_new_vip(
            user,
            expires_at,
        )

        print(
            "[VIP] YENİ ÜYE:",
            user.id,
            user.username,
        )

        return

    # =====================================================
    # ESKİ vip_onayli SİSTEMİNİ KAPAT
    # =====================================================

    if (
        args
        and
        args[0]
        ==
        "vip_onayli"
    ):

        await update.message.reply_text(
            (
                "⛔ Bu doğrulama yöntemi artık "
                "geçerli değil.\n\n"

                "VIP erişimi yalnızca özel "
                "davet bağlantısıyla verilmektedir."
            )
        )

        return

    # =====================================================
    # ZATEN VIP
    # =====================================================

    if is_vip(
        user.id
    ):

        keyboard = [
            [
                InlineKeyboardButton(
                    "🌐 VIP RADARI AÇ",
                    web_app=WebAppInfo(
                        url=MINI_APP_URL
                    ),
                )
            ]
        ]

        await update.message.reply_text(
            (
                f"👑 Hoş geldin "
                f"{user.first_name or 'VIP Üye'}!\n\n"

                "VIP erişiminiz aktif.\n\n"

                "👇 Radarı açabilirsiniz:"
            ),
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

        return

    # =====================================================
    # NORMAL KULLANICI
    # =====================================================

    await update.message.reply_text(
        (
            "🔒 ERİŞİM KISITLI\n\n"

            "Bu bot yalnızca davetli VIP "
            "üyeler için kullanılabilir.\n\n"

            "Geçerli VIP davet bağlantınız "
            "yoksa radar açılmaz."
        )
    )


# =========================================================
# MINI APP + NORMAL RADAR HTML
# =========================================================

RADAR_HTML = r"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"
>

<meta
name="theme-color"
content="#05070b"
>

<script
src="https://telegram.org/js/telegram-web-app.js"
></script>

<title>ÖDÜL AVCISI</title>

<style>

* {
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}

html,
body {

    margin: 0;
    padding: 0;

    width: 100%;
    min-height: 100%;

    background: #05070b;
    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

body {

    padding: 7px;
    overflow-x: hidden;
}

.head {

    width: 100%;
    text-align: center;

    padding:
        5px
        2px
        16px;
}

.title {

    font-size: 36px;
    line-height: 1;

    font-weight: 1000;

    letter-spacing: -1px;

    background:
        linear-gradient(
            90deg,
            #ffffff,
            #c084fc,
            #ffffff
        );

    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.sub {

    margin-top: 9px;

    font-size: 13px;

    font-weight: 1000;

    white-space: nowrap;
}

.status {

    display: inline-block;

    margin-top: 10px;

    padding:
        9px
        13px;

    border-radius: 13px;

    background: #102719;

    color: #55ff99;

    border:
        1px solid
        #247a48;

    font-size: 12px;

    font-weight: 1000;
}

.sections {

    width: 100%;

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 7px;

    align-items: start;
}

.panel {

    width: 100%;

    min-width: 0;

    background:
        linear-gradient(
            180deg,
            #0d131c,
            #080c12
        );

    border:
        1px solid
        #293342;

    border-radius: 15px;

    padding: 7px;

    overflow: hidden;
}

.section-title {

    width: 100%;

    min-height: 38px;

    display: flex;

    align-items: center;

    justify-content:
        space-between;

    gap: 4px;

    padding:
        1px
        1px
        8px;

    font-size: 14px;

    font-weight: 1000;
}

.section-name {

    min-width: 0;

    overflow: hidden;

    white-space: nowrap;

    text-overflow: ellipsis;
}

.count {

    flex-shrink: 0;

    min-width: 27px;

    padding: 5px;

    text-align: center;

    border-radius: 8px;

    background: #18212d;

    font-size: 11px;

    font-weight: 1000;
}

.list {

    width: 100%;

    display: flex;

    flex-direction: column;

    gap: 7px;
}

.card {

    position: relative;

    width: 100%;

    background:
        linear-gradient(
            145deg,
            #131a24,
            #0a0f16
        );

    border:
        1px solid
        #2a3441;

    border-radius: 12px;

    padding: 8px;

    overflow: hidden;

    box-shadow:
        0 5px 13px
        rgba(0,0,0,.30);
}

.card.goody {

    border-left:
        5px solid
        #a855f7;
}

.card.chest {

    border-left:
        5px solid
        #ffd400;
}

.card.new-item {

    animation:
        newDrop
        .65s
        cubic-bezier(
            .18,
            .85,
            .25,
            1
        );
}

@keyframes newDrop {

    0% {

        opacity: 0;

        transform:
            translateY(-55px)
            scale(.94);
    }

    55% {

        opacity: 1;

        transform:
            translateY(7px)
            scale(1.015);
    }

    75% {

        transform:
            translateY(-2px)
            scale(1.005);
    }

    100% {

        opacity: 1;

        transform:
            translateY(0)
            scale(1);
    }
}

.new-badge {

    position: absolute;

    top: 6px;
    right: 6px;

    z-index: 10;

    padding:
        4px
        5px;

    border-radius: 6px;

    background:
        linear-gradient(
            135deg,
            #ff1744,
            #ff5577
        );

    color: white;

    font-size: 7px;

    line-height: 1;

    font-weight: 1000;

    box-shadow:
        0 0 11px
        rgba(255,23,68,.65);

    animation:
        newPulse
        .9s
        ease-in-out
        infinite;
}

@keyframes newPulse {

    0%,
    100% {

        opacity: 1;
        transform: scale(1);
    }

    50% {

        opacity: .65;
        transform: scale(1.08);
    }
}

.card.hot {

    border-color:
        #ff6500;

    box-shadow:
        0 0 7px
        rgba(255,100,0,.40),

        0 0 20px
        rgba(255,55,0,.22);

    animation:
        fireGlow
        1.1s
        ease-in-out
        infinite;
}

@keyframes fireGlow {

    0%,
    100% {

        box-shadow:
            0 0 6px
            rgba(255,100,0,.30),

            0 0 15px
            rgba(255,55,0,.15);
    }

    50% {

        box-shadow:
            0 0 13px
            rgba(255,100,0,.70),

            0 0 27px
            rgba(255,55,0,.35);
    }
}

.fire-line {

    position: absolute;

    left: 0;
    top: 0;

    width: 100%;
    height: 2px;

    background:
        linear-gradient(
            90deg,
            transparent,
            #ff3d00,
            #ffb300,
            #ff3d00,
            transparent
        );

    animation:
        fireMove
        .8s
        linear
        infinite;
}

@keyframes fireMove {

    0% {
        transform:
            translateX(-100%);
    }

    100% {
        transform:
            translateX(100%);
    }
}

.user-row {

    width: 100%;

    display: flex;

    align-items:
        flex-start;

    gap: 3px;
}

.user {

    min-width: 0;

    width: 100%;

    padding-right:
        25px;

    font-size: 13px;

    line-height: 1.15;

    font-weight: 1000;

    overflow-wrap:
        anywhere;
}

.coin-box {

    width: 100%;

    margin-top: 7px;

    padding:
        6px
        7px;

    display: flex;

    align-items: center;

    justify-content:
        space-between;

    gap: 4px;

    border-radius: 8px;

    background: #080c12;

    border:
        1px solid
        #202a38;
}

.coin-label {

    font-size: 8px;

    font-weight: 1000;

    white-space: nowrap;
}

.coin-value {

    font-size: 17px;

    line-height: 1;

    font-weight: 1000;

    white-space: nowrap;
}

.hot .coin-value {

    color: #ff9d00;

    text-shadow:
        0 0 9px
        rgba(255,123,0,.75);
}

.fire {

    display: inline-block;

    margin-right: 2px;

    font-size: 15px;

    animation:
        flame
        .55s
        ease-in-out
        infinite;
}

@keyframes flame {

    0%,
    100% {

        transform:
            scale(1)
            rotate(-4deg);
    }

    50% {

        transform:
            scale(1.25)
            rotate(4deg);
    }
}

.info-grid {

    width: 100%;

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 4px;

    margin-top: 5px;
}

.info {

    min-width: 0;

    padding:
        5px;

    border-radius: 7px;

    background:
        #090d13;

    border:
        1px solid
        #1e2733;
}

.info-label {

    display: block;

    color: #929eae;

    font-size: 7px;

    line-height: 1;

    font-weight: 900;

    white-space: nowrap;
}

.info-value {

    display: block;

    margin-top: 3px;

    color: #ffffff;

    font-size: 11px;

    line-height: 1.05;

    font-weight: 1000;

    overflow-wrap:
        anywhere;
}

.live {

    display: block;

    width: 100%;

    margin-top: 6px;

    padding:
        8px
        3px;

    border-radius: 8px;

    background:
        linear-gradient(
            135deg,
            #d70d46,
            #f11955
        );

    color: #ffffff;

    text-align: center;

    text-decoration: none;

    font-size: 8px;

    line-height: 1;

    font-weight: 1000;
}

.empty {

    width: 100%;

    padding:
        22px
        3px;

    text-align: center;

    color: #657184;

    font-size: 10px;

    line-height: 1.4;

    font-weight: 900;
}

.error {

    padding:
        25px
        5px;

    text-align: center;

    color: #ff6b7d;

    font-size: 12px;

    font-weight: 1000;
}

@media(min-width:600px) {

    body {

        max-width: 900px;

        margin: 0 auto;
    }

    .title {

        font-size: 48px;
    }

    .panel {

        padding: 10px;
    }

    .user {

        font-size: 16px;
    }

    .coin-value {

        font-size: 20px;
    }

    .info-value {

        font-size: 14px;
    }

    .live {

        font-size: 10px;
    }
}

@media(max-width:340px) {

    body {

        padding: 5px;
    }

    .title {

        font-size: 31px;
    }

    .sub {

        font-size: 11px;
    }

    .status {

        font-size: 10px;

        padding:
            8px
            10px;
    }

    .sections {

        gap: 5px;
    }

    .panel {

        padding: 6px;
    }

    .section-title {

        font-size: 12px;
    }

    .user {

        font-size: 11px;
    }

    .coin-value {

        font-size: 15px;
    }

    .info {

        padding: 4px;
    }

    .info-label {

        font-size: 6px;
    }

    .info-value {

        font-size: 9px;
    }

    .live {

        font-size: 7px;

        padding:
            7px
            2px;
    }
}

</style>

</head>

<body>

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


<div class="sections">

<section class="panel">

    <div class="section-title">

        <span class="section-name">
            🟪 GOODY BAG
        </span>

        <span
            id="goodyCount"
            class="count"
        >
            0
        </span>

    </div>

    <div
        id="goodyList"
        class="list"
    >

        <div class="empty">
            Goody Bag bekleniyor...
        </div>

    </div>

</section>


<section class="panel">

    <div class="section-title">

        <span class="section-name">
            🟨 HAZİNE
        </span>

        <span
            id="chestCount"
            class="count"
        >
            0
        </span>

    </div>

    <div
        id="chestList"
        class="list"
    >

        <div class="empty">
            Hazine bekleniyor...
        </div>

    </div>

</section>

</div>


<script>

const tg =
    window.Telegram.WebApp;

const insideTelegram =
    !!(
        tg
        &&
        tg.initData
    );


if (tg) {

    tg.ready();
    tg.expand();

}


const goodyList =
    document.getElementById(
        "goodyList"
    );

const chestList =
    document.getElementById(
        "chestList"
    );

const goodyCount =
    document.getElementById(
        "goodyCount"
    );

const chestCount =
    document.getElementById(
        "chestCount"
    );

const statusEl =
    document.getElementById(
        "status"
    );


let firstLoad = true;

const knownItems =
    new Set();

const freshItems =
    new Map();


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


function itemKey(d) {

    return [

        d.type || "",

        d.room || "",

        d.username || "",

        d.coins || "",

        d.people || "",

        d.source_message_id || ""

    ].join("|");
}


function isNewItem(d) {

    const key =
        itemKey(d);


    if (firstLoad) {

        knownItems.add(
            key
        );

        return false;
    }


    if (
        !knownItems.has(
            key
        )
    ) {

        knownItems.add(
            key
        );

        freshItems.set(
            key,
            Date.now()
        );

        return true;
    }


    const created =
        freshItems.get(
            key
        );


    if (!created) {

        return false;
    }


    if (

        Date.now()
        -
        created
        <
        8000

    ) {

        return true;
    }


    freshItems.delete(
        key
    );

    return false;
}


function isHot(d) {

    return Number(
        d.coins || 0
    ) >= 100;
}


function card(d) {

    const isGoody =
        d.type === "GOODY BAG";

    const hot =
        isHot(d);

    const fresh =
        isNewItem(d);


    const classes = [

        "card",

        isGoody
            ? "goody"
            : "chest",

        hot
            ? "hot"
            : "",

        fresh
            ? "new-item"
            : ""

    ]

    .filter(Boolean)

    .join(" ");


    return `

        <div
            class="${classes}"
        >

            ${
                hot
                ? `
                    <div
                        class="fire-line"
                    ></div>
                `
                : ""
            }


            ${
                fresh
                ? `
                    <div
                        class="new-badge"
                    >
                        ✨ YENİ
                    </div>
                `
                : ""
            }


            <div class="user-row">

                <div class="user">

                    👤
                    ${esc(
                        d.username
                    )}

                </div>

            </div>


            <div class="coin-box">

                <span class="coin-label">

                    ${
                        hot
                        ? `
                            <span class="fire">
                                🔥
                            </span>
                        `
                        : "🪙"
                    }

                    COIN

                </span>


                <span class="coin-value">

                    ${esc(
                        d.coins
                    )}

                    ${
                        hot
                        ? " 🔥"
                        : ""
                    }

                </span>

            </div>


            <div class="info-grid">

                <div class="info">

                    <span class="info-label">
                        👥 KİŞİ
                    </span>

                    <span class="info-value">
                        ${esc(
                            d.people
                        )}
                    </span>

                </div>


                <div class="info">

                    <span class="info-label">
                        🙋 KATILAN
                    </span>

                    <span class="info-value">
                        ${esc(
                            d.joined
                        )}
                    </span>

                </div>


                <div class="info">

                    <span class="info-label">
                        📈 ORAN
                    </span>

                    <span class="info-value">
                        ${esc(
                            d.rate
                        )}
                    </span>

                </div>


                <div class="info">

                    <span class="info-label">
                        👀 İZLENME
                    </span>

                    <span class="info-value">
                        ${esc(
                            d.view
                        )}
                    </span>

                </div>

            </div>


            ${
                d.live
                ? `
                    <a
                        class="live"
                        href="${esc(
                            d.live
                        )}"
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        🔴 CANLI
                    </a>
                `
                : ""
            }


        </div>

    `;
}


function renderList(
    list,
    element,
    countElement
) {

    const sorted =
        [...list].sort(

            (a, b) =>

                Number(
                    b.detected_at || 0
                )

                -

                Number(
                    a.detected_at || 0
                )

        );


    countElement.textContent =
        sorted.length;


    if (!sorted.length) {

        element.innerHTML = `

            <div class="empty">
                Kayıt bekleniyor...
            </div>

        `;

        return;
    }


    element.innerHTML =
        sorted
        .map(card)
        .join("");
}


async function loadRadar() {

    try {

        const endpoint =
            insideTelegram
            ?
            "/api/miniapp-data"
            :
            "/api/all";


        const headers = {};


        if (insideTelegram) {

            headers[
                "X-Telegram-Init-Data"
            ] =
                tg.initData;

        }


        const response =
            await fetch(
                endpoint,
                {
                    method:
                        "GET",

                    headers:
                        headers,

                    cache:
                        "no-store"
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            statusEl.textContent =
                "🔴 VIP ERİŞİM YOK";


            goodyList.innerHTML = `

                <div class="error">

                    🔒 VIP erişiminiz bulunmuyor.

                </div>

            `;


            chestList.innerHTML = `

                <div class="error">

                    🔒 VIP erişiminiz bulunmuyor.

                </div>

            `;


            return;
        }


        statusEl.textContent =
            "🟢 RADAR AKTİF • CANLI VERİ";


        const goodies =
            data.goody_bags
            ||
            [];


        const chests =
            data.chests
            ||
            [];


        if (firstLoad) {

            goodies.forEach(
                d =>
                    knownItems.add(
                        itemKey(d)
                    )
            );


            chests.forEach(
                d =>
                    knownItems.add(
                        itemKey(d)
                    )
            );

        }


        renderList(
            goodies,
            goodyList,
            goodyCount
        );


        renderList(
            chests,
            chestList,
            chestCount
        );


        firstLoad = false;


    } catch (error) {

        console.error(
            error
        );

        statusEl.textContent =
            "🔴 BAĞLANTI HATASI";
    }
}


loadRadar();


setInterval(
    loadRadar,
    3000
);

</script>

</body>

</html>
"""


# =========================================================
# MINI APP HTML
# =========================================================
#
# RADAR_HTML aynı zamanda Mini App tasarımıdır.
# Normal tarayıcıda /api/all,
# Telegram Mini App'te /api/miniapp-data kullanır.
# =========================================================

MINI_APP_HTML = RADAR_HTML


# =========================================================
# VERIFY HTML
# =========================================================

VERIFY_HTML = r"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>VIP Davet</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    min-height: 100vh;

    display: flex;

    align-items: center;

    justify-content: center;

    padding: 20px;

    background:
        radial-gradient(
            circle at top,
            #28134d,
            #080a10 55%,
            #050609
        );

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.box {

    width: 100%;

    max-width: 460px;

    padding: 32px 24px;

    text-align: center;

    background: #111522;

    border:
        2px solid
        #8b45ff;

    border-radius: 25px;

    box-shadow:
        0 0 45px
        rgba(139,69,255,.35);
}

.icon {

    font-size: 58px;
}

h1 {

    color: #c084fc;

    font-size: 28px;

    font-weight: 1000;
}

p {

    color: #cbd5e1;

    line-height: 1.6;
}

.button {

    display: block;

    padding: 17px;

    border-radius: 15px;

    background:
        linear-gradient(
            135deg,
            #7c3aed,
            #a855f7
        );

    color: white;

    text-decoration: none;

    font-weight: 1000;

    font-size: 17px;
}

.error {

    color: #ff6578;

    font-weight: 1000;
}

</style>

</head>

<body>

<div class="box">

    <div class="icon">
        👑
    </div>

    <h1>
        VIP DAVET
    </h1>

    <p>
        VIP radar erişiminiz için
        aşağıdaki butona basarak
        Telegram'a geçin.
    </p>

    <div id="area">
        Hazırlanıyor...
    </div>

</div>


<script>

const params =
    new URLSearchParams(
        location.search
    );

const token =
    params.get(
        "token"
    );

const area =
    document.getElementById(
        "area"
    );


if (!token) {

    area.innerHTML = `

        <div class="error">

            ❌ Geçersiz davet bağlantısı.

        </div>

    `;

} else {

    const url =
        "https://t.me/YeniBirAirdropBot"
        +
        "?start=invite_"
        +
        encodeURIComponent(
            token
        );


    area.innerHTML = `

        <a
            class="button"
            href="${url}"
        >

            🚀 TELEGRAM'DA DEVAM ET

        </a>

    `;

}

</script>

</body>

</html>
"""


# =========================================================
# HTTP PAGES
# =========================================================

async def radar_page(
    request
):

    return web.Response(
        text=RADAR_HTML,
        content_type="text/html",
        charset="utf-8",
    )


async def miniapp_page(
    request
):

    return web.Response(
        text=MINI_APP_HTML,
        content_type="text/html",
        charset="utf-8",
    )


async def verify_page(
    request
):

    return web.Response(
        text=VERIFY_HTML,
        content_type="text/html",
        charset="utf-8",
    )


# =========================================================
# MINI APP API
# =========================================================

async def api_miniapp_data(
    request
):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        "",
    )

    user = validate_telegram_init_data(
        init_data
    )

    if not user:

        return web.json_response(
            {
                "status":
                    "error",

                "message":
                    "Telegram doğrulaması başarısız.",
            },
            status=401,
        )

    telegram_id = safe_int(
        user.get(
            "id"
        )
    )

    if not is_vip(
        telegram_id
    ):

        return web.json_response(
            {
                "status":
                    "error",

                "message":
                    "VIP erişiminiz yok.",
            },
            status=403,
        )

    return web.json_response(
        {
            "status":
                "success",

            "user":
                user,

            "goody_bags":
                list(
                    LIVE_GOODY_BAGS.values()
                ),

            "chests":
                list(
                    LIVE_CHESTS.values()
                ),

            "server_time":
                int(
                    time.time()
                ),
        }
    )


# =========================================================
# PUBLIC API
# =========================================================

async def api_all(
    request
):

    return web.json_response(
        {
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

            "stats":
                db_stats(),
        }
    )


async def api_boxes(
    request
):

    return web.json_response(
        list(
            LIVE_CHESTS.values()
        )
    )


async def api_goody(
    request
):

    return web.json_response(
        list(
            LIVE_GOODY_BAGS.values()
        )
    )


async def api_status(
    request
):

    stats = db_stats()

    return web.json_response(
        {
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

            "history":
                stats["total"],

            "total_coins":
                stats["total_coins"],

            "server_time":
                int(
                    time.time()
                ),

            "coin_alarm":
                COIN_ALARM_LIMIT,

            "people_alarm":
                PEOPLE_ALARM_LIMIT,
        }
    )


# =========================================================
# CORS
# =========================================================

@web.middleware
async def cors(
    request,
    handler
):

    if request.method == "OPTIONS":

        response = web.Response(
            status=204
        )

    else:

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
# HTTP SERVER
# =========================================================

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
        "/verify",
        verify_page
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
        PORT,
    )

    await site.start()

    print(
        "[HTTP] Sunucu başladı:",
        PORT,
    )


# =========================================================
# TELEGRAM LISTENER
# =========================================================

async def listener(event):

    try:

        key = (
            event.chat_id,
            event.message.id,
        )

        if key in processed_messages:

            return

        processed_messages.add(
            key
        )

        if (
            len(
                processed_messages
            )
            >
            50000
        ):

            processed_messages.clear()

        d = parse_event(
            event
        )

        if not d:

            return

        if not add_to_radar(
            d
        ):

            return

        await telegram_queue.put(
            {
                "kind":
                    "normal",

                "data":
                    d,
            }
        )

        alarms = get_alarms(
            d
        )

        event_key = make_event_key(
            d
        )

        for alarm in alarms:

            if alarm_exists(
                alarm["key"]
            ):

                continue

            if save_alarm(
                alarm["key"],
                event_key,
                alarm["type"],
            ):

                await telegram_queue.put(
                    {
                        "kind":
                            "alarm",

                        "data":
                            d,

                        "alarm":
                            alarm,
                    }
                )

    except Exception as e:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(e),
        )


# =========================================================
# WATCHDOG
# =========================================================

async def telegram_connection_watch():

    global client

    while True:

        try:

            if client:

                if not client.is_connected():

                    print(
                        "[TELEGRAM] Bağlantı koptu."
                    )

                    try:

                        await client.connect()

                        print(
                            "[TELEGRAM] Yeniden bağlandı."
                        )

                    except Exception as e:

                        print(
                            "[TELEGRAM] Yeniden bağlanma:",
                            repr(e),
                        )

                else:

                    print(
                        "[TELEGRAM] Bağlantı OK."
                    )

        except Exception as e:

            print(
                "[WATCHDOG]",
                repr(e),
            )

        await asyncio.sleep(
            30
        )


# =========================================================
# MAIN
# =========================================================

async def main():

    global http_session
    global client
    global bot_application

    print(
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
    )

    print(
        "🔐 DAVET TABANLI VIP SİSTEMİ AKTİF"
    )

    print(
        "📱 MINI APP AKTİF"
    )

    print(
        "🔥 YÜKSEK COIN EFEKTİ AKTİF"
    )

    print(
        "👑 ADMIN USER ID:",
        ADMIN_USER_ID,
    )

    print(
        "👑 ADMIN CHAT ID:",
        ADMIN_CHAT_ID,
    )

    db_load_recent()

    http_session = (
        aiohttp.ClientSession()
    )

    await start_http()

    # =====================================================
    # TELETHON
    # =====================================================

    client = TelegramClient(
        StringSession(
            STRING_SESSION
        ),
        API_ID,
        API_HASH,
    )

    while True:

        try:

            await client.start()

            print(
                "[TELETHON] Bağlandı."
            )

            break

        except Exception as e:

            print(
                "[TELETHON]",
                repr(e),
            )

            await asyncio.sleep(
                15
            )

    client.add_event_handler(
        listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        ),
    )

    # =====================================================
    # BOT
    # =====================================================

    bot_application = (
        ApplicationBuilder()
        .token(
            BOT_TOKEN
        )
        .build()
    )

    bot_application.add_handler(
        CommandHandler(
            "start",
            bot_start
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "davet",
            bot_davet
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "uyeler",
            bot_uyeler
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "silvip",
            bot_silvip
        )
    )

    bot_application.add_handler(
        CommandHandler(
            "id",
            bot_id
        )
    )

    await bot_application.initialize()

    await bot_application.start()

    if bot_application.updater:

        await bot_application.updater.start_polling(
            drop_pending_updates=True
        )

    print(
        "[BOT] VIP bot aktif."
    )

    print(
        "[BOT] /davet hazır."
    )

    print(
        "[BOT] /uyeler hazır."
    )

    print(
        "[BOT] /silvip hazır."
    )

    print(
        "[BOT] /id hazır."
    )

    # =====================================================
    # TASKLER
    # =====================================================

    asyncio.create_task(
        sender()
    )

    asyncio.create_task(
        telegram_connection_watch()
    )

    print(
        "[HAZIR] RADAR"
    )

    print(
        "[HAZIR] VIP"
    )

    print(
        "[HAZIR] MINI APP"
    )

    print(
        "[HAZIR] DAVET SİSTEMİ"
    )

    print(
        "[HAZIR] ÜYE BİLDİRİMİ"
    )

    print(
        "[MINI APP]",
        MINI_APP_URL,
    )

    try:

        await client.run_until_disconnected()

    finally:

        try:

            if (
                bot_application
                and
                bot_application.updater
            ):

                await (
                    bot_application
                    .updater
                    .stop()
                )

        except Exception as e:

            print(
                "[BOT STOP]",
                repr(e),
            )

        try:

            if bot_application:

                await bot_application.stop()

                await bot_application.shutdown()

        except Exception as e:

            print(
                "[BOT SHUTDOWN]",
                repr(e),
            )

        try:

            if client:

                await client.disconnect()

        except Exception:

            pass

        try:

            if http_session:

                await http_session.close()

        except Exception:

            pass

        try:

            db.close()

        except Exception:

            pass


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
            repr(e),
        )
