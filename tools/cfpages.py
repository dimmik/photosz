#!/usr/bin/env python3
"""Зеркало сайта на Cloudflare Pages: прямая загрузка, без git и без GitHub Actions.

  python3 tools/cfpages.py deploy     выложить то, что сейчас в HEAD репозитория (альбомы, корень, robots)
  python3 tools/cfpages.py status     последний деплой и адрес

Токен — claudflare_t.txt (права: Account → Cloudflare Pages → Edit), ID аккаунта — cloudflare.json.
Оба в git не попадают; cloudflare.json входит в зашифрованную копию. Протокол — тот же, что у wrangler:
upload-token → check-missing → upload → upsert-hashes → deployment с манифестом.
"""
import base64, hashlib, io, json, mimetypes, os, subprocess, sys, time, urllib.error, urllib.request, uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.cloudflare.com/client/v4"
PROJECT = "photosz"
HEADERS = "/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n  Referrer-Policy: no-referrer\n"
BATCH_BYTES, BATCH_FILES = 30 * 1024 * 1024, 1000


def conf():
    token = open(os.path.join(BASE, "claudflare_t.txt")).read().strip()
    acc = json.load(open(os.path.join(BASE, "cloudflare.json")))["account"]
    return token, acc


def call(method, url, token, body=None, raw=None, ctype="application/json", tries=4):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    for i in range(tries):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": ctype})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=300))
            if not r.get("success", True):
                raise RuntimeError(r.get("errors"))
            return r.get("result")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                sys.exit(f"Cloudflare {method} {url.split('/v4')[-1]}: {e.code} {e.read()[:300]!r}")
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def site_files():
    """Файлы сайта = то, что опубликовано в HEAD: корень + папки альбомов (по work/publish.json)."""
    slugs = {"albums"}
    for p in subprocess.run(["git", "-C", BASE, "ls-files"], capture_output=True, text=True).stdout.splitlines():
        if p.endswith("/version.txt") and p.count("/") == 1:     # <slug>/version.txt — признак папки альбома
            slugs.add(p.split("/")[0])
    files = [p for p in subprocess.run(["git", "-C", BASE, "ls-files", "--", "index.html", "robots.txt", *slugs],
                                       capture_output=True, text=True).stdout.splitlines()]
    out = {}
    for p in files:   # содержимое из HEAD, а не из рабочей папки (там могут быть несохранённые правки)
        out["/" + p] = subprocess.run(["git", "-C", BASE, "show", "HEAD:" + p], capture_output=True).stdout
    return out


def cmd_deploy():
    token, acc = conf()
    proj = f"{API}/accounts/{acc}/pages/projects"
    if not any(p["name"] == PROJECT for p in call("GET", proj, token) or []):
        call("POST", proj, token, {"name": PROJECT, "production_branch": "main"})
        print(f"создан проект {PROJECT}")
    files = site_files()
    # ключ файла — хеш содержимого и расширения (как у wrangler: 32 hex-символа)
    keyed = {p: (hashlib.sha256(d + os.path.splitext(p)[1].encode()).hexdigest()[:32], d) for p, d in files.items()}
    jwt = call("GET", f"{proj}/{PROJECT}/upload-token", token)["jwt"]
    hashes = sorted({h for h, _ in keyed.values()})
    missing = set(call("POST", f"{API}/pages/assets/check-missing", jwt, {"hashes": hashes}))
    todo = {h: (p, d) for p, (h, d) in keyed.items() if h in missing}
    print(f"файлов: {len(files)}, новых для загрузки: {len(todo)} ({sum(len(d) for _, d in todo.values()) / 1e6:.1f} МБ)", flush=True)
    batch, size, sent = [], 0, 0

    def flush():
        nonlocal batch, size, sent
        if batch:
            call("POST", f"{API}/pages/assets/upload", jwt, batch)
            sent += len(batch)
            print(f"  загружено {sent}/{len(todo)}", flush=True)
        batch, size = [], 0
    for h, (p, d) in todo.items():
        b64 = base64.b64encode(d).decode()
        if batch and (size + len(b64) > BATCH_BYTES or len(batch) >= BATCH_FILES):
            flush()
        batch.append({"key": h, "value": b64, "base64": True,
                      "metadata": {"contentType": mimetypes.guess_type(p)[0] or "application/octet-stream"}})
        size += len(b64)
    flush()
    call("POST", f"{API}/pages/assets/upsert-hashes", jwt, {"hashes": hashes})
    # деплой: multipart с манифестом и _headers (запрет индексации)
    bnd = uuid.uuid4().hex
    body = io.BytesIO()
    for name, val in (("manifest", json.dumps({p: h for p, (h, _) in keyed.items()})), ("branch", "main"), ("_headers", HEADERS)):
        body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{name}\"".encode())
        body.write(b"; filename=\"_headers\"\r\nContent-Type: text/plain" if name == "_headers" else b"")
        body.write(f"\r\n\r\n{val}\r\n".encode())
    body.write(f"--{bnd}--\r\n".encode())
    dep = call("POST", f"{proj}/{PROJECT}/deployments", token, raw=body.getvalue(), ctype=f"multipart/form-data; boundary={bnd}")
    sub = (call("GET", f"{proj}/{PROJECT}", token) or {}).get("subdomain")
    print(f"деплой {dep['id'][:8]}: {dep.get('url')}\nсайт: https://{sub}/")


def cmd_status():
    token, acc = conf()
    p = call("GET", f"{API}/accounts/{acc}/pages/projects/{PROJECT}", token)
    d = p.get("latest_deployment") or {}
    print(f"https://{p['subdomain']}/  последний деплой {d.get('created_on', '?')[:19]} {d.get('latest_stage', {}).get('status')}")


if __name__ == "__main__":
    {"deploy": cmd_deploy, "status": cmd_status}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
