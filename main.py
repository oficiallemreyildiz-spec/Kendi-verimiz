import os
import re
import json
import base64
import asyncio
import time
import sqlite3
from urllib.parse import unquote

from aiohttp import web

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

# =========================================================
# AYARLAR
# =========================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ["STRING_SESSION"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

SITE_URL = "https://sites.google.com/view/gody-bag-ve-chesture-/ana-sayfa"
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

COIN_ALARM_LIMIT = 100
PEOPLE_ALARM_LIMIT = 5

LIVE_GOODY_BAGS = {}
LIVE_CHESTS = {}
processed_messages = set()
processed_signatures = set()
telegram_queue = asyncio.Queue()

# =========================================================
# İSTEMCİLER (ÇİFT MOTOR)
# =========================================================
# 1. UserClient: Senin hesabın, sadece kanalları okumak için
user_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# 2. BotClient: Senin botun, mesaj atmak ve buton göstermek için
bot_client = TelegramClient('bot_session', API_ID, API_HASH)

# =========================================================
# SQLITE VERİTABANI
# =========================================================

db = sqlite3.connect(DB_PATH, check_same_thread=False)
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

db.execute("CREATE INDEX IF NOT EXISTS idx_radar_detected ON radar_history(detected_at)")

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
# YARDIMCI FONKSİYONLAR VE PARSER
# =========================================================

def safe_int(v, d=0):
    try: return int(float(v))
    except: return d

def safe_float(v, d=0):
    try: return float(v)
    except: return d

def db_save(d, event_key):
    try:
        db.execute("""
        INSERT OR IGNORE INTO radar_history
        (event_key, event_type, username, coins, people, joined, rate, viewers, room, live, target_time, detected_at, source_message_id, source_chat_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_key, d["type"], d["username"], safe_int(d["coins"]), safe_int(d["people"]), safe_int(d["joined"]), safe_float(d["rate"]), safe_int(d["view"]), str(d["room"]), d.get("live", ""), safe_int(d["target_time"]), safe_int(d["detected_at"]), safe_int(d.get("source_message_id")), safe_int(d.get("source_chat_id"))
        ))
        db.commit()
        return True
    except: return False

def db_exists(event_key):
    try:
        return db.execute("SELECT 1 FROM radar_history WHERE event_key=? LIMIT 1", (event_key,)).fetchone() is not None
    except: return False

def db_stats():
    try:
        total = db.execute("SELECT COUNT(*) FROM radar_history").fetchone()[0]
        total_coins = db.execute("SELECT COALESCE(SUM(coins),0) FROM radar_history").fetchone()[0]
        goody = db.execute("SELECT COUNT(*) FROM radar_history WHERE event_type='GOODY BAG'").fetchone()[0]
        chest = db.execute("SELECT COUNT(*) FROM radar_history WHERE event_type='CHEST'").fetchone()[0]
        return {"total": total, "total_coins": total_coins, "goody": goody, "chest": chest}
    except: return {"total": 0, "total_coins": 0, "goody": 0, "chest": 0}

def db_load_recent():
    try:
        rows = db.execute("SELECT * FROM radar_history ORDER BY detected_at DESC LIMIT ?", (MAX_HISTORY,)).fetchall()
        for row in reversed(rows):
            d = {
                "type": row["event_type"],
                "box_name": "Goody Bag" if row["event_type"] == "GOODY BAG" else "Hazine Sandığı",
                "username": row["username"], "coins": row["coins"], "people": row["people"],
                "joined": row["joined"], "rate": row["rate"], "view": row["viewers"],
                "room": row["room"], "live": row["live"], "target_time": row["target_time"],
                "detected_at": row["detected_at"], "source_message_id": row["source_message_id"],
                "source_chat_id": row["source_chat_id"]
            }
            target = LIVE_GOODY_BAGS if d["type"] == "GOODY BAG" else LIVE_CHESTS
            if d["room"]: target[d["room"]] = d
    except: pass

def token_from_event(e):
    try: t = e.message.raw_text or ""
    except: return None
    for p in [r'https?://[^ \n\]\)]+/t\.php\?token=([^&\s\]\)]+)', r'https?://[^ \n\]\)]+t\.php\?token=([^&\s\]\)]+)']:
        m1 = re.search(p, t, re.I)
        if m1: return unquote(m1.group(1))
    return None

def decode_token(tok):
    if not tok: return None
    try:
        s = unquote(str(tok)).strip()
        return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", errors="ignore"))
    except: return None

def room_from_text(t):
    for p in [r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)', r'https?://[^ \n]+/t/\?p=([A-Za-z0-9_\-+/=]+)']:
        m = re.search(p, t or "", re.I)
        if m:
            try:
                room = base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode("utf-8", errors="ignore").strip()
                if room.isdigit(): return room
            except: pass
    return None

def username_from_text(t):
    for p in [r'^\s*##\s*T\d+\s*[›>:]\s*([^\s\n]+)', r'^\s*T\d+\s*[›>:]\s*([^\s\n]+)']:
        m = re.search(p, t or "", re.M)
        if m: return m.group(1).strip()
    return None

def coins(t, d=None):
    for p in [r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/', r'BOX\s*:\s*(\d+)\s*/', r'(\d+)\s*/\s*(\d+)']:
        m = re.search(p, t or "", re.I)
        if m: return safe_int(m.group(1))
    if d:
        for k in ["coins", "coin", "gem", "diamond", "amount"]:
            if k in d: return safe_int(d[k])
    return 0

def people(t):
    for p in [r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)', r'BOX\s*:\s*\d+\s*/\s*(\d+)', r'(\d+)\s*/\s*(\d+)']:
        m = re.search(p, t or "", re.I)
        if m: return safe_int(m.group(1) if "TÚI" in p or "TUI" in p or "BOX" in p else m.group(2))
    return 0

def is_goody(t, d):
    u = (t or "").upper()
    if re.search(r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG', u): return True
    if re.search(r'\bBOX\b|RƯƠNG|TREO|HAZİNE', u) or "🟡" in t: return False
    if d:
        if d.get("is_goody_bag") in [True, 1, "1", "true", "True"]: return True
        if d.get("is_goody_bag") in [False, 0, "0", "false", "False"]: return False
    return None

def parse(event):
    t = event.message.raw_text or ""
    d = decode_token(token_from_event(event))
    g = is_goody(t, d)
    if g is None: return None

    username = None
    for k in ["username", "user", "unique_id", "uniqueId"]:
        if d and d.get(k):
            username = str(d[k])
            break
    username = username or username_from_text(t) or "bilinmiyor"

    room = None
    for k in ["room", "room_id", "roomid", "roomId", "roomID"]:
        if d and d.get(k):
            room = str(d[k])
            break
    room = room or room_from_text(t) or "msg:" + str(event.message.id)

    p = people(t)
    if not p and d: p = safe_int(d.get("people") or d.get("person") or d.get("count") or 0)
    
    live = ""
    if d and d.get("openitok") and str(d["openitok"]).startswith("http"): live = str(d["openitok"])
    live = live or (f"https://www.tiktok.com/share/live/{room}" if room else f"https://www.tiktok.com/@{username}/live")

    return {
        "type": "GOODY BAG" if g else "CHEST", "box_name": "Goody Bag" if g else "Hazine Sandığı",
        "username": username, "coins": coins(t, d), "people": p,
        "joined": safe_int(d.get("joined") if d else 0), "rate": safe_float(d.get("rate") if d else 0),
        "view": safe_int(d.get("view") if d else 0), "room": room, "live": live,
        "target_time": int(time.time()) + 180, "detected_at": int(time.time()),
        "source_message_id": event.message.id, "source_chat_id": safe_int(event.chat_id)
    }

def make_event_key(d):
    return "|".join([d["type"], str(d.get("room", "")), str(d.get("username", "")).lower(), str(d.get("coins", 0)), str(d.get("people", 0)), str(d.get("source_message_id", 0))])

def add(d):
    if not d or not d.get("room"): return False
    event_key = make_event_key(d)
    if event_key in processed_signatures or db_exists(event_key):
        processed_signatures.add(event_key)
        return False
    target = LIVE_GOODY_BAGS if d["type"] == "GOODY BAG" else LIVE_CHESTS
    if d["room"] in target and int(time.time()) - safe_int(target[d["room"]].get("detected_at")) < 5: return False
    db_save(d, event_key)
    processed_signatures.add(event_key)
    target[d["room"]] = d
    print("[RADAR]", d["type"], "|", d["username"], "| COIN:", d["coins"], "| KİŞİ:", d["people"])
    return True

def get_alarms(d):
    alarms = []
    if COIN_ALARM_LIMIT > 0 and safe_int(d.get("coins")) >= COIN_ALARM_LIMIT:
        alarms.append({"type": "COIN", "title": "🚨 COIN ALARMI", "key": f"{make_event_key(d)}|COIN|{COIN_ALARM_LIMIT}"})
    if PEOPLE_ALARM_LIMIT > 0 and 0 < safe_int(d.get("people")) <= PEOPLE_ALARM_LIMIT:
        alarms.append({"type": "PEOPLE", "title": "⚠️ DÜŞÜK KİŞİ ALARMI", "key": f"{make_event_key(d)}|PEOPLE|{PEOPLE_ALARM_LIMIT}"})
    return alarms

def alarm_exists(alarm_key):
    try: return db.execute("SELECT 1 FROM alarm_history WHERE alarm_key=? LIMIT 1", (alarm_key,)).fetchone() is not None
    except: return False

def save_alarm(alarm_key, event_key, alarm_type):
    try:
        db.execute("INSERT OR IGNORE INTO alarm_history (alarm_key, event_key, alarm_type, created_at) VALUES (?, ?, ?, ?)", (alarm_key, event_key, alarm_type, int(time.time())))
        db.commit()
        return True
    except: return False

# =========================================================
# TELEGRAM MESAJ GÖNDERİCİ (Artık Bot API kullanıyor)
# =========================================================

async def sender():
    while True:
        job = await telegram_queue.get()
        try:
            d = job["data"]
            if job["kind"] == "normal":
                box = "🟪 GOODY BAG" if d["type"] == "GOODY BAG" else "🟨 HAZİNE SANDIĞI"
                text = (
                    f"**{box}**\n\n"
                    f"👤 Kullanıcı: `{d['username']}`\n"
                    f"🪙 Coin: **{d['coins']}**\n"
                    f"👥 Kişi: {d['people']}\n"
                    f"🏠 Oda: `{d['room']}`"
                )
            elif job["kind"] == "alarm":
                alarm = job["alarm"]
                title = "🚨 COIN ALARMI" if alarm["type"] == "COIN" else "⚠️ DÜŞÜK KİŞİ ALARMI"
                box = "🟪 GOODY BAG" if d["type"] == "GOODY BAG" else "🟨 HAZİNE SANDIĞI"
                text = (
                    f"**{title}**\n\n"
                    f"{box}\n"
                    f"👤 Kullanıcı: `{d['username']}`\n"
                    f"🪙 Coin: **{d['coins']}**\n"
                    f"👥 Kişi: {d['people']}\n"
                    f"🏠 Oda: `{d['room']}`"
                )
            
            # Burada normal HTTP request yerine doğrudan botu kullanıyoruz (buton garantili)
            await bot_client.send_message(
                TARGET_CHAT_ID,
                text,
                buttons=[[Button.url("🌐 RADARI AÇ", SITE_URL)]],
                link_preview=False
            )
        except Exception as e:
            print("[SENDER HATA]", repr(e))
        finally:
            telegram_queue.task_done()

# =========================================================
# HTML VE WEB SUNUCU
# =========================================================

RADAR_HTML = """
<!doctype html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🏆 ÖDÜL AVCISI</title>
<style>
body{margin:0;padding:12px;background:#05060c;color:#fff;font-family:Arial,sans-serif;}
.wrap{max-width:1600px;margin:auto;}
.head{text-align:center;padding:12px 5px 18px;}
.title{font-size:32px;font-weight:bold;color:#fff;}
</style>
</head>
<body>
<div class="wrap">
<div class="head">
<div class="title">🏆 ÖDÜL AVCISI RADARI AKTİF</div>
</div>
</div>
</body>
</html>
"""

async def radar_page(request):
    return web.Response(text=RADAR_HTML, content_type="text/html", charset="utf-8")

async def start_http():
    app = web.Application()
    app.router.add_get("/", radar_page)
    app.router.add_get("/radar", radar_page)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("[HTTP] Sunucu başladı:", PORT)

# =========================================================
# ANA ÇALIŞTIRMA MANTIĞI
# =========================================================

async def main():
    print("🏆 ÖDÜL AVCISI BAŞLIYOR (ÇİFT MOTORLU SİSTEM)")
    db_load_recent()
    await start_http()

    # İki istemciyi aynı anda başlatıyoruz
    await user_client.start()
    await bot_client.start(bot_token=BOT_TOKEN)
    print("[TELEGRAM] Hesabın (Dinleyici) ve Bot'un (Gönderici) başarıyla bağlandı!")

    # 1. Kullanıcı İstemcisi: Sadece Kanalları Dinler
    @user_client.on(events.NewMessage(chats=SOURCE_CHATS))
    async def user_listener(event):
        try:
            key = (event.chat_id, event.message.id)
            if key in processed_messages: return
            processed_messages.add(key)
            if len(processed_messages) > 50000: processed_messages.clear()

            d = parse(event)
            if not d: return

            d["source_chat_id"] = safe_int(event.chat_id)
            if not add(d): return

            await telegram_queue.put({"kind": "normal", "data": d})

            alarms = get_alarms(d)
            event_key = make_event_key(d)
            for alarm in alarms:
                if not alarm_exists(alarm["key"]) and save_alarm(alarm["key"], event_key, alarm["type"]):
                    await telegram_queue.put({"kind": "alarm", "data": d, "alarm": alarm})
        except Exception as e:
            print("[USER LİSTENER HATA]", repr(e))

    # 2. Bot İstemcisi: Gelen /start Komutlarını Dinler ve Butonlu Yanıt Verir
    @bot_client.on(events.NewMessage(pattern=r'^/start'))
    async def bot_listener(event):
        await event.respond(
            "👋 **Ödül Avcısı Radarına Hoş Geldiniz!**\n\n"
            "Canlı radar verilerine ve sistem arayüzüne erişmek için aşağıdaki butona tıklayabilirsiniz.",
            buttons=[[Button.url("🌐 RADARI AÇ", SITE_URL)]]
        )

    # Gönderim kuyruğunu başlat
    asyncio.create_task(sender())

    # Sistemi sonsuz döngüde açık tut
    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
