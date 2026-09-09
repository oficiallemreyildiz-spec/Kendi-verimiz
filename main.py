import os
import re
import json
import base64
import asyncio
import time
import sqlite3
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
    -1002583301445
]

PORT = int(os.environ.get("PORT", "10000"))

DB_PATH = os.environ.get(
    "DATABASE_PATH",
    "radar.db"
)

MAX_HISTORY = 500


# =========================================================
# 🚨 ALARM AYARLARI
# =========================================================

# ÖRNEK:
#
# COIN_ALARM_LIMIT = 500
# -> 500 ve üstü coin alarmı
#
# PEOPLE_ALARM_LIMIT = 3
# -> 3 ve altı kişi alarmı
#
# 0 yaparsan ilgili alarm kapanır.

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
# SQLITE KAYIT
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
            safe_int(d["coins"]),
            safe_int(d["people"]),
            safe_int(d["joined"]),
            safe_float(d["rate"]),
            safe_int(d["view"]),
            str(d["room"]),
            d.get("live", ""),
            safe_int(d["target_time"]),
            safe_int(d["detected_at"]),
            safe_int(d.get("source_message_id")),
            safe_int(d.get("source_chat_id"))
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
            (event_key,)
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
            "SELECT COUNT(*) FROM radar_history"
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
            "chest": chest
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
        """, (MAX_HISTORY,)).fetchall()

        for row in reversed(rows):

            d = {
                "type": row["event_type"],

                "box_name":
                    "Goody Bag"
                    if row["event_type"] == "GOODY BAG"
                    else "Hazine Sandığı",

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
                    row["source_chat_id"]
            }

            target = (
                LIVE_GOODY_BAGS
                if d["type"] == "GOODY BAG"
                else LIVE_CHESTS
            )

            if d["room"]:
                target[d["room"]] = d

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
# TOKEN
# =========================================================

def token_from_event(e):

    try:
        m = e.message
        t = m.raw_text or ""

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

        for ent in m.entities or []:

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

    try:

        for ent, _ in m.get_entities_text():

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
            "[TOKEN ENTITY TEXT]",
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
                s + "=" * (-len(s) % 4)
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
                    s + "=" * (-len(s) % 4)
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

def coins(t, d=None):

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

def rate(t, d=None):

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

def is_goody(t, d):

    u = (t or "").upper()

    if re.search(
        r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG',
        u
    ):
        return True

    if (
        re.search(
            r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
            u
        )
        or "🟡" in t
    ):
        return False

    if d:

        if d.get("is_goody_bag") in [
            True,
            1,
            "1",
            "true",
            "True"
        ]:
            return True

        if d.get("is_goody_bag") in [
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

def target_time(t, d=None):

    now = int(time.time())

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
                        float(d[k])
                    )

                    if v > 10_000_000_000:
                        return v // 1000

                    if v > 1_000_000_000:
                        return v

                    if 0 < v < 86400:
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
            + safe_int(m.group(1)) * 60
            + safe_int(m.group(2))
        )

    return now + 180


# =========================================================
# PARSE
# =========================================================

def parse(event):

    t = event.message.raw_text or ""

    d = decode_token(
        token_from_event(event)
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
        or username_from_text(t)
        or "bilinmiyor"
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
        or room_from_text(t)
        or "msg:" + str(event.message.id)
    )

    p = people(t)

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

    j = joined(t)

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

    v = viewers(t)

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
                and str(
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

    live = live or (
        f"https://www.tiktok.com/share/live/{room}"
        if room
        else f"https://www.tiktok.com/@{username}/live"
    )

    return {

        "type":
            "GOODY BAG"
            if g
            else "CHEST",

        "box_name":
            "Goody Bag"
            if g
            else "Hazine Sandığı",

        "username":
            username,

        "coins":
            coins(t, d),

        "people":
            p,

        "joined":
            j,

        "rate":
            rate(t, d),

        "view":
            v,

        "room":
            room,

        "live":
            live,

        "target_time":
            target_time(t, d),

        "detected_at":
            int(time.time()),

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
        str(d.get("room", "")),
        str(d.get("username", "")).lower(),
        str(d.get("coins", 0)),
        str(d.get("people", 0)),
        str(d.get("source_message_id", 0))
    ])


# =========================================================
# RADAR'A EKLE
# =========================================================

def add(d):

    if not d:
        return False

    room = d.get("room")

    if not room:
        return False

    event_key = make_event_key(d)

    if event_key in processed_signatures:
        return False

    if db_exists(event_key):

        processed_signatures.add(
            event_key
        )

        return False

    target = (
        LIVE_GOODY_BAGS
        if d["type"] == "GOODY BAG"
        else LIVE_CHESTS
    )

    if room in target:

        old = target[room]

        old_time = safe_int(
            old.get("detected_at")
        )

        if (
            int(time.time())
            - old_time
            < 5
        ):
            return False

    db_save(
        d,
        event_key
    )

    processed_signatures.add(
        event_key
    )

    target[room] = d

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
# ALARM KAYDI
# =========================================================

def alarm_exists(alarm_key):

    try:

        row = db.execute(
            """
            SELECT 1
            FROM alarm_history
            WHERE alarm_key=?
            LIMIT 1
            """,
            (alarm_key,)
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
                int(time.time())
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


# =========================================================
# ALARM KONTROL
# =========================================================

def get_alarms(d):

    alarms = []

    event_key = make_event_key(d)

    coin = safe_int(
        d.get("coins")
    )

    person = safe_int(
        d.get("people")
    )

    # COIN

    if (
        COIN_ALARM_LIMIT > 0
        and coin >= COIN_ALARM_LIMIT
    ):

        alarms.append({

            "type": "COIN",

            "title":
                "🚨 COIN ALARMI",

            "key":
                (
                    event_key
                    + "|COIN|"
                    + str(COIN_ALARM_LIMIT)
                )
        })

    # PEOPLE
    #
    # 0 değerini alarm saymıyoruz.
    # Çünkü veri okunamadığında 0 gelebilir.

    if (
        PEOPLE_ALARM_LIMIT > 0
        and person > 0
        and person <= PEOPLE_ALARM_LIMIT
    ):

        alarms.append({

            "type": "PEOPLE",

            "title":
                "⚠️ DÜŞÜK KİŞİ ALARMI",

            "key":
                (
                    event_key
                    + "|PEOPLE|"
                    + str(PEOPLE_ALARM_LIMIT)
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
        if d["type"] == "GOODY BAG"
        else "🟨 HAZİNE SANDIĞI"
    )

    text = (
        f"{title}\n\n"
        f"👤 Kullanıcı: {d['username']}\n"
        f"🪙 Coin: {d['coins']}\n"
        f"👥 Kişi: {d['people']}\n"
        f"🙋 Katılan: {d['joined']}\n"
        f"📈 Oran: {d['rate']}\n"
        f"👀 İzlenme: {d['view']}\n"
        f"🏠 Oda: {d['room']}\n"
    )

    if d.get("live"):

        text += (
            f"\n🔴 {d['live']}"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    for attempt in range(1, 9):

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
# 🚨 TELEGRAM ALARM
# =========================================================

async def send_alarm(d, alarm):

    global http_session

    if not http_session:
        return

    if alarm["type"] == "COIN":

        title = "🚨 COIN ALARMI"

        reason = (
            f"🪙 COIN: {d['coins']}\n"
            f"🎯 SENİN LİMİTİN: "
            f"{COIN_ALARM_LIMIT}"
        )

    else:

        title = "⚠️ DÜŞÜK KİŞİ ALARMI"

        reason = (
            f"👥 KİŞİ: {d['people']}\n"
            f"🎯 SENİN LİMİTİN: "
            f"{PEOPLE_ALARM_LIMIT}"
        )

    box = (
        "🟪 GOODY BAG"
        if d["type"] == "GOODY BAG"
        else "🟨 HAZİNE SANDIĞI"
    )

    text = (
        f"{title}\n\n"
        f"{box}\n"
        f"👤 Kullanıcı: {d['username']}\n"
        f"{reason}\n"
        f"🙋 Katılan: {d['joined']}\n"
        f"📈 Oran: {d['rate']}\n"
        f"👀 İzlenme: {d['view']}\n"
        f"🏠 Oda: {d['room']}\n"
    )

    if d.get("live"):

        text += (
            f"\n🔴 {d['live']}"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    for attempt in range(1, 9):

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
                    "[ALARM TELEGRAM HATA]",
                    response.status,
                    response_text
                )

                return

        except Exception as e:

            print(
                "[ALARM TELEGRAM]",
                repr(e)
            )

            await asyncio.sleep(
                min(
                    5 * attempt,
                    30
                )
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
# HTML
# =========================================================

RADAR_HTML = r"""
<!DOCTYPE html>
<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
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
    width: 100%;
    min-height: 100%;
}

body {
    background: #07090d;
    color: #ffffff;
    font-family:
        Arial,
        Helvetica,
        sans-serif;

    padding: 12px;
}

.head {
    text-align: center;
    padding: 18px 8px 28px;
}

.title {
    font-size: 58px;
    font-weight: 1000;
    line-height: 1;
}

.sub {
    margin-top: 14px;
    font-size: 22px;
    font-weight: 900;
}

.status {
    display: inline-block;

    margin-top: 14px;
    padding: 12px 20px;

    border-radius: 14px;

    background: #111722;
    border: 2px solid #273142;

    font-size: 17px;
    font-weight: 900;
}

.grid {
    display: grid;

    grid-template-columns:
        repeat(2, minmax(0, 1fr));

    gap: 18px;

    max-width: 1800px;
    margin: 0 auto;
}

.panel {
    background: #0d1118;

    border: 2px solid #202938;

    border-radius: 18px;

    padding: 16px;
}

.pnrow {
    display: flex;

    align-items: center;
    justify-content: space-between;

    gap: 12px;

    padding:
        5px
        5px
        16px;
}

.pn {
    font-size: 30px;
    font-weight: 1000;
}

.cnt {
    font-size: 18px;
    font-weight: 1000;

    padding: 8px 14px;

    border-radius: 12px;

    background: #18202c;
}

.card {
    background: #111720;

    border: 1px solid #26303e;

    border-radius: 16px;

    padding: 17px;

    margin-bottom: 12px;
}

.g .card {
    border-left:
        8px solid #9b5cff;
}

.c .card {
    border-left:
        8px solid #ffd400;
}

.ur {
    display: flex;

    align-items: center;

    justify-content: space-between;

    gap: 10px;

    margin-bottom: 14px;
}

.user {
    font-size: 22px;
    font-weight: 1000;

    overflow-wrap: anywhere;
}

.rank {
    font-size: 14px;
    font-weight: 800;

    opacity: .75;
}

.badge {
    font-size: 13px;
    font-weight: 1000;

    padding: 8px 11px;

    border-radius: 10px;

    white-space: nowrap;
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
        repeat(2, minmax(0, 1fr));

    gap: 9px;
}

.info {
    background: #0a0e14;

    border: 1px solid #202a38;

    border-radius: 12px;

    padding: 12px;

    font-size: 12px;
    font-weight: 800;
}

.info b {
    display: block;

    margin-top: 5px;

    font-size: 21px;
    font-weight: 1000;
}

.go {
    margin-top: 12px;

    padding: 15px;

    border-radius: 12px;

    background: #151c27;

    font-size: 15px;
    font-weight: 1000;

    overflow-wrap: anywhere;
}

.go a {
    color: #8ec5ff;
    text-decoration: none;
}

.empty {
    text-align: center;

    padding: 32px 15px;

    font-size: 16px;
    font-weight: 900;

    opacity: .65;
}

.foot {
    text-align: center;

    padding: 18px 8px;

    font-size: 12px;
    font-weight: 800;

    opacity: .6;
}


/* =====================================================
   MOBİL - HER ŞEY BÜYÜK
   ===================================================== */

@media (max-width: 700px) {

    body {
        padding: 8px;
    }

    .head {
        padding:
            14px
            4px
            22px;
    }

    .title {
        font-size: 48px;
        font-weight: 1000;
        line-height: 1.05;
    }

    .sub {
        font-size: 18px;
        font-weight: 1000;

        margin-top: 12px;
    }

    .status {
        font-size: 15px;
        font-weight: 1000;

        padding: 12px 16px;

        margin-top: 12px;
    }

    /* TELEFONDA TEK SÜTUN */
    .grid {
        grid-template-columns: 1fr;
        gap: 18px;
    }

    .panel {
        padding: 13px;
        border-radius: 17px;
    }

    .pnrow {
        padding:
            5px
            5px
            14px;
    }

    .pn {
        font-size: 25px;
        font-weight: 1000;
    }

    .cnt {
        font-size: 16px;
        padding: 8px 12px;
    }

    .card {
        padding: 16px;
        margin-bottom: 11px;

        border-radius: 15px;
    }

    .g .card,
    .c .card {
        border-left-width: 8px;
    }

    .ur {
        gap: 10px;
        margin-bottom: 13px;
    }

    .user {
        font-size: 21px;
        line-height: 1.25;
    }

    .rank {
        font-size: 13px;
    }

    .badge {
        font-size: 12px;
        padding: 8px 10px;
    }

    .ig {
        grid-template-columns: 1fr 1fr;
        gap: 8px;
    }

    .info {
        padding: 12px;

        font-size: 11px;
    }

    .info b {
        font-size: 20px;
        margin-top: 5px;
    }

    .go {
        padding: 15px;
        margin-top: 11px;

        font-size: 14px;
        line-height: 1.35;
    }

    .empty {
        padding: 28px 12px;
        font-size: 15px;
    }

    .foot {
        font-size: 11px;
        padding: 15px;
    }
}


/* =====================================================
   ÇOK KÜÇÜK TELEFON
   ===================================================== */

@media (max-width: 390px) {

    .title {
        font-size: 42px;
    }

    .sub {
        font-size: 16px;
    }

    .status {
        font-size: 13px;
    }

    .pn {
        font-size: 22px;
    }

    .cnt {
        font-size: 14px;
    }

    .user {
        font-size: 19px;
    }

    .info {
        font-size: 10px;
        padding: 10px;
    }

    .info b {
        font-size: 18px;
    }

    .go {
        font-size: 13px;
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
        class="status"
        id="status"
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
                class="cnt"
                id="goodyCount"
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
                class="cnt"
                id="chestCount"
            >
                0
            </div>

        </div>

        <div id="chestList">

            <div class="empty">
                Hazine Sandığı bekleniyor...
            </div>

        </div>

    </section>

</div>


<div class="foot">
    ⚡ ÖDÜL AVCISI • KALICI RADAR • CANLI VERİ
</div>


<script>

const goodyList =
    document.getElementById("goodyList");

const chestList =
    document.getElementById("chestList");

const goodyCount =
    document.getElementById("goodyCount");

const chestCount =
    document.getElementById("chestCount");

const statusEl =
    document.getElementById("status");


function esc(value) {

    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function card(d) {

    const type =
        d.type === "GOODY BAG"
            ? "GOODY BAG"
            : "HAZİNE SANDIĞI";

    const badge =
        d.type === "GOODY BAG"
            ? "🟪 GOODY"
            : "🟨 CHEST";

    const link =
        d.live || "";

    return `

        <div class="card">

            <div class="ur">

                <div>

                    <div class="user">
                        👤 ${esc(d.username)}
                    </div>

                    <div class="rank">
                        ${esc(type)}
                    </div>

                </div>

                <div class="badge">
                    ${badge}
                </div>

            </div>


            <div class="ig">

                <div class="info">
                    🪙 COIN
                    <b>
                        ${esc(d.coins)}
                    </b>
                </div>

                <div class="info">
                    👥 KİŞİ
                    <b>
                        ${esc(d.people)}
                    </b>
                </div>

                <div class="info">
                    🙋 KATILAN
                    <b>
                        ${esc(d.joined)}
                    </b>
                </div>

                <div class="info">
                    📈 ORAN
                    <b>
                        ${esc(d.rate)}
                    </b>
                </div>

                <div class="info">
                    👀 İZLENME
                    <b>
                        ${esc(d.view)}
                    </b>
                </div>

                <div class="info">
                    🏠 ODA
                    <b>
                        ${esc(d.room)}
                    </b>
                </div>

            </div>


            ${
                link
                    ? `
                        <div class="go">
                            🔴 CANLI YAYIN
                            <br><br>
                            <a
                                href="${esc(link)}"
                                target="_blank"
                                rel="noopener"
                            >
                                ${esc(link)}
                            </a>
                        </div>
                    `
                    : ""
            }

        </div>

    `;
}


function render(list, element, countElement) {

    const sorted = [...list].sort(
        (a, b) =>
            Number(b.detected_at || 0)
            -
            Number(a.detected_at || 0)
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
                    cache: "no-store"
                }
            );

        if (!response.ok) {
            throw new Error(
                "HTTP " + response.status
            );
        }

        const data =
            await response.json();

        render(
            data.goody_bags || [],
            goodyList,
            goodyCount
        );

        render(
            data.chests || [],
            chestList,
            chestCount
        );

        statusEl.textContent =
            "🟢 RADAR AKTİF • CANLI VERİ";

    } catch (error) {

        console.error(error);

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

async def radar_page(request):

    return web.Response(
        text=RADAR_HTML,
        content_type="text/html",
        charset="utf-8"
    )


@web.middleware
async def cors(request, handler):

    if request.method == "OPTIONS":

        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods":
                    "GET, OPTIONS",
                "Access-Control-Allow-Headers":
                    "*"
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

async def api_all(request):

    stats = db_stats()

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

        "stats":
            stats
    })


async def api_boxes(request):

    return web.json_response(
        list(
            LIVE_CHESTS.values()
        )
    )


async def api_goody(request):

    return web.json_response(
        list(
            LIVE_GOODY_BAGS.values()
        )
    )


async def api_status(request):

    stats = db_stats()

    return web.json_response({

        "status":
            "online",

        "chests":
            len(LIVE_CHESTS),

        "goody_bags":
            len(LIVE_GOODY_BAGS),

        "history":
            stats["total"],

        "total_coins":
            stats["total_coins"],

        "server_time":
            int(time.time()),

        "coin_alarm":
            COIN_ALARM_LIMIT,

        "people_alarm":
            PEOPLE_ALARM_LIMIT
    })


# =========================================================
# HTTP BAŞLAT
# =========================================================

async def start_http():

    app = web.Application(
        middlewares=[cors]
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

    for path in [
        "/api/all",
        "/api/boxes",
        "/api/goody_bags",
        "/api/status"
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

    await web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    ).start()

    print(
        "[HTTP] Sunucu başladı:",
        PORT
    )


# =========================================================
# LISTENER
# =========================================================

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

        if len(
            processed_messages
        ) > 50000:

            processed_messages.clear()

        d = parse(event)

        if not d:
            return

        d["source_chat_id"] = safe_int(
            event.chat_id
        )

        if not add(d):
            return

        # NORMAL BİLDİRİM

        await telegram_queue.put({

            "kind":
                "normal",

            "data":
                d
        })

        # ALARMLAR

        alarms = get_alarms(d)

        event_key = make_event_key(d)

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
# TELEGRAM WATCHDOG
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

        await asyncio.sleep(30)


# =========================================================
# MAIN
# =========================================================

async def main():

    global http_session
    global client

    print(
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
    )

    print(
        "🪙 COIN ALARM LİMİTİ:",
        COIN_ALARM_LIMIT
    )

    print(
        "👥 KİŞİ ALARM LİMİTİ:",
        PEOPLE_ALARM_LIMIT
    )

    db_load_recent()

    http_session = aiohttp.ClientSession()

    await start_http()

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
                "[TELEGRAM] İstemci bağlandı."
            )

            break

        except Exception as e:

            print(
                "[TELEGRAM] Bağlantı hatası:",
                repr(e)
            )

            await asyncio.sleep(15)

    client.add_event_handler(
        listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        )
    )

    asyncio.create_task(
        sender()
    )

    asyncio.create_task(
        telegram_connection_watch()
    )

    print(
        "[HAZIR] Goody Bag + Hazine Sandığı aktif."
    )

    print(
        "[HAZIR] Büyük yazılı mobil arayüz aktif."
    )

    print(
        "[HAZIR] Coin alarmı aktif."
    )

    print(
        "[HAZIR] Düşük kişi alarmı aktif."
    )

    print(
        "[HAZIR] Telegram normal tıklanabilir link aktif."
    )

    try:

        await client.run_until_disconnected()

    finally:

        if http_session:

            await http_session.close()

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
