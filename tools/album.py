#!/usr/bin/env python3
"""Единая точка входа: от зипа с фотками до ссылки. ИИ здесь не вызывается — команды готовят
промпты, которые оркестратор отдаёт дешёвым субагентам (см. .claude/skills/photo-album).

  python3 tools/album.py prepare  <zip | папка>     распаковка, метаданные, разделы, листы, промпты прохода 1
  python3 tools/album.py shortlist <альбом>         шорт-лист по оценкам, листы и промпт прохода 2
  python3 tools/album.py extras   <альбом>          после прохода 2: листы табличек и лиц, промпты для них
  python3 tools/album.py people   <альбом>          лист с лицами в камеру — спросить пользователя, кто это
  python3 tools/album.py build    <альбом>          сборка album/ (+ скриншоты глав в .shots/)
  python3 tools/album.py publish  <альбом> [--slug] шифр → <slug>/ в корне, push, ожидание Pages, проверка ссылки
  python3 tools/album.py status   <альбом>          что сделано / чего не хватает
  python3 tools/album.py find                       новые папки с зипом/фото/видео, ещё не опубликованные
  python3 tools/album.py push                       push уже сделанных коммитов (тулинг, план) с токеном

Альбом — папка, где лежит зип (или указанная папка): <альбом>/src, work, album.
"""
import argparse, base64, glob, json, os, re, shutil, subprocess, sys, time, unicodedata, urllib.request, zipfile
from datetime import datetime

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
REPO_DIR = BASE                # корень проекта = репозиторий (см. .gitignore)
REPO = "dimmik/photosz"
PAGES = "https://dimmik.github.io/photosz"
INDEX_ROOT = os.path.join(BASE, ".albums-index")   # источник индекса; вне git, как и всё с ключами
INDEX_SLUG = "albums"
SECTIONS = 10                  # цель — до 10 разделов
SECTION_RULE = ("- Up to {} sections; each 3–5 main photos and, where available, 1–2 videos. If there are more sections "
                "than that, drop the weakest (\"drop\": true). Never drop a section that has a funny series unless it is "
                "still clearly the weakest.")
# альбом «по дням» (select.py byday): глава = день, разделы = места этого дня
DAY_RULE = ("- The album is told DAY BY DAY: each day is a chapter, its sections are the places visited that day "
            "(\"day N\" in the context). EVERY day must keep at least 2 main photos in total (if the day has them at all), "
            "so never drop all sections of a day. A strong place gets 3–6 main photos, a minor one 1–3; drop (\"drop\": true) "
            "only empty or dull sections whose day is already covered by another section. Snow scenes and views over the "
            "city/landscape are welcome — include the good ones. \"MUST include\" labels are mandatory main photos "
            "(they do count toward variety, not toward a limit). Section titles name the place or what we did there "
            "(2–4 words), not the day.")
# альбом по плану пользователя (select.py plan): части с названиями, в них главы
PART_RULE = ("- The owner set the STRUCTURE: the album has named parts (\"part\" in the context), each part has sections. "
             "Keep EVERY section (never \"drop\"), each with 2–6 main photos (a strong place 4–6, a minor one 2–3). "
             "Follow the section \"hint\" from the owner for what it is about. \"MUST include\" labels are mandatory main "
             "photos. Section titles name the place or what we did there (2–4 words), not the part.")
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

def cmd_prepare(path, keep_all=False):
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
    py("select.py", root, "sections", "--sections", str(DRAFT), *(["--keep-all"] if keep_all else []))

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
        day = (f"part «{s['part']}», " if "part" in s else f"day {s['day']}, ") if "day" in s else ""
        lines.append(f"s{s['n']:02d}: {day}{s['start'][5:16].replace('T', ' ')}–{s['end'][11:16]}, near: {pl}")
        if s.get("hint"):
            lines.append("  hint from the owner: " + s["hint"])
        if s.get("must"):
            lines.append("  MUST include as main photos (the owner asked for them): " + ", ".join(map(str, s["must"])))
        lines.append("  photos: " + "; ".join(f"{i}={d(f)} ({sc.get(f, {}).get('score', '?')}/{sc.get(f, {}).get('fun', 0)})"
                                            for i, f in enumerate(s["candidates"], 1)))
        if s.get("fun_candidates"):
            lines.append("  fun: " + "; ".join(f"F{i}={d(f)}" for i, f in enumerate(s["fun_candidates"], 1)))
        if s.get("way_candidates"):
            lines.append("  on the way: " + "; ".join(f"W{i}={d(f)}" for i, f in enumerate(s["way_candidates"], 1)))
        if s.get("cand_videos"):
            lines.append("  videos: " + "; ".join(f"V{i}={d(f)} ({meta[f]['duration']}s)"
                                                for i, f in enumerate(s["cand_videos"], 1)))
        for fs in s.get("fun_series", []):
            lines.append(f"  already included funny series: «{fs['caption']}»")
    p = write_prompt(root, "pass2.txt", tpl(
        "pass2.md", TRIP=trip(root), SECTION_RULE=(PART_RULE if any("part" in s for s in secs) else DAY_RULE if any("day" in s for s in secs)
                                          else SECTION_RULE.format(SECTIONS)),
        CONTEXT="\n".join(lines),
        SHEETS=", ".join(os.path.join(root, s["sheet"]) for s in secs), OUT=os.path.join(work, "picks.json")))
    print(f"\nПРОМПТ ПРОХОДА 2:\n  sonnet  {p}")


# ── extras: таблички и лица ──────────────────────────────────────────────────

def cmd_extras(root):
    work = os.path.join(root, "work")
    py("build.py", root)                                 # нужен album.json
    py("extras.py", root, "signs")
    t, prompts = trip(root), []
    signs = sorted(glob.glob(os.path.join(work, "extras", "sign_*.jpg")))
    if signs:
        prompts.append(("sonnet", write_prompt(root, "signs.txt", tpl(
            "signs.md", TRIP=t, SHEETS=", ".join(signs), OUT=os.path.join(work, "signs.json")))))
    print("\nПРОМПТЫ ДОПОЛНЕНИЙ (параллельно):")
    for model, p in prompts:
        print(f"  {model:7} {p}")


def cmd_people(root):
    if not os.path.exists(os.path.join(root, "work", "faces.npz")):
        py("faces.py", root, "scan")
    py("faces.py", root, "learn")
    py("faces.py", root, "auto")


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


LIMIT_MB = 1024                # GitHub Pages: сайт ≤ 1 ГБ; репозиторий тоже лучше держать < 1 ГБ
WARN_SHARE = 0.8               # больше 80% — предлагаем новый репозиторий


def cmd_usage():
    """Сколько занято: опубликованный сайт (файлы в HEAD) и весь репозиторий с историей (по API GitHub)."""
    out = subprocess.run(["git", "-C", REPO_DIR, "ls-tree", "-r", "-l", "HEAD"], capture_output=True, text=True, check=True).stdout
    site, by = 0, {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        size = meta.split()[3]
        if size.isdigit():
            site += int(size)
            by[path.split("/")[0]] = by.get(path.split("/")[0], 0) + int(size)
    repo = None
    try:
        repo = gh("", open(os.path.join(BASE, "token.txt")).read().strip())["size"] * 1024
    except Exception as e:
        print("размер репозитория по API не получен:", e)
    mb = lambda b: b / 1048576
    print(f"сайт (Pages): {mb(site):.0f} МБ из {LIMIT_MB} ({mb(site) / LIMIT_MB:.0%})")
    if repo is not None:
        print(f"репозиторий с историей: {mb(repo):.0f} МБ из {LIMIT_MB} ({mb(repo) / LIMIT_MB:.0%})")
    print("  " + ", ".join(f"{k} {mb(v):.0f}" for k, v in sorted(by.items(), key=lambda x: -x[1]) if v > 1048576))
    worst = max(site, repo or 0) / 1048576 / LIMIT_MB
    if worst > WARN_SHARE:
        print(f"ВНИМАНИЕ: занято {worst:.0%} (> {WARN_SHARE:.0%}) — предложи пользователю новый репозиторий")
    return worst


def cmd_push():
    """Push в origin/master; токен из token.txt идёт заголовком и не пишется в .git/config."""
    token = open(os.path.join(BASE, "token.txt")).read().strip()
    auth = "Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    for attempt in range(3):            # большой пуш иногда обрывается — повторяем; токен в ошибку не печатаем
        r = subprocess.run(["git", "-C", REPO_DIR, "-c", "http.extraHeader=" + auth, "push", "-q", "-u", "origin", "master"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return
        print(f"push не прошёл (попытка {attempt + 1}): {r.stderr.strip()[-300:]}", flush=True)
        time.sleep(10)
    sys.exit("push не прошёл три раза; коммит есть локально — повторить: python3 tools/album.py push")


def build_index():
    """Личный индекс: все опубликованные альбомы со ссылками и ключами, под своим ключом."""
    roots = [os.path.dirname(os.path.dirname(p)) for p in glob.glob(os.path.join(BASE, "*", "work", "publish.json"))]
    roots += [os.path.dirname(os.path.dirname(p)) for p in glob.glob(os.path.join(BASE, "phone-index", "*", "work", "publish.json"))]
    media = os.path.join(INDEX_ROOT, "album", "media")
    shutil.rmtree(media, ignore_errors=True)
    os.makedirs(media)
    os.makedirs(os.path.join(INDEX_ROOT, "work"), exist_ok=True)
    albums = []
    for root in sorted(set(roots), key=lambda r: os.path.getmtime(os.path.join(r, "work", "access.key")) if os.path.exists(os.path.join(r, "work", "access.key")) else 0, reverse=True):
        kp = os.path.join(root, "work", "access.key")
        if os.path.abspath(root) == INDEX_ROOT or not os.path.exists(kp):
            continue
        a = json.load(open(os.path.join(root, "album", "album.json"), encoding="utf-8"))
        slug = json.load(open(os.path.join(root, "work", "publish.json")))["slug"]
        key = open(kp).read().strip()
        src = os.path.join(root, "album", "media")
        if a.get("chapters"):
            ids = [a["cover"]] + [it["id"] for c in a["chapters"] for it in c["items"] if it.get("hero") and it["id"] != a["cover"]][:1]
            files = [i + ".jpg" for i in ids]
        else:
            files = sorted(os.listdir(src))[:2]
        pics = []
        for i, f in enumerate(files):
            name = f"{slug}_{i}.jpg"
            with Image.open(os.path.join(src, f)) as im:
                im = im.convert("RGB")
                im.thumbnail((640, 640))
                im.save(os.path.join(media, name), quality=78)
            pics.append(name)
        albums.append({"title": a["title"], "dates": a["dates"], "slug": slug, "key": key,
                       "url": f"{PAGES}/{slug}/#k={key}", "pics": pics})
    json.dump({"template": "albums.html", "title": "Все альбомы", "dates": f"{len(albums)}", "albums": albums},
              open(os.path.join(INDEX_ROOT, "album", "album.json"), "w", encoding="utf-8"), ensure_ascii=False)
    subprocess.run([sys.executable, os.path.join(HERE, "publish.py"), INDEX_ROOT, "--repo", REPO_DIR, "--slug", INDEX_SLUG],
                   check=True, capture_output=True, text=True)


def mark_done(n, slug):
    """Пометить вариант n на странице выбора как готовый альбом и обновить страницу при следующей публикации."""
    vote = os.path.join(BASE, "phone-index", "vote")
    p = os.path.join(vote, "album", "album.json")
    a = json.load(open(p, encoding="utf-8"))
    key = open(os.path.join(root_of(slug), "work", "access.key")).read().strip()
    for c in a["candidates"]:
        if c["n"] == n:
            c["done"] = f"{PAGES}/{slug}/#k={key}"
    json.dump(a, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def root_of(slug):
    for s in glob.glob(os.path.join(BASE, "*", "work", "publish.json")):
        if json.load(open(s))["slug"] == slug:
            return os.path.dirname(os.path.dirname(s))
    sys.exit(f"нет альбома {slug}")


def cmd_publish(root, slug=None):
    saved = os.path.join(root, "work", "publish.json")    # slug закрепляется при первой публикации
    if not slug and os.path.exists(saved):
        slug = json.load(open(saved))["slug"]
    slug = slug or slugify(os.path.basename(os.path.abspath(root)))
    # папка сайта <repo>/<slug> не должна совпадать с рабочей папкой альбома: иначе git add заберёт оригиналы
    site = os.path.abspath(os.path.join(REPO_DIR, slug))
    if site == os.path.abspath(root) or any(os.path.exists(os.path.join(site, d)) for d in ("work", "src")):
        sys.exit(f"slug «{slug}» совпадает с папкой с исходниками ({site}); задай другой: --slug <имя>")
    json.dump({"slug": slug}, open(saved, "w"))
    token = open(os.path.join(BASE, "token.txt")).read().strip()
    if os.path.abspath(root) != INDEX_ROOT:              # стоп-лист: людей из never.npz не публикуем
        py("faces.py", root, "guard")
    out = subprocess.run([sys.executable, os.path.join(HERE, "publish.py"), root, "--repo", REPO_DIR, "--slug", slug],
                         check=True, capture_output=True, text=True).stdout
    key = re.search(r"^KEY (\S+)", out, re.M).group(1)
    print(out.replace(key, "<ключ>"))
    is_index = os.path.abspath(root) == INDEX_ROOT
    if not is_index:                                     # индекс обновляется при каждой публикации
        build_index()

    g = lambda *a, **kw: subprocess.run(["git", "-C", REPO_DIR, *a], check=True, text=True, **kw)
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        g("init", "-q", "-b", "master")
        g("config", "user.name", "dimmik")
        g("config", "user.email", "dimmik@users.noreply.github.com")
        g("remote", "add", "origin", f"https://github.com/{REPO}.git")
    ign = os.path.join(REPO_DIR, ".gitignore")          # белый список: открываем папку альбома
    if f"!/{slug}/" not in open(ign).read().split():
        open(ign, "a").write(f"!/{slug}/\n")
    if not is_index and f"!/{INDEX_SLUG}/" not in open(ign).read().split():
        open(ign, "a").write(f"!/{INDEX_SLUG}/\n")
    py("backup.py", "save", "core")                      # зашифрованная копия рабочих файлов (фраза не нужна)
    g("add", "-A", "--", slug, INDEX_SLUG, "index.html", "robots.txt", ".nojekyll", ".gitignore", "backup")
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
    cmd_usage()                                     # перед новым альбомом — сколько места осталось
    print()
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
    ap.add_argument("cmd", choices=["prepare", "shortlist", "build", "publish", "status", "find", "push", "extras", "people", "name", "index", "done",
                                       "suggest", "usage"])
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("pairs", nargs="*")                  # name: G1=Маша/Masha …
    ap.add_argument("--slug")
    ap.add_argument("--keep-all", action="store_true", help="prepare: не выбрасывать мелкие эпизоды (подборка за годы)")
    ap.add_argument("--title")
    a = ap.parse_args()
    a.path = os.path.abspath(a.path)
    {"prepare": lambda: cmd_prepare(a.path, a.keep_all), "shortlist": lambda: cmd_shortlist(a.path),
     "build": lambda: cmd_build(a.path, a.title), "publish": lambda: cmd_publish(a.path, a.slug),
     "status": lambda: cmd_status(a.path), "find": cmd_find, "push": cmd_push,
     "extras": lambda: cmd_extras(a.path), "index": lambda: (build_index(), cmd_publish(INDEX_ROOT, INDEX_SLUG)),
     "done": lambda: mark_done(int(os.path.basename(a.path)), a.pairs[0]), "people": lambda: cmd_people(a.path), "name": lambda: py("faces.py", a.path, "name", *a.pairs),
     "suggest": lambda: py("extras.py", a.path, "suggest"), "usage": cmd_usage}[a.cmd]()


if __name__ == "__main__":
    main()
