import os
import asyncio
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

SITE_URL = "https://sites.google.com/view/godybagvechesture/ana-sayfa"
VIP_RADAR_APP = "https://t.me/YeniBirAirdropBot/radar"
PORT = int(os.environ.get("PORT", "10000"))

# =========================================================
# TELEGRAM BOT KOMUTLARI
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    
    keyboard = [
        [InlineKeyboardButton("🚀 SİTEDEN DOĞRULA VE AÇ", url=SITE_URL)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 **Merhaba {user.first_name}!**\n\n"
        "🔒 VIP Radar sistemine erişim sağlamak için önce web sitemiz üzerinden doğrulama yapmalısınız.\n"
        "Aşağıdaki butona tıklayarak sitemize gidin ve VIP Radarı başlatın.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def run_telegram_bot(application):
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

# =========================================================
# RENDER SUNUCUSU
# =========================================================
async def health_check(request):
    return web.Response(text="Bot Aktif!", content_type="text/plain")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    if not BOT_TOKEN:
        print("HATA: BOT_TOKEN bulunamadı!")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    await start_web_server()
    await run_telegram_bot(application)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
