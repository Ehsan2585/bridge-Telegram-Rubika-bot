

# توکن ربات تلگرام (از @BotFather)
TELEGRAM_TOKEN = "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE"

# آیدی‌های مجاز تلگرام
ALLOWED_USER_IDS = [123456789, 987654321]  # آیدی عددی تلگرام خودت رو بذار

# رمز فایل zip (بهتره عوضش کنی چون لو رفته)
ZIP_PASSWORD = "PUT_A_STRONG_PASSWORD_HERE"

# پوشه موقت
TEMP_DIR = "/root/bridge-bot/temp"

# لاگ
LOG_FILE = "/root/bridge-bot/bot.log"

# روبیکا
RUBIKA_SESSION = "rubika_session"             # نام فایل سشن (بدون پسوند)
# مقصد: گروه روبیکا (با g0 شروع می‌شه - قبلاً کانال بود)
RUBIKA_TARGET_GUID = "PUT_TARGET_GROUP_GUID_HERE"

# اندازه‌ی هر پارت 7z به مگابایت
SPLIT_SIZE_MB = 50

# سقف اندازه‌ی فایل اصلی (مگابایت). None یعنی بدون سقف.
# با 16 گیگ دیسک و 1 گیگ RAM، 2 گیگ سقف امنیه.
MAX_FILE_SIZE_MB = 2048

# اگه فضای آزاد دیسک از این کمتر بشه، کار جدید قبول نمی‌شه
MIN_FREE_DISK_MB = 1500

# Local Bot API server (telegram-bot-api روی همین سرور باید بالا باشه)
LOCAL_BOT_API_URL = "http://localhost:8081/bot"
LOCAL_BOT_FILE_URL = "http://localhost:8081/file/bot"

# اسکریپت دانلود یوتیوب
DOWNLOAD_SCRIPT_PATH = "/root/bridge-bot/download.sh"

# مسیر فایل کوکی یوتیوب (با /upload_cookies در تلگرام قابل تعویضه)
COOKIES_PATH = "/root/bridge-bot/cookies.txt"

# مدت زمان (ثانیه) که دستور /upload_cookies منتظر فایل می‌مونه
COOKIE_UPLOAD_WINDOW_SEC = 300  # 5 دقیقه

# تایم‌اوت‌ها (ثانیه)
YT_DOWNLOAD_TIMEOUT = 1800   # 30 دقیقه برای دانلود یوتیوب
ZIP_TIMEOUT = 1800           # 30 دقیقه برای فشرده‌سازی

# ---------- تنظیمات دانلود مستقیم (لینک‌های ZIP/RAR/PDF/EXE/...) ----------
# تایم‌اوت connect (ثانیه) برای دانلود مستقیم
DIRECT_CONNECT_TIMEOUT = 30
# تایم‌اوت برای هر chunk که می‌خونیم (ثانیه) - اگه سرور لاگ بزنه detect می‌شه
DIRECT_READ_TIMEOUT = 300
# اندازه‌ی chunk دانلود (KB)
DIRECT_CHUNK_KB = 64
# هر چقدر دانلود شد، پیام پیشرفت به‌روزرسانی بشه (MB)
DIRECT_PROGRESS_EVERY_MB = 5
# سقف کل زمان دانلود مستقیم (ثانیه) - برای جلوگیری از hang
DIRECT_DOWNLOAD_TIMEOUT = 3600  # 1 ساعت
