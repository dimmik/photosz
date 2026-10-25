#!/usr/bin/env python3
"""Шаг 3: сборка альбома. Без ИИ.

  python3 tools/build.py "<папка альбома>" [--title "..."]

Читает work/meta.json, work/sections.json, work/picks.json (выбор + подписи) и
необязательный work/overrides.json (ручные правки, имеют приоритет над picks.json).
Пишет <папка>/album/index.html, album/album.json, album/media/*.
"""
import argparse, html, json, os, subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PIL import Image, ImageOps

try:                                     # HEIC/HEIF (iPhone)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

FULL, THUMB = 2048, 720
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
TPL = os.path.join(os.path.dirname(__file__), "template.html")


MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]


def fmt_day_en(t):
    return f"{MONTHS_EN[t.month - 1]} {t.day}"


def en(d, key="caption"):
    """Английский вариант текстового поля (тексты двуязычные: <поле> и <поле>_en)."""
    return {key + "_en": d[key + "_en"]} if d.get(key + "_en") else {}


def fmt_day(t):
    return f"{t.day} {MONTHS[t.month - 1]}"


def photo_job(src, dst_dir, fid):
    full, thumb = os.path.join(dst_dir, fid + ".jpg"), os.path.join(dst_dir, fid + "_t.jpg")
    if not (os.path.exists(full) and os.path.exists(thumb)):
        im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")   # EXIF (и GPS) не переносим
        im.thumbnail((FULL, FULL), Image.LANCZOS)
        im.save(full, quality=84, optimize=True, progressive=True)
        im.thumbnail((THUMB, THUMB), Image.LANCZOS)
        im.save(thumb, quality=80, optimize=True, progressive=True)
    with Image.open(full) as im:
        return im.size


def video_job(src, dst_dir, fid):
    out, poster = os.path.join(dst_dir, fid + ".mp4"), os.path.join(dst_dir, fid + "_p.jpg")
    if not os.path.exists(out):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", "scale='min(1280,iw)':-2",
                        "-c:v", "libx264", "-crf", "26", "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-map_metadata", "-1",
                        out], check=True)
    if not os.path.exists(poster):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", out, "-frames:v", "1",
                        "-q:v", "4", poster], check=True)
    with Image.open(poster) as im:
        return im.size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--title", default=None)
    a = ap.parse_args()
    root, work = a.root, os.path.join(a.root, "work")
    meta = {r["id"]: r for r in json.load(open(os.path.join(work, "meta.json")))}
    sections = json.load(open(os.path.join(work, "sections.json")))
    picks = json.load(open(os.path.join(work, "picks.json")))
    ov_p = os.path.join(work, "overrides.json")
    overrides = json.load(open(ov_p)) if os.path.exists(ov_p) else {}
    media = os.path.join(root, "album", "media")
    os.makedirs(media, exist_ok=True)

    chapters, jobs = [], []
    for s in sections:
        key = f"s{s['n']:02d}"
        p = {**picks.get(key, {}), **overrides.get(key, {})}
        if p.get("drop") or not p.get("photos"):
            continue
        items = []
        for ph in p["photos"]:
            fid = ph.get("id") or s["candidates"][ph["n"] - 1]
            items.append({"id": fid, "type": "photo", "caption": ph.get("caption", ""), **en(ph),
                          "hero": ph.get("n") == p.get("hero") or ph.get("id") == p.get("hero")})
        for ph in p.get("fun", []):                         # «моменты» — сверх основного лимита
            fid = ph.get("id") or (s["candidates"][ph["id_main"] - 1] if "id_main" in ph
                                   else s["fun_candidates"][ph["n"] - 1])
            if any(it["id"] == fid for it in items):             # уже среди основных — просто помечаем
                items = [it for it in items if it["id"] != fid]
            items.append({"id": fid, "type": "photo", "caption": ph.get("caption", ""), **en(ph), "fun": True})
        for v in p.get("videos", []):
            fid = v.get("id") or s.get("cand_videos", s["videos"])[v["v"] - 1]
            items.append({"id": fid, "type": "video", "caption": v.get("caption", ""), **en(v), "fun": bool(v.get("fun"))})
        # смешная серия (из sections.json, её выбрал проход по сериям) — сверх лимита, отдельной строкой
        if p.get("series", True):
            for fs in s.get("fun_series", []):
                items = [it for it in items if it["id"] not in fs["ids"]]
                for i, fid in enumerate(fs["ids"]):
                    items.append({"id": fid, "type": "photo", "caption": fs["caption"], **en(fs), "fun": True,
                                  "series": fs["key"], "sidx": i})
        # герой открывает главу, остальное — по времени
        items.sort(key=lambda it: (not it.get("hero"), meta[it["id"]]["time"]))
        for it in items:
            src = os.path.join(root, "src", meta[it["id"]]["file"])
            fn = photo_job if it["type"] == "photo" else video_job
            jobs.append((it, fn, src))
        t0 = datetime.fromisoformat(s["start"])
        chapters.append({"title": p.get("title", ""), "desc": p.get("desc", ""), "day": fmt_day(t0),
                         **en(p, "title"), **en(p, "desc"), "day_en": fmt_day_en(t0),
                         "time": t0.strftime("%H:%M"), "place": p.get("place") or place_label(s["place"]),
                         "place_en": p.get("place_en") or place_label(s.get("place_en") or s["place"]),
                         "items": items})

    with ThreadPoolExecutor(max_workers=os.cpu_count()) as ex:
        sizes = list(ex.map(lambda j: j[1](j[2], media, j[0]["id"]), jobs))
    for (it, _, _), (w, h) in zip(jobs, sizes):
        m = meta[it["id"]]
        it.update(w=w, h=h, time=datetime.fromisoformat(m["time"]).strftime("%H:%M"))
        if it["type"] == "video":
            it["duration"] = m["duration"]

    # медиа, которых больше нет в альбоме, удаляем (иначе они уедут и в публикацию)
    keep = {it["id"] for it, _, _ in jobs}
    for f in os.listdir(media):
        stem = f.rsplit(".", 1)[0]                       # <id>.jpg, <id>_t.jpg, <id>_p.jpg, <id>.mp4
        if stem not in keep and stem.rsplit("_", 1)[0] not in keep:
            os.remove(os.path.join(media, f))

    t_first = datetime.fromisoformat(sections[0]["start"])
    t_last = datetime.fromisoformat(sections[-1]["end"])
    dates = (f"{t_first.day}–{fmt_day(t_last)} {t_last.year}" if t_first.month == t_last.month
             else f"{fmt_day(t_first)} – {fmt_day(t_last)} {t_last.year}")
    dates_en = (f"{fmt_day_en(t_first)}–{t_last.day}, {t_last.year}" if t_first.month == t_last.month
                else f"{fmt_day_en(t_first)} – {fmt_day_en(t_last)}, {t_last.year}")
    cover = overrides.get("cover") or picked_cover(picks, sections) or pick_cover(chapters)
    # обложка не повторяется в главе (если в главе останется ≥ 3 фото)
    for c in chapters:
        ph = [it for it in c["items"] if it["type"] == "photo"]
        if any(it["id"] == cover for it in ph) and len(ph) > 3:
            c["items"] = [it for it in c["items"] if it["id"] != cover]
            if not any(it.get("hero") for it in c["items"]):
                land = [it for it in c["items"] if it["type"] == "photo" and it["w"] > it["h"]]
                if land:
                    max(land, key=lambda it: it["w"] * it["h"])["hero"] = True
                    c["items"].sort(key=lambda it: (not it.get("hero"), it["time"]))
    album = {"title": a.title or picks.get("album_title") or os.path.basename(os.path.abspath(root)), "dates": dates,
             "title_en": picks.get("album_title_en") or a.title or picks.get("album_title") or "", "dates_en": dates_en,
             "cover": cover,
             "chapters": chapters}
    with open(os.path.join(root, "album", "album.json"), "w") as f:
        json.dump(album, f, ensure_ascii=False, indent=1)
    page = open(TPL, encoding="utf-8").read()
    page = page.replace("{{TITLE}}", html.escape(album["title"]))
    page = page.replace("{{CFG}}", json.dumps({"mode": "plain", "album": album},
                                              ensure_ascii=False).replace("</", "<\\/"))
    with open(os.path.join(root, "album", "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    n_ph = sum(it["type"] == "photo" for c in chapters for it in c["items"])
    n_v = sum(it["type"] == "video" for c in chapters for it in c["items"])
    print(f"{len(chapters)} разделов, {n_ph} фото, {n_v} видео → {os.path.join(root, 'album', 'index.html')}")


def place_label(p):
    city = p.get("city") or p.get("town") or ""
    spot = p.get("tourism") or p.get("leisure") or p.get("neighbourhood") or p.get("suburb") or ""
    return " · ".join(x for x in (spot, city) if x)


def picked_cover(picks, sections):
    """Обложка, выбранная ИИ: picks["cover"] = {"s": номер раздела, "n": номер кандидата}."""
    c = picks.get("cover")
    if not c:
        return None
    s = next((s for s in sections if s["n"] == c["s"]), None)
    return s and s["candidates"][c["n"] - 1]


def pick_cover(chapters):
    """Обложка: самое широкое горизонтальное фото альбома."""
    best = max((it for c in chapters for it in c["items"] if it["type"] == "photo"),
               key=lambda it: it["w"] / it["h"] if it["w"] / it["h"] < 2.2 else 0)
    return best["id"]


if __name__ == "__main__":
    main()
