#!/bin/bash
# ============================================================
#  install.sh — نصب تعاملی Bridge Bot
#  اجرا:  sudo bash install.sh
#  این اسکریپت در حین نصب اطلاعات لازم را از شما می‌پرسد و
#  فایل config.py و سرویس‌ها را خودکار می‌سازد.
# ============================================================
set -e

echo "============================================================"
echo "            نصب Bridge Bot (تلگرام → روبیکا)"
echo "============================================================"

# --- بررسی دسترسی root ---
if [ "$EUID" -ne 0 ]; then
  echo "لطفاً با sudo اجرا کن:  sudo bash install.sh"
  exit 1
fi

# --- ۱. نصب پیش‌نیازهای سیستمی ---
echo ""
echo "==> [۱/۵] نصب پکیج‌های سیستمی (python venv, ffmpeg, 7z, git)..."
apt update -qq
apt install -y python3-venv python3-pip p7zip-full ffmpeg git

# --- ۲. ساخت محیط مجازی و نصب کتابخانه‌ها ---
echo ""
echo "==> [۲/۵] ساخت venv و نصب کتابخانه‌های پایتون..."
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
echo "    کتابخانه‌ها نصب شدند."

# --- ۳. گرفتن اطلاعات از کاربر و ساخت config.py ---
echo ""
echo "==> [۳/۵] تنظیمات ربات"
if [ -f config.py ]; then
  echo "    config.py از قبل وجود دارد."
  read -p "    می‌خواهی بازنویسی شود؟ (y/N): " OW
  if [ "$OW" != "y" ] && [ "$OW" != "Y" ]; then
    echo "    از config.py فعلی استفاده می‌شود."
    SKIP_CONFIG=1
  fi
fi

if [ -z "$SKIP_CONFIG" ]; then
  echo ""
  echo "    توکن ربات را از @BotFather در تلگرام بگیر (/newbot یا /token)."
  read -p "    TELEGRAM_TOKEN: " IN_TOKEN

  echo ""
  echo "    آیدی عددی تلگرامت را از @userinfobot بگیر."
  echo "    اگر چند نفر مجازند، با کاما جدا کن. مثال: 12345678, 98765432"
  read -p "    ALLOWED_USER_IDS: " IN_IDS

  echo ""
  echo "    شناسه (GUID) گروه یا کانال مقصد در روبیکا."
  read -p "    RUBIKA_TARGET_GUID: " IN_GUID

  echo ""
  echo "    یک رمز برای فایل‌های فشرده 7z (خالی بگذار تا خودکار ساخته شود)."
  read -s -p "    ZIP_PASSWORD: " IN_ZIP
  echo ""
  if [ -z "$IN_ZIP" ]; then
    IN_ZIP=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    echo "    رمز خودکار ساخته شد (در config.py ذخیره می‌شود)."
  fi

  # ساخت config.py از روی نمونه با مقادیر کاربر (با پایتون تا کاراکترهای خاص مشکل نسازند)
  TG_TOKEN="$IN_TOKEN" IDS="$IN_IDS" GUID="$IN_GUID" ZIPPW="$IN_ZIP" \
  ./venv/bin/python - <<'PY'
import os, re
src = open('config.py', encoding='utf-8').read()

token = os.environ['TG_TOKEN'].strip()
ids   = [s.strip() for s in os.environ['IDS'].split(',') if s.strip()]
guid  = os.environ['GUID'].strip()
zippw = os.environ['ZIPPW']

src = re.sub(r'TELEGRAM_TOKEN\s*=\s*".*?"', f'TELEGRAM_TOKEN = "{token}"', src)
src = re.sub(r'ALLOWED_USER_IDS\s*=\s*\[.*?\].*', f'ALLOWED_USER_IDS = [{", ".join(ids)}]', src)
src = re.sub(r'RUBIKA_TARGET_GUID\s*=\s*".*?"', f'RUBIKA_TARGET_GUID = "{guid}"', src)
src = re.sub(r'ZIP_PASSWORD\s*=\s*".*?"', f'ZIP_PASSWORD = "{zippw}"', src)

open('config.py','w',encoding='utf-8').write(src)
print("    config.py ساخته شد.")
PY
fi

# --- ۴. ساخت سرویس Local Bot API (اختیاری) ---
echo ""
echo "==> [۴/۵] سرور Local Bot API (برای فایل‌های بزرگ)"
read -p "    می‌خواهی همین حالا سرویس telegram-bot-api را بسازم؟ (y/N): " MK_API
if [ "$MK_API" = "y" ] || [ "$MK_API" = "Y" ]; then
  if [ ! -x /usr/local/bin/telegram-bot-api ]; then
    echo "    ⚠ باینری /usr/local/bin/telegram-bot-api پیدا نشد."
    echo "      اول طبق راهنمای https://github.com/tdlib/telegram-bot-api نصبش کن."
  fi
  echo "    api_id و api_hash را از https://my.telegram.org بگیر."
  read -p "    API_ID: " IN_APIID
  read -p "    API_HASH: " IN_APIHASH
  cat > /etc/systemd/system/telegram-bot-api.service <<UNIT
[Unit]
Description=Telegram Bot API
After=network.target

[Service]
ExecStart=/usr/local/bin/telegram-bot-api --api-id=${IN_APIID} --api-hash=${IN_APIHASH} --local --http-port=8081 --dir=/var/lib/telegram-bot-api
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
UNIT
  mkdir -p /var/lib/telegram-bot-api
  systemctl daemon-reload
  systemctl enable --now telegram-bot-api || echo "    (اگر خطا داد، احتمالاً باینری نصب نیست)"
  echo "    سرویس telegram-bot-api ساخته شد."
else
  echo "    رد شد. بعداً می‌توانی دستی از telegram-bot-api.service.example بسازی."
fi

chmod +x download.sh 2>/dev/null || true

# --- ۵. لاگین روبیکا ---
echo ""
echo "==> [۵/۵] لاگین روبیکا"
echo "    حالا یک بار ربات اجرا می‌شود تا با شماره‌ات وارد روبیکا شوی."
echo "    شماره و کد تأیید از تو پرسیده می‌شود. بعد از لاگین موفق، Ctrl+C بزن."
read -p "    آماده‌ای؟ (Enter برای شروع، یا n برای رد شدن): " GO
if [ "$GO" != "n" ] && [ "$GO" != "N" ]; then
  ./venv/bin/python bot.py || true
fi

echo ""
echo "============================================================"
echo " نصب تمام شد! برای اجرای دائمی با systemd:"
echo "   sudo cp bridge-bot.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now bridge-bot"
echo "   sudo systemctl status bridge-bot"
echo "============================================================"
