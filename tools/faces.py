#!/usr/bin/env python3
"""Лица без ИИ-затрат: OpenCV YuNet (поиск, MIT) + SFace (сравнение, Apache 2.0), всё локально.

  python3 tools/faces.py "<альбом>" scan            найти лица на всех фото → work/faces.npz
  python3 tools/faces.py "<альбом>" learn           запомнить людей из work/people.json (что уже назвал пользователь)
  python3 tools/faces.py "<альбом>" auto            подписать знакомых в work/people.json, незнакомых — лист групп
                                                    work/extras/people.jpg с метками G#
  python3 tools/faces.py "<альбом>" name G1=Даша/Dasha G2=…   назвать группы и снова подписать

Веса — в .faces/models/ (скачиваются сами, с проверкой SHA-256), справочник людей — .faces/people.npz.
Векторы лиц — биометрия семьи: .faces/ и work/ в git не попадают и никуда не отправляются.
"""
import argparse, hashlib, importlib.util, json, os, urllib.request

import cv2
import numpy as np
import pillow_heif
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(os.path.dirname(HERE), ".faces")
BOOK = os.path.join(HOME, "people.npz")
MODELS = {
    "face_detection_yunet_2023mar.onnx": ("face_detection_yunet", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": ("face_recognition_sface", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
SIDE = 1600            # фото для поиска — до 1600 px по длинной стороне
MIN_PX = 48            # лицо мельче — в группы не берём: вектор слишком шумный
MIN_SHARE = 0.07       # подписываем лицо не мельче 7% короткой стороны кадра
TURN = 0.35            # нос дальше 0.35 межглазья от центра — лицо сильно повёрнуто, подпись не ставим
SAME = 0.42            # косинус: то же лицо при группировке (порог SFace — 0.363, берём строже)
KNOWN = 0.45           # косинус: совпало со справочником
MARGIN = 0.06          # лучший человек должен обойти второго, иначе не подписываем
BOOK_MAX = 40          # векторов на человека


def model(name):
    path = os.path.join(HOME, "models", name)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        folder, sha = MODELS[name]
        data = urllib.request.urlopen(f"https://github.com/opencv/opencv_zoo/raw/main/models/{folder}/{name}").read()
        if hashlib.sha256(data).hexdigest() != sha:
            raise SystemExit(f"{name}: контрольная сумма не сошлась")
        open(path, "wb").write(data)
    return path


def load_rgb(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((SIDE, SIDE), Image.LANCZOS)
    return im


def cmd_scan(root):
    work = os.path.join(root, "work")
    out = os.path.join(work, "faces.npz")
    meta = [r for r in json.load(open(os.path.join(work, "meta.json"))) if r["type"] == "photo"]
    det = cv2.FaceDetectorYN.create(model("face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.8)
    rec = cv2.FaceRecognizerSF.create(model("face_recognition_sface_2021dec.onnx"), "")
    ids, boxes, vecs = [], [], []
    for i, r in enumerate(meta, 1):
        bgr = np.array(load_rgb(os.path.join(root, "src", r["file"])))[:, :, ::-1].copy()
        h, w = bgr.shape[:2]
        det.setInputSize((w, h))
        found = det.detect(bgr)[1]
        for f in found if found is not None else []:
            if min(f[2], f[3]) < MIN_PX:
                continue
            v = rec.feature(rec.alignCrop(bgr, f))[0]
            eye_mid, eyes = (f[4] + f[6]) / 2, max(abs(f[6] - f[4]), 1)
            ids.append(r["id"])
            # x y w h (в долях короткой стороны — только w), поворот, размер кадра
            boxes.append([f[0], f[1], f[2], f[3], abs(f[8] - eye_mid) / eyes, min(f[2], f[3]) / min(w, h), w, h])
            vecs.append(v / np.linalg.norm(v))
        if i % 50 == 0:
            print(f"  {i}/{len(meta)}", flush=True)
    np.savez_compressed(out, ids=np.array(ids), boxes=np.array(boxes).reshape(-1, 8), vecs=np.array(vecs).reshape(-1, 128))
    print(f"лиц: {len(ids)} на {len(set(ids))} фото из {len(meta)} → {out}")


def load_faces(root):
    d = np.load(os.path.join(root, "work", "faces.npz"))
    return d["ids"], d["boxes"], d["vecs"]


def load_book():
    """{ru: {"en": ..., "vecs": ndarray}}"""
    if not os.path.exists(BOOK):
        return {}
    d = np.load(BOOK)
    return {str(ru): {"en": str(en), "vecs": d[f"v{i}"]} for i, (ru, en) in enumerate(zip(d["ru"], d["en"]))}


def save_book(book):
    os.makedirs(HOME, exist_ok=True)
    names = list(book)
    np.savez_compressed(BOOK, ru=np.array(names), en=np.array([book[n]["en"] for n in names]),
                        **{f"v{i}": book[n]["vecs"][-BOOK_MAX:] for i, n in enumerate(names)})


def add(book, ru, en, vecs):
    p = book.setdefault(ru, {"en": en, "vecs": np.empty((0, 128))})
    new = [v for v in vecs if not len(p["vecs"]) or np.max(p["vecs"] @ v) < 0.999]   # learn запускается повторно
    if new:
        p["vecs"] = np.vstack([p["vecs"], new])
    p["en"] = en or p["en"]


def who(book, v):
    """Имя по справочнику или None: совпадение должно быть уверенным и однозначным."""
    sims = sorted(((float(np.max(p["vecs"] @ v)), n) for n, p in book.items()), reverse=True)
    if not sims or sims[0][0] < KNOWN or (len(sims) > 1 and sims[0][0] - sims[1][0] < MARGIN):
        return None
    return sims[0][1]


def clear(b):
    return b[5] >= MIN_SHARE and b[4] <= TURN


def split_names(p):
    """{"ru": "Даша и Оля", "en": "Dasha and Olya"} → [("Даша", "Dasha"), ("Оля", "Olya")]"""
    ru, en = p["ru"].split(" и "), (p.get("en") or "").split(" and ")
    return list(zip(ru, en)) if len(ru) == len(en) else [(n, "") for n in ru]


def cmd_learn(root):
    ids, boxes, vecs = load_faces(root)
    pp = os.path.join(root, "work", "people.json")
    people = json.load(open(pp, encoding="utf-8")) if os.path.exists(pp) else {}
    book = load_book()
    # сначала фото, где лиц столько же, сколько имён и имя одно; затем пары: второго определяем методом исключения
    todo = sorted(people, key=lambda f: len(split_names(people[f])))
    for f in todo:
        names = split_names(people[f])
        idx = [i for i in np.where(ids == f)[0] if clear(boxes[i])]
        left = [n for n in names if n[0] not in book or len(names) == 1]
        if len(idx) != len(names):
            print(f"  {f}: лиц {len(idx)}, имён {len(names)} — пропускаю")
            continue
        rest = [i for i in idx if who(book, vecs[i]) is None or len(names) == 1]
        if len(rest) == len(left) == 1:
            add(book, left[0][0], left[0][1], vecs[rest])
    save_book(book)
    print("в справочнике:", {n: len(p["vecs"]) for n, p in book.items()})


def album_photos(root):
    a = json.load(open(os.path.join(root, "album", "album.json")))
    return list(dict.fromkeys(it["id"] for c in a["chapters"] for it in c["items"] if it["type"] == "photo"))


def groups(vecs):
    """Жадная группировка по центроидам: [[индексы в vecs], …], крупные сверху."""
    cs, out = [], []
    for i, v in enumerate(vecs):
        sims = [float(c / np.linalg.norm(c) @ v) for c in cs]
        if sims and max(sims) >= SAME:
            k = int(np.argmax(sims))
            cs[k] = cs[k] + v
            out[k].append(i)
        else:
            cs.append(v.copy())
            out.append([i])
    return sorted(out, key=len, reverse=True)


def crop(root, meta, fid, b, size=200):
    im = load_rgb(os.path.join(root, "src", meta[fid]["file"]))
    x, y, w, h = b[:4]
    m = max(w, h) * 0.35
    return im.crop((int(x - m), int(y - m), int(x + w + m), int(y + h + m))).resize((size, size))


def cmd_auto(root):
    spec = importlib.util.spec_from_file_location("sel", os.path.join(HERE, "select.py"))
    sel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sel)
    work = os.path.join(root, "work")
    ids, boxes, vecs = load_faces(root)
    meta = {r["id"]: r for r in json.load(open(os.path.join(work, "meta.json")))}
    book = load_book()
    pp = os.path.join(work, "people.json")
    people = json.load(open(pp, encoding="utf-8")) if os.path.exists(pp) else {}
    in_album = album_photos(root)
    unknown, done = [], 0
    for f in in_album:
        found = sorted(((boxes[i][0], who(book, vecs[i])) for i in np.where(ids == f)[0] if clear(boxes[i])), key=lambda x: x[0])
        names = list(dict.fromkeys(n for _, n in found if n))
        unknown += [i for i in np.where(ids == f)[0] if clear(boxes[i]) and who(book, vecs[i]) is None]
        if names and f not in people:
            people[f] = {"ru": " и ".join(names), "en": " and ".join(book[n]["en"] for n in names)}
            done += 1
            print(f"  {f}: {people[f]['ru']}")
    json.dump(people, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"подписано новых фото: {done}, знакомых в справочнике: {len(book)}")
    # незнакомцы в альбоме: группы похожих лиц, по строке на группу
    out = os.path.join(work, "extras")
    os.makedirs(out, exist_ok=True)
    gs = groups(vecs[unknown]) if unknown else []
    gmap, rows = {}, []
    for k, g in enumerate(gs, 1):
        idx = [unknown[j] for j in g]
        gmap[f"G{k}"] = [int(i) for i in idx]
        strip = Image.new("RGB", (200 * 6, 200), (20, 20, 20))
        for n, i in enumerate(idx[:6]):
            strip.paste(crop(root, meta, str(ids[i]), boxes[i]), (n * 200, 0))
        sel.label(strip, f"G{k} ({len(idx)})")
        rows.append(strip)
    json.dump(gmap, open(os.path.join(out, "faces_groups.json"), "w"), indent=1)
    sheet = os.path.join(out, "people.jpg")
    if os.path.exists(sheet):
        os.remove(sheet)
    if not rows:
        print("незнакомых лиц нет")
        return
    im = Image.new("RGB", (1200, 200 * len(rows)))
    for n, s in enumerate(rows):
        im.paste(s, (0, n * 200))
    im.save(sheet, quality=86)
    print(f"незнакомых групп: {len(rows)} → {sheet}")
    print("  " + ", ".join(f"G{k}: {len(g)} лиц" for k, g in enumerate(gs, 1)))


def cmd_name(root, pairs):
    ids, boxes, vecs = load_faces(root)
    gmap = json.load(open(os.path.join(root, "work", "extras", "faces_groups.json")))
    book = load_book()
    for pair in pairs:
        g, name = pair.split("=", 1)
        ru, _, en = name.partition("/")
        add(book, ru, en, vecs[gmap[g]])
    save_book(book)
    cmd_auto(root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("cmd", choices=["scan", "learn", "auto", "name"])
    ap.add_argument("pairs", nargs="*")
    a = ap.parse_args()
    if a.cmd == "name":
        cmd_name(a.root, a.pairs)
    else:
        {"scan": cmd_scan, "learn": cmd_learn, "auto": cmd_auto}[a.cmd](a.root)


if __name__ == "__main__":
    main()
