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

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

TARGET_CHAT_ID = -1004421946217

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445
]

PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DATABASE_PATH", "radar.db")
MAX_HISTORY = 500

# =========================================================
# 🚨 ALARM AYARLARI
# =========================================================

COIN_ALARM_LIMIT = 100
PEOPLE_ALARM_LIMIT = 5

# =========================================================
# BELLEK (RAM)
# =========================================================

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}

processed_messages = set()
processed_signatures = set()

telegram_queue = asyncio.Queue()

http_session = None
client = None

# =========================================================
# SQLITE VERİTABANI
# =========================================================

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL;")

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
# YARDIMCI FONKSİYONLAR
# =========================================================

def safe_int(v, d=0):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return d

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return d

# =========================================================
# SQLITE İŞLEMLERİ
# =========================================================

def db_save(d, event_key):
    try:
        db.execute("""
        INSERT OR IGNORE INTO radar_history (
            event_key, event_type, username, coins, people, joined, rate, viewers, 
            room, live, target_time, detected_at, source_message_id, source_chat_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_key, d["type"], d["username"], safe_int(d["coins"]),
            safe_int(d["people"]), safe_int(d["joined"]), safe_float(d["rate"]),
            safe_int(d["view"]), str(d["room"]), d.get("live", ""),
            safe_int(d["target_time"]), safe_int(d["detected_at"]),
            safe_int(d.get("source_message_id")), safe_int(d.get("source_chat_id"))
        ))
        db.commit()
        return True
    except Exception as e:
        print("[SQLITE KAYIT HATASI]", repr(e))
        return False

def db_exists(event_key):
    try:
        row = db.execute("SELECT 1 FROM radar_history WHERE event_key=? LIMIT 1", (event_key,)).fetchone()
        return row is not None
    except Exception as e:
        print("[SQLITE KONTROL HATASI]", repr(e))
        return False

def db_stats():
    try:
        total = db.execute("SELECT COUNT(*) FROM radar_history").fetchone()[0]
        total_coins = db.execute("SELECT COALESCE(SUM(coins), 0) FROM radar_history").fetchone()[0]
        goody = db.execute("SELECT COUNT(*) FROM radar_history WHERE event_type='GOODY BAG'").fetchone()[0]
        chest = db.execute("SELECT COUNT(*) FROM radar_history WHERE event_type='CHEST'").fetchone()[0]
        return {"total": total, "total_coins": total_coins, "goody": goody, "chest": chest}
    except Exception as e:
        print("[SQLITE İSTATİSTİK HATASI]", repr(e))
        return {"total": 0, "total_coins": 0, "goody": 0, "chest": 0}

def db_load_recent():
    try:
        rows = db.execute("""
        SELECT * FROM radar_history ORDER BY detected_at DESC LIMIT ?
        """, (MAX_HISTORY,)).fetchall()
        for row in reversed(rows):
            d = {
                "type": row["event_type"],
                "box_name": "Goody Bag" if row["event_type"] == "GOODY BAG" else "Hazine Sandığı",
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
            target = LIVE_GOODY_BAGS if d["type"] == "GOODY BAG" else LIVE_CHESTS
            if d["room"]:
                target[d["room"]] = d
        print("[SQLITE] Geçmiş kayıtlar yüklendi:", len(rows))
    except Exception as e:
        print("[SQLITE YÜKLEME HATASI]", repr(e))

# =========================================================
# PARSING & VERİ ÇIKARMA
# =========================================================

def token_from_event(e):
    try:
        m = e.message
        t = m.raw_text or ""
    except Exception:
        return None

    patterns = [
        r'https?://[^\s\]\)]+/t\.php\?token=([^&\s\]\)]+)',
        r'https?://[^\s\]\)]+t\.php\?token=([^&\s\]\)]+)'
    ]
    for p in patterns:
        m1 = re.search(p, t, re.I)
        if m1:
            return unquote(m1.group(1))

    try:
        for ent in getattr(m, "entities", []) or []:
            u = getattr(ent, "url", None)
            if u:
                m1 = re.search(r't\.php\?token=([^&\s]+)', u, re.I)
                if m1:
                    return unquote(m1.group(1))
    except Exception as ex:
        print("[TOKEN ENTITY]", repr(ex))

    return None

def decode_token(tok):
    if not tok:
        return None
    try:
        s = unquote(str(tok)).strip()
        return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", errors="ignore"))
    except Exception as e:
        print("[TOKEN HATA]", repr(e))
        return None

def room_from_text(t):
    patterns = [
        r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)',
        r'https?://[^\s]+/t/\?p=([A-Za-z0-9_\-+/=]+)'
    ]
    for p in patterns:
        m = re.search(p, t or "", re.I)
        if m:
            try:
                s = m.group(1)
                room = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", errors="ignore").strip()
                if room.isdigit():
                    return room
            except Exception:
                pass
    return None

def username_from_text(t):
    patterns = [
        r'^\s*##\s*T\d+\s*[›>:]\s*([^\s\n]+)',
        r'^\s*T\d+\s*[›>:]\s*([^\s\n]+)'
    ]
    for p in patterns:
        m = re.search(p, t or "", re.M)
        if m:
            return m.group(1).strip()
    return None

def coins(t, d=None):
    patterns = [
        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
        r'BOX\s*:\s*(\d+)\s*/',
        r'(\d+)\s*/\s*(\d+)'
    ]
    for p in patterns:
        m = re.search(p, t or "", re.I)
        if m:
            return safe_int(m.group(1))
    if d:
        for k in ["coins", "coin", "gem", "diamond", "amount"]:
            if k in d:
                return safe_int(d[k])
    return 0

def people(t):
    patterns = [
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        r'(\d+)\s*/\s*(\d+)'
    ]
    for p in patterns:
        m = re.search(p, t or "", re.I)
        if m:
            if "TÚI" in p or "TUI" in p or "BOX" in p:
                return safe_int(m.group(1))
            return safe_int(m.group(2))
    return 0

def joined(t):
    m = re.search(r'(?:Đã\s*join|joined|join)\s*:\s*(\d+)', t or "", re.I)
    return safe_int(m.group(1)) if m else 0

def viewers(t):
    m = re.search(r'👀\s*(\d+)', t or "")
    return safe_int(m.group(1)) if m else 0

def rate(t, d=None):
    m = re.search(r'Rate\s*:\s*([0-9]+(?:\.[0-9]+)?)', t or "", re.I)
    if m:
        return safe_float(m.group(1))
    if d:
        for k in ["ratio", "rate"]:
            if k in d:
                return safe_float(d[k])
    return 0.0

def is_goody(t, d):
    u = (t or "").upper()
    if re.search(r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG', u):
        return True
    if re.search(r'\bBOX\b|RƯƠNG|TREO|HAZİNE', u) or "🟡" in t:
        return False
    if d:
        if d.get("is_goody_bag") in [True, 1, "1", "true", "True"]:
            return True
        if d.get("is_goody_bag") in [False, 0, "0", "false", "False"]:
            return False
    return None

def target_time(t, d=None):
    now = int(time.time())
    if d:
        for k in ["time", "target_time", "end_time", "endTime"]:
            if k in d:
                try:
                    v = int(float(d[k]))
                    if v > 10_000_000_000:
                        return v // 1000
                    if v > 1_000_000_000:
                        return v
                    if 0 < v < 86400:
                        return now + v
                except Exception:
                    pass
    m = re.search(r'TIME\s*:\s*(\d+):(\d+)', t or "", re.I)
    if m:
        return now + safe_int(m.group(1)) * 60 + safe_int(m.group(2))
    return now + 180

def parse(event):
    t = event.message.raw_text or ""
    d = decode_token(token_from_event(event))
    g = is_goody(t, d)
    if g is None:
        return None

    username = None
    if d:
        for k in ["username", "user", "unique_id", "uniqueId"]:
            if d.get(k):
                username = str(d[k])
                break
    username = username or username_from_text(t) or "bilinmiyor"

    room = None
    if d:
        for k in ["room", "room_id", "roomid", "roomId", "roomID"]:
            if d.get(k):
                room = str(d[k])
                break
    room = room or room_from_text(t) or ("msg:" + str(event.message.id))

    p = people(t)
    if not p and d:
        for k in ["people", "person", "count", "capacity"]:
            if k in d:
                p = safe_int(d[k])
                if p:
                    break

    j = joined(t)
    if not j and d:
        for k in ["joined", "join", "join_count", "joined_count"]:
            if k in d:
                j = safe_int(d[k])
                if j:
                    break

    v = viewers(t)
    if not v and d:
        for k in ["view", "views", "viewer", "viewers"]:
            if k in d:
                v = safe_int(d[k])
                if v:
                    break

    live = ""
    if d:
        for k in ["openitok", "live", "live_url", "url"]:
            if d.get(k) and str(d[k]).startswith(("http://", "https://")):
                live = str(d[k])
                break
    live = live or (f"https://www.tiktok.com/share/live/{room}" if room and not room.startswith("msg:") else f"https://www.tiktok.com/@{username}/live")

    return {
        "type": "GOODY BAG" if g else "CHEST",
        "box_name": "Goody Bag" if g else "Hazine Sandığı",
        "username": username,
        "coins": coins(t, d),
        "people": p,
        "joined": j,
        "rate": rate(t, d),
        "view": v,
        "room": room,
        "live": live,
        "target_time": target_time(t, d),
        "detected_at": int(time.time()),
        "source_message_id": event.message.id,
        "source_chat_id": safe_int(event.chat_id)
    }

def make_event_key(d):
    return "|".join([
        d["type"],
        str(d.get("room", "")),
        str(d.get("username", "")).lower(),
        str(d.get("coins", 0)),
        str(d.get("people", 0)),
        str(d.get("source_message_id", 0))
    ])

def add(d):
    if not d or not d.get("room"):
        return False
    event_key = make_event_key(d)
    if event_key in processed_signatures or db_exists(event_key):
        processed_signatures.add(event_key)
        return False

    target = LIVE_GOODY_BAGS if d["type"] == "GOODY BAG" else LIVE_CHESTS
    if d["room"] in target:
        old_time = safe_int(target[d["room"]].get("detected_at"))
        if int(time.time()) - old_time < 5:
            return False

    db_save(d, event_key)
    processed_signatures.add(event_key)
    target[d["room"]] = d
    print(f"[RADAR] {d['type']} | {d['username']} | COIN: {d['coins']} | KİŞİ: {d['people']}")
    return True

# =========================================================
# ALARM İŞLEMLERİ
# =========================================================

def alarm_exists(alarm_key):
    try:
        row = db.execute("SELECT 1 FROM alarm_history WHERE alarm_key=? LIMIT 1", (alarm_key,)).fetchone()
        return row is not None
    except Exception as e:
        print("[ALARM CHECK]", repr(e))
        return False

def save_alarm(alarm_key, event_key, alarm_type):
    try:
        db.execute("""
        INSERT OR IGNORE INTO alarm_history (alarm_key, event_key, alarm_type, created_at)
        VALUES (?, ?, ?, ?)
        """, (alarm_key, event_key, alarm_type, int(time.time())))
        db.commit()
        return True
    except Exception as e:
        print("[ALARM SAVE]", repr(e))
        return False

def get_alarms(d):
    alarms = []
    event_key = make_event_key(d)
    coin = safe_int(d.get("coins"))
    person = safe_int(d.get("people"))

    if COIN_ALARM_LIMIT > 0 and coin >= COIN_ALARM_LIMIT:
        alarms.append({
            "type": "COIN",
            "title": "🚨 COIN ALARMI",
            "key": f"{event_key}|COIN|{COIN_ALARM_LIMIT}"
        })

    if PEOPLE_ALARM_LIMIT > 0 and 0 < person <= PEOPLE_ALARM_LIMIT:
        alarms.append({
            "type": "PEOPLE",
            "title": "⚠️ DÜŞÜK KİŞİ ALARMI",
            "key": f"{event_key}|PEOPLE|{PEOPLE_ALARM_LIMIT}"
        })

    return alarms

# =========================================================
# TELEGRAM BİLDİRİM
# =========================================================

async def send_tg(d):
    global http_session
    if not http_session:
        return
    title = "🟪 GOODY BAG" if d["type"] == "GOODY BAG" else "🟨 HAZİNE SANDIĞI"
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
        text += f"\n🔴 {d['live']}"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(1, 9):
        try:
            async with http_session.post(url, json={
                "chat_id": TARGET_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            }) as response:
                response_text = await response.text()
                if response.status == 200:
                    return
                if response.status == 429:
                    try:
                        wait_time = json.loads(response_text).get("parameters", {}).get("retry_after", 30)
                    except Exception:
                        wait_time = 30
                    await asyncio.sleep(max(1, safe_int(wait_time, 30)))
                    continue
                if response.status in [500, 502, 503, 504]:
                    await asyncio.sleep(min(5 * attempt, 30))
                    continue
                print("[TELEGRAM HATA]", response.status, response_text)
                return
        except Exception as e:
            print("[TELEGRAM]", repr(e))
            await asyncio.sleep(min(5 * attempt, 30))

async def send_alarm(d, alarm):
    global http_session
    if not http_session:
        return
    if alarm["type"] == "COIN":
        title = "🚨 COIN ALARMI"
        reason = f"🪙 COIN: {d['coins']}\n🎯 SENİN LİMİTİN: {COIN_ALARM_LIMIT}"
    else:
        title = "⚠️ DÜŞÜK KİŞİ ALARMI"
        reason = f"👥 KİŞİ: {d['people']}\n🎯 SENİN LİMİTİN: {PEOPLE_ALARM_LIMIT}"

    box = "🟪 GOODY BAG" if d["type"] == "GOODY BAG" else "🟨 HAZİNE SANDIĞI"
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
        text += f"\n🔴 {d['live']}"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(1, 9):
        try:
            async with http_session.post(url, json={
                "chat_id": TARGET_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            }) as response:
                response_text = await response.text()
                if response.status == 200:
                    return
                if response.status == 429:
                    try:
                        wait_time = json.loads(response_text).get("parameters", {}).get("retry_after", 30)
                    except Exception:
                        wait_time = 30
                    await asyncio.sleep(max(1, safe_int(wait_time, 30)))
                    continue
                if response.status in [500, 502, 503, 504]:
                    await asyncio.sleep(min(5 * attempt, 30))
                    continue
                print("[ALARM TELEGRAM HATA]", response.status, response_text)
                return
        except Exception as e:
            print("[ALARM TELEGRAM]", repr(e))
            await asyncio.sleep(min(5 * attempt, 30))

async def sender():
    while True:
        job = await telegram_queue.get()
        try:
            if job["kind"] == "normal":
                await send_tg(job["data"])
            elif job["kind"] == "alarm":
                await send_alarm(job["data"], job["alarm"])
        except Exception as e:
            print("[SENDER HATA]", repr(e))
        finally:
            telegram_queue.task_done()

# =========================================================
# WEB ARAYÜZÜ (HTML)
# =========================================================

RADAR_HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🏆 ÖDÜL AVCISI RADAR</title>
<style>
* { box-sizing:border-box; }
body { margin:0; padding:12px; background:#05060c; color:#fff; font-family:Arial,sans-serif; }
.wrap { max-width:1600px; margin:auto; }
.head { text-align:center; padding:12px 5px 18px; }
.title { font-size:clamp(32px, 6vw, 64px); font-weight:900; line-height:1.05; text-shadow: 0 0 8px #fff, 0 0 22px #8b55ff; }
.sub { font-size:18px; color:#d6d9e5; font-weight:800; margin-top:10px; }
.status { display:inline-block; margin-top:12px; padding:8px 16px; border-radius:99px; background:#141428; border:1px solid #9d5cff; color:#74ff9a; font-size:14px; font-weight:800; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.column { min-width:0; }
.maxbox { background:#090c15ed; border-radius:14px; padding:12px; margin-bottom:12px; }
.maxbox.g { border:2px solid #9d51ff99; }
.maxbox.c { border:2px solid #f1c84b88; }
.max-title { font-size:16px; font-weight:800; margin-bottom:8px; }
.maxbox.g .max-title { color:#d5a8ff; }
.maxbox.c .max-title { color:#ffe37b; }
.max-card { background:#171d2df5; border-radius:10px; padding:10px; }
.max-user { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:8px; }
.max-user-name { font-size:16px; font-weight:800; word-break:break-word; }
.max-coin { font-size:20px; font-weight:800; white-space:nowrap; }
.maxbox.g .max-coin { color:#d9aaff; }
.maxbox.c .max-coin { color:#ffe16b; }
.max-stats { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
.max-stat { background:#ffffff09; border-radius:6px; padding:6px; font-size:11px; color:#aab2c2; }
.max-stat b { display:block; color:#fff; font-size:14px; margin-top:2px; }
.max-live { display:block; color:#fff; text-decoration:none; background:#d71950; padding:8px; margin-top:8px; border-radius:6px; text-align:center; font-size:12px; font-weight:800; }
.panel { background:#090c15ed; border-radius:14px; padding:12px; }
.panel.g { border:2px solid #9d51ff99; }
.panel.c { border:2px solid #f1c84b88; }
.pnrow { display:flex; justify-content:space-between; align-items:center; padding:4px 4px 10px; }
.pn { font-size:18px; font-weight:800; }
.g .pn { color:#d5a8ff; }
.c .pn { color:#ffe37b; }
.cnt { font-size:12px; background:#ffffff12; border-radius:99px; padding:4px 8px; font-weight:800; }
.card { margin-bottom:8px; background:#171d2df9; border:1px solid #293448; border-radius:10px; padding:10px; }
.g .card { border-left:4px solid #9d51ff; }
.c .card { border-left:4px solid #f1c84b; }
.ur { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:6px; }
.user { font-size:15px; font-weight:800; }
.rank { font-size:11px; color:#77849a; }
.badge { padding:4px 6px; border-radius:6px; font-size:10px; font-weight:800; background:#8d35ff; color:#fff; }
.c .badge { background:#f4d35e; color:#211700; }
.ig { display:grid; grid-template-columns:1fr 1fr; gap:4px; }
.info { background:#ffffff09; border-radius:6px; padding:6px; font-size:10px; color:#a2abbc; }
.info b { display:block; color:#fff; font-size:13px; margin-top:2px; }
.go { display:block; text-align:center; color:#fff; text-decoration:none; background:#d71950; padding:8px; margin-top:6px; border-radius:6px; font-size:11px; font-weight:800; }
.empty { text-align:center; padding:15px; color:#727d91; font-size:12px; }
.foot { text-align:center; color:#697489; font-size:11px; padding:15px; }
@media(max-width:700px){
  .grid { grid-template-columns:1fr; }
}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div class="title">🏆 ÖDÜL AVCISI</div>
    <div class="sub">🟪 GOODY BAG • 🟨 HAZİNE SANDIĞI</div>
    <div id="status" class="status">🟡 RADAR BAĞLANIYOR...</div>
  </div>
  <div class="grid">
    <div class="column">
      <div class="maxbox g">
        <div class="max-title">🪙 EN YÜKSEK COIN • GOODY BAG</div>
        <div id="goodyMax"><div class="empty">⚡ Henüz veri yok.</div></div>
      </div>
      <div class="panel g">
        <div class="pnrow">
          <div class="pn">🟪 GOODY BAG</div>
          <div id="gc" class="cnt">0</div>
        </div>
        <div id="gs"></div>
      </div>
    </div>
    <div class="column">
      <div class="maxbox c">
        <div class="max-title">🪙 EN YÜKSEK COIN • HAZİNE SANDIĞI</div>
        <div id="chestMax"><div class="empty">⚡ Henüz veri yok.</div></div>
      </div>
      <div class="panel c">
        <div class="pnrow">
          <div class="pn">🟨 HAZİNE SANDIĞI</div>
          <div id="cc" class="cnt">0</div>
        </div>
        <div id="cs"></div>
      </div>
    </div>
  </div>
  <div class="foot">⚡ ÖDÜL AVCISI • KALICI RADAR • CANLI VERİ</div>
</div>
<script>
let data = { goody_bags:[], chests:[] };
const esc = v => String(v??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

function renderMaxCoin(a, id, type) {
  const e = document.getElementById(id);
  if(!a || !a.length) { e.innerHTML = '<div class="empty">⚡ Henüz veri yok.</div>'; return; }
  const max = [...a].sort((a,b) => Number(b.coins||0) - Number(a.coins||0))[0];
  const icon = type === "G" ? "🟪" : "🟨";
  e.innerHTML = `
    <div class="max-card">
      <div class="max-user">
        <div class="max-user-name">${icon} ${esc(max.username)}</div>
        <div class="max-coin">🪙 ${esc(max.coins)}</div>
      </div>
      <div class="max-stats">
        <div class="max-stat">🪙 COIN <b>${esc(max.coins)}</b></div>
        <div class="max-stat">👥 KİŞİ <b>${esc(max.people)}</b></div>
        <div class="max-stat">🙋 KATILAN <b>${esc(max.joined)}</b></div>
        <div class="max-stat">📈 ORAN <b>${esc(max.rate)}</b></div>
      </div>
      ${max.live ? `<a class="max-live" href="${esc(max.live)}" target="_blank" rel="noopener">🔴 TIKTOK CANLI YAYIN</a>` : ""}
    </div>
  `;
}

function list(a, id, cid, icon) {
  const e = document.getElementById(id);
  const n = document.getElementById(cid);
  n.textContent = a ? a.length : 0;
  if(!a || !a.length) { e.innerHTML = '<div class="empty">⚡ Henüz veri yok.</div>'; return; }
  e.innerHTML = a.map((x, i) => `
    <div class="card">
      <div class="ur">
        <div class="user">${icon} ${esc(x.username)}</div>
        <div class="rank">#${i+1}</div>
      </div>
      <div class="ig">
        <div class="info">🪙 COIN <b>${esc(x.coins)}</b></div>
        <div class="info">👥 KİŞİ <b>${esc(x.people)}</b></div>
        <div class="info">🙋 KATILAN <b>${esc(x.joined)}</b></div>
        <div class="info">🏠 ODA <b>${esc(x.room)}</b></div>
      </div>
      ${x.live ? `<a class="go" href="${esc(x.live)}" target="_blank" rel="noopener">🔴 CANLI YAYIN</a>` : ""}
    </div>
  `).join("");
}

function render() {
  renderMaxCoin(data.goody_bags, "goodyMax", "G");
  renderMaxCoin(data.chests, "chestMax", "C");
  list(data.goody_bags, "gs", "gc", "🟪");
  list(data.chests, "cs", "cc", "🟨");
}

async function load() {
  try {
    const r = await fetch("/api/all?t=" + Date.now(), { cache: "no-store" });
    if(!r.ok) throw Error(r.status);
    const d = await r.json();
    data = {
      goody_bags: Array.isArray(d.goody_bags) ? d.goody_bags : [],
      chests: Array.isArray(d.chests) ? d.chests : []
    };
    const s = document.getElementById("status");
    s.className = "status";
    s.textContent = "🟢 RADAR AKTİF • CANLI VERİ";
    render();
  } catch(e) {
    const s = document.getElementById("status");
    s.className = "status";
    s.textContent = "🔴 VERİ BAĞLANTISI HATASI";
  }
}
setInterval(load, 2000);
load();
</script>
</body>
</html>
"""

# =========================================================
# HTTP ROUTER & MIDDLEWARE
# =========================================================

async def radar_page(request):
    return web.Response(text=RADAR_HTML, content_type="text/html", charset="utf-8")

@web.middleware
async def cors(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        })
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response

async def api_all(request):
    return web.json_response({
        "status": "online",
        "server_time": int(time.time()),
        "chests": list(LIVE_CHESTS.values()),
        "goody_bags": list(LIVE_GOODY_BAGS.values()),
        "stats": db_stats()
    })

async def api_boxes(request):
    return web.json_response(list(LIVE_CHESTS.values()))

async def api_goody(request):
    return web.json_response(list(LIVE_GOODY_BAGS.values()))

async def api_status(request):
    stats = db_stats()
    return web.json_response({
        "status": "online",
        "chests": len(LIVE_CHESTS),
        "goody_bags": len(LIVE_GOODY_BAGS),
        "history": stats["total"],
        "total_coins": stats["total_coins"],
        "server_time": int(time.time()),
        "coin_alarm": COIN_ALARM_LIMIT,
        "people_alarm": PEOPLE_ALARM_LIMIT
    })

async def start_http():
    app = web.Application(middlewares=[cors])
    app.router.add_get("/", radar_page)
    app.router.add_get("/radar", radar_page)
    app.router.add_get("/api/all", api_all)
    app.router.add_get("/api/boxes", api_boxes)
    app.router.add_get("/api/goody_bags", api_goody)
    app.router.add_get("/api/status", api_status)

    for path in ["/api/all", "/api/boxes", "/api/goody_bags", "/api/status"]:
        app.router.add_options(path, lambda request: web.Response(status=204))

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("[HTTP] Sunucu başladı:", PORT)

# =========================================================
# TELEGRAM DİNLEYİCİ VE WATCHDOG
# =========================================================

async def listener(event):
    try:
        key = (event.chat_id, event.message.id)
        if key in processed_messages:
            return
        processed_messages.add(key)
        if len(processed_messages) > 50000:
            processed_messages.clear()

        d = parse(event)
        if not d:
            return

        d["source_chat_id"] = safe_int(event.chat_id)
        if not add(d):
            return

        await telegram_queue.put({"kind": "normal", "data": d})

        alarms = get_alarms(d)
        event_key = make_event_key(d)
        for alarm in alarms:
            if alarm_exists(alarm["key"]):
                continue
            if save_alarm(alarm["key"], event_key, alarm["type"]):
                print(f"[ALARM] {alarm['title']} | {d['username']} | COIN: {d['coins']} | KİŞİ: {d['people']}")
                await telegram_queue.put({"kind": "alarm", "data": d, "alarm": alarm})
    except Exception as e:
        print("[DİNLEYİCİ HATASI]", repr(e))

async def telegram_connection_watch():
    global client
    while True:
        try:
            if client:
                if not client.is_connected():
                    print("[TELEGRAM] Bağlantı koptu, yeniden bağlanılıyor...")
                    try:
                        await client.connect()
                        print("[TELEGRAM] Yeniden bağlandı.")
                    except Exception as e:
                        print("[TELEGRAM] Yeniden bağlanma hatası:", repr(e))
        except Exception as e:
            print("[TELEGRAM WATCH]", repr(e))
        await asyncio.sleep(30)

# =========================================================
# ANA ÇALIŞTIRMA FONKSİYONU
# =========================================================

async def main():
    global http_session, client
    print("🏆 ÖDÜL AVCISI BAŞLIYOR")
    print("🪙 COIN ALARM LİMİTİ:", COIN_ALARM_LIMIT)
    print("👥 KİŞİ ALARM LİMİTİ:", PEOPLE_ALARM_LIMIT)

    db_load_recent()
    http_session = aiohttp.ClientSession()
    await start_http()

    client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

    while True:
        try:
            await client.start(bot_token=BOT_TOKEN if BOT_TOKEN else None)
            print("[TELEGRAM] İstemci bağlandı.")
            break
        except Exception as e:
            print("[TELEGRAM] Bağlantı hatası:", repr(e))
            await asyncio.sleep(15)

    client.add_event_handler(listener, events.NewMessage(chats=SOURCE_CHATS))
    asyncio.create_task(sender())
    asyncio.create_task(telegram_connection_watch())

    print("[HAZIR] Goody Bag + Hazine Sandığı aktif.")
    print("[HAZIR] Mobil uyumlu Web Arayüzü ve Alarmlar aktif.")

    try:
        await client.run_until_disconnected()
    finally:
        if http_session:
            await http_session.close()
        try:
            db.close()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Kapatıldı.")
    except Exception as e:
        print("[KRİTİK HATA]", repr(e))
