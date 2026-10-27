#!/usr/bin/env python3
"""Шаг 2: разделы и контактные листы для двух проходов ИИ. Сам скрипт ИИ не вызывает.

  python3 tools/select.py "<альбом>" sections [--sections 10]
      → work/sections.json, work/pass1/*.jpg + work/pass1.json
        Проход 1 (ИИ): оценить ВСЕ фото (после схлопывания дублей) и ВСЕ видео → work/scores.json
  python3 tools/select.py "<альбом>" shortlist [--candidates 8]
      → work/sheets/sNN.jpg (крупные миниатюры лучших по оценке) + кандидаты в sections.json
        Проход 2 (ИИ): финальный выбор, герой, обложка, подписи → work/picks.json
"""
import argparse, json, math, os, re, time, urllib.parse, urllib.request
from datetime import datetime, timedelta

import imagehash
from PIL import Image, ImageDraw, ImageFont

GAP = timedelta(minutes=30)       # пауза, после которой начинается новый эпизод
JUMP_KM = 1.5                     # переезд, после которого начинается новый эпизод
MIN_SEG = 6                       # меньше — эпизод не раздел (вливается в соседа или выпадает)
DUP_HASH = 4                      # pHash-расстояние для «почти идентичных» (серии с действием не схлопываем)
GRID = 5                          # проход 1: сетка 5×5 по 300px
CELL = 300
CELL2 = 400                       # проход 2: сетка 3×N по 400px
WEAK = 6                          # раздел, где нет ни одного кадра/видео с оценкой ≥ WEAK, выпадает…
FUN = 7                           # …если в нём нет и «момента» (fun ≥ FUN); моменты идут сверх лимита
FUN_MAX = 3                       # кандидатов-моментов на раздел для прохода 2
BURST_GAP = 30                    # серия: подряд идущие фото с паузами ≤ 30 с…
BURST_MIN = 4                     # …не короче 4 кадров (смешное часто видно только в последовательности)
BURST_FRAMES = 5                  # кадров серии на ленте для ИИ
WAY = 6                           # «дорога»: как добрались и что по пути (story ≥ WAY) — сверх лимита
WAY_MAX = 3                       # кандидатов «дорога» на раздел для прохода 2
# старые альбомы без оценки "story": узнаём «дорогу» по описанию
WAY_RE = re.compile(r"stair|steps|trail|tunnel|cave|descen|climb|ferry|cable car|elevator", re.I)


def km(a, b):
    return 6371 * math.hypot(math.radians(a["lat"] - b["lat"]),
                             math.radians(a["lon"] - b["lon"]) * math.cos(math.radians(a["lat"])))


def fill_gps(items):
    """Кадры без GPS получают координаты ближайшего по времени кадра (если он в пределах 30 мин)."""
    known = [r for r in items if "lat" in r]
    for r in items:
        if "lat" not in r and known:
            n = min(known, key=lambda x: abs(x["t"] - r["t"]))
            if abs(n["t"] - r["t"]) < GAP:
                r["lat"], r["lon"], r["gps_inferred"] = n["lat"], n["lon"], True


def segment(items):
    segs, cur = [], []
    for r in items:
        if cur and (r["t"] - cur[-1]["t"] > GAP or
                    ("lat" in r and "lat" in cur[-1] and km(r, cur[-1]) > JUMP_KM)):
            segs.append(cur)
            cur = []
        cur.append(r)
    return segs + [cur]


def center(seg):
    """Медианная точка эпизода (устойчива к выбросам вроде перелёта)."""
    ll = [r for r in seg if "lat" in r]
    if not ll:
        return None
    return {"lat": sorted(r["lat"] for r in ll)[len(ll) // 2], "lon": sorted(r["lon"] for r in ll)[len(ll) // 2]}


def near(a, b):
    ca, cb = center(a), center(b)
    return not ca or not cb or km(ca, cb) < 2 * JUMP_KM


def split_largest(out, min_part=25):
    """Самый крупный раздел делим там, где сильнее всего расходятся половины:
    расстояние между их медианными точками (км) + пауза в точке разреза (мин / 10)."""
    for s in sorted(out, key=len, reverse=True):
        best = None
        for i in range(min_part, len(s) - min_part + 1):
            ca, cb = center(s[:i]), center(s[i:])
            d = km(ca, cb) if ca and cb else 0
            score = d + (s[i]["t"] - s[i - 1]["t"]).total_seconds() / 600
            if not best or score > best[0]:
                best = (score, i)
        if best:
            k, i = out.index(s), best[1]
            out[k:k + 1] = [s[:i], s[i:]]
            return True
    return False


def build_sections(segs, n):
    # мелкие эпизоды вливаем в соседа того же дня (если близко по времени и месту), иначе выбрасываем
    out = []
    for s in segs:
        if out and out[-1][-1]["t"].date() == s[0]["t"].date() and s[0]["t"] - out[-1][-1]["t"] < 3 * GAP \
                and (len(s) < MIN_SEG or len(out[-1]) < MIN_SEG) and near(out[-1], s):
            out[-1] = out[-1] + s
        else:
            out.append(s)
    out = [s for s in out if len(s) >= MIN_SEG]
    # больше n — сливаем соседние эпизоды одного дня с наименьшим суммарным размером
    while len(out) > n:
        pairs = [(len(out[i]) + len(out[i + 1]), i) for i in range(len(out) - 1)
                 if out[i][0]["t"].date() == out[i + 1][0]["t"].date()]
        if not pairs:
            out.remove(min(out, key=len))
            continue
        _, i = min(pairs)
        out[i:i + 2] = [out[i] + out[i + 1]]
    # меньше n — дробим самые крупные разделы
    while len(out) < n and split_largest(out):
        pass
    return out


def place_name(lat, lon, cache, lang="ru"):
    key = f"{lat:.4f},{lon:.4f}" + ("" if lang == "ru" else ":" + lang)
    if key not in cache:
        q = urllib.parse.urlencode({"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 16,
                                    "accept-language": lang})
        req = urllib.request.Request("https://nominatim.openstreetmap.org/reverse?" + q,
                                     headers={"User-Agent": "photo-alb/0.1 (personal album)"})
        a = json.load(urllib.request.urlopen(req, timeout=20)).get("address", {})
        cache[key] = {k: a.get(k) for k in ("tourism", "leisure", "road", "neighbourhood", "suburb",
                                            "city", "town", "state", "country") if a.get(k)}
        time.sleep(1.1)           # политика Nominatim: не чаще 1 запроса в секунду
    return cache[key]


def dedupe(photos):
    """Почти-дубли схлопываем в группы; представитель группы — самый резкий кадр."""
    groups = []
    for r in photos:
        h = imagehash.hex_to_hash(r["phash"])
        for g in groups:
            if h - imagehash.hex_to_hash(g[0]["phash"]) <= DUP_HASH:
                g.append(r)
                break
        else:
            groups.append([r])
    return [max(g, key=lambda r: r["sharp"]) for g in groups]


def bursts(photos):
    out, cur = [], photos[:1]
    for a, b in zip(photos, photos[1:]):
        if (b["t"] - a["t"]).total_seconds() <= BURST_GAP:
            cur.append(b)
        else:
            out.append(cur)
            cur = [b]
    return [b for b in out + [cur] if len(b) >= BURST_MIN]


def sample(seq, k):
    """k элементов равномерно, включая первый и последний."""
    if len(seq) <= k:
        return list(seq)
    return [seq[round(i * (len(seq) - 1) / (k - 1))] for i in range(k)]


def junk(r):
    """Только совсем пустые кадры (чёрный/белый). Смаз не брак: смешные моменты часто смазаны."""
    return not (10 < r["bright"] < 248)


def font(size):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    return ImageFont.truetype(p, size) if os.path.exists(p) else ImageFont.load_default()


def label(im, text, size=28):
    d = ImageDraw.Draw(im)
    f = font(size)
    w = d.textlength(text, font=f)
    d.rectangle([0, 0, w + 12, size + 8], fill=(0, 0, 0))
    d.text((6, 2), text, fill=(255, 255, 0), font=f)


def cell(path, size, text):
    im = Image.open(path)
    im.thumbnail((size, size))
    c = Image.new("RGB", (size, size), (40, 40, 40))
    c.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
    label(c, text)
    return c


def grid(cells, cols, size):
    rows = math.ceil(len(cells) / cols)
    sheet = Image.new("RGB", (cols * size, rows * size), (20, 20, 20))
    for i, c in enumerate(cells):
        sheet.paste(c, ((i % cols) * size, (i // cols) * size))
    return sheet


def filmstrips(videos, labels, work, width=1500, frames=6):
    """Каждое видео — строка из `frames` кадров слева направо (начало → конец)."""
    fw = width // frames
    fh = fw * 3 // 4
    sheet = Image.new("RGB", (width, fh * len(videos)), (20, 20, 20))
    for j, (v, lab) in enumerate(zip(videos, labels)):
        for f in range(frames):
            p = os.path.join(work, "vframes", f"{v['id']}_{f}.jpg")
            if not os.path.exists(p):
                continue
            im = Image.open(p)
            im.thumbnail((fw - 4, fh - 4))
            sheet.paste(im, (f * fw + (fw - im.width) // 2, j * fh + (fh - im.height) // 2))
        strip = sheet.crop((0, j * fh, 140, j * fh + 36))
        label(strip, lab)
        sheet.paste(strip, (0, j * fh))
    return sheet


def load(root):
    work = os.path.join(root, "work")
    items = json.load(open(os.path.join(work, "meta.json")))
    for r in items:
        r["t"] = datetime.fromisoformat(r["time"])
    return work, items


def cmd_sections(root, n):
    work, items = load(root)
    fill_gps(items)
    secs = build_sections(segment(items), n)
    cache_p = os.path.join(work, "geocache.json")
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
    out_dir = os.path.join(work, "pass1")
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))

    sections, labels, cells, page, vids, series = [], {}, [], 1, [], {}
    for k, sec in enumerate(secs, 1):
        photos = [r for r in dedupe([r for r in sec if r["type"] == "photo"]) if not junk(r)]
        for i, r in enumerate(photos, 1):
            lab = f"{k}-{i}"
            labels[lab] = r["id"]
            cells.append(cell(os.path.join(work, "thumbs", r["id"] + ".jpg"), CELL, lab))
        for i, r in enumerate([r for r in sec if r["type"] == "video"], 1):
            lab = f"{k}-V{i}"
            labels[lab] = r["id"]
            vids.append((r, lab))
        for b in bursts([r for r in sec if r["type"] == "photo"]):
            series[f"S{len(series) + 1}"] = {"section": k, "ids": [r["id"] for r in sample(b, BURST_FRAMES)],
                                            "all": [r["id"] for r in b]}
        c = center(sec)
        sections.append({"n": k, "start": sec[0]["time"], "end": sec[-1]["time"], "count": len(sec),
                         "lat": round(c["lat"], 5), "lon": round(c["lon"], 5),
                         "place": place_name(c["lat"], c["lon"], cache),
                         "place_en": place_name(c["lat"], c["lon"], cache, "en"),
                         "photos": [r["id"] for r in photos],
                         "videos": [r["id"] for r in sec if r["type"] == "video"]})
        print(f"s{k:02d} {sec[0]['t']:%m-%d %H:%M}–{sec[-1]['t']:%H:%M} {len(sec):4} кадров → "
              f"{len(photos)} на оценку, {len(sections[-1]['videos'])} видео; {sections[-1]['place'].get('neighbourhood') or sections[-1]['place'].get('road', '')}")
    per = GRID * GRID
    sheets = []
    for i in range(0, len(cells), per):
        p = os.path.join(out_dir, f"p{i // per + 1:02d}.jpg")
        grid(cells[i:i + per], GRID, CELL).save(p, quality=78)
        sheets.append(p)
    for i in range(0, len(vids), 5):
        chunk = vids[i:i + 5]
        p = os.path.join(out_dir, f"v{i // 5 + 1:02d}.jpg")
        filmstrips([v for v, _ in chunk], [l for _, l in chunk], work).save(p, quality=78)
        sheets.append(p)
    # серии: строка = одна серия, BURST_FRAMES кадров слева направо по времени
    keys = list(series)
    fw = 300
    for i in range(0, len(keys), 5):
        chunk = keys[i:i + 5]
        sheet = Image.new("RGB", (fw * BURST_FRAMES, fw * 3 // 4 * len(chunk)), (20, 20, 20))
        for j, key in enumerate(chunk):
            y = j * fw * 3 // 4
            for f, fid in enumerate(series[key]["ids"]):
                im = Image.open(os.path.join(work, "thumbs", fid + ".jpg"))
                im.thumbnail((fw - 4, fw * 3 // 4 - 4))
                sheet.paste(im, (f * fw + (fw - im.width) // 2, y + (fw * 3 // 4 - im.height) // 2))
            strip = sheet.crop((0, y, 90, y + 36))
            label(strip, key)
            sheet.paste(strip, (0, y))
        p = os.path.join(out_dir, f"q{i // 5 + 1:02d}.jpg")
        sheet.save(p, quality=80)
        sheets.append(p)
    json.dump(series, open(os.path.join(work, "series.json"), "w"), indent=1)
    json.dump(cache, open(cache_p, "w"), ensure_ascii=False, indent=1)
    json.dump(sections, open(os.path.join(work, "sections.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(labels, open(os.path.join(work, "pass1.json"), "w"), indent=1)
    print(f"Проход 1: {len(cells)} фото + {len(vids)} видео + {len(series)} серий на {len(sheets)} листах в {out_dir}")


def load_scores(work):
    """Оценки прохода 1, привязанные к id файла (переживают пересборку разделов).
    Новые work/scores.json (ключи — метки листов) вливаются в work/scores_id.json."""
    p_id = os.path.join(work, "scores_id.json")
    by_id = json.load(open(p_id)) if os.path.exists(p_id) else {}
    p_lab = os.path.join(work, "scores.json")
    if os.path.exists(p_lab):
        labels = json.load(open(os.path.join(work, "pass1.json")))
        by_id.update({labels[l]: v for l, v in json.load(open(p_lab)).items() if l in labels})
        json.dump(by_id, open(p_id, "w"), indent=1, ensure_ascii=False)
        os.rename(p_lab, p_lab + ".merged")
    return by_id


def story(s):
    """Оценка «дорога» прохода 1; у старых альбомов — по описанию."""
    if "story" in s:
        return s["story"]
    return WAY if WAY_RE.search(s.get("what", "")) else 0


def cmd_shortlist(root, k):
    work, items = load(root)
    meta = {r["id"]: r for r in items}
    sections = json.load(open(os.path.join(work, "sections.json")))
    scores = load_scores(work)
    sc = lambda fid: scores.get(fid, {}).get("score", 0)
    fun = lambda fid: scores.get(fid, {}).get("fun", 0)
    series = json.load(open(os.path.join(work, "series.json"))) if os.path.exists(os.path.join(work, "series.json")) else {}
    ss_p = os.path.join(work, "series_scores.json")
    sscores = json.load(open(ss_p)) if os.path.exists(ss_p) else {}
    os.makedirs(os.path.join(work, "sheets"), exist_ok=True)
    cache_p = os.path.join(work, "geocache.json")
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
    for s in sections:
        if "place_en" not in s:
            s["place_en"] = place_name(s["lat"], s["lon"], cache, "en")
    json.dump(cache, open(cache_p, "w"), ensure_ascii=False, indent=1)
    for s in sections:
        # лучшие по оценке, без повторов: одинаковое описание или похожий pHash — повтор
        cand = []
        for f in sorted(s["photos"], key=lambda f: (sc(f), meta[f]["sharp"]), reverse=True):
            what = scores.get(f, {}).get("what", "").lower()
            h = imagehash.hex_to_hash(meta[f]["phash"])
            if any(what and what == scores.get(c, {}).get("what", "").lower() or
                   h - imagehash.hex_to_hash(meta[c]["phash"]) <= 14 for c in cand):
                continue
            cand.append(f)
            if len(cand) == k:
                break
        cand.sort(key=lambda f: meta[f]["time"])
        # моменты: самые смешные/необычные кадры не из основного шорт-листа; из одной серии (±60 с) — один
        funs = []
        for f in sorted(s["photos"], key=lambda f: (fun(f), sc(f)), reverse=True):
            if fun(f) < FUN or len(funs) == FUN_MAX:
                break
            t = datetime.fromisoformat(meta[f]["time"])
            if f in cand or any(abs((t - datetime.fromisoformat(meta[g]["time"])).total_seconds()) < 60 for g in funs):
                continue
            funs.append(f)
        funs.sort(key=lambda f: meta[f]["time"])
        s["fun_candidates"] = funs
        # дорога: как добрались и что по пути; из одного места (±60 с) — один кадр
        way = []
        for f in sorted(s["photos"], key=lambda f: (story(scores.get(f, {})), sc(f)), reverse=True):
            if story(scores.get(f, {})) < WAY or len(way) == WAY_MAX:
                break
            t = datetime.fromisoformat(meta[f]["time"])
            if f in cand or f in funs or any(abs((t - datetime.fromisoformat(meta[g]["time"])).total_seconds()) < 60 for g in way):
                continue
            way.append(f)
        way.sort(key=lambda f: meta[f]["time"])
        s["way_candidates"] = way
        vids = sorted(s["videos"], key=lambda v: max(sc(v), fun(v)), reverse=True)[:3]
        vids = [v for v in vids if max(sc(v), fun(v)) >= 5]
        vids.sort(key=lambda f: meta[f]["time"])
        s["candidates"], s["cand_videos"] = cand, vids
        s["best"] = max([sc(f) for f in cand + vids] or [0])
        # смешная серия раздела (лучшая по fun) — идёт в альбом целиком выбранными кадрами, сверх лимита
        fs = sorted(((v.get("fun", 0), k) for k, v in sscores.items()
                     if k in series and series[k]["section"] == s["n"] and v.get("fun", 0) >= FUN), reverse=True)
        s["fun_series"] = []
        for _, key in fs[:1]:
            v = sscores[key]
            ids = [series[key]["ids"][i - 1] for i in v.get("frames", []) if 0 < i <= len(series[key]["ids"])]
            s["fun_series"].append({"key": key, "ids": ids or series[key]["ids"], "caption": v.get("caption", ""),
                                    "caption_en": v.get("caption_en", "")})
        s["weak"] = s["best"] < WEAK and not s["fun_series"] and not any(fun(f) >= FUN for f in funs + vids)
        if s["weak"]:
            print(f"s{s['n']:02d}: слабый раздел (лучшая оценка {s['best']}) — пропущен")
            continue
        cells = [cell(os.path.join(work, "thumbs", f + ".jpg"), CELL2, str(i))
                 for i, f in enumerate(cand, 1)]
        cells += [cell(os.path.join(work, "thumbs", f + ".jpg"), CELL2, f"F{i}")
                  for i, f in enumerate(funs, 1)]
        cells += [cell(os.path.join(work, "thumbs", f + ".jpg"), CELL2, f"W{i}")
                  for i, f in enumerate(way, 1)]
        parts = [grid(cells, 3, CELL2)] if cells else []
        if vids:
            parts.append(filmstrips([meta[v] for v in vids], [f"V{i}" for i in range(1, len(vids) + 1)],
                                    work, width=3 * CELL2))
        sheet = Image.new("RGB", (3 * CELL2, sum(p.height for p in parts)), (20, 20, 20))
        y = 0
        for p in parts:
            sheet.paste(p, (0, y))
            y += p.height
        s["sheet"] = os.path.join("work", "sheets", f"s{s['n']:02d}.jpg")
        sheet.save(os.path.join(root, s["sheet"]), quality=80)
        print(f"s{s['n']:02d}: {len(cand)} фото (лучшая оценка {s['best']}), {len(funs)} моментов, {len(way)} в пути, {len(vids)} видео")
    json.dump(sections, open(os.path.join(work, "sections.json"), "w"), indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("cmd", choices=["sections", "shortlist"])
    ap.add_argument("--sections", type=int, default=10)
    ap.add_argument("--candidates", type=int, default=8)
    a = ap.parse_args()
    if a.cmd == "sections":
        cmd_sections(a.root, a.sections)
    else:
        cmd_shortlist(a.root, a.candidates)


if __name__ == "__main__":
    main()
