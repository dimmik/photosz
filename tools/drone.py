#!/usr/bin/env python3
"""Альбом «Красивые виды» из съёмки дрона (DJI). Оригиналы лежат в drone/<дата>/ и не меняются;
в альбом идут отобранные фото и короткие клипы из видео. По разделу на место.

  python3 tools/drone.py scan  [drone]   места, миниатюры, кадры из видео (по LRF), листы и промпт выбора
      → ИИ (sonnet) пишет work/drone_picks.json
  python3 tools/drone.py clips [drone]   нарезка клипов из 4K, листы проверки «нет людей» и её промпт
      → ИИ (sonnet, кусками) пишут work/people_check_<i>.json; кадры с людьми — сократить клип
        (work/drone_overrides.json) / убрать фото, повторить clips
  python3 tools/drone.py album [drone]   src/ (ссылки на фото и клипы), meta/sections/picks для build.py
  затем album.py build drone → album.py publish drone

Новые полёты — новая папка drone/<дата>/: scan подхватит её, старые выборы останутся (ключи — id мест).
"""
import glob, importlib.util, json, math, os, re, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("sel", os.path.join(HERE, "select.py"))   # имя select занято stdlib
_sel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sel)
font = _sel.font

STEP = 6            # кадр из видео каждые 6 с на листе выбора
CHECK_STEP = 1.5    # и каждые 1,5 с на листе проверки клипа
GAP_H = 3           # новое место: пауза больше 3 ч…
PLACE_KM = 5        # …или дальше 5 км от предыдущего кадра
TILE = (400, 225)
NAME = re.compile(r"DJI_(\d{14})_(\d{4})")


def tm(f):
    return datetime.strptime(NAME.search(f)[1], "%Y%m%d%H%M%S")


def km(a, b):
    return 6371 * math.hypot(math.radians(a[0] - b[0]), math.radians(a[1] - b[1]) * math.cos(math.radians(a[0])))


def exif_gps(path):
    try:
        g = Image.open(path).getexif().get_ifd(0x8825)
        lat = sum(float(v) / d for v, d in zip(g[2], (1, 60, 3600))) * (-1 if g.get(1) == "S" else 1)
        lon = sum(float(v) / d for v, d in zip(g[4], (1, 60, 3600))) * (-1 if g.get(3) == "W" else 1)
        return (lat, lon) if lat or lon else None
    except Exception:
        return None


def srt_gps(path):
    if not os.path.exists(path):
        return None
    pts = [(float(a), float(b)) for a, b in re.findall(r"latitude: ([\d.\-]+)\] \[longitude: ([\d.\-]+)", open(path).read())]
    pts = [p for p in pts if p != (0.0, 0.0)]
    return pts[len(pts) // 2] if pts else None


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def geocode(lat, lon, cache, lang):
    key = f"{lat:.3f},{lon:.3f}:{lang}"
    if key not in cache:
        q = urllib.parse.urlencode({"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 14, "accept-language": lang})
        req = urllib.request.Request("https://nominatim.openstreetmap.org/reverse?" + q,
                                     headers={"User-Agent": "photo-alb/0.1 (personal album)"})
        r = json.load(urllib.request.urlopen(req, timeout=20))
        cache[key] = {"name": r.get("name") or "", **{k: v for k, v in r.get("address", {}).items()
                      if k in ("natural", "water", "leisure", "tourism", "hamlet", "village", "suburb", "town",
                               "city", "municipality", "county", "state", "country")}}
        time.sleep(1.1)
    return cache[key]


def tile(path, text):
    im = Image.open(path).convert("RGB")
    im.thumbnail(TILE)
    c = Image.new("RGB", TILE, (30, 30, 30))
    c.paste(im, ((TILE[0] - im.width) // 2, (TILE[1] - im.height) // 2))
    d = ImageDraw.Draw(c)
    f = font(22)
    w = d.textlength(text, font=f)
    d.rectangle([0, 0, w + 12, 30], fill=(0, 0, 0))
    d.text((6, 2), text, fill=(255, 255, 0), font=f)
    return c


def sheets(tiles, out_prefix, cols=4, per=24):
    paths = []
    for i in range(0, len(tiles), per):
        chunk = tiles[i:i + per]
        rows = (len(chunk) + cols - 1) // cols
        sh = Image.new("RGB", (cols * TILE[0] + (cols - 1) * 4, rows * TILE[1] + (rows - 1) * 4), (255, 255, 255))
        for j, t in enumerate(chunk):
            sh.paste(t, ((j % cols) * (TILE[0] + 4), (j // cols) * (TILE[1] + 4)))
        p = f"{out_prefix}_{i // per + 1}.jpg"
        sh.save(p, quality=82)
        paths.append(p)
    return paths


def mmss(s):
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def cmd_scan(root):
    work = os.path.join(root, "work")
    for d in ("thumbs", "vf", "sheets", "prompts"):
        os.makedirs(os.path.join(work, d), exist_ok=True)
    items = []
    for f in sorted(glob.glob(os.path.join(root, "[0-9]*", "DJI_*"))):
        ext = f.rsplit(".", 1)[-1].upper()
        if ext not in ("JPG", "MP4"):
            continue
        fid = os.path.basename(f).rsplit(".", 1)[0]
        r = {"id": fid, "path": os.path.relpath(f, root), "t": tm(f).isoformat(), "type": "photo" if ext == "JPG" else "video"}
        if ext == "JPG":
            r["gps"] = exif_gps(f)
            th = os.path.join(work, "thumbs", fid + ".jpg")
            if not os.path.exists(th):
                with Image.open(f) as im:
                    im.draft("RGB", (800, 800))
                    im = im.convert("RGB")
                    im.thumbnail((640, 640))
                    im.save(th, quality=80)
        else:
            base = f[:-4]
            r["gps"] = srt_gps(base + ".SRT")
            low = base + ".LRF" if os.path.exists(base + ".LRF") else f
            r["dur"] = round(duration(low), 1)
            done = glob.glob(os.path.join(work, "vf", fid + "_*.jpg"))
            if not done:   # кадры из прокси LRF (720p) — быстро, без декодирования 4K
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", low, "-vf", f"fps=1/{STEP},scale=640:-2",
                                "-start_number", "0", os.path.join(work, "vf", fid + "_%04d.jpg")], check=True)
        items.append(r)
    items.sort(key=lambda r: r["t"])
    # места: пауза > GAP_H или переезд > PLACE_KM; пустой GPS (видео, снятое на земле) — к соседу
    places, cur, last = [], [], None
    for r in items:
        t = datetime.fromisoformat(r["t"])
        g = r.get("gps")
        if cur and (t - datetime.fromisoformat(cur[-1]["t"]) > timedelta(hours=GAP_H) or (g and last and km(g, last) > PLACE_KM)):
            places.append(cur)
            cur = []
        cur.append(r)
        last = g or last
    if cur:
        places.append(cur)
    gp = os.path.join(work, "geo.json")
    cache = json.load(open(gp)) if os.path.exists(gp) else {}
    out = []
    for n, pl in enumerate(places, 1):
        gs = [r["gps"] for r in pl if r.get("gps")]
        c = (sorted(g[0] for g in gs)[len(gs) // 2], sorted(g[1] for g in gs)[len(gs) // 2]) if gs else (0, 0)
        key = f"{pl[0]['t'][:10]}_{c[0]:.2f}_{c[1]:.2f}"          # устойчивый id места (новые полёты не сдвигают старые)
        addr, addr_en = (geocode(*c, cache, "ru"), geocode(*c, cache, "en")) if gs else ({}, {})
        tiles, photos, videos = [], [], []
        for r in pl:
            if r["type"] == "photo":
                photos.append({"l": f"P{len(photos) + 1}", "id": r["id"]})
                tiles.append(tile(os.path.join(work, "thumbs", r["id"] + ".jpg"), photos[-1]["l"]))
        for r in pl:
            if r["type"] == "video" and r["dur"] >= 4:                  # 2–3 с — случайные нажатия
                videos.append({"l": f"V{len(videos) + 1}", "id": r["id"], "dur": r["dur"]})
                for i, fr in enumerate(sorted(glob.glob(os.path.join(work, "vf", r["id"] + "_*.jpg")))):
                    tiles.append(tile(fr, f"{videos[-1]['l']} {mmss(i * STEP)}"))
        sh = sheets(tiles, os.path.join(work, "sheets", f"place{n:02d}")) if tiles else []
        out.append({"n": n, "key": key, "start": pl[0]["t"], "end": pl[-1]["t"], "lat": round(c[0], 5), "lon": round(c[1], 5),
                    "addr": addr, "addr_en": addr_en, "photos": photos, "videos": videos, "sheets": sh})
        print(f"место {n:2} {pl[0]['t'][:16]} {len(photos):3} фото {len(videos):2} видео  "
              f"{addr.get('name') or ''} · {addr.get('village') or addr.get('town') or addr.get('city') or addr.get('municipality') or ''}")
    json.dump(cache, open(gp, "w"), ensure_ascii=False, indent=1)
    json.dump({"items": items, "places": out}, open(os.path.join(work, "drone.json"), "w"), ensure_ascii=False, indent=1)
    write_pick_prompt(root, out)


def write_pick_prompt(root, places, max_sheets=16):
    """Промпты выбора кусками (не больше max_sheets листов на агента) → work/drone_picks_<i>.json."""
    work = os.path.join(root, "work")
    for f in glob.glob(os.path.join(work, "prompts", "drone_pick*.txt")):
        os.remove(f)
    chunks, cur = [], []
    for p in places:
        if cur and sum(len(x["sheets"]) for x in cur) + len(p["sheets"]) > max_sheets:
            chunks.append(cur)
            cur = []
        cur.append(p)
    chunks.append(cur)
    print("\nПРОМПТЫ ВЫБОРА (параллельно):")
    for i, ch in enumerate(chunks, 1):
        print(f"  sonnet  {pick_prompt(work, ch, i)}")


def pick_prompt(work, places, i):
    lines = []
    for p in places:
        a = p["addr"]
        where = ", ".join(x for x in [a.get("name"), a.get("natural"), a.get("water"), a.get("village") or a.get("hamlet"),
                                     a.get("town") or a.get("city") or a.get("municipality"), a.get("county")] if x)
        vids = "; ".join(f"{v['l']} {mmss(v['dur'])}" for v in p["videos"]) or "нет"
        lines.append(f"{p['key']}: {p['start'][:16].replace('T', ' ')}, GPS {p['lat']},{p['lon']}, рядом: {where}\n"
                     f"  photos: {len(p['photos'])} (P1..P{len(p['photos'])}); videos: {vids}\n  sheets: {', '.join(p['sheets'])}")
    out = os.path.join(work, f"drone_picks_{i}.json")
    txt = f"""You are building a photo album of drone views ("beautiful views"): one section per place, each with the place, the date and a short description. Work cheaply: use the Read tool ONLY on the sheets listed below (one Read each), no other files, no shell. Then write one JSON file and finish.

Sheets are grids of tiles. "P3" tiles are drone photos. "V2 1:24" tiles are frames of video V2 at 1 min 24 s (one frame every {STEP} s, in time order).

Places:
{chr(10).join(lines)}

For EACH place:
- "title" / "title_en": the place, 2–4 words (use the nearby names above: a river, lake, village, monastery…; say what is seen, don't invent names you can't support).
- "desc" / "desc_en": one short sentence on what the view shows (Russian / natural English).
- "photos": the 3–8 most beautiful photos, no near-duplicates: [{{"l": "P3", "caption": "", "caption_en": ""}}]; caption optional, ≤ 6 words, only if it adds something.
- "hero": the label of the single best wide photo of the place.
- "clips": 1–2 of the best video moments, each 8–20 s, from ONE video, a smooth stretch (no take-off/landing, no ground, no spinning): [{{"v": "V2", "from": 84, "to": 102, "caption": "", "caption_en": ""}}] (seconds; use the frame times you see).
- "people": EVERY photo label and every video frame (like "V2 1:24") where any person is visible, even tiny (on a beach, a path, a boat, a field). Do NOT choose photos with people, and clips must avoid the times around such frames (± {STEP} s). If unsure — count it as a person.
Also "cover": the label of the most striking photo among these places, as "<place key>/P3".

Write with the Write tool to {out}:
{{"cover": "...", "places": {{"<place key>": {{"title": "...", "title_en": "...", "desc": "...", "desc_en": "...", "hero": "P3", "photos": [...], "clips": [...], "people": [...]}}, ...}}}}
Reply just "done N" and the labels with people.
"""
    p = os.path.join(work, "prompts", f"drone_pick_{i}.txt")
    open(p, "w").write(txt)
    return p


def load(root):
    work = os.path.join(root, "work")
    d = json.load(open(os.path.join(work, "drone.json")))
    picks = {"places": {}}
    for f in sorted(glob.glob(os.path.join(work, "drone_picks_*.json"))):   # куски выбора; обложка — из первого
        part = json.load(open(f))
        picks["places"].update(part.get("places", {}))
        picks.setdefault("cover", part.get("cover"))
    cp = os.path.join(work, "cover.txt")                 # обложку можно задать руками: <ключ места>/P3
    if os.path.exists(cp):
        picks["cover"] = open(cp).read().strip()
    ov_p = os.path.join(work, "drone_overrides.json")    # правки руками: {"<key>": {"clips": [...], "photos": [...]}}
    ov = json.load(open(ov_p)) if os.path.exists(ov_p) else {}
    for k, v in ov.items():
        picks["places"].setdefault(k, {}).update(v)
    return work, d, picks


def clip_id(vid, a):
    return f"{vid}_c{int(a):03d}"


def cmd_clips(root):
    work, d, picks = load(root)
    cdir, chk = os.path.join(root, "clips"), os.path.join(work, "check")
    os.makedirs(cdir, exist_ok=True)
    os.makedirs(chk, exist_ok=True)
    byid = {r["id"]: r for r in d["items"]}
    tiles, want = [], set()
    for p in d["places"]:
        pk = picks["places"].get(p["key"], {})
        vids = {v["l"]: v for v in p["videos"]}
        for c in pk.get("clips", []):
            v = vids[c["v"]]
            a, b = max(0, float(c["from"])), min(float(c["to"]), v["dur"])
            cid = clip_id(v["id"], a)
            want.add(cid)
            out = os.path.join(cdir, cid + ".mp4")
            if not os.path.exists(out):     # из оригинала 4K: 1920 px, без звука, с запасом качества (build пережмёт)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.2f}", "-i", os.path.join(root, byid[v["id"]]["path"]),
                                "-t", f"{b - a:.2f}", "-vf", "scale=1920:-2", "-an", "-c:v", "libx264", "-crf", "19",
                                "-preset", "medium", "-pix_fmt", "yuv420p", "-map_metadata", "-1", out], check=True)
            fr = os.path.join(chk, cid)
            os.makedirs(fr, exist_ok=True)
            if not os.listdir(fr):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out, "-vf", f"fps=1/{CHECK_STEP},scale=960:-2",
                                os.path.join(fr, "%03d.jpg")], check=True)
            for i, f in enumerate(sorted(glob.glob(os.path.join(fr, "*.jpg")))):
                tiles.append((f, f"{p['key']} {c['v']} {a + i * CHECK_STEP:.0f}s"))
        for ph in pk.get("photos", []):
            fid = next(x["id"] for x in p["photos"] if x["l"] == ph["l"])
            tiles.append((os.path.join(work, "thumbs", fid + ".jpg"), f"{p['key']} {ph['l']}"))
    for f in os.listdir(cdir):                  # клипы, которых больше нет в выборе
        if f[:-4] not in want:
            os.remove(os.path.join(cdir, f))
    # лист проверки: крупнее (3 в ряд, 640 px), чтобы маленькие фигурки было видно
    global TILE
    TILE = (640, 360)
    shs = sheets([tile(f, t) for f, t in tiles], os.path.join(chk, "check"), cols=3, per=12)
    for f in glob.glob(os.path.join(chk, "check_*.jpg")):
        if f not in shs:
            os.remove(f)
    for f in glob.glob(os.path.join(work, "prompts", "people_check*.txt")) + glob.glob(os.path.join(work, "people_check*.json")):
        os.remove(f)                            # старые результаты проверки к новым клипам не относятся
    print(f"клипов: {len(want)}, плиток на проверку: {len(tiles)}, листов: {len(shs)}\n\nПРОМПТЫ ПРОВЕРКИ (параллельно):")
    for i in range(0, len(shs), 10):            # не больше 10 листов на агента
        n = i // 10 + 1
        out = os.path.join(work, f"people_check_{n}.json")
        txt = f"""Safety check for a public photo album of drone views: it must contain NO people. Use the Read tool ONLY on these sheets (one Read each), no other files, no shell; then write one JSON file and finish.

Sheets: {", ".join(shs[i:i + 10])}
Each tile has a yellow label: "<place> V2 34s" (a frame of a video clip at 34 s) or "<place> P3" (a photo).
Look closely at every tile, including tiny figures on beaches, paths, roads, boats, fields, water, and people near the camera's edge. Cars and buildings alone are fine.

Write with the Write tool to {out}:
{{"people": [{{"label": "<exact label>", "where": "3–6 words where in the frame"}}, ...]}}
Include every tile with a person or something that might be a person. Reply just "done N".
"""
        p = os.path.join(work, "prompts", f"people_check_{n}.txt")
        open(p, "w").write(txt)
        print(f"  sonnet  {p}")


def cmd_album(root):
    work, d, picks = load(root)
    src = os.path.join(root, "src")
    os.makedirs(src, exist_ok=True)
    for f in os.listdir(src):
        os.remove(os.path.join(src, f))
    byid = {r["id"]: r for r in d["items"]}
    sections, bp, times = [], {}, {}
    for p in d["places"]:
        pk = picks["places"].get(p["key"])
        if not pk or pk.get("drop"):
            continue
        n = len(sections) + 1
        ph_ids, v_ids, sp = [], [], {"title": pk["title"], "title_en": pk.get("title_en", ""),
                                      "desc": pk.get("desc", ""), "desc_en": pk.get("desc_en", ""), "photos": [], "videos": []}
        lab = {x["l"]: x["id"] for x in p["photos"]}
        for ph in pk.get("photos", []):
            fid = lab[ph["l"]]
            orig = os.path.abspath(os.path.join(root, byid[fid]["path"]))
            if ph.get("crop"):                   # [x0, y0, x1, y1] в долях: отрезать край (например, с людьми)
                os.makedirs(os.path.join(root, "edits"), exist_ok=True)
                orig = os.path.abspath(os.path.join(root, "edits", fid + ".jpg"))
                with Image.open(os.path.join(root, byid[fid]["path"])) as im:
                    x0, y0, x1, y1 = ph["crop"]
                    exif = im.info.get("exif", b"")
                    im.crop((int(x0 * im.width), int(y0 * im.height), int(x1 * im.width), int(y1 * im.height))).save(
                        orig, quality=92, exif=exif)
            os.symlink(orig, os.path.join(src, fid + ".jpg"))
            times[fid] = byid[fid]["t"]
            ph_ids.append(fid)
            sp["photos"].append({"id": fid, "caption": ph.get("caption", ""), "caption_en": ph.get("caption_en", "")})
        if pk.get("hero") in lab:
            sp["hero"] = lab[pk["hero"]]
        vids = {v["l"]: v for v in p["videos"]}
        for c in pk.get("clips", []):
            v = vids[c["v"]]
            cid = clip_id(v["id"], max(0, float(c["from"])))
            os.symlink(os.path.abspath(os.path.join(root, "clips", cid + ".mp4")), os.path.join(src, cid + ".mp4"))
            times[cid] = (datetime.fromisoformat(byid[v["id"]]["t"]) + timedelta(seconds=float(c["from"]))).isoformat()
            v_ids.append(cid)
            sp["videos"].append({"id": cid, "caption": c.get("caption", ""), "caption_en": c.get("caption_en", "")})
        a, ae = p["addr"], p["addr_en"]
        pl = lambda x: " · ".join(dict.fromkeys(v for v in [x.get("village") or x.get("hamlet") or x.get("town") or x.get("city"),
                                                            x.get("municipality") or x.get("county")] if v))
        sp["place"], sp["place_en"] = pk.get("place") or pl(a), pk.get("place_en") or pl(ae)
        ts = sorted(times[i] for i in ph_ids + v_ids)
        sections.append({"n": n, "start": ts[0], "end": ts[-1], "count": len(ts), "lat": p["lat"], "lon": p["lon"],
                         "place": {}, "photos": ph_ids, "videos": v_ids, "candidates": [], "key": p["key"]})
        bp[f"s{n:02d}"] = sp
    subprocess.run([sys.executable, os.path.join(HERE, "extract.py"), root], check=True)
    meta = json.load(open(os.path.join(work, "meta.json")))
    for r in meta:                              # время — местное, из имени файла (у клипа — от начала куска)
        r["time"] = times[r["id"]]
        src_id = re.sub(r"_c\d{3}$", "", r["id"])
        if r["type"] == "video" and byid[src_id].get("gps"):   # клип — точка полёта (из SRT)
            r["lat"], r["lon"] = byid[src_id]["gps"]
    meta.sort(key=lambda r: r["time"])
    json.dump(meta, open(os.path.join(work, "meta.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(sections, open(os.path.join(work, "sections.json"), "w"), ensure_ascii=False, indent=1)
    cov = picks.get("cover", "")
    k, _, l = cov.partition("/")
    place = next((p for p in d["places"] if p["key"] == k), None)
    cover = place and next((x["id"] for x in place["photos"] if x["l"] == l), None)
    bp.update({"album_title": "С высоты", "album_title_en": "From above"})
    json.dump(bp, open(os.path.join(work, "picks.json"), "w"), ensure_ascii=False, indent=1)
    ov_p = os.path.join(work, "overrides.json")
    ov = json.load(open(ov_p)) if os.path.exists(ov_p) else {}
    if cover:
        ov["cover"] = cover
    json.dump(ov, open(ov_p, "w"), ensure_ascii=False, indent=1)
    print(f"разделов: {len(sections)}, фото: {sum(len(s['photos']) for s in sections)}, клипов: {sum(len(s['videos']) for s in sections)}")


if __name__ == "__main__":
    cmd, root = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(HERE), "drone"))
    {"scan": cmd_scan, "clips": cmd_clips, "album": cmd_album}[cmd](root)
