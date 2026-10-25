#!/usr/bin/env bash
# Скриншоты альбома для визуальной проверки (WSL + Windows Chrome), по одной главе —
# в одном высоком окне часть картинок не успевает загрузиться.
#   tools/qa_shot.sh "<папка альбома>" [главы, по умолчанию все]  → .shots/ch<N>.jpg (+ cover.jpg)
set -e
ROOT="$1"; shift; A="$ROOT/album"; OUT="$(pwd)/.shots"; mkdir -p "$OUT"
C="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
N=$(grep -o '"title": ' "$A/album.json" | wc -l); N=$((N - 1))
CH="${*:-$(seq 1 $N)}"
WOUT="$(wslpath -w "$OUT")"
shot() {  # $1 — CSS, $2 — имя, $3 — размер окна
  sed "s|</style>|$1</style>|" "$A/index.html" > "$A/_qa.html"
  URL="file:///$(wslpath -w "$A/_qa.html" | sed 's#\\#/#g; s# #%20#g')"
  timeout 90 "$C" --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=15000 \
    --window-size="$3" --screenshot="$WOUT\\$2.png" "$URL" >/dev/null 2>&1 || true
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
