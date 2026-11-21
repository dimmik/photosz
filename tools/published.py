#!/usr/bin/env python3
"""Что из уже опубликованного пропало бы при новой публикации альбома.

  python3 tools/published.py "<альбом>" [slug]     → список id, которые когда-то были в <slug>/data, а в album/ их нет

Имена файлов в <slug>/data — HMAC от имени медиа (<id>.jpg, <id>_t.jpg, <id>.mp4, <id>_p.jpg) ключом альбома
(см. publish.py), поэтому по work/meta.json и ключу можно узнать, какие кадры публиковались во всех версиях
(история git). Правило: из выданного альбома без явного «да» пользователя ничего не убираем.
"""
import hashlib, hmac, json, os, subprocess, sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SUFFIXES = (".jpg", "_t.jpg", ".mp4", "_p.jpg")


def missing(root, slug, repo_dir=REPO):
    sys.path.insert(0, HERE)
    import publish
    work = os.path.join(root, "work")
    salt_p = os.path.join(work, "access.salt")
    if not os.path.exists(salt_p):
        return []                                          # ещё не публиковался
    norm = "".join(c for c in publish.access_key(work).upper() if c.isalnum())
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=open(salt_p, "rb").read(),
                     iterations=publish.ITER).derive(norm.encode())
    fname = lambda n: hmac.new(key, b"\0".join([b"name", n.encode()]), hashlib.sha256).digest().hex()[:20] + ".bin"
    names = {fname(r["id"] + s): r["id"] for r in json.load(open(os.path.join(work, "meta.json"))) for s in SUFFIXES}
    git = lambda *a: subprocess.run(["git", "-C", repo_dir, *a], capture_output=True, text=True).stdout
    ever = set()
    for c in git("log", "--format=%H", "--", slug).split():
        for path in git("ls-tree", "--name-only", "-r", c, f"{slug}/data").split():
            fid = names.get(path.rsplit("/", 1)[-1])
            if fid:
                ever.add(fid)
    album = json.load(open(os.path.join(root, "album", "album.json")))
    now = {it["id"] for c in album["chapters"] for it in c["items"]} | {album["cover"]}
    return sorted(ever - now)


if __name__ == "__main__":
    root = sys.argv[1]
    pj = json.load(open(os.path.join(root, "work", "publish.json")))
    slug = sys.argv[2] if len(sys.argv) > 2 else pj["slug"]
    repo_dir = REPO if pj.get("repo", "photosz") == "photosz" else os.path.join(REPO, ".repos", pj["repo"])
    gone = missing(root, slug, repo_dir)
    print("\n".join(gone) if gone else "ничего не пропало")
    sys.exit(1 if gone else 0)
