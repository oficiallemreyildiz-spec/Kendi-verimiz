import os
import asyncio
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# GÜNCEL GOOGLE SITES ADRESLERİNİZ
VIP_RADAR_URL = "https://sites.google.com/view/godybagvechesture/vip-radar"
SITE_URL = "https://sites.google.com/view/godybagvechesture/ana-sayfa"
PORT = int(os.environ.get("PORT", "10000"))

# =========================================================
# TELEGRAM BOT KOMUTLARI
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args  # Siteden gelen şifreli doğrulama anahtarını okur (?start=vip_onayli)

    # 1. KULLANICI SİTEDEKİ DOĞRULA BUTONUNDAN GELDİYSE
    if args and args[0] == "vip_onayli":
        keyboard = [
            [InlineKeyboardButton("🌐 VIP RADARI AÇ", web_app=WebAppInfo(url=VIP_RADAR_URL))]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ **Doğrulama Başarılı, {user.first_name}!**\n\n"
            "Siteden üyeliğiniz onaylandı. VIP Canlı Radar ekranına erişmek için aşağıdaki butona tıklayabilirsiniz.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        # 2. BOTA DOĞRUDAN GİREN VEYA LİNKİ PAYLAŞANLAR İÇİN ENGEL
        keyboard = [
            [InlineKeyboardButton("🔒 SİTEDEN DOĞRULAMA YAP", url=SITE_URL)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"⚠️ **Erişim Engellendi!**\n\n"
            "Bu bota doğrudan erişim izni bulunmamaktadır.\n"
            "VIP Radarı kullanabilmek için önce web sitemiz üzerinden doğrulama yapmalısınız.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

# =========================================================
# RENDER SAĞLIK KONTROLÜ VE WEB SUNUCUSU
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
    print(f"[HTTP] Sunucu {PORT} portunda aktif edildi.")

async def main():
    if not BOT_TOKEN:
        print("HATA: BOT_TOKEN bulunamadı! Render Environment Variables kısmını kontrol edin.")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    await start_web_server()
    print("Bot polling modunda başlatılıyor...")
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
