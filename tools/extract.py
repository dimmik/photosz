#!/usr/bin/env python3
"""Шаг 1: метаданные + технические оценки + миниатюры. Без ИИ.

  python3 tools/extract.py "<папка альбома>"

Читает <папка>/src/*, пишет <папка>/work/meta.json, work/thumbs/*.jpg (≈400px),
work/vframes/<id>_{0..5}.jpg (кадры из видео).
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone
from multiprocessing import Pool

import imagehash
import numpy as np
from PIL import Image, ImageOps

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}

try:                                     # HEIC/HEIF (iPhone)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass
THUMB = 400
VFRAMES = (0.05, 0.22, 0.4, 0.58, 0.76, 0.94)   # доли длительности: полоса из 6 кадров на видео


def dms(v, ref):
    d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    return -d if ref in ("S", "W") else d


def sharpness(gray):
    """Дисперсия Лапласиана на ~1000px; больше — резче."""
    a = np.asarray(gray, dtype=np.float32)
    lap = a[1:-1, 2:] + a[1:-1, :-2] + a[2:, 1:-1] + a[:-2, 1:-1] - 4 * a[1:-1, 1:-1]
    return float(lap.var())


def photo(args):
    path, work = args
    fid = os.path.splitext(os.path.basename(path))[0]
    im = Image.open(path)
    w, h = im.size
    exif = im.getexif()
    ex = exif.get_ifd(0x8769)
    gps = exif.get_ifd(0x8825)
    orient = exif.get(0x0112, 1)
    t = ex.get(0x9003) or exif.get(0x0132)        # DateTimeOriginal / DateTime
    off = ex.get(0x9011)                          # OffsetTimeOriginal
    rec = {"id": fid, "file": os.path.basename(path), "type": "photo",
           "w": w, "h": h, "orient": orient}
    if t:
        dt = datetime.strptime(t, "%Y:%m:%d %H:%M:%S")
        if off:
            sign = -1 if off[0] == "-" else 1
            hh, mm = off[1:].split(":")
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(hh), minutes=int(mm))))
        rec["time"] = dt.isoformat()
    if 2 in gps and 4 in gps:
        rec["lat"] = round(dms(gps[2], gps.get(1)), 6)
        rec["lon"] = round(dms(gps[4], gps.get(3)), 6)

    im.draft("RGB", (1000, 1000))                 # быстрое JPEG-декодирование в уменьшенном масштабе
    im = ImageOps.exif_transpose(im.convert("RGB"))
    gray = im.convert("L")
    gray.thumbnail((1000, 1000))
    g = np.asarray(gray, dtype=np.float32)
    rec["sharp"] = round(sharpness(gray), 1)
    rec["bright"] = round(float(g.mean()), 1)
    rec["clip"] = round(float(((g < 5) | (g > 250)).mean()), 3)
    rec["phash"] = str(imagehash.phash(im))
    im.thumbnail((THUMB, THUMB))
    im.save(os.path.join(work, "thumbs", fid + ".jpg"), quality=80)
    return rec


def video(args):
    path, work = args
    fid = os.path.splitext(os.path.basename(path))[0]
    d = json.loads(subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True).stdout)
    fmt = d["format"]
    tags = fmt.get("tags", {})
    vs = next(s for s in d["streams"] if s["codec_type"] == "video")
    dur = float(fmt.get("duration", 0))
    rec = {"id": fid, "file": os.path.basename(path), "type": "video",
           "w": vs["width"], "h": vs["height"], "duration": round(dur, 1)}
    if "creation_time" in tags:
        rec["time"] = datetime.fromisoformat(tags["creation_time"].replace("Z", "+00:00")).isoformat()
    loc = tags.get("location") or tags.get("com.apple.quicktime.location.ISO6709")
    if loc:
        import re
        m = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", loc)
        if m and (float(m[1]), float(m[2])) != (0.0, 0.0):
            rec["lat"], rec["lon"] = float(m[1]), float(m[2])
    for i, frac in enumerate(VFRAMES):
        out = os.path.join(work, "vframes", f"{fid}_{i}.jpg")
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-ss", f"{dur * frac:.2f}", "-i", path,
                        "-frames:v", "1", "-vf", f"scale={THUMB}:{THUMB}:force_original_aspect_ratio=decrease",
                        out])
    return rec


def main():
    root = sys.argv[1]
    src, work = os.path.join(root, "src"), os.path.join(root, "work")
    for d in ("thumbs", "vframes"):
        os.makedirs(os.path.join(work, d), exist_ok=True)
    files = sorted(os.listdir(src))
    photos = [(os.path.join(src, f), work) for f in files if os.path.splitext(f)[1].lower() in PHOTO_EXT]
    # видео с тем же именем, что у фото, — это Live Photo (iPhone), пропускаем
    stems = {os.path.splitext(f)[0] for f in files if os.path.splitext(f)[1].lower() in PHOTO_EXT}
    videos = [(os.path.join(src, f), work) for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXT
              and os.path.splitext(f)[0] not in stems]
    with Pool() as p:
        recs = p.map(photo, photos, chunksize=4) + p.map(video, videos)
    # фото без OffsetTime — пояс ближайшего (по локальному времени) фото с поясом
    ph = [datetime.fromisoformat(r["time"]) for r in recs if r["type"] == "photo" and "time" in r]
    ph = [p for p in ph if p.tzinfo]
    for r in recs:
        if r["type"] == "photo" and "time" in r and ph:
            t = datetime.fromisoformat(r["time"])
            if not t.tzinfo:
                near = min(ph, key=lambda p: abs(p.replace(tzinfo=None) - t))
                r["time"] = t.replace(tzinfo=near.tzinfo).isoformat()
                r["tz_inferred"] = True
    # видео хранят время в UTC — приводим к поясу ближайшего по времени фото
    for r in recs:
        if r["type"] == "video" and "time" in r and ph:
            t = datetime.fromisoformat(r["time"])
            near = min(ph, key=lambda p: abs(p - t))
            r["time"] = t.astimezone(near.tzinfo).isoformat()
    recs.sort(key=lambda r: datetime.fromisoformat(r["time"]) if "time" in r else datetime.max.replace(tzinfo=timezone.utc))
    recs.sort(key=lambda r: r.get("time", ""))
    with open(os.path.join(work, "meta.json"), "w") as f:
        json.dump(recs, f, indent=1, ensure_ascii=False)
    print(f"{len(photos)} фото, {len(videos)} видео; без времени: {sum('time' not in r for r in recs)}, "
          f"без GPS: {sum('lat' not in r for r in recs)}")


if __name__ == "__main__":
    main()
