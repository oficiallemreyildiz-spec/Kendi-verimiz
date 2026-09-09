import os
import sqlite3
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telethon import TelegramClient, events
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

# =========================================================
# SQLITE VERİTABANI (ÜYELİK VE RADAR)
# =========================================================
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    joined_at INTEGER
)
""")
db.commit()

def register_user(user_id, username):
    try:
        db.execute("INSERT OR REPLACE INTO users (user_id, username, joined_at) VALUES (?, ?, ?)", 
                   (user_id, username or "Bilinmiyor", int(asyncio.get_event_loop().time())))
        db.commit()
    except: pass

def check_user_exists(user_id):
    try:
        res = db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
        return res is not None
    except: return False

# =========================================================
# TELEGRAM BOT (python-telegram-bot - /start komutu için)
# =========================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username)
    
    keyboard = [[InlineKeyboardButton("🌐 RADARI VE LİNKLERİ AÇ", url=SITE_URL)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 **Hoş Geldin, {user.first_name} (@{user.username or 'Kayıtsiz'})!**\n\n"
        "✅ Üyeliğin başarıyla onaylandı ve sisteme kaydedildi.\n"
        "Canlı radar verilerine ve airdrop odalarına erişmek için aşağıdaki butona tıklayabilirsin.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def run_telegram_bot():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

# =========================================================
# TELETHON KANALLARINI DİNLEYEN MOTOR
# =========================================================
user_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

async def run_telethon_listener():
    await user_client.start()
    
    @user_client.on(events.NewMessage(chats=SOURCE_CHATS))
    async def listener(event):
        try:
            text = event.message.raw_text or ""
            if "t.php?token=" in text or "GOODY" in text.upper():
                await user_client.send_message(
                    TARGET_CHAT_ID,
                    f"🔔 **Yeni Airdrop Tespit Edildi!**\n\n{text}",
                    buttons=[[dict(text="🌐 RADARI AÇ", url=SITE_URL)]]
                )
        except Exception as e:
            print("[LİSTENER HATA]", repr(e))

    await user_client.run_until_disconnected()

# =========================================================
# WEB SUNUCUSU (Aiohttp)
# =========================================================
async def radar_page(request):
    html = """
    <!doctype html>
    <html lang="tr">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Ödül Avcısı Radar</title>
    <style>
    body{margin:0;padding:0;background:#05060c;color:#fff;font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;}
    .card{background:#121324;border:1px solid #7c3aed;padding:30px;border-radius:15px;text-align:center;max-width:400px;width:90%;box-shadow:0 0 20px rgba(124,58,237,0.4);}
    a{display:block;width:100%;padding:14px;background:linear-gradient(135deg,#7c3aed,#4c1d95);color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;margin-top:20px;box-sizing:border-box;}
    </style>
    </head>
    <body>
    <div class="card">
        <h2>🚀 Ödül Avcısı Sistem</h2>
        <p style="color:#a78bfa;font-size:14px;">Canlı yayın bildirimlerini alabilmek ve botu aktif etmek için önce Telegram botumuzdan kayıt olmalısın.</p>
        <a href="https://t.me/AirdropGameReferansBot" target="_blank">🤖 Botu Aç ve Kayıt Ol</a>
    </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type="text/html", charset="utf-8")

async def start_http():
    app = web.Application()
    app.router.add_get("/", radar_page)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"[HTTP] Sunucu {PORT} portunda aktif.")

# =========================================================
# ANA ÇALIŞTIRMA
# =========================================================
async def main():
    await start_http()
    await asyncio.gather(
        run_telegram_bot(),
        run_telethon_listener()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
