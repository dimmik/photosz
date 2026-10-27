#!/usr/bin/env python3
"""Дополнения после прохода 2: таблички с описаниями объектов и люди, смотрящие в камеру. Без ИИ.

  python3 tools/extras.py "<альбом>" signs    табличка крупно + снимки рядом по времени → work/extras/sign_NN.jpg
  python3 tools/extras.py "<альбом>" faces    фото альбома листами для поиска лиц → work/extras/faces_N.jpg
  python3 tools/extras.py "<альбом>" people   лист для пользователя: только фото с лицами → work/extras/people.jpg
  python3 tools/extras.py "<альбом>" suggest  «что ещё могло бы попасть»: дыры по времени, «дорога», моменты
                                              → work/extras/suggest.jpg

Карты меток (что за файл под какой меткой) — work/extras/*_map.json.
"""
import argparse, glob, importlib.util, json, os, re
from datetime import datetime

import imagehash
from PIL import Image, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("sel", os.path.join(HERE, "select.py"))
sel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sel)

# старые альбомы без флага "sign" в проходе 1: узнаём табличку по описанию
SIGN_RE = re.compile(r"plaque|info(rmation)? board|panel|placard|exhibit|historic|interpretive|memorial", re.I)
NEAR_MIN = 15          # снимки объекта ищем в пределах ±15 минут от таблички
GROUP_S = 120          # таблички ближе 2 минут друг к другу — одна табличка (несколько дублей)
OBJECTS = 6            # кандидатов в «сам объект» на лист
HOLE_MIN = 12          # дыра: ≥ 12 минут внутри главы без единого кадра в альбоме…
HOLE_N = 4             # …и при этом снято ≥ 4 кадров
SUGGEST = 6            # предложений на главу


def is_sign(s):
    return bool(s.get("sign")) or bool(SIGN_RE.search(s.get("what", "")))


def kept_sections(work):
    picks = json.load(open(os.path.join(work, "picks.json")))
    ov_p = os.path.join(work, "overrides.json")
    ov = json.load(open(ov_p)) if os.path.exists(ov_p) else {}
    out = []
    for s in json.load(open(os.path.join(work, "sections.json"))):
        key = f"s{s['n']:02d}"
        p = {**picks.get(key, {}), **ov.get(key, {})}
        if p.get("photos") and not p.get("drop"):
            out.append((key, s))
    return out


def cmd_signs(root):
    work = os.path.join(root, "work")
    meta = {r["id"]: r for r in json.load(open(os.path.join(work, "meta.json")))}
    scores = sel.load_scores(work)
    t = lambda fid: datetime.fromisoformat(meta[fid]["time"])
    out_dir = os.path.join(work, "extras")
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "sign_*.jpg")):
        os.remove(f)
    smap = {}
    for key, s in kept_sections(work):
        ids = sorted((f for f in s["photos"] if f in meta), key=t)
        signs = [f for f in ids if is_sign(scores.get(f, {}))]
        groups = []
        for f in signs:
            if groups and (t(f) - t(groups[-1][-1])).total_seconds() <= GROUP_S:
                groups[-1].append(f)
            else:
                groups.append([f])
        for g in groups:
            best = max(g, key=lambda f: meta[f]["sharp"])
            near = [f for f in ids if f not in g and not is_sign(scores.get(f, {}))
                    and scores.get(f, {}).get("score", 0) >= 4
                    and abs((t(f) - t(best)).total_seconds()) <= NEAR_MIN * 60]
            near.sort(key=lambda f: abs((t(f) - t(best)).total_seconds()))
            objs, hashes = [], []
            for f in near:                               # без почти одинаковых кадров
                h = imagehash.hex_to_hash(meta[f]["phash"])
                if all(h - x > 8 for x in hashes):
                    objs.append(f); hashes.append(h)
                if len(objs) == OBJECTS:
                    break
            n = f"{len(smap) + 1:02d}"
            im = ImageOps.exif_transpose(Image.open(os.path.join(root, "src", meta[best]["file"]))).convert("RGB")
            im.thumbnail((1400, 1400), Image.LANCZOS)
            cells = [sel.cell(os.path.join(work, "thumbs", f + ".jpg"), 300, str(i)) for i, f in enumerate(objs, 1)]
            right = sel.grid(cells, 2, 300) if cells else Image.new("RGB", (0, 0))   # снимков рядом нет — табличка одна
            sheet = Image.new("RGB", (im.width + 20 + right.width, max(im.height, right.height)), (20, 20, 20))
            sheet.paste(im, (0, 0))
            sheet.paste(right, (im.width + 20, 0))
            sel.label(sheet, f"SIGN {n}")
            sheet.save(os.path.join(out_dir, f"sign_{n}.jpg"), quality=86)
            smap[n] = {"section": key, "sign": best, "objects": objs}
    json.dump(smap, open(os.path.join(out_dir, "signs_map.json"), "w"), indent=1)
    print(f"табличек: {len(smap)} → {out_dir}/sign_*.jpg")


def album_photos(root):
    album = json.load(open(os.path.join(root, "album", "album.json")))
    return [it["id"] for c in album["chapters"] for it in c["items"] if it["type"] == "photo"]


def cmd_faces(root):
    work = os.path.join(root, "work")
    out_dir = os.path.join(work, "extras")
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "faces_*.jpg")):
        os.remove(f)
    ids = list(dict.fromkeys(album_photos(root)))
    fmap = {f"P{i}": f for i, f in enumerate(ids, 1)}
    labs = list(fmap)
    for j in range(0, len(labs), 20):                    # 4 × 5 по 360 px — лица ещё различимы
        cells = [sel.cell(os.path.join(work, "thumbs", fmap[l] + ".jpg"), 360, l) for l in labs[j:j + 20]]
        sel.grid(cells, 4, 360).save(os.path.join(out_dir, f"faces_{j // 20 + 1}.jpg"), quality=85)
    json.dump(fmap, open(os.path.join(out_dir, "faces_map.json"), "w"), indent=1)
    print(f"фото альбома: {len(ids)} → {out_dir}/faces_*.jpg")


def cmd_people(root):
    work = os.path.join(root, "work")
    out_dir = os.path.join(work, "extras")
    fmap = json.load(open(os.path.join(out_dir, "faces_map.json")))
    found = json.load(open(os.path.join(work, "faces.json")))
    pp = os.path.join(work, "people.json")
    named = json.load(open(pp)) if os.path.exists(pp) else {}
    in_album = set(album_photos(root))
    labs = [l for l in fmap if found.get(l) and fmap[l] in in_album]
    if not labs:
        print("лиц в камеру не найдено")
        return
    media = os.path.join(root, "album", "media")
    cells = [sel.cell(os.path.join(media, fmap[l] + "_t.jpg"), 520, l) for l in labs]
    sel.grid(cells, 3, 520).save(os.path.join(out_dir, "people.jpg"), quality=86)
    print(f"лица в камеру: {len(labs)} фото → {os.path.join(out_dir, 'people.jpg')}")
    for l in labs:
        print(f"  {l:4} {fmap[l]}" + (f"  уже подписано: {named[fmap[l]].get('ru')}" if fmap[l] in named else ""))


def cmd_suggest(root):
    work = os.path.join(root, "work")
    meta = {r["id"]: r for r in json.load(open(os.path.join(work, "meta.json")))}
    scores = sel.load_scores(work)
    album = json.load(open(os.path.join(root, "album", "album.json")))
    t = lambda fid: datetime.fromisoformat(meta[fid]["time"])
    g = lambda f, k: scores.get(f, {}).get(k, 0)
    rank = lambda f: (sel.story(scores.get(f, {})) >= sel.WAY or g(f, "fun") >= sel.FUN, g(f, "score"), meta[f]["sharp"])
    cells, smap, report = [], {}, []
    # главы альбома идут в том же порядке, что оставленные разделы
    for ch, ((key, s), c) in enumerate(zip(kept_sections(work), album["chapters"]), 1):
        chosen = [it["id"] for it in c["items"]]
        hs = [imagehash.hex_to_hash(meta[f]["phash"]) for f in chosen if "phash" in meta[f]]
        pool = [f for f in s["photos"] if f in meta and f not in chosen
                and all(imagehash.hex_to_hash(meta[f]["phash"]) - h > 8 for h in hs)]
        # дыры: промежутки между взятыми кадрами (и краями главы), где много снято и ничего не взято
        marks = sorted([datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])] + [t(f) for f in chosen])
        picks, why = [], {}
        for a, b in zip(marks, marks[1:]):
            inside = [f for f in pool if a < t(f) < b]
            if (b - a).total_seconds() >= HOLE_MIN * 60 and len(inside) >= HOLE_N:
                report.append(f"гл. {ch} «{c['title']}»: {a:%H:%M}–{b:%H:%M} в альбоме ничего, снято {len(inside)}")
                best = sorted(inside, key=rank, reverse=True)[:2]
                picks += best
                why.update({f: "дыра" for f in best})
        way = [f for f in pool if sel.story(scores.get(f, {})) >= sel.WAY]
        fun = [f for f in pool if g(f, "fun") >= sel.FUN]
        for f, w in [(f, "в пути") for f in sorted(way, key=rank, reverse=True)] + \
                    [(f, "момент") for f in sorted(fun, key=rank, reverse=True)]:
            if f not in picks:
                picks.append(f); why[f] = w
        out = []
        for f in picks:                                  # из одного места (±60 с) — один кадр
            if len(out) < SUGGEST and all(abs((t(f) - t(x)).total_seconds()) >= 60 for x in out):
                out.append(f)
        for i, f in enumerate(sorted(out, key=t), 1):
            lab = f"{ch}.{i}"
            smap[lab] = {"id": f, "section": key, "chapter": c["title"], "why": why[f], "what": scores.get(f, {}).get("what", "")}
            cells.append(sel.cell(os.path.join(work, "thumbs", f + ".jpg"), 300, lab))
        cells += [Image.new("RGB", (300, 300), (20, 20, 20))] * (-len(cells) % SUGGEST)   # глава — с новой строки
    out_dir = os.path.join(work, "extras")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(smap, open(os.path.join(out_dir, "suggest_map.json"), "w"), ensure_ascii=False, indent=1)
    print("\n".join(report) or "дыр по времени нет")
    if not smap:
        print("предложений нет")
        return
    sel.grid(cells, SUGGEST, 300).save(os.path.join(out_dir, "suggest.jpg"), quality=82)
    print(f"предложений: {len(smap)} → {os.path.join(out_dir, 'suggest.jpg')}")
    for lab, m in smap.items():
        print(f"  {lab:5} {m['id']}  [{m['why']}] {m['what']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("cmd", choices=["signs", "faces", "people", "suggest"])
    a = ap.parse_args()
    {"signs": cmd_signs, "faces": cmd_faces, "people": cmd_people, "suggest": cmd_suggest}[a.cmd](a.root)


if __name__ == "__main__":
    main()
