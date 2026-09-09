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
    WebAppInfo
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler
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
    -1002583301445
]

PORT = int(
    os.environ.get(
        "PORT",
        "10000"
    )
)

DB_PATH = os.environ.get(
    "DATABASE_PATH",
    "radar.db"
)

MAX_HISTORY = 500


# =========================================================
# ALARM
# =========================================================

COIN_ALARM_LIMIT = 100
PEOPLE_ALARM_LIMIT = 5


# =========================================================
# VIP
# =========================================================

MINI_APP_URL = (
    "https://kendi-verimiz.onrender.com/miniapp"
)

VERIFY_URL = (
    "https://kendi-verimiz.onrender.com/verify"
)

VIP_DAYS = 30


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
# SQLITE
# =========================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row


db.execute("""
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
""")


db.execute("""
CREATE INDEX IF NOT EXISTS idx_radar_detected
ON radar_history(detected_at)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS alarm_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alarm_key TEXT UNIQUE,
    event_key TEXT,
    alarm_type TEXT,
    created_at INTEGER
)
""")


# =========================================================
# VIP TABLOLARI
# =========================================================

db.execute("""
CREATE TABLE IF NOT EXISTS vip_users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    verified_at INTEGER DEFAULT 0,
    expires_at INTEGER DEFAULT 0
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS verify_tokens (
    token TEXT PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0
)
""")


db.commit()


# =========================================================
# YARDIMCI
# =========================================================

def safe_int(v, d=0):

    try:
        return int(float(v))

    except:
        return d


def safe_float(v, d=0):

    try:
        return float(v)

    except:
        return d


# =========================================================
# SQLITE
# =========================================================

def db_save(d, event_key):

    try:

        db.execute("""
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
        """, (

            event_key,

            d["type"],

            d["username"],

            safe_int(
                d["coins"]
            ),

            safe_int(
                d["people"]
            ),

            safe_int(
                d["joined"]
            ),

            safe_float(
                d["rate"]
            ),

            safe_int(
                d["view"]
            ),

            str(
                d["room"]
            ),

            d.get(
                "live",
                ""
            ),

            safe_int(
                d["target_time"]
            ),

            safe_int(
                d["detected_at"]
            ),

            safe_int(
                d.get(
                    "source_message_id"
                )
            ),

            safe_int(
                d.get(
                    "source_chat_id"
                )
            )
        ))


        db.commit()

        return True


    except Exception as e:

        print(
            "[SQLITE KAYIT HATASI]",
            repr(e)
        )

        return False


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
            )
        ).fetchone()

        return row is not None


    except Exception as e:

        print(
            "[SQLITE KONTROL HATASI]",
            repr(e)
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

            "total":
                total,

            "total_coins":
                total_coins,

            "goody":
                goody,

            "chest":
                chest
        }


    except Exception as e:

        print(
            "[SQLITE İSTATİSTİK HATASI]",
            repr(e)
        )

        return {

            "total": 0,

            "total_coins": 0,

            "goody": 0,

            "chest": 0
        }


def db_load_recent():

    try:

        rows = db.execute("""
        SELECT *
        FROM radar_history
        ORDER BY detected_at DESC
        LIMIT ?
        """, (
            MAX_HISTORY,
        )).fetchall()


        for row in reversed(rows):

            d = {

                "type":
                    row["event_type"],

                "box_name":
                    (
                        "Goody Bag"
                        if row["event_type"]
                        == "GOODY BAG"
                        else
                        "Hazine Sandığı"
                    ),

                "username":
                    row["username"],

                "coins":
                    row["coins"],

                "people":
                    row["people"],

                "joined":
                    row["joined"],

                "rate":
                    row["rate"],

                "view":
                    row["viewers"],

                "room":
                    row["room"],

                "live":
                    row["live"],

                "target_time":
                    row["target_time"],

                "detected_at":
                    row["detected_at"],

                "source_message_id":
                    row["source_message_id"],

                "source_chat_id":
                    row["source_chat_id"]
            }


            target = (

                LIVE_GOODY_BAGS

                if d["type"]
                == "GOODY BAG"

                else
                LIVE_CHESTS
            )


            if d["room"]:

                target[
                    d["room"]
                ] = d


        print(
            "[SQLITE] Geçmiş kayıtlar yüklendi:",
            len(rows)
        )


    except Exception as e:

        print(
            "[SQLITE YÜKLEME HATASI]",
            repr(e)
        )


# =========================================================
# VIP TOKEN
# =========================================================

def create_verify_token(
    telegram_id
):

    token = secrets.token_urlsafe(
        32
    )

    now = int(
        time.time()
    )

    expires = (
        now
        +
        15 * 60
    )


    db.execute(
        """
        INSERT INTO verify_tokens
        (
            token,
            telegram_id,
            created_at,
            expires_at,
            used
        )
        VALUES (?, ?, ?, ?, 0)
        """,
        (
            token,
            safe_int(
                telegram_id
            ),
            now,
            expires
        )
    )


    db.commit()

    return token


def verify_token_for_user(
    token,
    telegram_id,
    username="",
    first_name=""
):

    if not token:

        return False


    now = int(
        time.time()
    )


    row = db.execute(
        """
        SELECT *
        FROM verify_tokens
        WHERE token=?
        LIMIT 1
        """,
        (
            token,
        )
    ).fetchone()


    if not row:

        return False


    if safe_int(
        row["used"]
    ) == 1:

        return False


    if safe_int(
        row["telegram_id"]
    ) != safe_int(
        telegram_id
    ):

        return False


    if safe_int(
        row["expires_at"]
    ) < now:

        return False


    db.execute(
        """
        UPDATE verify_tokens
        SET used=1
        WHERE token=?
        """,
        (
            token,
        )
    )


    vip_expires = (
        now
        +
        VIP_DAYS
        *
        24
        *
        60
        *
        60
    )


    db.execute(
        """
        INSERT INTO vip_users
        (
            telegram_id,
            username,
            first_name,
            verified_at,
            expires_at
        )
        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            verified_at=excluded.verified_at,
            expires_at=excluded.expires_at
        """,
        (
            safe_int(
                telegram_id
            ),

            str(
                username or ""
            ),

            str(
                first_name or ""
            ),

            now,

            vip_expires
        )
    )


    db.commit()

    return True


def is_vip(
    telegram_id
):

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
            safe_int(
                telegram_id
            ),
        )
    ).fetchone()


    if not row:

        return False


    return (
        safe_int(
            row["expires_at"]
        )
        >
        int(
            time.time()
        )
    )


# =========================================================
# TELEGRAM MINI APP DOĞRULAMA
# =========================================================

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

            f"{key}={value}"

            for key, value
            in sorted(
                pairs.items()
            )

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


        if not auth_date:

            return None


        if (
            int(
                time.time()
            )
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


        user = json.loads(
            user_json
        )


        return user


    except Exception as e:

        print(
            "[MINI APP AUTH HATASI]",
            repr(e)
        )

        return None


# =========================================================
# TOKEN
# =========================================================

def token_from_event(e):

    try:

        m = e.message

        t = (
            m.raw_text
            or ""
        )

    except:

        return None


    patterns = [

        r'https?://[^ \n\]\)]+/t\.php\?token=([^&\s\]\)]+)',

        r'https?://[^ \n\]\)]+t\.php\?token=([^&\s\]\)]+)'
    ]


    for p in patterns:

        m1 = re.search(
            p,
            t,
            re.I
        )


        if m1:

            return unquote(
                m1.group(1)
            )


    try:

        for ent in (
            m.entities
            or []
        ):

            u = getattr(
                ent,
                "url",
                None
            )


            if u:

                m1 = re.search(

                    r't\.php\?token=([^&\s]+)',

                    u,

                    re.I
                )


                if m1:

                    return unquote(
                        m1.group(1)
                    )


    except Exception as e:

        print(
            "[TOKEN ENTITY]",
            repr(e)
        )


    return None


def decode_token(tok):

    if not tok:

        return None


    try:

        s = unquote(
            str(tok)
        ).strip()


        return json.loads(

            base64.urlsafe_b64decode(

                s
                +
                "="
                *
                (
                    -len(s) % 4
                )

            ).decode(
                "utf-8",
                errors="ignore"
            )

        )


    except Exception as e:

        print(
            "[TOKEN HATA]",
            repr(e)
        )

        return None


# =========================================================
# ROOM
# =========================================================

def room_from_text(t):

    patterns = [

        r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)',

        r'https?://[^ \n]+/t/\?p=([A-Za-z0-9_\-+/=]+)'
    ]


    for p in patterns:

        m = re.search(
            p,
            t or "",
            re.I
        )


        if m:

            try:

                s = m.group(1)


                room = base64.urlsafe_b64decode(

                    s
                    +
                    "="
                    *
                    (
                        -len(s) % 4
                    )

                ).decode(
                    "utf-8",
                    errors="ignore"
                ).strip()


                if room.isdigit():

                    return room


            except:

                pass


    return None


# =========================================================
# USERNAME
# =========================================================

def username_from_text(t):

    patterns = [

        r'^\s*##\s*T\d+\s*[›>:]\s*([^\s\n]+)',

        r'^\s*T\d+\s*[›>:]\s*([^\s\n]+)'
    ]


    for p in patterns:

        m = re.search(
            p,
            t or "",
            re.M
        )


        if m:

            return m.group(1).strip()


    return None


# =========================================================
# COIN
# =========================================================

def coins(
    t,
    d=None
):

    patterns = [

        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',

        r'BOX\s*:\s*(\d+)\s*/',

        r'(\d+)\s*/\s*(\d+)'
    ]


    for p in patterns:

        m = re.search(
            p,
            t or "",
            re.I
        )


        if m:

            if (
                "TÚI" in p
                or "TUI" in p
                or "BOX" in p
            ):

                return safe_int(
                    m.group(1)
                )


            return safe_int(
                m.group(1)
            )


    if d:

        for k in [

            "coins",
            "coin",
            "gem",
            "diamond",
            "amount"

        ]:

            if k in d:

                return safe_int(
                    d[k]
                )


    return 0


# =========================================================
# PEOPLE
# =========================================================

def people(t):

    patterns = [

        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',

        r'BOX\s*:\s*\d+\s*/\s*(\d+)',

        r'(\d+)\s*/\s*(\d+)'
    ]


    for p in patterns:

        m = re.search(
            p,
            t or "",
            re.I
        )


        if m:

            if (
                "TÚI" in p
                or "TUI" in p
                or "BOX" in p
            ):

                return safe_int(
                    m.group(1)
                )


            return safe_int(
                m.group(2)
            )


    return 0


# =========================================================
# JOINED
# =========================================================

def joined(t):

    patterns = [

        r'Đã\s*join\s*:\s*(\d+)',

        r'joined\s*:\s*(\d+)',

        r'join\s*:\s*(\d+)'
    ]


    for p in patterns:

        m = re.search(
            p,
            t or "",
            re.I
        )


        if m:

            return safe_int(
                m.group(1)
            )


    return 0


# =========================================================
# VIEWERS
# =========================================================

def viewers(t):

    m = re.search(
        r'👀\s*(\d+)',
        t or ""
    )


    if m:

        return safe_int(
            m.group(1)
        )


    return 0


# =========================================================
# RATE
# =========================================================

def rate(
    t,
    d=None
):

    m = re.search(

        r'Rate\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        t or "",

        re.I
    )


    if m:

        return safe_float(
            m.group(1)
        )


    if d:

        for k in [
            "ratio",
            "rate"
        ]:

            if k in d:

                return safe_float(
                    d[k]
                )


    return 0


# =========================================================
# GOODY / CHEST
# =========================================================

def is_goody(
    t,
    d
):

    u = (
        t or ""
    ).upper()


    if re.search(

        r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG',

        u

    ):

        return True


    if re.search(

        r'\bBOX\b|RƯƠNG|TREO|HAZİNE',

        u

    ):

        return False


    if d:

        if d.get(
            "is_goody_bag"
        ) in [

            True,
            1,
            "1",
            "true",
            "True"

        ]:

            return True


        if d.get(
            "is_goody_bag"
        ) in [

            False,
            0,
            "0",
            "false",
            "False"

        ]:

            return False


    return None


# =========================================================
# TARGET TIME
# =========================================================

def target_time(
    t,
    d=None
):

    now = int(
        time.time()
    )


    if d:

        for k in [

            "time",
            "target_time",
            "end_time",
            "endTime"

        ]:

            if k in d:

                try:

                    v = int(
                        float(
                            d[k]
                        )
                    )


                    if v > 10_000_000_000:

                        return v // 1000


                    if v > 1_000_000_000:

                        return v


                    if (
                        0 < v < 86400
                    ):

                        return now + v


                except:

                    pass


    m = re.search(

        r'TIME\s*:\s*(\d+):(\d+)',

        t or "",

        re.I
    )


    if m:

        return (

            now

            +

            safe_int(
                m.group(1)
            )
            *
            60

            +

            safe_int(
                m.group(2)
            )

        )


    return now + 180


# =========================================================
# PARSE
# =========================================================

def parse(event):

    t = (

        event.message.raw_text

        or ""

    )


    d = decode_token(

        token_from_event(
            event
        )

    )


    g = is_goody(
        t,
        d
    )


    if g is None:

        return None


    username = None


    for k in [

        "username",
        "user",
        "unique_id",
        "uniqueId"

    ]:

        if d and d.get(k):

            username = str(
                d[k]
            )

            break


    username = (

        username

        or

        username_from_text(
            t
        )

        or

        "bilinmiyor"

    )


    room = None


    for k in [

        "room",
        "room_id",
        "roomid",
        "roomId",
        "roomID"

    ]:

        if d and d.get(k):

            room = str(
                d[k]
            )

            break


    room = (

        room

        or

        room_from_text(
            t
        )

        or

        "msg:"
        +
        str(
            event.message.id
        )

    )


    p = people(
        t
    )


    if not p and d:

        for k in [

            "people",
            "person",
            "count",
            "capacity"

        ]:

            if k in d:

                p = safe_int(
                    d[k]
                )

                if p:

                    break


    j = joined(
        t
    )


    if not j and d:

        for k in [

            "joined",
            "join",
            "join_count",
            "joined_count"

        ]:

            if k in d:

                j = safe_int(
                    d[k]
                )

                if j:

                    break


    v = viewers(
        t
    )


    if not v and d:

        for k in [

            "view",
            "views",
            "viewer",
            "viewers"

        ]:

            if k in d:

                v = safe_int(
                    d[k]
                )

                if v:

                    break


    live = ""


    if d:

        for k in [

            "openitok",
            "live",
            "live_url",
            "url"

        ]:

            if (

                d.get(k)

                and

                str(
                    d[k]
                ).startswith(
                    (
                        "http://",
                        "https://"
                    )
                )

            ):

                live = str(
                    d[k]
                )

                break


    live = (

        live

        or

        (

            f"https://www.tiktok.com/share/live/{room}"

            if room

            else

            f"https://www.tiktok.com/@{username}/live"

        )

    )


    return {

        "type":
            (
                "GOODY BAG"
                if g
                else
                "CHEST"
            ),

        "box_name":
            (
                "Goody Bag"
                if g
                else
                "Hazine Sandığı"
            ),

        "username":
            username,

        "coins":
            coins(
                t,
                d
            ),

        "people":
            p,

        "joined":
            j,

        "rate":
            rate(
                t,
                d
            ),

        "view":
            v,

        "room":
            room,

        "live":
            live,

        "target_time":
            target_time(
                t,
                d
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
            )
    }


# =========================================================
# EVENT KEY
# =========================================================

def make_event_key(d):

    return "|".join([

        d["type"],

        str(
            d.get(
                "room",
                ""
            )
        ),

        str(
            d.get(
                "username",
                ""
            )
        ).lower(),

        str(
            d.get(
                "coins",
                0
            )
        ),

        str(
            d.get(
                "people",
                0
            )
        ),

        str(
            d.get(
                "source_message_id",
                0
            )
        )

    ])


# =========================================================
# RADAR'A EKLE
# =========================================================

def add(d):

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


    if (
        event_key
        in
        processed_signatures
    ):

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
        == "GOODY BAG"

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

        d["people"]

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
            )

        ).fetchone()


        return row is not None


    except Exception as e:

        print(
            "[ALARM CHECK]",
            repr(e)
        )

        return False


def save_alarm(
    alarm_key,
    event_key,
    alarm_type
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
                int(
                    time.time()
                )
            )

        )


        db.commit()

        return True


    except Exception as e:

        print(
            "[ALARM SAVE]",
            repr(e)
        )

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


    person = safe_int(
        d.get(
            "people"
        )
    )


    if (

        COIN_ALARM_LIMIT > 0

        and

        coin >=
        COIN_ALARM_LIMIT

    ):

        alarms.append({

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
                )
        })


    if (

        PEOPLE_ALARM_LIMIT > 0

        and

        person > 0

        and

        person <=
        PEOPLE_ALARM_LIMIT

    ):

        alarms.append({

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
                )
        })


    return alarms


# =========================================================
# TELEGRAM NORMAL
# =========================================================

async def send_tg(d):

    global http_session


    if not http_session:

        return


    title = (

        "🟪 GOODY BAG"

        if d["type"]
        == "GOODY BAG"

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


    if d.get(
        "live"
    ):

        text += (

            f"\n🔴 "
            f"{d['live']}"

        )


    url = (

        "https://api.telegram.org/"

        f"bot{BOT_TOKEN}/sendMessage"

    )


    for attempt in range(
        1,
        9
    ):

        try:

            async with http_session.post(

                url,

                json={

                    "chat_id":
                        TARGET_CHAT_ID,

                    "text":
                        text,

                    "disable_web_page_preview":
                        True

                }

            ) as response:


                response_text = (
                    await response.text()
                )


                if response.status == 200:

                    return


                if response.status == 429:

                    try:

                        wait_time = (

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

                    except:

                        wait_time = 30


                    await asyncio.sleep(

                        max(
                            1,
                            safe_int(
                                wait_time,
                                30
                            )
                        )

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

                    response_text

                )


                return


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


# =========================================================
# TELEGRAM ALARM
# =========================================================

async def send_alarm(
    d,
    alarm
):

    global http_session


    if not http_session:

        return


    if alarm["type"] == "COIN":

        title = "🚨 COIN ALARMI"

        reason = (

            f"🪙 COIN: "
            f"{d['coins']}\n"

            f"🎯 LİMİT: "
            f"{COIN_ALARM_LIMIT}"

        )


    else:

        title = (
            "⚠️ DÜŞÜK KİŞİ ALARMI"
        )

        reason = (

            f"👥 KİŞİ: "
            f"{d['people']}\n"

            f"🎯 LİMİT: "
            f"{PEOPLE_ALARM_LIMIT}"

        )


    box = (

        "🟪 GOODY BAG"

        if d["type"]
        == "GOODY BAG"

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


    if d.get(
        "live"
    ):

        text += (

            f"\n🔴 "
            f"{d['live']}"

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
                    True

            }

        ) as response:

            if response.status != 200:

                print(

                    "[ALARM TELEGRAM]",

                    response.status,

                    await response.text()

                )


    except Exception as e:

        print(
            "[ALARM TELEGRAM]",
            repr(e)
        )


# =========================================================
# QUEUE
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

                    job["alarm"]

                )


        except Exception as e:

            print(
                "[SENDER HATA]",
                repr(e)
            )


        finally:

            telegram_queue.task_done()


# =========================================================
# BOT /START
# =========================================================

async def bot_start(
    update,
    context
):

    user = (
        update.effective_user
    )


    if not user:

        return


    args = context.args


    # =====================================================
    # VIP TOKEN
    # =====================================================

    if (

        args

        and

        args[0].startswith(
            "vip_"
        )

    ):

        token = args[0][4:]


        ok = verify_token_for_user(

            token,

            user.id,

            user.username or "",

            user.first_name or ""

        )


        if not ok:

            await update.message.reply_text(

                "❌ Doğrulama başarısız.\n\n"

                "Bağlantı geçersiz, süresi dolmuş "
                "veya başka bir Telegram hesabına "
                "ait olabilir."

            )

            return


        keyboard = [[

            InlineKeyboardButton(

                "🌐 VIP RADARI AÇ",

                web_app=WebAppInfo(

                    url=MINI_APP_URL

                )

            )

        ]]


        await update.message.reply_text(

            f"✅ **Doğrulama Başarılı, "
            f"{user.first_name}!**\n\n"

            "VIP erişiminiz aktif.\n"

            "Canlı Ödül Avcısı radarını "
            "Telegram içerisinde açabilirsiniz.\n\n"

            "👇 Aşağıdaki butona basın:",

            reply_markup=
                InlineKeyboardMarkup(
                    keyboard
                ),

            parse_mode="Markdown"

        )


        print(

            "[VIP] DOĞRULANDI:",

            user.id,

            user.username

        )


        return


    # =====================================================
    # ZATEN VIP
    # =====================================================

    if is_vip(
        user.id
    ):

        keyboard = [[

            InlineKeyboardButton(

                "🌐 VIP RADARI AÇ",

                web_app=WebAppInfo(

                    url=MINI_APP_URL

                )

            )

        ]]


        await update.message.reply_text(

            f"✅ **VIP erişiminiz aktif, "
            f"{user.first_name}!**\n\n"

            "Canlı radar ekranını açabilirsiniz.",

            reply_markup=
                InlineKeyboardMarkup(
                    keyboard
                ),

            parse_mode="Markdown"

        )


        return


    # =====================================================
    # YENİ KULLANICI
    # =====================================================

    token = create_verify_token(
        user.id
    )


    verify_link = (

        f"{VERIFY_URL}"

        f"?token={token}"

    )


    keyboard = [[

        InlineKeyboardButton(

            "🔒 SİTEDEN DOĞRULAMA YAP",

            url=verify_link

        )

    ]]


    await update.message.reply_text(

        "⚠️ **ERİŞİM ENGELLENDİ!**\n\n"

        "VIP Radarı kullanabilmek için "
        "önce doğrulama yapmanız gerekiyor.\n\n"

        "👇 Aşağıdaki butona basın:",

        reply_markup=
            InlineKeyboardMarkup(
                keyboard
            ),

        parse_mode="Markdown"

    )


    print(

        "[VIP] TOKEN OLUŞTURULDU:",

        user.id

    )


# =========================================================
# VERIFY SAYFASI
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

<title>VIP Doğrulama</title>

<style>

* {
    box-sizing: border-box;
}

html,
body {

    margin: 0;

    padding: 0;

    min-height: 100%;

}

body {

    min-height: 100vh;

    display: flex;

    align-items: center;

    justify-content: center;

    padding: 20px;

    background:
        radial-gradient(
            circle at top,
            #28134d,
            #090b12 50%,
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

    max-width: 470px;

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

.lock {

    font-size: 60px;

    margin-bottom: 12px;
}

h1 {

    margin:
        0 0 14px;

    color: #c084fc;

    font-size: 28px;

    font-weight: 1000;
}

p {

    color: #cbd5e1;

    font-size: 16px;

    line-height: 1.6;

    margin-bottom: 25px;
}

.button {

    display: block;

    width: 100%;

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

    font-size: 17px;

    font-weight: 1000;

    box-shadow:
        0 8px 25px
        rgba(124,58,237,.4);
}

.note {

    margin-top: 18px;

    color: #64748b;

    font-size: 13px;
}

.error {

    color: #ff6b7d;

    font-weight: 1000;
}

</style>

</head>

<body>

<div class="box">

    <div class="lock">
        🔐
    </div>

    <h1>
        VIP DOĞRULAMA
    </h1>

    <p>
        Ödül Avcısı VIP radarına erişmek
        için Telegram hesabınızı
        doğrulamanız gerekiyor.
    </p>

    <div id="content">

        Doğrulama hazırlanıyor...

    </div>

</div>


<script>

const params =
    new URLSearchParams(
        window.location.search
    );

const token =
    params.get("token");


const content =
    document.getElementById(
        "content"
    );


if (!token) {

    content.innerHTML = `

        <div class="error">

            ❌ Geçersiz doğrulama bağlantısı.

        </div>

    `;

} else {

    const botUrl =

        "https://t.me/YeniBirAirdropBot"
        +
        "?start=vip_"
        +
        encodeURIComponent(
            token
        );


    content.innerHTML = `

        <a
            class="button"
            href="${botUrl}"
        >

            🚀 TELEGRAM'DA DOĞRULA

        </a>


        <div class="note">

            Butona bastıktan sonra
            Telegram hesabınız doğrulanacaktır.

        </div>

    `;
}

</script>

</body>

</html>
"""


# =========================================================
# MINI APP
# =========================================================

MINI_APP_HTML = r"""
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

/* =====================================================
   TEMEL
   ===================================================== */

* {

    box-sizing: border-box;

    -webkit-tap-highlight-color:
        transparent;
}

html,
body {

    margin: 0;

    padding: 0;

    width: 100%;

    min-height: 100%;

    background: #05070b;

    color: #ffffff;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

body {

    padding: 7px;

    overflow-x: hidden;
}


/* =====================================================
   BAŞLIK
   ===================================================== */

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

    -webkit-background-clip:
        text;

    -webkit-text-fill-color:
        transparent;
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


/* =====================================================
   İKİLİ PANEL
   ===================================================== */

.sections {

    width: 100%;

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 7px;

    align-items: start;
}


/* =====================================================
   PANEL
   ===================================================== */

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


/* =====================================================
   BAŞLIK
   ===================================================== */

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

    padding:
        5px;

    text-align: center;

    border-radius: 8px;

    background: #18212d;

    font-size: 11px;

    font-weight: 1000;
}


/* =====================================================
   LİSTE
   ===================================================== */

.list {

    width: 100%;

    display: flex;

    flex-direction: column;

    gap: 7px;
}


/* =====================================================
   KART
   ===================================================== */

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


/* =====================================================
   YENİ KAYIT
   ===================================================== */

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


/* =====================================================
   YENİ ROZET
   ===================================================== */

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

        transform:
            scale(1);
    }

    50% {

        opacity: .65;

        transform:
            scale(1.08);
    }
}


/* =====================================================
   YÜKSEK COIN
   ===================================================== */

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


/* =====================================================
   ALEV ÇİZGİSİ
   ===================================================== */

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


/* =====================================================
   KULLANICI
   ===================================================== */

.user-row {

    width: 100%;

    display: flex;

    align-items:
        flex-start;

    justify-content:
        space-between;

    gap: 3px;
}

.user {

    min-width: 0;

    padding-right:
        2px;

    font-size: 13px;

    line-height: 1.15;

    font-weight: 1000;

    overflow-wrap:
        anywhere;
}

.badge {

    flex-shrink: 0;

    padding:
        4px
        5px;

    border-radius: 6px;

    font-size: 6px;

    line-height: 1;

    font-weight: 1000;
}

.goody .badge {

    background: #3b1761;

    color: #dfbaff;
}

.chest .badge {

    background: #504600;

    color: #ffe878;
}


/* =====================================================
   COIN
   ===================================================== */

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


/* =====================================================
   ALEV
   ===================================================== */

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


/* =====================================================
   BİLGİLER
   ===================================================== */

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


/* =====================================================
   CANLI BUTONU
   ===================================================== */

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


/* =====================================================
   BOŞ
   ===================================================== */

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


/* =====================================================
   HATA
   ===================================================== */

.error {

    padding:
        25px
        5px;

    text-align: center;

    color: #ff6b7d;

    font-size: 12px;

    font-weight: 1000;
}


/* =====================================================
   GENİŞ
   ===================================================== */

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


/* =====================================================
   DAR TELEFON
   ===================================================== */

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


<!-- =====================================================
     BAŞLIK
     ===================================================== -->

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


<!-- =====================================================
     YAN YANA
     ===================================================== -->

<div class="sections">


    <!-- GOODY -->

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


    <!-- CHEST -->

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

/* =====================================================
   TELEGRAM
   ===================================================== */

const tg =
    window.Telegram.WebApp;


tg.ready();

tg.expand();


/* =====================================================
   ELEMENTLER
   ===================================================== */

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


/* =====================================================
   İLK YÜKLEME
   ===================================================== */

let firstLoad = true;


/* =====================================================
   BİLİNENLER
   ===================================================== */

const knownItems =
    new Set();


/* =====================================================
   GERÇEKTEN YENİ OLANLAR
   ===================================================== */

const freshItems =
    new Map();


/* =====================================================
   ESCAPE
   ===================================================== */

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


/* =====================================================
   KAYIT ANAHTARI
   ===================================================== */

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


/* =====================================================
   YENİ Mİ?
   ===================================================== */

function isNewItem(d) {

    const key =
        itemKey(d);


    /*
       İlk açılışta mevcut kayıtlar
       yeni sayılmıyor.
    */

    if (firstLoad) {

        knownItems.add(
            key
        );

        return false;
    }


    /*
       Sonradan gelen yeni kayıt.
    */

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


    /*
       8 saniye boyunca YENİ etiketi.
    */

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


/* =====================================================
   100+ COIN ALEV
   ===================================================== */

function isHot(d) {

    return Number(
        d.coins || 0
    ) >= 100;
}


/* =====================================================
   KART
   ===================================================== */

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


            <!-- KULLANICI -->

            <div class="user-row">

                <div class="user">

                    👤
                    ${esc(
                        d.username
                    )}

                </div>


                <div class="badge">

                    ${
                        isGoody
                            ? "🟪 GOODY"
                            : "🟨 CHEST"
                    }

                </div>

            </div>


            <!-- COIN -->

            <div class="coin-box">

                <span class="coin-label">

                    ${
                        hot
                            ? `<span class="fire">🔥</span>`
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


            <!-- BİLGİLER -->

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


            <!-- CANLI -->

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


/* =====================================================
   LİSTE
   ===================================================== */

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


    /*
       En yeni yukarıda.
    */

    element.innerHTML =
        sorted
            .map(card)
            .join("");
}


/* =====================================================
   RADAR
   ===================================================== */

async function loadRadar() {

    try {

        const response =
            await fetch(

                "/api/miniapp-data",

                {

                    method:
                        "GET",

                    headers: {

                        "X-Telegram-Init-Data":
                            tg.initData

                    },

                    cache:
                        "no-store"

                }

            );


        const data =
            await response.json();


        /* =============================================
           VIP HATASI
           ============================================= */

        if (
            !response.ok
        ) {

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


        /* =============================================
           AKTİF
           ============================================= */

        statusEl.textContent =
            "🟢 RADAR AKTİF • CANLI VERİ";


        const goodies =
            data.goody_bags
            || [];


        const chests =
            data.chests
            || [];


        /* =============================================
           İLK YÜKLEME
           ============================================= */

        if (firstLoad) {

            goodies.forEach(

                d => knownItems.add(
                    itemKey(d)
                )

            );


            chests.forEach(

                d => knownItems.add(
                    itemKey(d)
                )

            );

        }


        /* =============================================
           ÇİZ
           ============================================= */

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


        /*
           İlk yükleme tamamlandı.
        */

        firstLoad = false;


    } catch (error) {

        console.error(
            error
        );


        statusEl.textContent =
            "🔴 BAĞLANTI HATASI";

    }
}


/* =====================================================
   İLK ÇALIŞTIR
   ===================================================== */

loadRadar();


/* =====================================================
   3 SANİYEDE BİR
   ===================================================== */

setInterval(

    loadRadar,

    3000

);

</script>


</body>

</html>
"""


# =========================================================
# ANA RADAR SAYFASI
# =========================================================

RADAR_HTML = r"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>ÖDÜL AVCISI</title>

<style>

* {
    box-sizing: border-box;
}

html,
body {

    margin: 0;
    padding: 0;

    background: #07090d;

    color: white;
}

body {

    padding: 10px;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.head {

    text-align: center;

    padding:
        15px
        5px
        25px;
}

.title {

    font-size: 58px;

    font-weight: 1000;
}

.sub {

    margin-top: 12px;

    font-size: 21px;

    font-weight: 1000;
}

.status {

    display: inline-block;

    margin-top: 14px;

    padding:
        12px
        18px;

    border-radius: 15px;

    background: #102719;

    color: #55ff99;

    font-size: 16px;

    font-weight: 1000;
}

.grid {

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 18px;

    max-width: 1800px;

    margin:
        0
        auto;
}

.panel {

    background: #0d1118;

    border:
        2px solid
        #202938;

    border-radius: 18px;

    padding: 15px;
}

.pnrow {

    display: flex;

    align-items: center;

    justify-content:
        space-between;

    padding:
        5px
        5px
        16px;
}

.pn {

    font-size: 29px;

    font-weight: 1000;
}

.cnt {

    padding:
        8px
        14px;

    border-radius: 12px;

    background: #18202c;

    font-size: 18px;

    font-weight: 1000;
}

.card {

    background: #111720;

    border:
        1px solid
        #26303e;

    border-radius: 16px;

    padding: 17px;

    margin-bottom: 12px;
}

.g .card {

    border-left:
        8px solid
        #9b5cff;
}

.c .card {

    border-left:
        8px solid
        #ffd400;
}

.ur {

    display: flex;

    justify-content:
        space-between;

    gap: 10px;

    margin-bottom: 13px;
}

.user {

    font-size: 21px;

    font-weight: 1000;

    overflow-wrap:
        anywhere;
}

.rank {

    margin-top: 3px;

    font-size: 13px;

    opacity: .7;
}

.badge {

    font-size: 12px;

    font-weight: 1000;

    padding:
        8px
        10px;

    border-radius: 10px;
}

.g .badge {

    background: #34145e;

    color: #d9baff;
}

.c .badge {

    background: #554900;

    color: #ffe878;
}

.ig {

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 9px;
}

.info {

    background: #0a0e14;

    border:
        1px solid
        #202a38;

    border-radius: 12px;

    padding: 12px;

    font-size: 11px;

    font-weight: 900;
}

.info b {

    display: block;

    margin-top: 5px;

    font-size: 20px;

    font-weight: 1000;

    overflow-wrap:
        anywhere;
}

.go {

    margin-top: 12px;

    padding: 15px;

    border-radius: 12px;

    background: #151c27;

    font-size: 14px;

    font-weight: 1000;

    overflow-wrap:
        anywhere;
}

.go a {

    color: #8ec5ff;

    text-decoration: none;
}

.empty {

    text-align: center;

    padding: 30px 10px;

    font-size: 15px;

    font-weight: 900;

    opacity: .65;
}

@media(max-width:700px) {

    body {
        padding: 8px;
    }

    .title {
        font-size: 48px;
    }

    .sub {
        font-size: 18px;
    }

    .status {
        font-size: 14px;
    }

    .grid {

        grid-template-columns:
            1fr;

        gap: 18px;
    }

    .panel {
        padding: 13px;
    }

    .pn {
        font-size: 25px;
    }

    .user {
        font-size: 21px;
    }

    .info {
        padding: 12px;
        font-size: 10px;
    }

    .info b {
        font-size: 20px;
    }

    .go {
        font-size: 14px;
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


<div class="grid">


<section class="panel g">

    <div class="pnrow">

        <div class="pn">
            🟪 GOODY BAG
        </div>

        <div
            id="goodyCount"
            class="cnt"
        >
            0
        </div>

    </div>

    <div id="goodyList">

        <div class="empty">
            Goody Bag bekleniyor...
        </div>

    </div>

</section>


<section class="panel c">

    <div class="pnrow">

        <div class="pn">
            🟨 HAZİNE SANDIĞI
        </div>

        <div
            id="chestCount"
            class="cnt"
        >
            0
        </div>

    </div>

    <div id="chestList">

        <div class="empty">
            Hazine bekleniyor...
        </div>

    </div>

</section>


</div>


<script>

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


function card(d) {

    const isGoody =
        d.type === "GOODY BAG";


    return `

        <div class="card">

            <div class="ur">

                <div>

                    <div class="user">

                        👤
                        ${esc(
                            d.username
                        )}

                    </div>

                    <div class="rank">

                        ${
                            isGoody
                                ? "GOODY BAG"
                                : "HAZİNE SANDIĞI"
                        }

                    </div>

                </div>


                <div class="badge">

                    ${
                        isGoody
                            ? "🟪 GOODY"
                            : "🟨 CHEST"
                    }

                </div>

            </div>


            <div class="ig">

                <div class="info">

                    🪙 COIN

                    <b>
                        ${esc(
                            d.coins
                        )}
                    </b>

                </div>


                <div class="info">

                    👥 KİŞİ

                    <b>
                        ${esc(
                            d.people
                        )}
                    </b>

                </div>


                <div class="info">

                    🙋 KATILAN

                    <b>
                        ${esc(
                            d.joined
                        )}
                    </b>

                </div>


                <div class="info">

                    📈 ORAN

                    <b>
                        ${esc(
                            d.rate
                        )}
                    </b>

                </div>


                <div class="info">

                    👀 İZLENME

                    <b>
                        ${esc(
                            d.view
                        )}
                    </b>

                </div>


                <div class="info">

                    🏠 ODA

                    <b>
                        ${esc(
                            d.room
                        )}
                    </b>

                </div>

            </div>


            ${
                d.live

                ? `

                    <div class="go">

                        🔴 CANLI YAYIN

                        <br><br>

                        <a
                            href="${esc(
                                d.live
                            )}"
                            target="_blank"
                            rel="noopener"
                        >

                            ${esc(
                                d.live
                            )}

                        </a>

                    </div>

                `

                : ""
            }

        </div>

    `;
}


function render(
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


async function refresh() {

    try {

        const response =
            await fetch(

                "/api/all",

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


        render(

            data.goody_bags
            || [],

            goodyList,

            goodyCount

        );


        render(

            data.chests
            || [],

            chestList,

            chestCount

        );


        statusEl.textContent =
            "🟢 RADAR AKTİF • CANLI VERİ";


    } catch (error) {

        console.error(
            error
        );


        statusEl.textContent =
            "🔴 RADAR BAĞLANTI HATASI";

    }
}


refresh();


setInterval(
    refresh,
    3000
);

</script>


</body>

</html>
"""


# =========================================================
# HTTP
# =========================================================

async def radar_page(
    request
):

    return web.Response(

        text=RADAR_HTML,

        content_type="text/html",

        charset="utf-8"

    )


async def miniapp_page(
    request
):

    return web.Response(

        text=MINI_APP_HTML,

        content_type="text/html",

        charset="utf-8"

    )


async def verify_page(
    request
):

    return web.Response(

        text=VERIFY_HTML,

        content_type="text/html",

        charset="utf-8"

    )


# =========================================================
# MINI APP API
# =========================================================

async def api_miniapp_data(
    request
):

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

                "status":
                    "error",

                "message":
                    "Telegram kullanıcı doğrulaması başarısız."

            },

            status=401

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
                    "Bu radar sadece VIP üyeler içindir."

            },

            status=403

        )


    return web.json_response({

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
            )

    })


# =========================================================
# API
# =========================================================

async def api_all(
    request
):

    stats = db_stats()


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

        "stats":
            stats

    })


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
            PEOPLE_ALARM_LIMIT

    })


# =========================================================
# CORS
# =========================================================

@web.middleware
async def cors(
    request,
    handler
):

    if request.method == "OPTIONS":

        return web.Response(
            status=204
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
# HTTP BAŞLAT
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


    for path in [

        "/api/all",
        "/api/boxes",
        "/api/goody_bags",
        "/api/status",
        "/api/miniapp-data"

    ]:

        app.router.add_options(

            path,

            lambda request:
                web.Response(
                    status=204
                )

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


# =========================================================
# TELETHON LISTENER
# =========================================================

async def listener(
    event
):

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


        d = parse(
            event
        )


        if not d:

            return


        d["source_chat_id"] = safe_int(

            event.chat_id

        )


        if not add(
            d
        ):

            return


        # NORMAL

        await telegram_queue.put({

            "kind":
                "normal",

            "data":
                d

        })


        # ALARM

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

                alarm["type"]

            ):

                print(

                    "[ALARM]",

                    alarm["title"],

                    "|",

                    d["username"],

                    "| COIN:",

                    d["coins"],

                    "| KİŞİ:",

                    d["people"]

                )


                await telegram_queue.put({

                    "kind":
                        "alarm",

                    "data":
                        d,

                    "alarm":
                        alarm

                })


    except Exception as e:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(e)
        )


# =========================================================
# TELETHON WATCHDOG
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

                            "[TELEGRAM] Yeniden bağlanma hatası:",

                            repr(e)

                        )


                else:

                    print(
                        "[TELEGRAM] Bağlantı OK."
                    )


        except Exception as e:

            print(
                "[TELEGRAM WATCH]",
                repr(e)
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
        "🪙 COIN ALARM:",
        COIN_ALARM_LIMIT
    )


    print(
        "👥 KİŞİ ALARM:",
        PEOPLE_ALARM_LIMIT
    )


    print(
        "🔐 VIP SİSTEMİ AKTİF"
    )


    print(
        "📱 MINI APP AKTİF"
    )


    print(
        "🔥 100+ COIN ALEV EFEKTİ AKTİF"
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

        API_HASH

    )


    while True:

        try:

            await client.start()


            print(
                "[TELEGRAM] Telethon bağlandı."
            )


            break


        except Exception as e:

            print(

                "[TELETHON] Bağlantı hatası:",

                repr(e)

            )


            await asyncio.sleep(
                15
            )


    client.add_event_handler(

        listener,

        events.NewMessage(
            chats=SOURCE_CHATS
        )

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


    await bot_application.initialize()


    await bot_application.start()


    if bot_application.updater:

        await bot_application.updater.start_polling(

            drop_pending_updates=True

        )


    print(
        "[BOT] VIP Telegram bot aktif."
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
        "[HAZIR] Goody Bag aktif."
    )


    print(
        "[HAZIR] Hazine Sandığı aktif."
    )


    print(
        "[HAZIR] VIP doğrulama aktif."
    )


    print(
        "[HAZIR] Mini App aktif."
    )


    print(
        "[HAZIR] Mini App URL:",
        MINI_APP_URL
    )


    try:

        await client.run_until_disconnected()


    finally:

        print(
            "[KAPANIŞ] Sistem kapatılıyor..."
        )


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
                repr(e)
            )


        try:

            if bot_application:

                await bot_application.stop()

                await bot_application.shutdown()

        except Exception as e:

            print(
                "[BOT SHUTDOWN]",
                repr(e)
            )


        try:

            if client:

                await client.disconnect()

        except:

            pass


        try:

            if http_session:

                await http_session.close()

        except:

            pass


        try:

            db.close()

        except:

            pass


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
