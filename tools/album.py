#!/usr/bin/env python3
"""Единая точка входа: от зипа с фотками до ссылки. ИИ здесь не вызывается — команды готовят
промпты, которые оркестратор отдаёт дешёвым субагентам (см. .claude/skills/photo-album).

  python3 tools/album.py prepare  <zip | папка>     распаковка, метаданные, разделы, листы, промпты прохода 1
  python3 tools/album.py shortlist <альбом>         шорт-лист по оценкам, листы и промпт прохода 2
  python3 tools/album.py build    <альбом>          сборка album/ (+ скриншоты глав в .shots/)
  python3 tools/album.py publish  <альбом> [--slug] шифр → <slug>/ в корне, push, ожидание Pages, проверка ссылки
  python3 tools/album.py status   <альбом>          что сделано / чего не хватает
  python3 tools/album.py find                       новые папки с зипом/фото/видео, ещё не опубликованные
  python3 tools/album.py push                       push уже сделанных коммитов (тулинг, план) с токеном

Альбом — папка, где лежит зип (или указанная папка): <альбом>/src, work, album.
"""
import argparse, base64, glob, json, os, re, subprocess, sys, time, unicodedata, urllib.request, zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
REPO_DIR = BASE                # корень проекта = репозиторий (см. .gitignore)
REPO = "dimmik/photosz"
PAGES = "https://dimmik.github.io/photosz"
SECTIONS = 10                  # цель — до 10 разделов
DRAFT = 16                     # черновых разделов с запасом (слабые отсеются)
CHUNK = 10                     # листов прохода 1 на одного Haiku-агента


def run(*cmd):
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def py(script, *args):
    run(sys.executable, os.path.join(HERE, script), *args)


def tpl(name, **kw):
    s = open(os.path.join(HERE, "prompts", name), encoding="utf-8").read()
    for k, v in kw.items():
        s = s.replace("{{" + k + "}}", str(v))
    return s


def trip(root):
    """Короткое описание поездки для промптов: места и даты из метаданных."""
    work = os.path.join(root, "work")
    secs = json.load(open(os.path.join(work, "sections.json")))
    cities = []
    for s in secs:
        c = s["place"].get("city") or s["place"].get("town")
        if c and c not in cities:
            cities.append(c)
    return f"trip to {', '.join(cities[:4]) or 'unknown place'}, {secs[0]['start'][:10]} – {secs[-1]['end'][:10]}"


def write_prompt(root, name, text):
    d = os.path.join(root, "work", "prompts")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    open(p, "w", encoding="utf-8").write(text)
    return p


# ── prepare ──────────────────────────────────────────────────────────────────

def cmd_prepare(path):
    if os.path.isfile(path) and path.lower().endswith(".zip"):
        root = os.path.dirname(os.path.abspath(path))
        if root == BASE:                                 # зип в корне проекта — переносим в свою папку
            root = os.path.join(BASE, os.path.splitext(os.path.basename(path))[0])
            os.makedirs(root, exist_ok=True)
            os.rename(path, os.path.join(root, os.path.basename(path)))
            path = os.path.join(root, os.path.basename(path))
        zips = [path]
    else:
        root = os.path.abspath(path)
        zips = sorted(glob.glob(os.path.join(root, "*.zip")))
        loose = [f for f in os.listdir(root) if f.lower().endswith(MEDIA_EXT)]
        if loose:                                        # фото/видео прямо в папке — переносим в src/
            os.makedirs(os.path.join(root, "src"), exist_ok=True)
            for f in loose:
                os.rename(os.path.join(root, f), os.path.join(root, "src", f))
    src = os.path.join(root, "src")
    os.makedirs(src, exist_ok=True)
    for z in zips:
        print(f"распаковка {os.path.basename(z)}")
        with zipfile.ZipFile(z) as zf:
            for m in zf.infolist():                     # плоско, без вложенных папок и служебных файлов
                name = os.path.basename(m.filename)
                if m.is_dir() or not name or name.startswith(".") or "__MACOSX" in m.filename:
                    continue
                with zf.open(m) as fi, open(os.path.join(src, name), "wb") as fo:
                    fo.write(fi.read())
    py("extract.py", root)
    py("select.py", root, "sections", "--sections", str(DRAFT))

    work = os.path.join(root, "work")
    sheets = sorted(glob.glob(os.path.join(work, "pass1", "p*.jpg")))
    vsheets = sorted(glob.glob(os.path.join(work, "pass1", "v*.jpg")))
    qsheets = sorted(glob.glob(os.path.join(work, "pass1", "q*.jpg")))
    t = trip(root)
    prompts = []
    chunks = [sheets[i:i + CHUNK] for i in range(0, len(sheets), CHUNK)] or [[]]
    for i, ch in enumerate(chunks):
        last = i == len(chunks) - 1 and vsheets
        prompts.append(("haiku", write_prompt(root, f"pass1_{i + 1}.txt", tpl(
            "pass1.md", TRIP=t, SHEETS=", ".join(ch),
            OUT=os.path.join(work, f"scores_{i + 1}.json"),
            VIDEOS_WORD=" and videos" if last else "",
            VIDEO_BLOCK=("Video sheets: " + ", ".join(vsheets) + "\nEach row = one video labeled like \"8-V2\", "
                         "6 frames left→right from start to end; compare frames to understand what HAPPENS.\n") if last else "",
            VIDEO_SCORE=(" For videos: high when something distinctive happens, low when static/shaky/nothing "
                         "happens. Videos are rare and valuable — describe each carefully.") if last else ""))))
    if qsheets:
        prompts.append(("sonnet", write_prompt(root, "series.txt", tpl(
            "series.md", TRIP=t, SHEETS=", ".join(qsheets), OUT=os.path.join(work, "series_scores.json")))))
    print("\nПРОМПТЫ ПРОХОДА 1 (запускать параллельно, модель — в первой колонке):")
    for model, p in prompts:
        print(f"  {model:7} {p}")


# ── shortlist ────────────────────────────────────────────────────────────────

def cmd_shortlist(root):
    work = os.path.join(root, "work")
    parts = sorted(glob.glob(os.path.join(work, "scores_[0-9]*.json")))
    if parts:                                            # части прохода 1 → scores.json (метки листов)
        merged = {}
        for p in parts:
            merged.update(json.load(open(p)))
        labels = json.load(open(os.path.join(work, "pass1.json")))
        miss = [l for l in labels if l not in merged]
        print(f"проход 1: {len(merged)} оценок, без оценки {len(miss)}" + (f": {miss[:10]}" if miss else ""))
        json.dump(merged, open(os.path.join(work, "scores.json"), "w"), indent=1, ensure_ascii=False)
        for p in parts:
            os.rename(p, p + ".done")
    for f in glob.glob(os.path.join(work, "sheets", "*.jpg")):
        os.remove(f)
    py("select.py", root, "shortlist")

    secs = [s for s in json.load(open(os.path.join(work, "sections.json"))) if not s.get("weak")]
    sc = json.load(open(os.path.join(work, "scores_id.json")))
    meta = {r["id"]: r for r in json.load(open(os.path.join(work, "meta.json")))}
    d = lambda f: sc.get(f, {}).get("what", "?")
    lines = []
    for s in secs:
        p = s["place"]
        pl = ", ".join(v for k, v in p.items() if k in ("tourism", "leisure", "road", "neighbourhood", "suburb", "city"))
        lines.append(f"s{s['n']:02d}: {s['start'][5:16].replace('T', ' ')}–{s['end'][11:16]}, near: {pl}")
        lines.append("  photos: " + "; ".join(f"{i}={d(f)} ({sc.get(f, {}).get('score', '?')}/{sc.get(f, {}).get('fun', 0)})"
                                            for i, f in enumerate(s["candidates"], 1)))
        if s.get("fun_candidates"):
            lines.append("  fun: " + "; ".join(f"F{i}={d(f)}" for i, f in enumerate(s["fun_candidates"], 1)))
        if s.get("cand_videos"):
            lines.append("  videos: " + "; ".join(f"V{i}={d(f)} ({meta[f]['duration']}s)"
                                                for i, f in enumerate(s["cand_videos"], 1)))
        for fs in s.get("fun_series", []):
            lines.append(f"  already included funny series: «{fs['caption']}»")
    p = write_prompt(root, "pass2.txt", tpl(
        "pass2.md", TRIP=trip(root), MAX_SECTIONS=SECTIONS, CONTEXT="\n".join(lines),
        SHEETS=", ".join(os.path.join(root, s["sheet"]) for s in secs), OUT=os.path.join(work, "picks.json")))
    print(f"\nПРОМПТ ПРОХОДА 2:\n  sonnet  {p}")


# ── build ────────────────────────────────────────────────────────────────────

def cmd_build(root, title=None):
    args = [root] + (["--title", title] if title else [])
    py("build.py", *args)
    subprocess.run([os.path.join(HERE, "qa_shot.sh"), root], cwd=BASE)
    print(f"скриншоты: {os.path.join(BASE, '.shots')}/cover.jpg, ch*.jpg")


# ── publish ──────────────────────────────────────────────────────────────────

def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def gh(path, token):
    req = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
                                 headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def cmd_push():
    """Push в origin/master; токен из token.txt идёт заголовком и не пишется в .git/config."""
    token = open(os.path.join(BASE, "token.txt")).read().strip()
    auth = "Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    subprocess.run(["git", "-C", REPO_DIR, "-c", "http.extraHeader=" + auth, "push", "-q", "-u", "origin", "master"],
                   check=True)


def cmd_publish(root, slug=None):
    saved = os.path.join(root, "work", "publish.json")    # slug закрепляется при первой публикации
    if not slug and os.path.exists(saved):
        slug = json.load(open(saved))["slug"]
    slug = slug or slugify(os.path.basename(os.path.abspath(root)))
    json.dump({"slug": slug}, open(saved, "w"))
    token = open(os.path.join(BASE, "token.txt")).read().strip()
    out = subprocess.run([sys.executable, os.path.join(HERE, "publish.py"), root, "--repo", REPO_DIR, "--slug", slug],
                         check=True, capture_output=True, text=True).stdout
    key = re.search(r"^KEY (\S+)", out, re.M).group(1)
    print(out.replace(key, "<ключ>"))

    g = lambda *a, **kw: subprocess.run(["git", "-C", REPO_DIR, *a], check=True, text=True, **kw)
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        g("init", "-q", "-b", "master")
        g("config", "user.name", "dimmik")
        g("config", "user.email", "dimmik@users.noreply.github.com")
        g("remote", "add", "origin", f"https://github.com/{REPO}.git")
    ign = os.path.join(REPO_DIR, ".gitignore")          # белый список: открываем папку альбома
    if f"!/{slug}/" not in open(ign).read().split():
        open(ign, "a").write(f"!/{slug}/\n")
    g("add", "-A", "--", slug, "index.html", "robots.txt", ".nojekyll", ".gitignore")
    if subprocess.run(["git", "-C", REPO_DIR, "diff", "--cached", "--quiet"]).returncode:
        g("commit", "-q", "-m", f"Publish album {slug}\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
          "Claude-Session: https://claude.ai/code/session_01XarAvaQnY5vkZUXThqQNei")
        cmd_push()
    sha = g("rev-parse", "HEAD", capture_output=True).stdout.strip()

    try:
        gh("/pages", token)
    except urllib.error.HTTPError:                       # Pages ещё не включён — включаем
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/pages", method="POST",
                                     data=json.dumps({"source": {"branch": "master", "path": "/"}}).encode(),
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"})
        urllib.request.urlopen(req, timeout=30)
    print("жду сборку GitHub Pages…", flush=True)
    for _ in range(60):
        try:
            b = gh("/pages/builds/latest", token)
            if b.get("status") == "built" and b.get("commit") == sha:
                break
            if b.get("status") == "errored":
                sys.exit(f"Pages: ошибка сборки {b.get('error')}")
        except urllib.error.HTTPError:
            pass
        time.sleep(10)
    album_file = json.loads(re.search(r'<script id="cfg" type="application/json">(.*?)</script>',
                                      open(os.path.join(REPO_DIR, slug, "index.html")).read(), re.S).group(1))["album"]
    url = f"{PAGES}/{slug}/"
    for _ in range(30):                                  # CDN может отдавать старое ещё несколько секунд
        live = urllib.request.urlopen(f"{url}?v={int(time.time())}", timeout=30).read().decode()
        if album_file in live:
            code = urllib.request.urlopen(f"{url}data/{album_file}", timeout=30).status
            if code == 200:
                break
        time.sleep(5)
    else:
        sys.exit("живая страница не обновилась — проверь вручную")
    a = json.load(open(os.path.join(root, "album", "album.json")))
    print(f"\nГОТОВО\nссылка: {url}#k={key}\nключ:   {key}\n\nТекст для рассылки (RU):\n"
          f"Фотоальбом «{a['title']}», {a['dates']}: {url}#k={key}\n\nText to share (EN):\n"
          f"Photo album “{a.get('title_en') or a['title']}”, {a.get('dates_en') or a['dates']}: {url}#k={key}&l=en")


# ── find ─────────────────────────────────────────────────────────────────────

MEDIA_EXT = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".mp4", ".mov", ".m4v")


def cmd_find():
    """Папки в корне проекта (и на уровень глубже), где есть зип или фото/видео, но альбом не опубликован."""
    skip = {"tools", ".claude", ".shots", ".git"}
    found = []
    for d in sorted(os.listdir(BASE)):
        p = os.path.join(BASE, d)
        if d in skip or d.startswith(".") or not os.path.isdir(p):
            continue
        if os.path.exists(os.path.join(p, "work", "publish.json")):
            continue
        files = [os.path.join(p, f) for f in os.listdir(p)]
        zips = [f for f in files if f.lower().endswith(".zip")]
        media = [f for f in files if f.lower().endswith(MEDIA_EXT)]
        if os.path.isdir(os.path.join(p, "src")):
            media += glob.glob(os.path.join(p, "src", "*"))
        if zips or media:
            mtime = max(os.path.getmtime(f) for f in (zips or media)[:50])
            found.append((mtime, p, len(zips), len(media), os.path.isdir(os.path.join(p, "work"))))
    for f in sorted(os.listdir(BASE)):                   # зип прямо в корне — альбомом станет папка с его именем
        if f.lower().endswith(".zip"):
            found.append((os.path.getmtime(os.path.join(BASE, f)), os.path.join(BASE, f), 1, 0, False))
    if not found:
        print("новых альбомов не найдено")
    for mtime, p, z, m, started in sorted(found, reverse=True):
        print(f"{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}  zip:{z} media:{m}{'  (начат)' if started else ''}  {p}")


# ── status ───────────────────────────────────────────────────────────────────

def cmd_status(root):
    w = lambda *p: os.path.exists(os.path.join(root, *p))
    steps = [("распаковано (src/)", w("src")), ("метаданные (work/meta.json)", w("work", "meta.json")),
             ("разделы и листы прохода 1", w("work", "pass1.json")),
             ("оценки прохода 1", w("work", "scores_id.json") or bool(glob.glob(os.path.join(root, "work", "scores_[0-9]*.json")))),
             ("оценки серий", w("work", "series_scores.json")), ("шорт-лист / промпт прохода 2", w("work", "prompts", "pass2.txt")),
             ("выбор прохода 2 (picks.json)", w("work", "picks.json")), ("сборка (album/)", w("album", "index.html")),
             ("ключ (опубликован)", w("work", "access.key"))]
    for name, ok in steps:
        print(("✓ " if ok else "· ") + name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "shortlist", "build", "publish", "status", "find", "push"])
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--slug")
    ap.add_argument("--title")
    a = ap.parse_args()
    a.path = os.path.abspath(a.path)
    {"prepare": lambda: cmd_prepare(a.path), "shortlist": lambda: cmd_shortlist(a.path),
     "build": lambda: cmd_build(a.path, a.title), "publish": lambda: cmd_publish(a.path, a.slug),
     "status": lambda: cmd_status(a.path), "find": cmd_find, "push": cmd_push}[a.cmd]()


if __name__ == "__main__":
    main()
