import os
import re
import time
import json
import base64
import asyncio
import threading
from urllib.parse import unquote
from http.server import HTTPServer, BaseHTTPRequestHandler

import aiohttp
from telethon import TelegramClient, events
from telethon.sessions import StringSession


# ============================================================
# ENV
# ============================================================

API_ID = int(os.getenv("API_ID", "36135300"))
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")

TARGET_CHAT_ID = -1004421946217

SOURCE_CHATS = [
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445,
]


# ============================================================
# RADAR DATA
# ============================================================

LIVE_CHESTS = []
LIVE_GOODY_BAGS = []

DATA_LOCK = threading.Lock()


# ============================================================
# HTTP API
# ============================================================

class RadarAPIHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        now = int(time.time())

        with DATA_LOCK:
            global LIVE_CHESTS, LIVE_GOODY_BAGS

            LIVE_CHESTS = [
                b for b in LIVE_CHESTS
                if b.get("target_time", 0) > now
            ]

            LIVE_GOODY_BAGS = [
                b for b in LIVE_GOODY_BAGS
                if b.get("target_time", 0) > now
            ]

            if self.path.startswith("/api/boxes"):
                data = list(LIVE_CHESTS)

            elif self.path.startswith("/api/goody_bags"):
                data = list(LIVE_GOODY_BAGS)

            else:
                data = None

        if data is not None:
            self.send_json_response(data)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b"VIP Radar API Aktif")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "*"
        )
        self.end_headers()

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )
        self.end_headers()

        self.wfile.write(
            json.dumps(
                data,
                ensure_ascii=False
            ).encode("utf-8")
        )

    def log_message(self, *args):
        pass


def start_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        RadarAPIHandler
    )

    print(f"[WEB] Radar API : {port}")

    server.serve_forever()


# ============================================================
# TELEGRAM SEND QUEUE
# ============================================================

send_queue: asyncio.Queue[str] = asyncio.Queue()

MIN_INTERVAL = 1.0


async def sender_worker(session: aiohttp.ClientSession):

    if not BOT_TOKEN:
        print("[WARN] BOT_TOKEN yok.")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    last_sent = 0.0

    while True:

        msg = await send_queue.get()

        try:

            elapsed = (
                asyncio.get_event_loop().time()
                - last_sent
            )

            if elapsed < MIN_INTERVAL:
                await asyncio.sleep(
                    MIN_INTERVAL - elapsed
                )

            payload = {
                "chat_id": TARGET_CHAT_ID,
                "text": msg,
                "disable_web_page_preview": True
            }

            for attempt in range(3):

                try:

                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(
                            total=10
                        )
                    ) as res:

                        data = await res.json()

                        if data.get("ok"):
                            break

                        if res.status == 429:
                            await asyncio.sleep(2)

                except Exception as e:

                    print(
                        f"[SEND ERROR] {e}"
                    )

                    await asyncio.sleep(
                        1.5 * (attempt + 1)
                    )

            last_sent = (
                asyncio.get_event_loop().time()
            )

        except Exception as e:

            print(
                f"[QUEUE ERROR] {e}"
            )

        finally:

            send_queue.task_done()


# ============================================================
# TOKEN BULMA
# ============================================================

def extract_token_url(text: str):
    """
    Mesaj içerisindeki:

    dichvu321.com/tiktok/t.php?token=...

    bağlantısını bulur.
    """

    pattern = (
        r'https?://[^)\s]+/tiktok/t\.php\?token=([^)\s]+)'
    )

    m = re.search(
        pattern,
        text,
        re.IGNORECASE
    )

    if not m:
        return None

    token = m.group(1)

    # Markdown / URL sonu temizliği
    token = token.rstrip(
        ")]}>\"'"
    )

    return unquote(token)


# ============================================================
# TOKEN ÇÖZME
# ============================================================

def decode_token(token: str):
    """
    Base64 token -> JSON
    """

    try:

        # URL-safe Base64
        token = token.strip()

        padding = len(token) % 4

        if padding:
            token += "=" * (4 - padding)

        raw = base64.urlsafe_b64decode(
            token.encode("utf-8")
        )

        data = json.loads(
            raw.decode("utf-8")
        )

        if not isinstance(data, dict):
            return None

        return data

    except Exception as e:

        print(
            f"[TOKEN ERROR] {e}"
        )

        return None


# ============================================================
# KULLANICI ADI TEMİZLEME
# ============================================================

def clean_username(username):

    if not username:
        return None

    username = str(username).strip()

    username = username.lstrip("@")

    username = re.sub(
        r"[^a-zA-Z0-9_.-]",
        "",
        username
    )

    if not username:
        return None

    return username


# ============================================================
# TIME
# ============================================================

def calculate_target_time(data):

    now = int(time.time())

    # Token içerisindeki gerçek zaman
    token_time = data.get("time")

    try:

        token_time = int(token_time)

        # Eğer token zamanı gelecekteyse
        # doğrudan bunu kullan.
        if token_time > now:
            return token_time

    except Exception:
        pass

    # Fallback: time_display
    display = str(
        data.get("time_display", "")
    )

    m = re.search(
        r"(\d+):(\d+)",
        display
    )

    if m:

        minutes = int(m.group(1))
        seconds = int(m.group(2))

        duration = (
            minutes * 60
            + seconds
        )

        return now + duration

    # Son fallback
    return now + 180


# ============================================================
# TOKEN'DAN VERİ ÇIKAR
# ============================================================

def parse_source_message(text: str):

    token = extract_token_url(text)

    if not token:

        print(
            "[SKIP] t.php token bulunamadı."
        )

        return None

    data = decode_token(token)

    if not data:

        print(
            "[SKIP] Token çözülemedi."
        )

        return None

    username = clean_username(
        data.get("username")
    )

    room = str(
        data.get("room", "")
    ).strip()

    openitok = str(
        data.get("openitok", "")
    ).strip()

    # --------------------------------------------------------
    # Goody Bag
    # --------------------------------------------------------

    is_goody = bool(
        data.get("is_goody_bag", False)
    )

    # --------------------------------------------------------
    # TÚI / BOX
    # --------------------------------------------------------

    item_label = str(
        data.get("item_label", "")
    )

    # TÚI: 50/20 gibi değerde ilk sayı
    coins = 10

    m = re.search(
        r"(\d+)\s*/\s*(\d+)",
        text
    )

    if m:

        try:
            coins = int(m.group(1))
        except Exception:
            pass

    # Token'daki maxzem / benzeri alan varsa
    # onu sadece fallback olarak kullan.
    if coins == 10:

        for key in (
            "maxzem",
            "value",
            "coins",
            "coin"
        ):

            try:

                value = data.get(key)

                if value is not None:
                    value = int(value)

                    if value > 0:
                        coins = value
                        break

            except Exception:
                pass

    # --------------------------------------------------------
    # People / View / Ratio
    # --------------------------------------------------------

    people = data.get("people", 0)
    ratio = data.get("ratio", 0)
    view = data.get("view", 0)

    try:
        people = int(people)
    except Exception:
        people = 0

    try:
        ratio = float(ratio)
    except Exception:
        ratio = 0

    try:
        view = int(view)
    except Exception:
        view = 0

    # --------------------------------------------------------
    # Target time
    # --------------------------------------------------------

    target_time = calculate_target_time(data)

    remaining = max(
        0,
        target_time - int(time.time())
    )

    # --------------------------------------------------------
    # LIVE LINK
    # --------------------------------------------------------

    if openitok.startswith(
        "https://www.tiktok.com/"
    ):
        live_link = openitok

    elif room:
        live_link = (
            "https://www.tiktok.com/"
            f"share/live/{room}"
        )

    elif username:
        live_link = (
            f"https://www.tiktok.com/"
            f"@{username}/live"
        )

    else:
        live_link = "https://www.tiktok.com/live"

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    box_data = {

        "username": (
            username
            if username
            else "Bilinmeyen_Yayinci"
        ),

        "coins": coins,

        "can_open": people,

        "viewers": view,

        "ratio": ratio,

        "room": room,

        "live_link": live_link,

        "box_name": (
            "🎒 ŞANS ÇANTASI"
            if is_goody
            else "📦 HAZİNE SANDIĞI"
        ),

        "is_goody": is_goody,

        "is_gold": coins >= 100,

        "target_time": target_time,

        "total_duration": remaining,

        "detected_at": int(time.time())
    }

    return box_data, data


# ============================================================
# MESSAGE PROCESS
# ============================================================

def process_message(
    text: str,
    chat_title: str
):

    result = parse_source_message(text)

    if not result:
        return None

    box_data, token_data = result

    username = box_data["username"]
    coins = box_data["coins"]
    is_goody = box_data["is_goody"]
    live_link = box_data["live_link"]
    target_time = box_data["target_time"]

    # --------------------------------------------------------
    # RADAR
    # --------------------------------------------------------

    with DATA_LOCK:

        if is_goody:

            LIVE_GOODY_BAGS.append(
                box_data
            )

        else:

            LIVE_CHESTS.append(
                box_data
            )

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    print(
        "\n=============================="
    )

    print(
        "[NEW GOODY BAG]"
        if is_goody
        else "[NEW CHEST]"
    )

    print(
        f"username : {username}"
    )

    print(
        f"room     : {box_data['room']}"
    )

    print(
        f"coins    : {coins}"
    )

    print(
        f"people   : {box_data['can_open']}"
    )

    print(
        f"ratio    : {box_data['ratio']}"
    )

    print(
        f"view     : {box_data['viewers']}"
    )

    print(
        f"live     : {live_link}"
    )

    print(
        f"target   : {target_time}"
    )

    print(
        "==============================\n"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    header = (
        "🎒 YENİ GOODY BAG!"
        if is_goody
        else "🚨 YENİ SANDIK!"
    )

    msg = (
        f"{header}\n"
        f"👤 @{username}\n"
        f"💎 Değer: {coins} Coin\n"
        f"👥 Katılım: {box_data['can_open']}\n"
        f"📈 Rate: {box_data['ratio']}\n"
        f"👀 View: {box_data['viewers']}\n"
        f"🏠 Room: {box_data['room']}\n"
        f"\n"
        f"🟢 CANLIYA GİT:\n"
        f"{live_link}"
    )

    return msg


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH,

    connection_retries=None,
    retry_delay=1,

    auto_reconnect=True,
    request_retries=5
)

http_session: aiohttp.ClientSession | None = None


# ============================================================
# LISTENER
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHATS
    )
)
async def message_listener(event):

    if http_session is None:
        return

    try:

        chat = await event.get_chat()

        chat_title = getattr(
            chat,
            "title",
            f"Grup ({event.chat_id})"
        )

    except Exception:

        chat_title = (
            f"Kanal ({event.chat_id})"
        )

    text = event.raw_text or ""

    try:

        formatted_msg = process_message(
            text,
            chat_title
        )

        if formatted_msg:

            await send_queue.put(
                formatted_msg
            )

    except Exception as e:

        print(
            f"[MESSAGE ERROR] {e}"
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global http_session

    connector = aiohttp.TCPConnector(
        limit=50,
        ttl_dns_cache=300
    )

    async with aiohttp.ClientSession(
        connector=connector
    ) as session:

        http_session = session

        asyncio.create_task(
            sender_worker(session)
        )

        print("[TELEGRAM] Başlatılıyor...")

        await client.start()

        print(
            "[TELEGRAM] Bağlantı başarılı."
        )

        await client.get_dialogs()

        print(
            "[TELEGRAM] Kaynak gruplar dinleniyor..."
        )

        await client.run_until_disconnected()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    threading.Thread(
        target=start_server,
        daemon=True
    ).start()

    asyncio.run(main())
