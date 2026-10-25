#!/usr/bin/env python3
"""Этап 2: зашифрованная копия альбома для статического хостинга (GitHub Pages).

  python3 tools/publish.py "<альбом>" --repo . --slug sf-2026-10

Берёт собранный album/ (album.json + media/), шифрует album.json и каждый медиафайл
AES-256-GCM ключом из PBKDF2-SHA256(ключ доступа, соль). Пишет <repo>/<slug>/index.html
(экран ключа + расшифровщик) и <repo>/<slug>/data/*.bin с непрозрачными именами.

Ключ доступа свой у каждого альбома: генерируется один раз и хранится в work/access.key
(вне репозитория), поэтому повторная публикация не меняет ни ключ, ни ссылку.
Имена и IV детерминированы (HMAC от ключа и содержимого): неизменившиеся файлы дают
тот же шифротекст и не раздувают историю git.
"""
import argparse, base64, hashlib, hmac, html, json, os, secrets, shutil

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 250_000
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32: без I, L, O, U
TPL = os.path.join(os.path.dirname(__file__), "template.html")

ROOT_INDEX = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>·</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4efe6;color:#8f877b;
font:16px system-ui}@media(prefers-color-scheme:dark){body{background:#15130f}}</style></head>
<body>·</body></html>
"""


def access_key(work):
    p = os.path.join(work, "access.key")
    if os.path.exists(p):
        return open(p).read().strip()
    raw = "".join(secrets.choice(ALPHABET) for _ in range(16))       # 80 бит
    k = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
    with open(p, "w") as f:
        f.write(k + "\n")
    os.chmod(p, 0o600)
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--slug", required=True)
    a = ap.parse_args()
    work, src = os.path.join(a.root, "work"), os.path.join(a.root, "album")
    album = json.load(open(os.path.join(src, "album.json")))

    key_str = access_key(work)
    norm = "".join(c for c in key_str.upper() if c.isalnum())
    salt_p = os.path.join(work, "access.salt")
    if not os.path.exists(salt_p):
        open(salt_p, "wb").write(secrets.token_bytes(16))
    salt = open(salt_p, "rb").read()
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(norm.encode())
    aes = AESGCM(key)
    mac = lambda *parts: hmac.new(key, b"\0".join(parts), hashlib.sha256).digest()

    def seal(name, data):
        fname = mac(b"name", name.encode()).hex()[:20] + ".bin"
        iv = mac(b"iv", name.encode(), hashlib.sha256(data).digest())[:12]
        with open(os.path.join(data_dir, fname), "wb") as f:
            f.write(iv + aes.encrypt(iv, data, None))
        return fname

    out = os.path.join(a.repo, a.slug)
    data_dir = os.path.join(out, "data")
    shutil.rmtree(data_dir, ignore_errors=True)
    os.makedirs(data_dir)
    files = {}
    for n in sorted(os.listdir(os.path.join(src, "media"))):
        files[n] = seal(n, open(os.path.join(src, "media", n), "rb").read())
    album_file = seal("album.json", json.dumps({**album, "files": files}, ensure_ascii=False).encode())

    cfg = {"mode": "enc", "salt": base64.b64encode(salt).decode(), "iter": ITER, "album": album_file}
    page = open(TPL, encoding="utf-8").read()
    page = page.replace("{{TITLE}}", "Фотоальбом")              # настоящее название — только внутри шифра
    page = page.replace("{{CFG}}", json.dumps(cfg))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    # корень репозитория: пустая страница, robots, без Jekyll
    for name, body in (("index.html", ROOT_INDEX), ("robots.txt", "User-agent: *\nDisallow: /\n"),
                       (".nojekyll", "")):
        p = os.path.join(a.repo, name)
        if not os.path.exists(p):
            open(p, "w").write(body)

    size = sum(os.path.getsize(os.path.join(data_dir, f)) for f in os.listdir(data_dir))
    print(f"{len(files)} файлов → {out} ({size / 1e6:.1f} МБ)")
    print(f"KEY {key_str}")


if __name__ == "__main__":
    main()
