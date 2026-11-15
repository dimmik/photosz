#!/usr/bin/env bash
# Переезд: вся папка проекта скопирована на другой компьютер (Linux или WSL). Скрипт возвращает всё,
# что живёт вне папки, и проверяет остальное. Можно запускать сколько угодно раз.
#   bash tools/restore_here.sh
# Что делает:
#   1. память Claude: .claude-memory/ → ~/.claude/projects/<путь проекта>/memory (существующее не трогает);
#   2. права 600 на токены и ключи (после копирования права бывают любые);
#   3. git: safe.directory (если папку копировал другой пользователь), автор коммитов;
#   4. пакеты: если чего-то нет и sudo без пароля доступен — ставит tools/setup.sh, иначе подсказывает команду;
#   5. проверка: setup.sh --check и живые запросы к GitHub и Cloudflare (токены не печатаются).
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "проект: $ROOT"

# 1. память
MEM="$(python3 -c 'import sys; sys.path.insert(0, "tools"); import backup; print(backup.memory_dir())')"
if [ -d .claude-memory ] && [ -n "$(ls -A .claude-memory 2>/dev/null)" ]; then
  mkdir -p "$MEM"
  n=0
  for f in .claude-memory/*; do
    b="$(basename "$f")"
    if [ ! -e "$MEM/$b" ]; then cp "$f" "$MEM/$b"; n=$((n + 1)); fi
  done
  echo "память Claude: скопировано $n, всего $(ls "$MEM" | wc -l) → $MEM"
else
  echo "память Claude: копии .claude-memory/ нет — нужна зашифрованная: python3 tools/backup.py restore (фраза)"
fi

# 2. права
chmod 600 token.txt claudflare_t.txt cloudflare.json 2>/dev/null
find . -path '*/work/access.key' -exec chmod 600 {} + 2>/dev/null
echo "права на токены и ключи: 600"

# 3. git
if ! git status >/dev/null 2>&1; then
  git config --global --add safe.directory "$ROOT" && echo "git: папка добавлена в safe.directory"
fi
[ -n "$(git config user.email)" ] || git config user.email "dimmik@users.noreply.github.com"
[ -n "$(git config user.name)" ] || git config user.name "dimmik"
echo "git: $(git log -1 --format='%h %s' 2>/dev/null), ветка $(git rev-parse --abbrev-ref HEAD 2>/dev/null)"

# 4. пакеты
if ! bash tools/setup.sh --check >/dev/null 2>&1; then          # Python-пакеты ставятся и без sudo
  echo "ставлю Python-пакеты (pip --user)…"
  python3 -m pip install --user -q -r requirements.txt >/dev/null 2>&1 ||
    python3 -m pip install --user -q --break-system-packages -r requirements.txt >/dev/null 2>&1 || true
fi
if ! bash tools/setup.sh --check >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then
    echo "ставлю пакеты…"; bash tools/setup.sh >/dev/null 2>&1 || true
  else
    echo "НУЖНА УСТАНОВКА: sudo просит пароль — запусти сам: ! bash tools/setup.sh"
  fi
fi

# 5. проверка
echo; bash tools/setup.sh --check; ok=$?
python3 - <<'PY' || ok=1
import json, urllib.request, sys
ua = {"User-Agent": "photo-alb"}
bad = 0
try:
    t = open("token.txt").read().strip()
    r = json.load(urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/dimmik/photosz",
                                         headers={**ua, "Authorization": "Bearer " + t}), timeout=30))
    print(f"{'GitHub токен':<28} работает (push: {r['permissions'].get('push')})")
except Exception as e:
    print(f"{'GitHub токен':<28} НЕ РАБОТАЕТ ({type(e).__name__})"); bad = 1
try:
    c = open("claudflare_t.txt").read().strip(); acc = json.load(open("cloudflare.json"))["account"]
    urllib.request.urlopen(urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{acc}/pages/projects/photosz",
                           headers={**ua, "Authorization": "Bearer " + c}), timeout=30)
    print(f"{'Cloudflare токен':<28} работает")
except Exception as e:
    print(f"{'Cloudflare токен':<28} НЕ РАБОТАЕТ ({type(e).__name__})"); bad = 1
sys.exit(bad)
PY
[ "$ok" = 0 ] && echo "ВСЁ ВОССТАНОВЛЕНО" || { echo "ЕСТЬ ПРОБЛЕМЫ — см. выше"; exit 1; }
