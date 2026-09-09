import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Loglama
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SITE_URL = "https://sites.google.com/view/gody-bag-ve-chesture-/ana-sayfa"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    keyboard = [[InlineKeyboardButton("🌐 RADARI VE LİNKLERİ AÇ", url=SITE_URL)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 **Hoş Geldin, {user.first_name}!**\n\n"
        "Canlı radar verilerine ve sistem arayüzüne erişmek için aşağıdaki butona tıklayabilirsin.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

def main():
    if not BOT_TOKEN:
        print("HATA: BOT_TOKEN bulunamadı!")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    
    print("Bot polling modunda başlatılıyor...")
    application.run_polling()

if __name__ == '__main__':
    main()
