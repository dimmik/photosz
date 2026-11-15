#!/usr/bin/env bash
# Скриншоты альбома для визуальной проверки (WSL + Windows Chrome), по одной главе —
# в одном высоком окне часть картинок не успевает загрузиться.
#   tools/qa_shot.sh "<папка альбома>" [главы, по умолчанию все]  → .shots/ch<N>.jpg (+ cover.jpg)
set -e
ROOT="$1"; shift; A="$ROOT/album"; OUT="$(pwd)/.shots"; mkdir -p "$OUT"
C="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"      # WSL: Chrome из Windows
WIN=1
if [ ! -f "$C" ] || ! command -v wslpath >/dev/null; then          # обычный Linux: свой Chrome/Chromium
  WIN=0; C=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser || true)
  [ -n "$C" ] || { echo "нет Chrome/Chromium — скриншоты пропущены"; exit 0; }
fi
win() { if [ "$WIN" = 1 ]; then wslpath -w "$1"; else echo "$1"; fi; }
N=$(grep -o '"title": ' "$A/album.json" | wc -l); N=$((N - 1))
CH="${*:-$(seq 1 $N)}"
WOUT="$(win "$OUT")"; SEP=$([ "$WIN" = 1 ] && echo "\\" || echo /)
shot() {  # $1 — CSS, $2 — имя, $3 — размер окна
  sed "s|</style>|$1</style>|" "$A/index.html" > "$A/_qa.html"
  URL="file:///$(win "$A/_qa.html" | sed 's#\\#/#g; s#^/##; s# #%20#g')"
  timeout 90 "$C" --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=15000 \
    --window-size="$3" --screenshot="$WOUT$SEP$2.png" "$URL" >/dev/null 2>&1 || true
}
shot ".toc,.chapter,footer{display:none!important}" cover 1400,900
for n in $CH; do
  shot ".cover,.toc,footer,.chapter:not(#ch$n){display:none!important}.chapter{padding-top:24px}" "ch$n" 1400,2600
done
rm -f "$A/_qa.html"
python3 - "$OUT" <<'PY'
import sys, glob; from PIL import Image, ImageOps
for p in glob.glob(f"{sys.argv[1]}/*.png"):
    im = Image.open(p).convert("RGB")
    bg = Image.new("RGB", im.size, im.getpixel((im.width - 1, im.height - 1)))
    from PIL import ImageChops
    box = ImageChops.difference(im, bg).getbbox()          # обрезаем пустой низ
    if box: im = im.crop((0, 0, im.width, min(im.height, box[3] + 20)))
    im.thumbnail((700, 1300)); im.save(p[:-4] + ".jpg", quality=70)
PY
