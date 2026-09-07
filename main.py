import os
import re
import asyncio
import threading
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler
import aiohttp
from telethon import TelegramClient, events

from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "36135300"))
API_HASH = os.getenv("API_HASH", "737566711ac17fecd1ebeab1e2123773")
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

# ---------------------------------------------------------------------------
# Health-check server (Render port bağlaması için)
# ---------------------------------------------------------------------------

class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheck)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Gönderim kuyruğu: tek worker + rate-limit + 429 backoff
# ---------------------------------------------------------------------------

send_queue: "asyncio.Queue[str]" = asyncio.Queue()

# Aynı hedef sohbete Telegram Bot API ~1 msg/sn öneriyor.
MIN_INTERVAL = 1.0


async def sender_worker(session: aiohttp.ClientSession):
    """Kuyruktaki mesajları sırayla, rate-limit'e uyarak gönderir."""
    if not BOT_TOKEN:
        print("❌ HATA: BOT_TOKEN tanımlı değil!")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    last_sent = 0.0

    while True:
        msg = await send_queue.get()
        try:
            elapsed = asyncio.get_event_loop().time() - last_sent
            if elapsed < MIN_INTERVAL:
                await asyncio.sleep(MIN_INTERVAL - elapsed)

            payload = {
                "chat_id": TARGET_CHAT_ID,
                "text": msg,
                "disable_web_page_preview": True,
            }

            for attempt in range(3):
                try:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as res:
                        data = await res.json()
                        if data.get("ok"):
                            print(f"🚀 Mesaj iletildi -> {TARGET_CHAT_ID}")
                            break
                        if res.status == 429:
                            retry_after = data.get("parameters", {}).get("retry_after", 2)
                            print(f"⏳ Flood limit, {retry_after}s bekleniyor...")
                            await asyncio.sleep(retry_after)
                            continue
                        print(f"❌ Telegram API Red Etti: {data}")
                        break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    print(f"⚠️ Gönderim denemesi {attempt + 1}/3 başarısız: {e}")
                    await asyncio.sleep(1.5 * (attempt + 1))

            last_sent = asyncio.get_event_loop().time()
        except Exception as e:
            print(f"❌ Sender worker hatası: {e}")
        finally:
            send_queue.task_done()


# ---------------------------------------------------------------------------
# Mesaj ayrıştırma
# ---------------------------------------------------------------------------

def get_username(text: str):
    for line in text.splitlines():
        if "##" in line:
            cleaned = re.sub(r'##\s*\S+', '', line).strip()
            cleaned = re.sub(r'^[\s>›:|-]+', '', cleaned).strip()
            if cleaned:
                return cleaned
    return None


def build_username_candidates(username: str) -> list[str]:
    """
    Kaynak kanal username'i nokta ve/veya alt tire ile obfuske edebiliyor
    (ör. ".za_hi_a_", "shiro_gy.m1", "r.el.ax_cs1"). Gerçek hesabın hangi
    temizlenmiş hali olduğu belirsiz olduğu için birden fazla aday üretip
    sırayla TikTok'ta doğrulayacağız. En "muhafazakar" adaydan (hiçbir şey
    silinmemiş) en agresife (hem nokta hem alt tire silinmiş) doğru sıralı.
    """
    no_dots = username.replace(".", "")
    no_underscore = username.replace("_", "")
    no_both = username.replace(".", "").replace("_", "")

    candidates = []
    for cand in [no_dots, no_underscore, no_both, username]:
        cand = cand.strip("._-")
        if cand and cand not in candidates:
            candidates.append(cand)
    return candidates


async def verify_tiktok_username(session: aiohttp.ClientSession, username: str) -> bool:
    """
    Bir kullanıcı adının gerçekten var olan bir TikTok hesabı olup
    olmadığını kontrol eder. TikTok'un "hesap bulunamadı" sayfası ile
    gerçek profil sayfası farklı içerik döndürür; buna bakıyoruz.
    """
    url = f"https://www.tiktok.com/@{quote(username, safe='_-')}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        )
    }
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=6), allow_redirects=True
        ) as res:
            if res.status != 200:
                return False
            html = await res.text()
            if "Couldn't find this account" in html or "user-not-found" in html.lower():
                return False
            # Gerçek profil sayfalarında bu anahtar veri her zaman geçer.
            return "webapp.user-detail" in html or f'"uniqueId":"{username}"' in html
    except Exception as e:
        print(f"⚠️ TikTok doğrulama hatası ({username}): {e}")
        return False


async def resolve_tiktok_username(session: aiohttp.ClientSession, raw_username: str) -> str | None:
    """İlk geçerli adayı bulup döner; hiçbiri doğrulanamazsa None döner."""
    for candidate in build_username_candidates(raw_username):
        if await verify_tiktok_username(session, candidate):
            return candidate
    return None


async def parse_tiktok_message(session: aiohttp.ClientSession, text: str, chat_title: str) -> str:
    username = get_username(text)

    clean_lines = []
    for line in text.splitlines():
        if any(bad in line for bad in ["dichvu321", "junb.io.vn", "box-countdown", "http"]):
            continue
        if line.strip() in [">", "=", "-", ""]:
            continue
        clean_lines.append(line)

    body = "\n".join(clean_lines).strip()
    if not body and not username:
        body = text.strip()

    msg = f"🚨 YENİ SANDIK!\nKaynak: {chat_title}\n\n{body}\n\n"

    if username:
        verified_username = await resolve_tiktok_username(session, username)
        if verified_username:
            live_link = f"https://www.tiktok.com/@{quote(verified_username, safe='_-')}/live"
            msg += f"🟢 CANLIYA GİT:\n{live_link}"
        else:
            msg += "🔴 Link yok"

    return msg


# ---------------------------------------------------------------------------
# Telethon client
# ---------------------------------------------------------------------------

client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH,
    connection_retries=None,   # sonsuz yeniden bağlanma denemesi
    retry_delay=1,
    auto_reconnect=True,
    request_retries=5,
)

http_session: aiohttp.ClientSession | None = None


@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def message_listener(event):
    print(f"\n📥 YAKALANDI! Kaynak ID: {event.chat_id}")
    text = event.raw_text or ""
    print(f"📄 Metin: {text[:80]}...")

    try:
        chat = await event.get_chat()
        chat_title = getattr(chat, "title", f"Grup ({event.chat_id})")
    except Exception:
        chat_title = f"Kanal ({event.chat_id})"

    if http_session is None:
        print("⚠️ HTTP session henüz hazır değil, mesaj atlandı.")
        return

    formatted_msg = await parse_tiktok_message(http_session, text, chat_title)
    await send_queue.put(formatted_msg)


async def main():
    global http_session
    print("=== VIP Kaynak Dinleyici Başlatılıyor... ===")

    connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        http_session = session
        worker_task = asyncio.create_task(sender_worker(session))

        # catch_up=True: bağlantı kopukken gelen mesajları da yakalar.
        await client.start()

        # Kritik adım: entity/peer cache'ini önceden ısıt.
        # Bu olmadan bazı kanallarda event tetiklenmeyebiliyor.
        await client.get_dialogs()
        print("✅ Dialog/entity cache yüklendi.")

        print(f"✅ Dinleme aktif! Hedef Grup: {TARGET_CHAT_ID}")
        try:
            await client.run_until_disconnected()
        finally:
            worker_task.cancel()


if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    asyncio.run(main())
