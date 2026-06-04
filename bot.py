"""
Telegram → Rubika bridge bot.

Architecture notes:
- Handlers add jobs to an asyncio.Queue and return immediately.
- A single worker task processes jobs sequentially (safe for 1GB RAM).
- The Rubika client is persistent and reused, with auto-reconnect on failure.
- All blocking subprocesses (yt-dlp, 7z) run via asyncio.create_subprocess_exec
  so the event loop is never blocked.
- Direct URL downloads use httpx streaming (chunk-by-chunk, low RAM).
- Status messages are edited in place; the chat stays clean.
- Disk space + max-file-size guards prevent runaway usage.
- Run under systemd with Restart=always for true 24/7 uptime.

Supported inputs:
- Any Telegram file / photo / video / audio / voice / video_note.
- A YouTube URL (downloaded via yt-dlp + download.sh).
- A direct download URL (ZIP / RAR / PDF / EXE / ...).

Author: Javid Shah
"""

# Written and maintained by Javid Shah
import os
import re
import sys
import time
import shutil
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple
from urllib.parse import urlparse, unquote

import httpx

# منطقه‌ی زمانی تهران برای اسم‌گذاری timestamp فایل‌ها
try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    # Fallback: ایران از سال 2022 ساعت تابستانی نداره، پس UTC+3:30 ثابت
    from datetime import timezone, timedelta
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30), name="Asia/Tehran")

from telegram import Update, Message
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    filters, ContextTypes,
)
from telegram.request import HTTPXRequest

import rubpy

from config import (
    TELEGRAM_TOKEN, ALLOWED_USER_IDS, ZIP_PASSWORD,
    TEMP_DIR, RUBIKA_TARGET_GUID, RUBIKA_SESSION,
    SPLIT_SIZE_MB, MAX_FILE_SIZE_MB, MIN_FREE_DISK_MB,
    LOCAL_BOT_API_URL, LOCAL_BOT_FILE_URL,
    DOWNLOAD_SCRIPT_PATH, LOG_FILE,
    YT_DOWNLOAD_TIMEOUT, ZIP_TIMEOUT,
    DIRECT_CONNECT_TIMEOUT, DIRECT_READ_TIMEOUT,
    DIRECT_CHUNK_KB, DIRECT_PROGRESS_EVERY_MB,
    DIRECT_DOWNLOAD_TIMEOUT,
    COOKIES_PATH, COOKIE_UPLOAD_WINDOW_SEC,
)

# -------------------- logging --------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("rubpy").setLevel(logging.WARNING)
log = logging.getLogger("bridge-bot")

Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)

# -------------------- regex --------------------
URL_REGEX = re.compile(r"https?://[^\s]+", re.IGNORECASE)
YOUTUBE_REGEX = re.compile(
    r"(?:youtube\.com|youtu\.be|youtube-nocookie\.com)", re.IGNORECASE
)

# -------------------- globals --------------------
job_queue: "asyncio.Queue[dict]" = asyncio.Queue()
rubika_lock = asyncio.Lock()
_rubika_client: Optional[rubpy.Client] = None

# user_id -> monotonic deadline (ثانیه)
# وقتی کاربر /upload_cookies می‌زنه، تا COOKIE_UPLOAD_WINDOW_SEC منتظر فایل کوکی هستیم
_pending_cookie_uploads: "dict[int, float]" = {}


# -------------------- helpers --------------------
# JS
def is_allowed(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in ALLOWED_USER_IDS)


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\.\-]", "_", name)
    return (name[:80] or "file")


def make_hashtag(name: Optional[str]) -> str:
    """نام تلگرام رو به یه هشتگ معتبر تبدیل می‌کنه (Unicode-safe، حروف فارسی هم پشتیبانی میشه)."""
    if not name:
        return "#Anonymous"
    # فاصله‌ها → underscore
    s = re.sub(r"\s+", "_", name.strip())
    # هرچی جز حرف/عدد/زیرخط (شامل فارسی) → حذف
    s = re.sub(r"[^\w]", "", s, flags=re.UNICODE)
    return f"#{s}" if s else "#Anonymous"


def _tehran_timestamp() -> str:
    """زمان فعلی تهران به فرمت YYYY-MM-DD_HH-MM-SSam/pm (مثل 2026-05-15_03-45-22pm)."""
    return datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d_%I-%M-%S%p").lower()


def rename_to_timestamp(file_path: str) -> str:
    """
    فایل رو in-place rename می‌کنه به اسم timestamp تهران، با حفظ پسوند اصلی.
    مثال: Thermory_Benchmark.zip → 2026-05-15_03-45-22pm.zip
    شامل ثانیه است تا تصادم اسم عملاً غیرممکن باشه.
    """
    directory = os.path.dirname(file_path)
    _, ext = os.path.splitext(file_path)
    base_ts = _tehran_timestamp()
    new_name = f"{base_ts}{ext}"
    new_path = os.path.join(directory, new_name)
    counter = 1
    while (
        os.path.exists(new_path)
        and os.path.abspath(new_path) != os.path.abspath(file_path)
    ):
        counter += 1
        new_path = os.path.join(directory, f"{base_ts}_{counter}{ext}")
    if new_path != file_path:
        os.rename(file_path, new_path)
    return new_path


def free_disk_mb(path: str) -> int:
    return shutil.disk_usage(path).free // (1024 * 1024)


def cleanup_dir(prefix: str = "") -> None:
    """Remove files in TEMP_DIR; optionally only those whose name starts with prefix."""
    try:
        for f in os.listdir(TEMP_DIR):
            if prefix and not f.startswith(prefix):
                continue
            fp = os.path.join(TEMP_DIR, f)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except Exception as e:
                    log.warning("cleanup failed for %s: %s", fp, e)
    except Exception as e:
        log.warning("cleanup_dir error: %s", e)


async def safe_edit(msg: Message, text: str) -> None:
    """Edit a status message, swallowing harmless errors (rate-limit / not modified)."""
    try:
        await msg.edit_text(text)
    except Exception as e:
        log.debug("safe_edit ignored: %s", e)


# -------------------- persistent Rubika client --------------------
# core logic — Javid Shah
async def get_rubika() -> rubpy.Client:
    global _rubika_client
    if _rubika_client is None:
        log.info("Starting Rubika client…")
        _rubika_client = rubpy.Client(RUBIKA_SESSION)
        await _rubika_client.start()
        log.info("Rubika client ready.")
    return _rubika_client


async def restart_rubika() -> None:
    """Force-restart the Rubika client (used after failures)."""
    global _rubika_client
    if _rubika_client is not None:
        try:
            await _rubika_client.stop()
        except Exception as e:
            log.warning("Rubika stop on restart failed: %s", e)
    _rubika_client = None


async def upload_to_rubika(
    file_path: str,
    caption: Optional[str] = None,
    retries: int = 4,
) -> None:
    """Send one file to Rubika with retries, timeout, and client restart between attempts."""
    last_err: Optional[Exception] = None
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    # Generous timeout: 30s base + 15s per MB (covers slow uploads)
    upload_timeout = 30 + int(file_size_mb * 15)
    log.info("Uploading to Rubika: %s (%.1fMB, timeout=%ds, caption=%r)",
             os.path.basename(file_path), file_size_mb, upload_timeout, caption)

    for attempt in range(1, retries + 1):
        try:
            async with rubika_lock:
                client = await get_rubika()
                await asyncio.wait_for(
                    client.send_document(
                        object_guid=RUBIKA_TARGET_GUID,
                        document=file_path,
                        caption=caption,
                    ),
                    timeout=upload_timeout,
                )
            log.info("Rubika upload OK on attempt %d", attempt)
            return
        except asyncio.TimeoutError as e:
            last_err = e
            log.warning("Rubika upload attempt %d/%d TIMED OUT after %ds",
                        attempt, retries, upload_timeout)
            await restart_rubika()
            await asyncio.sleep(min(2 ** attempt, 30))
        except Exception as e:
            last_err = e
            log.warning("Rubika upload attempt %d/%d failed: %s",
                        attempt, retries, e)
            await restart_rubika()
            await asyncio.sleep(min(2 ** attempt, 30))
    raise last_err or RuntimeError("Rubika upload failed (unknown)")


# -------------------- 7z zip + split --------------------
async def zip_and_split(file_path: str) -> List[str]:
    output_base = file_path + ".7z"
    directory = os.path.dirname(file_path)
    base_name = os.path.basename(output_base)

    # remove any stale archive parts before starting
    for f in os.listdir(directory):
        if f.startswith(base_name):
            try:
                os.remove(os.path.join(directory, f))
            except Exception:
                pass

    cmd = [
        "7z", "a",
        f"-p{ZIP_PASSWORD}",
        "-mhe=on",          # encrypt headers
        "-mx0",             # store only (fast, low RAM, low CPU)
        "-mmt=off",         # single-thread (safer on 1GB box)
        f"-v{SPLIT_SIZE_MB}m",
        output_base, file_path,
    ]
    log.info("7z: %s", " ".join(cmd[:3]) + " … " + os.path.basename(file_path))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=ZIP_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"7z timed out after {ZIP_TIMEOUT}s")

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="ignore")[-400:]
        raise RuntimeError(f"7z exit {proc.returncode}: {err}")

    parts = sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.startswith(base_name)
    )
    if not parts:
        raise RuntimeError("7z produced no output files")
    return parts


# -------------------- yt-dlp --------------------
async def download_youtube(url: str, output_template: str) -> Tuple[Optional[str], str]:
    """
    دانلود از یوتیوب با اسکریپت download.sh.
    خروجی: (مسیر فایل یا None، رشته‌ی خطا). در صورت موفقیت، خطا خالیه.
    """
    cleanup_dir(prefix="yt_video")
    proc = await asyncio.create_subprocess_exec(
        DOWNLOAD_SCRIPT_PATH, url, output_template,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=YT_DOWNLOAD_TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None, f"yt-dlp timed out after {YT_DOWNLOAD_TIMEOUT}s"

    stderr_text = (stderr or b"").decode("utf-8", errors="ignore")
    if proc.returncode != 0:
        log.error("yt-dlp error (full): %s", stderr_text)
        # خلاصه‌ی آخرین خطوط خطا برای کاربر
        last_lines = "\n".join(
            line for line in stderr_text.strip().splitlines()[-6:]
            if line.strip()
        )
        return None, last_lines or f"yt-dlp exit {proc.returncode}"

    # find the merged output (ignore .part, .ytdl, .7z* leftovers)
    for f in os.listdir(TEMP_DIR):
        full = os.path.join(TEMP_DIR, f)
        if not os.path.isfile(full):
            continue
        if not f.startswith("yt_video"):
            continue
        if f.endswith((".part", ".ytdl", ".tmp")):
            continue
        if ".7z" in f:
            continue
        return full, ""
    return None, "yt-dlp فایلی تولید نکرد (خروجی پیدا نشد)"


# -------------------- direct URL download --------------------
def _extract_filename_from_headers(headers: httpx.Headers, fallback_url: str) -> str:
    """نام فایل رو از Content-Disposition یا path URL استخراج کن."""
    name: Optional[str] = None
    cd = headers.get("content-disposition", "")
    if cd:
        # filename*=UTF-8''something یا filename="something"
        m = re.search(
            r"filename\*\s*=\s*(?:UTF-8'')?([^;\s]+)",
            cd, flags=re.IGNORECASE,
        )
        if m:
            name = unquote(m.group(1).strip().strip('"\''))
        else:
            m2 = re.search(
                r'filename\s*=\s*"?([^";]+)"?',
                cd, flags=re.IGNORECASE,
            )
            if m2:
                name = m2.group(1).strip()
    if not name:
        parsed = urlparse(fallback_url)
        name = unquote(os.path.basename(parsed.path)) or "download.bin"
    return sanitize_filename(name)


async def download_direct_url(
    url: str, status: Message
) -> Tuple[Optional[str], str]:
    """
    دانلود مستقیم یک URL با httpx به‌صورت streaming.
    خروجی: (مسیر فایل یا None، پیام خطا).
    """
    timeout = httpx.Timeout(
        connect=DIRECT_CONNECT_TIMEOUT,
        read=DIRECT_READ_TIMEOUT,
        write=DIRECT_READ_TIMEOUT,
        pool=DIRECT_CONNECT_TIMEOUT,
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    chunk_size = max(8, DIRECT_CHUNK_KB) * 1024
    progress_step = max(1, DIRECT_PROGRESS_EVERY_MB) * 1024 * 1024
    deadline = time.monotonic() + DIRECT_DOWNLOAD_TIMEOUT

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers,
        ) as client:
            # --- HEAD برای تشخیص نام و حجم (اختیاری) ---
            head_filename: Optional[str] = None
            head_total: int = 0
            try:
                head = await client.head(url)
                if head.status_code < 400:
                    head_filename = _extract_filename_from_headers(head.headers, url)
                    cl = head.headers.get("content-length")
                    if cl and cl.isdigit():
                        head_total = int(cl)
            except Exception as e:
                log.info("HEAD failed (continuing): %s", e)

            if head_total:
                size_mb = head_total / (1024 * 1024)
                if MAX_FILE_SIZE_MB and size_mb > MAX_FILE_SIZE_MB:
                    return None, (
                        f"حجم فایل ({size_mb:.1f}MB) بیشتر از سقف "
                        f"({MAX_FILE_SIZE_MB}MB) است."
                    )
                free_mb = free_disk_mb(TEMP_DIR)
                if size_mb + 200 > free_mb:  # 200MB حاشیه‌ی اطمینان
                    return None, (
                        f"فضای دیسک کافی نیست: فایل {size_mb:.1f}MB، "
                        f"آزاد {free_mb}MB."
                    )

            # --- GET به‌صورت streaming ---
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return None, f"HTTP {resp.status_code} از سرور."

                # نام فایل از header های GET دوباره چک می‌شه
                filename = (
                    _extract_filename_from_headers(resp.headers, url)
                    or head_filename
                    or "download.bin"
                )
                total = head_total
                cl2 = resp.headers.get("content-length")
                if cl2 and cl2.isdigit():
                    total = int(cl2)

                file_path = os.path.join(TEMP_DIR, filename)
                # اگه فایل هم‌نام قبلاً وجود داره، پاکش کن
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

                downloaded = 0
                last_update_at = 0

                await safe_edit(
                    status,
                    f"⬇️ شروع دانلود: {filename}"
                    + (f" ({total/1024/1024:.1f}MB)" if total else "")
                )

                try:
                    with open(file_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                            if not chunk:
                                continue
                            # چک کردن deadline کلی
                            if time.monotonic() > deadline:
                                raise asyncio.TimeoutError(
                                    f"دانلود از سقف زمانی ({DIRECT_DOWNLOAD_TIMEOUT}s) رد شد"
                                )
                            f.write(chunk)
                            downloaded += len(chunk)

                            # سقف اندازه‌ی فایل (وقتی Content-Length نبود)
                            if (
                                MAX_FILE_SIZE_MB
                                and downloaded > MAX_FILE_SIZE_MB * 1024 * 1024
                            ):
                                raise RuntimeError(
                                    f"حین دانلود از سقف ({MAX_FILE_SIZE_MB}MB) رد شد."
                                )

                            # به‌روزرسانی پیام پیشرفت
                            if downloaded - last_update_at >= progress_step:
                                last_update_at = downloaded
                                mb_done = downloaded / (1024 * 1024)
                                if total:
                                    pct = (downloaded / total) * 100
                                    mb_total = total / (1024 * 1024)
                                    await safe_edit(
                                        status,
                                        f"⬇️ دانلود: {mb_done:.1f}/"
                                        f"{mb_total:.1f}MB ({pct:.0f}%)"
                                    )
                                else:
                                    await safe_edit(
                                        status,
                                        f"⬇️ دانلود: {mb_done:.1f}MB"
                                    )
                except Exception:
                    # پاک کردن فایل ناقص
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    except Exception:
                        pass
                    raise

                if downloaded == 0:
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                    return None, "هیچ داده‌ای از سرور دریافت نشد."

                log.info(
                    "Direct download OK: %s (%.1fMB)",
                    filename, downloaded / (1024 * 1024)
                )
                return file_path, ""

    except httpx.HTTPError as e:
        return None, f"خطای شبکه: {type(e).__name__}: {e}"
    except asyncio.TimeoutError as e:
        return None, f"timeout: {e}"
    except Exception as e:
        log.exception("direct download error")
        return None, f"{type(e).__name__}: {e}"


# -------------------- job processing --------------------
async def handle_telegram_file(
    job: dict, status: Message
) -> Tuple[Optional[str], str]:
    media = job["media"]
    file_name = sanitize_filename(job["original_name"])
    file_path = os.path.join(TEMP_DIR, file_name)
    await safe_edit(status, f"⬇️ دانلود از تلگرام: {file_name}")
    try:
        tg_file = await media.get_file()
        await tg_file.download_to_drive(file_path)
        return file_path, ""
    except Exception as e:
        log.exception("telegram download error")
        return None, f"خطای دانلود از تلگرام: {type(e).__name__}: {e}"


async def handle_youtube_link(
    job: dict, status: Message
) -> Tuple[Optional[str], str]:
    url = job["url"]
    await safe_edit(status, "⬇️ دانلود از یوتیوب (ممکنه چند دقیقه طول بکشه)…")
    output_template = os.path.join(TEMP_DIR, "yt_video.%(ext)s")
    return await download_youtube(url, output_template)


async def handle_direct_url(
    job: dict, status: Message
) -> Tuple[Optional[str], str]:
    url = job["url"]
    await safe_edit(status, "🌐 آماده‌سازی دانلود از لینک مستقیم…")
    return await download_direct_url(url, status)


async def process_job(job: dict) -> None:
    status: Message = job["status"]
    job_type = job["type"]
    file_path: Optional[str] = None
    parts: List[str] = []

    try:
        free_mb = free_disk_mb(TEMP_DIR)
        if free_mb < MIN_FREE_DISK_MB:
            await safe_edit(
                status,
                f"❌ فضای دیسک کمه: {free_mb}MB آزاد. کارها قبل از ادامه پاک شدن."
            )
            cleanup_dir()
            return

        err_msg = ""
        if job_type == "file":
            file_path, err_msg = await handle_telegram_file(job, status)
        elif job_type == "youtube":
            file_path, err_msg = await handle_youtube_link(job, status)
        elif job_type == "direct_url":
            file_path, err_msg = await handle_direct_url(job, status)
        else:
            await safe_edit(status, "❌ نوع کار ناشناخته")
            return

        if not file_path or not os.path.exists(file_path):
            # خطای واقعی رو به کاربر نشون بده (نه پیام عمومی)
            short = (err_msg or "علت نامشخص").strip()
            # تشخیص خاص: خطای کوکی یوتیوب
            if job_type == "youtube" and re.search(
                r"sign in to confirm|not a bot|cookies|consent",
                short, flags=re.IGNORECASE,
            ):
                if len(short) > 300:
                    short = short[-300:]
                await safe_edit(
                    status,
                    f"❌ دانلود یوتیوب ناموفق — کوکی expire شده.\n\n"
                    f"📌 برای تازه کردن کوکی:\n"
                    f"1. دستور /upload_cookies رو بزن\n"
                    f"2. فایل cookies.txt جدید رو بفرست\n\n"
                    f"جزئیات خطا:\n{short}"
                )
            else:
                if len(short) > 400:
                    short = short[-400:]
                await safe_edit(status, f"❌ دانلود ناموفق:\n{short}")
            return

        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if MAX_FILE_SIZE_MB and size_mb > MAX_FILE_SIZE_MB:
            await safe_edit(
                status,
                f"❌ فایل بیش از حد بزرگه: {size_mb:.1f}MB "
                f"(سقف: {MAX_FILE_SIZE_MB}MB)"
            )
            return

        # تغییر نام فایل به timestamp تهران، با حفظ پسوند اصلی
        # مثال: Thermory_Benchmark.zip → 2026-05-15_03-45pm.zip
        original_basename = os.path.basename(file_path)
        file_path = rename_to_timestamp(file_path)
        new_basename = os.path.basename(file_path)
        log.info("Renamed: %s → %s", original_basename, new_basename)

        # ساخت هشتگ caption از اسم فرستنده
        sender_name = job.get("sender_name", "")
        caption = make_hashtag(sender_name)

        await safe_edit(status, f"🗜 بسته‌بندی و رمزگذاری ({size_mb:.1f}MB)…")
        parts = await zip_and_split(file_path)

        # original file no longer needed
        try:
            os.remove(file_path)
        except Exception:
            pass
        file_path = None

        total = len(parts)
        await safe_edit(status, f"📦 فایل به {total} پارت تقسیم شد. شروع آپلود…")

        for i, part in enumerate(parts, 1):
            part_size_mb = os.path.getsize(part) / (1024 * 1024)
            await safe_edit(
                status, f"📤 آپلود پارت {i}/{total} ({part_size_mb:.1f}MB)…"
            )
            await upload_to_rubika(part, caption=caption)
            try:
                os.remove(part)
            except Exception:
                pass

        await safe_edit(status, f"✅ همه‌ی {total} پارت با موفقیت آپلود شد.")

    except Exception as e:
        log.exception("Job failed")
        await safe_edit(status, f"❌ خطا: {str(e)[:250]}")
    finally:
        # guarantee disk cleanup regardless of outcome
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        for p in parts:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


async def queue_worker() -> None:
    log.info("Queue worker started")
    while True:
        job = await job_queue.get()
        try:
            await process_job(job)
        except Exception:
            log.exception("Unexpected worker error")
        finally:
            job_queue.task_done()


# -------------------- handlers --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "👋 سلام!\n\n"
        "📁 هر فایل / عکس / ویدیو / صدا بفرست\n"
        "🎬 لینک یوتیوب بفرست\n"
        "🌐 یا لینک مستقیم دانلود (ZIP / RAR / PDF / EXE / ...)\n\n"
        "🔐 فایل‌ها زیپ‌شده با رمز به روبیکا منتقل میشن.\n\n"
        "دستورها:\n"
        "/status  وضعیت صف و دیسک\n"
        "/cleanup پاکسازی فایل‌های موقت\n"
        "/restart_rubika  ری‌استارت کلاینت روبیکا\n"
        "/upload_cookies  تعویض کوکی یوتیوب (وقتی expire شد)"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        f"📊 وضعیت:\n"
        f"• کارهای در صف: {job_queue.qsize()}\n"
        f"• فضای آزاد دیسک: {free_disk_mb(TEMP_DIR)} MB\n"
        f"• کلاینت روبیکا: "
        f"{'✅ متصل' if _rubika_client is not None else '❌ نامتصل'}"
    )


async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    cleanup_dir()
    await update.message.reply_text(
        f"🧹 پاک شد. فضای آزاد: {free_disk_mb(TEMP_DIR)} MB"
    )


async def cmd_restart_rubika(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await restart_rubika()
    try:
        await get_rubika()
        await update.message.reply_text("🔄 کلاینت روبیکا ری‌استارت شد.")
    except Exception as e:
        await update.message.reply_text(f"❌ ری‌استارت روبیکا شکست خورد: {e}")


async def cmd_upload_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرایند آپلود کوکی جدید یوتیوب. کاربر باید در 5 دقیقه‌ی بعد فایل cookies.txt رو بفرسته."""
    if not is_allowed(update):
        return
    uid = update.effective_user.id
    import time as _time
    _pending_cookie_uploads[uid] = _time.monotonic() + COOKIE_UPLOAD_WINDOW_SEC
    minutes = COOKIE_UPLOAD_WINDOW_SEC // 60
    await update.message.reply_text(
        f"📥 لطفاً فایل کوکی جدید (cookies.txt) رو در {minutes} دقیقه‌ی آینده بفرست.\n\n"
        "• فایل باید فرمت Netscape باشه (همون چیزی که اکستنشن Get cookies.txt LOCALLY می‌ده)\n"
        "• از کوکی قبلی خودکار backup گرفته می‌شه\n"
        "• اگه اشتباهی فایل غیرمعتبر فرستادی، فایل قبلی برمی‌گرده"
    )


async def handle_cookies_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فایل document فعلی رو به‌عنوان کوکی یوتیوب نصب می‌کنه (validate + backup + replace)."""
    msg = update.message
    doc = msg.document
    status = await msg.reply_text("⬇️ در حال دریافت فایل کوکی…")

    backup_path: Optional[str] = None
    try:
        # backup فایل قبلی (اگه بود)
        import time as _time
        if os.path.exists(COOKIES_PATH):
            backup_path = f"{COOKIES_PATH}.backup-{int(_time.time())}"
            shutil.copy2(COOKIES_PATH, backup_path)
            log.info("Backed up old cookies to %s", backup_path)

        # دانلود فایل جدید از تلگرام به مسیر کوکی
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(COOKIES_PATH)

        # validation سبک: حداقل یه خط .youtube.com یا header Netscape داشته باشه
        try:
            with open(COOKIES_PATH, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(4096)
        except Exception as e:
            raise RuntimeError(f"نمی‌تونم فایل دانلود شده رو بخونم: {e}")

        looks_valid = (
            "# Netscape" in head
            or ".youtube.com" in head
            or "youtube.com" in head
        )
        if not looks_valid:
            # restore backup
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, COOKIES_PATH)
                await safe_edit(
                    status,
                    "❌ فایل ارسالی فرمت کوکی Netscape نیست. کوکی قبلی برگردونده شد."
                )
            else:
                try:
                    os.remove(COOKIES_PATH)
                except Exception:
                    pass
                await safe_edit(
                    status,
                    "❌ فایل ارسالی فرمت کوکی Netscape نیست. کوکی قبلی هم وجود نداشت."
                )
            return

        size_kb = os.path.getsize(COOKIES_PATH) / 1024
        await safe_edit(
            status,
            f"✅ کوکی جدید نصب شد ({size_kb:.1f}KB).\n"
            f"حالا می‌تونی لینک یوتیوب بفرستی."
        )
        log.info("Cookies updated successfully (%.1fKB)", size_kb)

    except Exception as e:
        log.exception("cookie upload failed")
        # سعی کن backup رو برگردونی
        if backup_path and os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, COOKIES_PATH)
                await safe_edit(status, f"❌ خطا در نصب کوکی: {e}\nکوکی قبلی برگردونده شد.")
            except Exception:
                await safe_edit(status, f"❌ خطا در نصب کوکی: {e}")
        else:
            await safe_edit(status, f"❌ خطا در نصب کوکی: {e}")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    msg = update.message
    uid = update.effective_user.id

    # اگه کاربر تازگی /upload_cookies زده و document فرستاده،
    # این document رو به‌عنوان کوکی پردازش کن، نه job معمولی.
    if msg.document and uid in _pending_cookie_uploads:
        import time as _time
        deadline = _pending_cookie_uploads.get(uid, 0)
        if _time.monotonic() < deadline:
            _pending_cookie_uploads.pop(uid, None)
            await handle_cookies_upload(update, context)
            return
        else:
            # window منقضی شده
            _pending_cookie_uploads.pop(uid, None)

    if msg.photo:
        media = msg.photo[-1]
        original_name = f"photo_{media.file_unique_id}.jpg"
    elif msg.document:
        media = msg.document
        original_name = getattr(media, "file_name", None) or f"file_{media.file_unique_id}"
    elif msg.video:
        media = msg.video
        original_name = getattr(media, "file_name", None) or f"video_{media.file_unique_id}.mp4"
    elif msg.audio:
        media = msg.audio
        original_name = getattr(media, "file_name", None) or f"audio_{media.file_unique_id}.mp3"
    elif msg.voice:
        media = msg.voice
        original_name = f"voice_{media.file_unique_id}.ogg"
    elif msg.video_note:
        media = msg.video_note
        original_name = f"vnote_{media.file_unique_id}.mp4"
    else:
        await msg.reply_text("❌ نوع فایل پشتیبانی نمی‌شه")
        return

    pos = job_queue.qsize() + 1
    status = await msg.reply_text(f"📥 در صف قرار گرفت (نوبت #{pos})…")
    await job_queue.put({
        "type": "file",
        "media": media,
        "original_name": original_name,
        "status": status,
        "chat_id": msg.chat_id,
        "sender_name": update.effective_user.first_name or "",
    })


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler واحد برای متن‌های شامل URL.
    اگه یوتیوب بود → job نوع youtube
    در غیر اینصورت → job نوع direct_url
    """
    if not is_allowed(update):
        return
    text = (update.message.text or "").strip()
    m = URL_REGEX.search(text)
    if not m:
        return  # احتیاطی - این handler نباید بدون URL trigger بشه
    url = m.group(0).rstrip(").,;'\"")

    if YOUTUBE_REGEX.search(url):
        job_type = "youtube"
        queued_text = f"🎬 لینک یوتیوب در صف (نوبت #{job_queue.qsize() + 1})…"
    else:
        job_type = "direct_url"
        queued_text = f"🌐 لینک دانلود در صف (نوبت #{job_queue.qsize() + 1})…"

    status = await update.message.reply_text(queued_text)
    await job_queue.put({
        "type": job_type,
        "url": url,
        "status": status,
        "chat_id": update.message.chat_id,
        "sender_name": update.effective_user.first_name or "",
    })


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Telegram error:", exc_info=context.error)


# -------------------- lifecycle --------------------
async def on_startup(app: Application) -> None:
    cleanup_dir()  # start clean
    try:
        await get_rubika()
    except Exception as e:
        log.warning("Rubika init failed at startup (will retry on first job): %s", e)
    app.bot_data["worker_task"] = asyncio.create_task(queue_worker())


async def on_shutdown(app: Application) -> None:
    task = app.bot_data.get("worker_task")
    if task:
        task.cancel()
    global _rubika_client
    if _rubika_client is not None:
        try:
            await _rubika_client.stop()
        except Exception:
            pass


def main() -> None:
    # entrypoint — Javid Shah
    # Long timeouts for large files via local Bot API
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=3600,
        write_timeout=3600,
        connect_timeout=60,
        pool_timeout=60,
    )
    # Separate (short) request object for getUpdates polling
    polling_request = HTTPXRequest(
        connection_pool_size=4,
        read_timeout=60,
        write_timeout=60,
        connect_timeout=30,
        pool_timeout=30,
    )

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .base_url(LOCAL_BOT_API_URL)
        .base_file_url(LOCAL_BOT_FILE_URL)
        .local_mode(True)
        .request(request)
        .get_updates_request(polling_request)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))
    app.add_handler(CommandHandler("restart_rubika", cmd_restart_rubika))
    app.add_handler(CommandHandler("upload_cookies", cmd_upload_cookies))
    # هر متنی که شامل http(s)://‌ هست (یوتیوب یا لینک عادی) → handle_url
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"https?://"),
        handle_url,
    ))
    app.add_handler(MessageHandler(
        filters.Document.ALL
        | filters.VIDEO
        | filters.AUDIO
        | filters.PHOTO
        | filters.VOICE
        | filters.VIDEO_NOTE,
        handle_file,
    ))
    app.add_error_handler(error_handler)

    log.info("✅ Bot starting (polling)…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    # (c) Javid Shah
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    except Exception:
        log.exception("Fatal error — systemd will restart.")
        sys.exit(1)
