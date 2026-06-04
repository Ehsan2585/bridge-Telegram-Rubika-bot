# Bridge Bot — پل تلگرام به روبیکا

رباتی که فایل‌ها را از **تلگرام** می‌گیرد و به‌صورت خودکار در یک گروه/کانال **روبیکا** آپلود می‌کند.
از فایل‌های بزرگ (تا چند گیگابایت) پشتیبانی می‌کند، می‌تواند ویدیوهای یوتیوب را دانلود کند و فایل‌های حجیم را به‌صورت رمزگذاری‌شده تکه‌تکه (7z) ارسال می‌کند.

> **توجه:** این ربات با اکانت شخصی شما در روبیکا کار می‌کند. مسئولیت استفاده با خودتان است.

---

## امکانات

- انتقال فایل، عکس، ویدیو، صدا و voice از تلگرام به روبیکا
- دانلود ویدیو از یوتیوب (با `yt-dlp`) و ارسال به روبیکا
- دانلود مستقیم از لینک (ZIP/RAR/PDF و...)
- تکه‌تکه کردن و رمزگذاری فایل‌های بزرگ با 7z
- محدودسازی دسترسی به آیدی‌های مجاز
- اجرای دائمی با systemd (ری‌استارت خودکار روی خطا)
- دستورات مدیریتی: `/status`، `/cleanup`، `/restart_rubika`

---

## پیش‌نیازها

- سرور لینوکس (اوبونتو ۲۲.۰۴ یا بالاتر توصیه می‌شود) با حداقل ۱ گیگ رم و ۱۵ گیگ دیسک
- پایتون ۳.۱۰ به بالا
- یک **ربات تلگرام** و توکن آن (از [@BotFather](https://t.me/BotFather))
- `api_id` و `api_hash` از [my.telegram.org](https://my.telegram.org) (برای Local Bot API)
- یک **اکانت روبیکا** (شماره موبایل) برای لاگین

---

## نصب سریع

```bash
git clone https://github.com/Ehsan2585/bridge-Telegram-Rubika-bot.git
cd bridge-bot
sudo bash install.sh
```

اسکریپت، پیش‌نیازها را نصب می‌کند، محیط مجازی می‌سازد و `config.py` را از روی نمونه ایجاد می‌کند.
سپس مراحل زیر را دستی انجام دهید.

---

## ۱. تنظیم config.py

فایل `config.py` را باز کنید و مقادیر زیر را پر کنید:

| فیلد | توضیح |
|------|-------|
| `TELEGRAM_TOKEN` | توکن رباتی که از BotFather گرفتید |
| `ALLOWED_USER_IDS` | آیدی عددی تلگرام شما (و هرکس دیگری که اجازه دارد) |
| `RUBIKA_TARGET_GUID` | شناسه (GUID) گروه/کانال مقصد در روبیکا |
| `ZIP_PASSWORD` | یک رمز قوی برای فایل‌های فشرده |

> آیدی عددی تلگرام خود را می‌توانید از رباتی مثل [@userinfobot](https://t.me/userinfobot) بگیرید.

---

## ۲. راه‌اندازی Local Bot API

این ربات برای پشتیبانی از فایل‌های بزرگ از سرور محلی Telegram Bot API استفاده می‌کند (نه فقط API ابری که سقف ۵۰ مگابایت دارد).

ابتدا باینری `telegram-bot-api` را نصب/بیلد کنید
(راهنما: [دستورالعمل رسمی](https://github.com/tdlib/telegram-bot-api)).

سپس فایل سرویس را بسازید — می‌توانید از `telegram-bot-api.service.example` در همین مخزن کمک بگیرید:

```bash
sudo cp telegram-bot-api.service.example /etc/systemd/system/telegram-bot-api.service
sudo nano /etc/systemd/system/telegram-bot-api.service   # api-id و api-hash خودت را بذار
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot-api
sudo systemctl status telegram-bot-api
```

مطمئن شوید روی پورت `8081` در حال اجراست (همان مقداری که در `config.py` تنظیم شده).

---

## ۳. اولین لاگین روبیکا

سشن روبیکا باید یک بار به‌صورت دستی ساخته شود. ربات را مستقیم اجرا کنید:

```bash
./venv/bin/python bot.py
```

بار اول، شماره موبایل و کد تأیید روبیکا از شما پرسیده می‌شود. پس از لاگین موفق، فایل `rubika_session.rp` ساخته می‌شود و دیگر نیازی به ورود دوباره نیست. حالا با `Ctrl+C` متوقفش کنید.

> فایل `rubika_session.rp` کلید ورود به اکانت شماست — هرگز آن را با کسی به اشتراک نگذارید و در گیت آپلودش نکنید.

---

## ۴. اجرای دائمی با systemd

```bash
sudo cp bridge-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bridge-bot
sudo systemctl status bridge-bot
```

> اگر مسیر نصب شما `/root/bridge-bot` نیست، مسیرهای داخل `bridge-bot.service` را اصلاح کنید.

---

## دستورات داخل تلگرام

| دستور | کار |
|-------|-----|
| `/status` | نمایش صف کارها و فضای دیسک |
| `/cleanup` | پاک‌سازی پوشه موقت |
| `/restart_rubika` | ری‌استارت کلاینت روبیکا بدون قطع کل ربات |

---

## عیب‌یابی

```bash
# لاگ زنده‌ی سرویس
journalctl -u bridge-bot -f

# لاگ فایلی ربات
tail -f bot.log

# ری‌استارت دستی
sudo systemctl restart bridge-bot
```

اگر روبیکا قطع شد `/restart_rubika`، و اگر دیسک پر شد `/cleanup` را بزنید.

---

## امنیت

فایل‌های زیر **هرگز** نباید در گیت منتشر شوند (در `.gitignore` لحاظ شده‌اند):
`config.py` · `rubika_session.rp` · `cookies.txt` · `*.log`

## لایسنس

این پروژه تحت لایسنس MIT منتشر شده است. جزئیات در فایل [LICENSE](LICENSE).

© Javid Shah
