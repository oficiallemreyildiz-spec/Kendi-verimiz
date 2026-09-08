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
CREATE INDEX IF NOT EXISTS idx_radar_type
ON radar_history(event_type)
""")

db.commit()


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
            "SELECT COALESCE(SUM(coins),0) FROM radar_history"
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
                "source_message_id": row["source_message_id"],
                "source_chat_id": row["source_chat_id"]
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

    return (
        safe_int(m.group(1))
        if m
        else 0
    )


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

    # USERNAME

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


    # ROOM

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


    # PEOPLE

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


    # JOINED

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


    # VIEWERS

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


    # LIVE

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
# DUPLICATE ANAHTARI
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

    # RAM duplicate

    if event_key in processed_signatures:

        print(
            "[DUPLICATE] RAM:",
            event_key
        )

        return False

    # SQLITE duplicate

    if db_exists(event_key):

        processed_signatures.add(
            event_key
        )

        print(
            "[DUPLICATE] SQLITE:",
            d["username"]
        )

        return False


    target = (
        LIVE_GOODY_BAGS
        if d["type"] == "GOODY BAG"
        else LIVE_CHESTS
    )


    # Aynı oda çok kısa sürede tekrar gelirse
    # spam oluşturma

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
        "| PEOPLE:",
        d["people"]
    )

    return True


# =========================================================
# TELEGRAM
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


    # BUTON YOK.
    # NORMAL TIKLANABİLİR URL.

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

            payload = {
                "chat_id":
                    TARGET_CHAT_ID,

                "text":
                    text,

                "disable_web_page_preview":
                    True
            }


            async with http_session.post(
                url,
                json=payload
            ) as response:

                response_text = (
                    await response.text()
                )


                if response.status == 200:

                    print(
                        "[TELEGRAM] Gönderildi:",
                        d["username"]
                    )

                    return


                if response.status == 429:

                    try:

                        retry_after = (
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

                        retry_after = 30


                    print(
                        "[TELEGRAM] RATE LIMIT:",
                        retry_after,
                        "sn"
                    )


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


async def sender():

    while True:

        d = await telegram_queue.get()

        try:

            await send_tg(d)

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
<!doctype html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>🏆 ÖDÜL AVCISI</title>

<style>

*{
box-sizing:border-box
}

body{
margin:0;
padding:7px;
background:#05060c;
color:#fff;
font-family:Arial,sans-serif
}

.wrap{
max-width:1200px;
margin:auto
}

.head{
text-align:center;
padding:7px 4px 10px
}

.title{
font-size:clamp(25px,7vw,48px);
font-weight:1000;
text-shadow:
0 0 5px #fff,
0 0 16px #8b55ff,
0 0 35px #5d25ff
}

.sub{
font-size:11px;
color:#aeb5c8;
font-weight:800;
margin-top:6px
}

.status{
display:inline-block;
margin-top:7px;
padding:5px 10px;
border-radius:99px;
background:#141428;
border:1px solid #9d5cff55;
color:#74ff9a;
font-size:9px;
font-weight:900
}

.statsbar{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:5px;
margin-bottom:8px
}

.statbox{
background:#090c15ed;
border:1px solid #293448;
border-radius:10px;
padding:6px;
text-align:center
}

.stat-title{
font-size:6px;
color:#77849a;
font-weight:900
}

.stat-value{
font-size:12px;
font-weight:1000;
margin-top:3px
}

.grid{
display:grid;
grid-template-columns:1fr 1fr;
gap:8px
}

.column{
min-width:0
}

.maxbox{
background:#090c15ed;
border-radius:13px;
padding:7px;
margin-bottom:8px
}

.maxbox.g{
border:1px solid #9d51ff99
}

.maxbox.c{
border:1px solid #f1c84b88
}

.max-title{
font-size:11px;
font-weight:1000;
margin-bottom:6px
}

.maxbox.g .max-title{
color:#d5a8ff
}

.maxbox.c .max-title{
color:#ffe37b
}

.max-card{
background:#171d2df5;
border-radius:9px;
padding:7px
}

.maxbox.g .max-card{
border-left:3px solid #9d51ff
}

.maxbox.c .max-card{
border-left:3px solid #f1c84b
}

.max-user{
display:flex;
justify-content:space-between;
align-items:center;
gap:5px;
margin-bottom:6px
}

.max-user-name{
font-size:9px;
font-weight:1000;
word-break:break-word
}

.max-coin{
font-size:12px;
font-weight:1000;
white-space:nowrap
}

.maxbox.g .max-coin{
color:#d9aaff
}

.maxbox.c .max-coin{
color:#ffe16b
}

.max-stats{
display:grid;
grid-template-columns:1fr 1fr;
gap:3px
}

.max-stat{
background:#ffffff09;
border-radius:5px;
padding:4px;
font-size:6px;
color:#818da1
}

.max-stat b{
display:block;
color:#fff;
font-size:8px;
margin-top:1px;
word-break:break-word
}

.max-live{
display:block;
color:#fff;
text-decoration:none;
background:#d71950;
padding:6px;
margin-top:6px;
border-radius:6px;
text-align:center;
font-size:7px;
font-weight:1000
}

.max-link{
display:block;
color:#73a7ff;
font-size:6px;
margin-top:5px;
word-break:break-all;
text-decoration:none
}

.panel{
min-width:0;
background:#090c15ed;
border-radius:13px;
padding:7px
}

.panel.g{
border:1px solid #9d51ff99
}

.panel.c{
border:1px solid #f1c84b88
}

.pnrow{
display:flex;
justify-content:space-between;
align-items:center;
padding:2px 2px 6px
}

.pn{
font-size:11px;
font-weight:1000
}

.g .pn{
color:#d5a8ff
}

.c .pn{
color:#ffe37b
}

.cnt{
font-size:7px;
background:#ffffff12;
border-radius:99px;
padding:3px 5px
}

.card{
margin-bottom:4px;
background:linear-gradient(
145deg,
#171d2df9,
#0a0e17f9
);
border:1px solid #293448;
border-radius:8px;
padding:6px
}

.card:last-child{
margin-bottom:0
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
align-items:center;
gap:4px;
margin-bottom:5px
}

.user{
font-size:8px;
font-weight:1000;
word-break:break-word
}

.rank{
font-size:6px;
color:#77849a
}

.badge{
padding:3px 5px;
border-radius:5px;
font-size:6px;
font-weight:1000;
background:#8d35ff;
color:#fff
}

.c .badge{
background:#f4d35e;
color:#211700
}

.ig{
display:grid;
grid-template-columns:1fr 1fr;
gap:3px
}

.info{
background:#ffffff09;
border-radius:5px;
padding:3px;
font-size:5px;
color:#818da1
}

.info b{
display:block;
color:#fff;
font-size:8px;
margin-top:1px;
word-break:break-word
}

.go{
display:block;
text-align:center;
color:#fff;
text-decoration:none;
background:#d71950;
padding:5px;
margin-top:5px;
border-radius:6px;
font-size:6px;
font-weight:1000
}

.empty{
text-align:center;
padding:16px;
color:#626e82;
font-size:7px
}

.foot{
text-align:center;
color:#59647a;
font-size:6px;
padding:8px
}

.new{
animation:in .7s ease-out
}

@keyframes in{

0%{
opacity:.3;
transform:
translateY(-7px)
scale(.97)
}

40%{
box-shadow:
0 0 25px #9d51ff77
}

100%{
opacity:1;
transform:none
}

}

@media(max-width:700px){

body{
padding:4px
}

.title{
font-size:27px
}

.sub{
font-size:8px
}

.status{
font-size:7px
}

.statsbar{
gap:3px
}

.statbox{
padding:5px 2px
}

.stat-title{
font-size:5px
}

.stat-value{
font-size:9px
}

.grid{
gap:4px
}

.maxbox,
.panel{
padding:5px;
border-radius:9px
}

.max-title,
.pn{
font-size:8px
}

.max-user-name{
font-size:7px
}

.max-coin{
font-size:9px
}

.max-stat{
font-size:4px
}

.max-stat b{
font-size:7px
}

.card{
padding:5px
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

.badge{
font-size:5px;
padding:2px 4px
}

.go,
.max-live{
font-size:5px;
padding:4px
}

.max-link{
font-size:5px
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

<div id="status" class="status">
🟡 RADAR BAĞLANIYOR...
</div>

</div>


<div class="statsbar">

<div class="statbox">

<div class="stat-title">
📡 TOPLAM KAYIT
</div>

<div id="totalRecords" class="stat-value">
0
</div>

</div>


<div class="statbox">

<div class="stat-title">
🪙 TOPLAM COIN
</div>

<div id="totalCoins" class="stat-value">
0
</div>

</div>


<div class="statbox">

<div class="stat-title">
🟪 GOODY
</div>

<div id="totalGoody" class="stat-value">
0
</div>

</div>


<div class="statbox">

<div class="stat-title">
🟨 CHEST
</div>

<div id="totalChest" class="stat-value">
0
</div>

</div>

</div>


<div class="grid">


<div class="column">

<div class="maxbox g">

<div class="max-title">
🪙 EN YÜKSEK COIN • GOODY BAG
</div>

<div id="goodyMax"></div>

</div>


<div class="panel g">

<div class="pnrow">

<div class="pn">
🟪 GOODY BAG
</div>

<div id="gc" class="cnt">
0
</div>

</div>

<div id="gs"></div>

</div>

</div>


<div class="column">

<div class="maxbox c">

<div class="max-title">
🪙 EN YÜKSEK COIN • HAZİNE SANDIĞI
</div>

<div id="chestMax"></div>

</div>


<div class="panel c">

<div class="pnrow">

<div class="pn">
🟨 HAZİNE SANDIĞI
</div>

<div id="cc" class="cnt">
0
</div>

</div>

<div id="cs"></div>

</div>

</div>

</div>


<div class="foot">
⚡ ÖDÜL AVCISI • KALICI RADAR • CANLI VERİ
</div>

</div>


<script>

let data={
goody_bags:[],
chests:[]
};

let first=true;

const seenG=new Set();
const seenC=new Set();

const newG=new Set();
const newC=new Set();


const esc=v=>
String(v??"")
.replace(/&/g,"&amp;")
.replace(/</g,"&lt;")
.replace(/>/g,"&gt;")
.replace(/"/g,"&quot;")
.replace(/'/g,"&#039;");


const key=x=>
String(
x.source_message_id ??
x.room ??
(
(x.username??"")
+"_"+(x.detected_at??"")
)
);


const time=x=>
Number(
x.detected_at ??
x.created_at ??
x.timestamp ??
0
);


const five=a=>
Array.isArray(a)
?
[...a]
.sort(
(a,b)=>time(b)-time(a)
)
.slice(0,5)
:
[];


function renderMaxCoin(a,id,type){

const e=document.getElementById(id);

const arr=five(a);

if(!arr.length){

e.innerHTML=
'<div class="empty">⚡ Henüz veri yok.</div>';

return;

}


const max=
[...arr]
.sort(
(a,b)=>
Number(b.coins||0)
-
Number(a.coins||0)
)[0];


const icon=
type==="G"
?"🟪"
:"🟨";


e.innerHTML=`

<div class="max-card">

<div class="max-user">

<div class="max-user-name">
${icon} ${esc(max.username)}
</div>

<div class="max-coin">
🪙 ${esc(max.coins)}
</div>

</div>


<div class="max-stats">

<div class="max-stat">
🪙 COIN
<b>${esc(max.coins)}</b>
</div>

<div class="max-stat">
👥 KİŞİ
<b>${esc(max.people)}</b>
</div>

<div class="max-stat">
🙋 KATILAN
<b>${esc(max.joined)}</b>
</div>

<div class="max-stat">
📈 ORAN
<b>${esc(max.rate)}</b>
</div>

<div class="max-stat">
👀 İZLENME
<b>${esc(max.view)}</b>
</div>

<div class="max-stat">
🏠 ODA
<b>${esc(max.room)}</b>
</div>

</div>


${
max.live
?
`

<a
class="max-live"
href="${esc(max.live)}"
target="_blank"
rel="noopener"
>
🔴 TIKTOK CANLI YAYIN
</a>

<a
class="max-link"
href="${esc(max.live)}"
target="_blank"
rel="noopener"
>
${esc(max.live)}
</a>

`
:
""
}

</div>

`;

}


function list(a,id,cid,icon,type){

const e=document.getElementById(id);

const n=document.getElementById(cid);

const arr=five(a);

n.textContent=arr.length;


if(!arr.length){

e.innerHTML=
'<div class="empty">⚡ Henüz veri yok.</div>';

return;

}


const seen=
type==="G"
?
seenG
:
seenC;


const ns=
type==="G"
?
newG
:
newC;


arr.forEach(x=>{

const k=key(x);

if(first){

seen.add(k);

}

else if(!seen.has(k)){

seen.add(k);

ns.add(k);

}

});


e.innerHTML=

arr.map((x,i)=>{

const k=key(x);

const fresh=ns.has(k);

return `

<div class="card ${fresh?"new":""}">

<div class="ur">

<div class="user">
${icon} ${esc(x.username)}
</div>

${
fresh
?
'<div class="badge">⚡ YENİ</div>'
:
`<div class="rank">#${i+1}</div>`
}

</div>


<div class="ig">

<div class="info">
🪙 COIN
<b>${esc(x.coins)}</b>
</div>

<div class="info">
👥 KİŞİ
<b>${esc(x.people)}</b>
</div>

<div class="info">
🙋 KATILAN
<b>${esc(x.joined)}</b>
</div>

<div class="info">
📈 ORAN
<b>${esc(x.rate)}</b>
</div>

<div class="info">
👀 İZLENME
<b>${esc(x.view)}</b>
</div>

<div class="info">
🏠 ODA
<b>${esc(x.room)}</b>
</div>

</div>


${
x.live
?
`

<a
class="go"
href="${esc(x.live)}"
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

}).join("");

}


function render(){

renderMaxCoin(
data.goody_bags,
"goodyMax",
"G"
);

renderMaxCoin(
data.chests,
"chestMax",
"C"
);

list(
data.goody_bags,
"gs",
"gc",
"🟪",
"G"
);

list(
data.chests,
"cs",
"cc",
"🟨",
"C"
);

}


async function load(){

try{

const r=await fetch(
"/api/all?t="+Date.now(),
{
cache:"no-store"
}
);

if(!r.ok){

throw Error(r.status);

}


const d=await r.json();


data={

goody_bags:
Array.isArray(d.goody_bags)
?
d.goody_bags
:
[],

chests:
Array.isArray(d.chests)
?
d.chests
:
[],

stats:
d.stats || {}

};


const stats=data.stats;


document.getElementById(
"totalRecords"
).textContent=
stats.total ?? 0;


document.getElementById(
"totalCoins"
).textContent=
stats.total_coins ?? 0;


document.getElementById(
"totalGoody"
).textContent=
stats.goody ?? 0;


document.getElementById(
"totalChest"
).textContent=
stats.chest ?? 0;


const s=
document.getElementById(
"status"
);


s.className="status";

s.textContent=
"🟢 RADAR AKTİF • CANLI VERİ";


render();

first=false;


}catch(e){

const s=
document.getElementById(
"status"
);

s.className=
"status error";

s.textContent=
"🔴 VERİ BAĞLANTISI HATASI";

console.error(e);

}

}


setInterval(
load,
2000
);

load();

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


async def api_all(request):

    stats = db_stats()

    return web.json_response({

        "status":
            "online",

        "server_time":
            int(time.time()),

        "chests":
            list(LIVE_CHESTS.values()),

        "goody_bags":
            list(LIVE_GOODY_BAGS.values()),

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
            int(time.time())

    })


# =========================================================
# HTTP SERVER
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
# TELEGRAM LISTENER
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


        if add(d):

            await telegram_queue.put(
                d
            )


    except Exception as e:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(e)
        )


# =========================================================
# TELEGRAM BAĞLANTI KONTROLÜ
# =========================================================

async def telegram_connection_watch():

    global client

    while True:

        try:

            if client:

                connected = client.is_connected()

                if not connected:

                    print(
                        "[TELEGRAM] Bağlantı koptu."
                    )

                    try:

                        await client.connect()

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
# ANA PROGRAM
# =========================================================

async def main():

    global http_session
    global client


    print(
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
    )


    # SQLITE GEÇMİŞİNİ YÜKLE

    db_load_recent()


    # HTTP

    http_session = aiohttp.ClientSession()


    await start_http()


    # TELEGRAM

    client = TelegramClient(
        StringSession(
            STRING_SESSION
        ),
        API_ID,
        API_HASH
    )


    # İlk bağlantı

    while True:

        try:

            await client.start()

            print(
                "[TELEGRAM] İstemci bağlandı."
            )

            break

        except Exception as e:

            print(
                "[TELEGRAM] İlk bağlantı başarısız:",
                repr(e)
            )

            print(
                "[TELEGRAM] 15 saniye sonra tekrar denenecek."
            )

            await asyncio.sleep(15)


    # EVENT

    client.add_event_handler(
        listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        )
    )


    # TELEGRAM SENDER

    asyncio.create_task(
        sender()
    )


    # BAĞLANTI WATCHDOG

    asyncio.create_task(
        telegram_connection_watch()
    )


    print(
        "[HAZIR] Goody Bag + Hazine Sandığı aktif."
    )

    print(
        "[HAZIR] SQLite kalıcı geçmiş aktif."
    )

    print(
        "[HAZIR] Güçlü duplicate koruması aktif."
    )

    print(
        "[HAZIR] Telegram otomatik reconnect aktif."
    )

    print(
        "[HAZIR] Son 5 kayıt gösteriliyor."
    )

    print(
        "[HAZIR] En yüksek coin ayrı ayrı hesaplanıyor."
    )

    print(
        "[HAZIR] Telegram linkleri butonsuz normal link."
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
