import os
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

# Logging Ayarları
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Flask Uygulaması ve CORS İzinleri (Google Sites / Iframe Uyumluluğu)
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Konfigürasyon
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SENIN_BOT_TOKEN_BURAYA")
GOOGLE_SITE_VERIFY_URL = "https://sites.google.com/view/godybagvechesture/ana-sayfa"
MINI_APP_DIRECT_LINK = "https://t.me/YeniBirAirdropBot/Radar"

# ==================== TELEGRAM BOT MANTIĞI ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start komutunu işler:
    - Doğrudan girenleri (ör. /start) Google Sites doğrulama sayfasına yönlendirir.
    - Siteden gelen onaylı kullanıcıları (?start=vip_onayli) Mini App açma butonuna yönlendirir.
    """
    args = context.args
    
    # Kullanıcı web sitesindeki doğrulama adımlarını geçip geldiyse:
    if args and args[0] == "vip_onayli":
        keyboard = [
            [
                InlineKeyboardButton(
                    text="🌐 VIP RADARI AÇ", 
                    url=MINI_APP_DIRECT_LINK
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"✅ **Doğrulama Başarılı, {update.effective_user.first_name}!**\n\n"
            "Siteden üyeliğiniz onaylandı. VIP Canlı Radar ekranına erişmek için "
            "aşağıdaki butona tıklayabilirsiniz.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        # Doğrudan bota girmeye çalışanları engelle ve siteye at:
        keyboard = [
            [
                InlineKeyboardButton(
                    text="🔒 SİTEDEN DOĞRULAMA YAP", 
                    url=GOOGLE_SITE_VERIFY_URL
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ **Erişim Engellendi!**\n\n"
            "Bu bota doğrudan erişim izni bulunmamaktadır.\n"
            "VIP Radarı kullanabilmek için önce web sitemiz üzerinden doğrulama yapmalısınız.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

# ==================== API VE WEB ENDPOINTLERİ ====================

@app.route('/', methods=['GET'])
def index():
    return "VIP Radar Backend Active & Running!"

@app.route('/api/radar-data', methods=['GET'])
def get_radar_data():
    """VIP Radar canlı yayın ve jeton verilerini döndüren API endpointi"""
    mock_data = [
        {"user": "@tiktok_live_1", "status": "🔴 CANLI", "coins": "125,400", "region": "TR"},
        {"user": "@tiktok_live_2", "status": "🔴 CANLI", "coins": "89,200", "region": "TR"},
        {"user": "@tiktok_live_3", "status": "🟡 BEKLEMEDE", "coins": "45,000", "region": "GLOBAL"},
        {"user": "@tiktok_live_4", "status": "🔴 CANLI", "coins": "310,000", "region": "GLOBAL"}
    ]
    return jsonify({"status": "success", "data": mock_data})

# ==================== BOT BAŞLATMA ====================

def run_bot():
    """Telegram Bot polling başlatıcı"""
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    # Render port ayarı
    port = int(os.environ.get("PORT", 5000))
    
    # Telegram Botu ayrı bir thread/süreç yerine Render ortamında başlatmak için:
    import threading
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Flask sunucusunu başlat
    app.run(host="0.0.0.0", port=port)
