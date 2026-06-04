#!/bin/bash
# Wrapper around yt-dlp for the bridge bot.
# Called as: ./download.sh "<URL>" "<OUTPUT_TEMPLATE>"
# Output template example: /root/bridge-bot/temp/yt_video.%(ext)s
#
# استراتژی:
#   1) اول با کوکی + چند player_client تلاش می‌کنه.
#   2) اگه شکست خورد، بدون کوکی + چند player_client دوباره تلاش می‌کنه.
#   3) همه‌ی خطاها به stderr می‌رن تا bot.py به کاربر نشون بده.

set -u
export PATH="/root/.deno/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export DENO_DIR="/root/.deno"
export HOME="/root"

URL="${1:-}"
OUTPUT="${2:-}"

if [[ -z "$URL" || -z "$OUTPUT" ]]; then
  echo "usage: $0 <url> <output_template>" >&2
  exit 2
fi

YTDLP="/root/bridge-bot/venv/bin/yt-dlp"
COOKIES="/root/bridge-bot/cookies.txt"

# پرچم‌های مشترک
# - فرمت انعطاف‌پذیر: اگه bestvideo+bestaudio<=720 نبود، به best معمولی برگرد.
# - player_client های متعدد: yt-dlp خودش بین این‌ها fallback می‌کنه.
COMMON_FLAGS=(
  --no-playlist
  --no-warnings
  --no-progress
  --retries 5
  --fragment-retries 5
  --socket-timeout 30
  --concurrent-fragments 1
  -f "bestvideo[height<=720]+bestaudio/best[height<=720]/bestvideo+bestaudio/best"
  --merge-output-format mp4
  --extractor-args "youtube:player_client=default,tv,android_vr,ios,web_safari"
  -o "$OUTPUT"
)

run_ytdlp() {
  # $1 = label, بقیه = آرگ‌های اضافه
  local label="$1"; shift
  echo ">>> yt-dlp attempt: $label" >&2
  "$YTDLP" "${COMMON_FLAGS[@]}" "$@" "$URL"
  return $?
}

# تلاش ۱: با کوکی
if [[ -f "$COOKIES" ]]; then
  if run_ytdlp "with-cookies" --cookies "$COOKIES"; then
    exit 0
  fi
  echo ">>> first attempt failed, retrying WITHOUT cookies..." >&2
fi

# تلاش ۲: بدون کوکی (گاهی کوکی expire شده باعث 403 می‌شه)
if run_ytdlp "no-cookies"; then
  exit 0
fi

echo ">>> all yt-dlp attempts failed" >&2
exit 1
