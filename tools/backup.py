#!/usr/bin/env python3
"""Зашифрованная резервная копия рабочих файлов (то, чего нет в репозитории).

Шифрование с открытым ключом: из фразы пользователя (scrypt) получается закрытый ключ X25519,
в репозитории лежит только открытый (backup/public.json). Шифровать можно без фразы,
расшифровать — только с ней. Фраза нигде не хранится.

  python3 tools/backup.py init [--phrase-file F]   фраза → backup/public.json (соль + открытый ключ)
  python3 tools/backup.py selftest [--phrase-file F]
        шифрует тестовую строку открытым ключом и расшифровывает фразой (без файла — ввод скрыто)
  python3 tools/backup.py save [core|archive|all]   зашифровать (фраза не нужна); без изменений — не трогает
  python3 tools/backup.py check [core|archive|all]  расшифровать в памяти, сверить с манифестом и с текущими файлами
  python3 tools/backup.py restore [--dest DIR] [--force]
        разложить по местам (память Claude — под путь проекта); существующие файлы без --force не трогает

core.bin — память Claude, личный план, кандидаты, archive.py, справочник лиц и стоп-лист, рабочие
файлы альбомов (ключи, отбор, правки, имена). archive.bin — индекс архива телефона (с векторами лиц).
Оригиналы, миниатюры, листы, token.txt и модели не входят.
"""
import argparse, base64, getpass, glob, gzip, hashlib, io, json, os, re, secrets, sys, tarfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
PUB = os.path.join(BASE, "backup", "public.json")
MAGIC = b"PABK1"
STATE = os.path.join(BASE, "backup", ".state.json")  # хеши последних сохранённых копий (не в git)
MEM = "memory"                                       # память Claude в архиве — под этим именем
SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}          # ~128 МБ памяти и ~0.5 с на одну попытку подбора
RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw
b64, unb64 = (lambda b: base64.b64encode(b).decode()), base64.b64decode


def normalize(phrase):
    """Регистр, «ё», лишние пробелы и переводы строк не важны."""
    return re.sub(r"\s+", " ", phrase.replace("ё", "е").replace("Ё", "Е")).strip().lower()


def read_phrase(path):
    if path:
        lines = [l for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
        return normalize(lines[-1])                  # фраза — последняя непустая строка файла
    return normalize(getpass.getpass("Фраза (ввод не виден): "))


def private_key(phrase, cfg):
    k = Scrypt(salt=unb64(cfg["salt"]), length=32, **{x: cfg[x] for x in ("n", "r", "p")}).derive(phrase.encode())
    return X25519PrivateKey.from_private_bytes(k)


def stream_key(shared, eph, pub):
    return HKDF(hashes.SHA256(), 32, salt=eph + pub, info=b"photosz-backup v1").derive(shared)


def encrypt(data, cfg):
    pub = unb64(cfg["pub"])
    e = X25519PrivateKey.generate()                  # одноразовый ключ на каждое шифрование
    eph = e.public_key().public_bytes(*RAW)
    key = stream_key(e.exchange(X25519PublicKey.from_public_bytes(pub)), eph, pub)
    nonce = secrets.token_bytes(12)
    return MAGIC + eph + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def decrypt(blob, cfg, phrase):
    if blob[:5] != MAGIC:
        raise ValueError("не резервная копия")
    eph, nonce, ct = blob[5:37], blob[37:49], blob[49:]
    priv = private_key(phrase, cfg)
    pub = priv.public_key().public_bytes(*RAW)
    if b64(pub) != cfg["pub"]:
        raise ValueError("фраза не подходит")
    return AESGCM(stream_key(priv.exchange(X25519PublicKey.from_public_bytes(eph)), eph, pub)).decrypt(nonce, ct, MAGIC)


def load_cfg():
    return json.load(open(PUB))


def memory_dir(base=BASE):
    """Папка памяти Claude Code для проекта: путь проекта, где всё кроме букв и цифр — «-»."""
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", base), "memory")


def album_roots():
    roots = [os.path.dirname(os.path.dirname(p)) for p in glob.glob(os.path.join(BASE, "*", "work", "publish.json"))]
    roots += [os.path.dirname(os.path.dirname(p)) for p in glob.glob(os.path.join(BASE, ".*", "work", "publish.json"))]
    roots += [os.path.dirname(os.path.dirname(p)) for p in glob.glob(os.path.join(BASE, "phone-index", "*", "work", "publish.json"))]
    return sorted(set(roots))


def files_of(part):
    """[(путь в архиве, путь на диске)] для части core/archive."""
    out = []
    add = lambda path: out.append((os.path.relpath(path, BASE), path)) if os.path.isfile(path) else None
    if part == "core":
        for f in sorted(glob.glob(os.path.join(memory_dir(), "*"))):
            if os.path.isfile(f):
                out.append((MEM + "/" + os.path.basename(f), f))
        for f in ("plan.private.md", "album-candidates.md", "tools/archive.py", ".faces/never.npz", ".faces/people.npz",
                  "phone-index/vote/album/album.json", "cloudflare.json"):   # страница выбора собрана вручную, build её не восстановит
            add(os.path.join(BASE, f))
        for root in album_roots():
            w = os.path.join(root, "work")
            pats = ["*.json", "access.key", "access.salt", "faces.npz", "extras/*.json", "boards/**/*.json"]
            for pat in pats:
                for f in sorted(glob.glob(os.path.join(w, pat), recursive=True)):
                    add(f)
    else:
        for pat in ("*.json", "*.jsonl", "*.npz", "*.txt"):
            for f in sorted(glob.glob(os.path.join(BASE, "phone-index", pat))):
                add(f)
    return sorted(set(out))


def pack(files):
    """Детерминированный tar.gz (одинаковые файлы → одинаковые байты) + MANIFEST.json с sha256."""
    man = {name: hashlib.sha256(open(p, "rb").read()).hexdigest() for name, p in files}
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        def put(name, data):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode = len(data), 0, 0o600
            tar.addfile(ti, io.BytesIO(data))
        put("MANIFEST.json", json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True).encode())
        for name, p in files:
            put(name, open(p, "rb").read())
    return buf.getvalue(), man


def unpack(data):
    tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    man = json.loads(files.pop("MANIFEST.json"))
    bad = [n for n, h in man.items() if hashlib.sha256(files.get(n, b"")).hexdigest() != h]
    return files, man, bad


def parts(which):
    return ["core", "archive"] if which == "all" else [which]


def cmd_save(which):
    cfg = load_cfg()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    for part in parts(which):
        files = files_of(part)
        if not files:
            print(f"{part}: нечего сохранять")
            continue
        data, man = pack(files)
        h = hashlib.sha256(data).hexdigest()
        out = os.path.join(BASE, "backup", part + ".bin")
        if state.get(part) == h and os.path.exists(out):
            print(f"{part}: без изменений ({len(man)} файлов)")
            continue
        open(out, "wb").write(encrypt(data, cfg))
        state[part] = h
        print(f"{part}: {len(man)} файлов, {len(data) / 1048576:.1f} МБ сжато → {out}")
    json.dump(state, open(STATE, "w"), indent=1)


def open_part(part, phrase, cfg):
    p = os.path.join(BASE, "backup", part + ".bin")
    if not os.path.exists(p):
        return None
    return unpack(decrypt(open(p, "rb").read(), cfg, phrase))


def target(name, dest):
    if name.startswith(MEM + "/"):
        return os.path.join(memory_dir(dest) if dest == BASE else os.path.join(dest, "_claude_memory"),
                            name[len(MEM) + 1:])
    return os.path.join(dest, name)


def cmd_check(which, phrase_file):
    cfg, phrase, ok = load_cfg(), read_phrase(phrase_file), True
    for part in parts(which):
        r = open_part(part, phrase, cfg)
        if r is None:
            print(f"{part}: копии нет")
            continue
        files, man, bad = r
        ok &= not bad
        cur = dict(files_of(part))
        changed = [n for n in man if n in cur and hashlib.sha256(open(cur[n], "rb").read()).hexdigest() != man[n]]
        missing_here = [n for n in man if n not in cur]
        new_here = [n for n in cur if n not in man]
        print(f"{part}: {len(man)} файлов, целостность {'OK' if not bad else 'ОШИБКА: ' + ', '.join(bad[:5])}; "
              f"с текущими: совпадает {len(man) - len(changed) - len(missing_here)}, изменено {len(changed)}, "
              f"нет на диске {len(missing_here)}, новых (ещё не в копии) {len(new_here)}")
    sys.exit(0 if ok else 1)


def cmd_restore(dest, force, phrase_file):
    dest = os.path.abspath(dest or BASE)
    cfg, phrase = load_cfg(), read_phrase(phrase_file)
    for part in ("core", "archive"):
        r = open_part(part, phrase, cfg)
        if r is None:
            continue
        files, man, bad = r
        if bad:
            sys.exit(f"{part}: копия повреждена: {bad[:5]}")
        wrote = skipped = 0
        for name, data in files.items():
            t = target(name, dest)
            if os.path.exists(t) and not force:
                skipped += 1
                continue
            os.makedirs(os.path.dirname(t), exist_ok=True)
            open(t, "wb").write(data)
            wrote += 1
        print(f"{part}: записано {wrote}, пропущено (уже есть) {skipped}")
    print("память Claude →", memory_dir(dest) if dest == BASE else os.path.join(dest, "_claude_memory"))


def cmd_init(phrase_file):
    if os.path.exists(PUB):
        sys.exit(f"{PUB} уже есть — не перезаписываю (старые копии перестали бы открываться)")
    phrase = read_phrase(phrase_file)
    words = phrase.split(" ")
    print(f"фраза: {len(words)} слов, {len(phrase)} символов")
    cfg = {"v": 1, "kdf": "scrypt", **SCRYPT, "salt": b64(secrets.token_bytes(16))}
    cfg["pub"] = b64(private_key(phrase, cfg).public_key().public_bytes(*RAW))
    os.makedirs(os.path.dirname(PUB), exist_ok=True)
    json.dump(cfg, open(PUB, "w"), indent=1)
    print("открытый ключ →", PUB)


def cmd_selftest(phrase_file):
    cfg = load_cfg()
    sample = ("проверка резервной копии · " + secrets.token_hex(8)).encode()
    blob = encrypt(sample, cfg)                      # шифруем без фразы — только открытым ключом
    print(f"зашифровано открытым ключом: {len(blob)} байт")
    try:
        ok = decrypt(blob, cfg, read_phrase(phrase_file)) == sample
    except Exception as e:
        sys.exit(f"НЕ РАСШИФРОВАЛОСЬ: {e or type(e).__name__}")
    print("расшифровано фразой и совпало" if ok else "РАСШИФРОВАЛОСЬ, НО НЕ СОВПАЛО")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init", "selftest", "save", "check", "restore"])
    ap.add_argument("part", nargs="?", default="all", choices=["core", "archive", "all"])
    ap.add_argument("--phrase-file")
    ap.add_argument("--dest")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"init": lambda: cmd_init(a.phrase_file), "selftest": lambda: cmd_selftest(a.phrase_file),
     "save": lambda: cmd_save(a.part), "check": lambda: cmd_check(a.part, a.phrase_file),
     "restore": lambda: cmd_restore(a.dest, a.force, a.phrase_file)}[a.cmd]()


if __name__ == "__main__":
    main()
