import os
import asyncio
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Giriş ve Doğrulama Sayfası Linkiniz
VERIFY_SITE_URL = "https://sites.google.com/view/gody-bag-ve-chesture-/ana-sayfa"
PORT = int(os.environ.get("PORT", "10000"))

# =========================================================
# 1. TELEGRAM BOT KOMUTLARI
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    username_str = f"@{user.username}" if user.username else user.first_name
    
    keyboard = [
        [InlineKeyboardButton("🔐 VIP ÜYELİK DOĞRULA VE GİRİŞ YAP", url=VERIFY_SITE_URL)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 **Selam {user.first_name}!**\n\n"
        f"🆔 **Telegram ID:** `{user.id}`\n"
        f"👤 **Kullanıcı Adı:** {username_str}\n\n"
        "⚠️ **VIP Radar sistemine erişebilmek için önce üyeliğinizi doğrulamanız gerekmektedir.**\n"
        "Aşağıdaki butona tıklayarak doğrulama adımını tamamlayabilir ve kilitsiz radara erişebilirsiniz.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def run_telegram_bot(application):
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

# =========================================================
# 2. RENDER İÇİN WEB SUNUCUSU
# =========================================================
async def health_check(request):
    return web.Response(text="Bot Aktif ve Çalışıyor!", content_type="text/plain")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[HTTP] Web sunucusu {PORT} portunda aktif edildi.")

# =========================================================
# 3. ANA ÇALIŞTIRMA
# =========================================================
async def main():
    if not BOT_TOKEN:
        print("HATA: BOT_TOKEN bulunamadı!")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    await start_web_server()
    print("Bot polling modunda başlatılıyor...")
    
    await run_telegram_bot(application)
    
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
