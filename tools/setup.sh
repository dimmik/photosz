#!/usr/bin/env bash
# Установка всего, что нужно пайплайну, на новом компьютере (Ubuntu / WSL).
#   bash tools/setup.sh          поставить и проверить
#   bash tools/setup.sh --check  только проверить, ничего не ставить
set -u
cd "$(dirname "$0")/.."
CHECK=${1:-}

if [ "$CHECK" != "--check" ]; then
  sudo apt-get update -q
  sudo apt-get install -y -q python3 python3-pip ffmpeg git curl fonts-dejavu-core
  # Ubuntu 23.04+ запрещает pip в системный Python без флага (PEP 668)
  python3 -m pip install --user -q -r requirements.txt || python3 -m pip install --user -q --break-system-packages -r requirements.txt
fi

ok=1
say() { printf '%-28s %s\n' "$1" "$2"; }
for c in python3 ffmpeg ffprobe git curl; do
  if command -v "$c" >/dev/null; then say "$c" "есть"; else say "$c" "НЕТ"; ok=0; fi
done
python3 - <<'PY' || ok=0
import importlib, sys
bad = []
for m in ["PIL", "pillow_heif", "imagehash", "numpy", "cv2", "reverse_geocoder", "cryptography"]:
    try:
        importlib.import_module(m)
        print(f"{m:<28} есть")
    except Exception as e:
        print(f"{m:<28} НЕТ ({e})"); bad.append(m)
if sys.version_info < (3, 10):
    print("python3 старее 3.10"); bad.append("python")
sys.exit(1 if bad else 0)
PY
if [ -f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf ]; then say "шрифт DejaVu" "есть"; else say "шрифт DejaVu" "НЕТ (подписи на листах будут мелкие)"; fi
# модели лиц: скачиваются при первом запуске faces.py, со сверкой SHA-256
if [ "$CHECK" != "--check" ]; then
  python3 -c "import sys; sys.path.insert(0, 'tools'); import faces; [faces.model(n) for n in faces.MODELS]" && say "модели лиц" "скачаны"
else
  n=$(ls .faces/models/*.onnx 2>/dev/null | wc -l); say "модели лиц" "$n из 2 (скачаются сами)"
fi
CHROME="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
if [ -x "$CHROME" ] || [ -f "$CHROME" ]; then say "Chrome (скриншоты)" "есть"; else say "Chrome (скриншоты)" "нет — сборка работает, но без скриншотов проверки"; fi
[ -f token.txt ] && say "token.txt" "есть" || say "token.txt" "НЕТ — нужен новый fine-grained токен на репозиторий"
[ -f claudflare_t.txt ] && say "Cloudflare токен" "есть" || say "Cloudflare токен" "нет — публикация только на GitHub Pages"
[ -f .faces/never.npz ] && say "стоп-лист" "есть" || say "стоп-лист" "НЕТ — нужно восстановить копию: python3 tools/backup.py restore"
[ "$ok" = 1 ] && echo "ГОТОВО" || { echo "ЕСТЬ ПРОБЛЕМЫ — см. выше"; exit 1; }
