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
