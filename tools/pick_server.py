#!/usr/bin/env python3
"""Одноразовая страница «выбери свои кадры» для владельца телефона (или любого участника поездки).

  python3 tools/pick_server.py "<альбом>" [--match _s24] [--tunnel] [--minutes 720]

Показывает кадры альбома, у которых в id есть --match (например, все с второго телефона), сеткой по
времени. Те, что уже в альбоме, помечены «уже в альбоме». Человек отмечает «★ хочу в альбом»
(для невзятых) или «✕ убрать» (для взятых), может оставить комментарий и жмёт «Отправить» один раз.
Решение пишется в <альбом>/work/pick_result.json, сервер и туннель гаснут. Без отправки — через --minutes.

Сервер — на 127.0.0.1, путь случайный (он и есть ключ), запросы не логируются. С --tunnel — cloudflared
quick tunnel (https://….trycloudflare.com). Картинки — уменьшенные копии без EXIF/GPS (work/pick/),
видео — кадр-обложка. Печатает только ссылку и итог.
"""
import argparse, html, http.server, json, os, re, secrets, subprocess, sys, threading, time, urllib.parse
from datetime import datetime

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MAX_BODY = 200_000


def prepare(root, ids, meta):
    out = os.path.join(root, "work", "pick")
    os.makedirs(out, exist_ok=True)
    for fid in ids:
        full, thumb = os.path.join(out, fid + ".jpg"), os.path.join(out, fid + "_t.jpg")
        if os.path.exists(full) and os.path.exists(thumb):
            continue
        src = os.path.join(root, "src", meta[fid]["file"])
        if meta[fid]["type"] == "video":
            tmp = os.path.join(out, fid + "_v.jpg")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", src, "-frames:v", "1", tmp], check=True)
            src = tmp
        im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")      # EXIF/GPS не переносим
        im.thumbnail((1600, 1600), Image.LANCZOS)
        im.save(full, quality=82, optimize=True, progressive=True)
        im.thumbnail((480, 480), Image.LANCZOS)
        im.save(thumb, quality=78, optimize=True, progressive=True)
    return out


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">
<title>Выбор кадров</title>
<style>
:root{--bg:#f4efe7;--card:#fffdf9;--ink:#1f1b16;--mute:#7a6f63;--acc:#c4582f;--ok:#2f7d4f;--bad:#b3261e;--line:#e3d9cc}
@media (prefers-color-scheme:dark){:root{--bg:#191612;--card:#24201b;--ink:#f1ebe2;--mute:#a89c8e;--acc:#e07a4f;--ok:#5cbf83;--bad:#ef6b62;--line:#3a332b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-bottom:120px}
header{padding:22px 16px 6px;max-width:1100px;margin:0 auto}
h1{font:600 26px/1.15 Georgia,"Times New Roman",serif;margin:0 0 6px}
.lead{color:var(--mute);margin:0 0 4px;max-width:640px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:14px;color:var(--mute);margin:10px 0 0}
.legend b{color:var(--ink);font-weight:600}
h2{font:600 15px/1 system-ui;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:22px 0 10px}
main{max-width:1100px;margin:0 auto;padding:0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.tile{position:relative;background:var(--card);border-radius:12px;overflow:hidden;border:2px solid transparent;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.tile img{display:block;width:100%;aspect-ratio:1;object-fit:cover;cursor:zoom-in;background:var(--line)}
.tile .bar{display:flex;align-items:center;justify-content:space-between;gap:6px;padding:6px 8px}
.tile .t{font-size:13px;color:var(--mute)}
.tile .tg{white-space:nowrap;font-size:14px;padding:5px 10px}
.badge{position:absolute;left:6px;top:6px;font-size:12px;font-weight:600;padding:3px 7px;border-radius:99px;background:rgba(0,0,0,.6);color:#fff}
.badge.in{background:var(--ok)}
.vid{position:absolute;right:6px;top:6px;font-size:12px;padding:3px 7px;border-radius:99px;background:rgba(0,0,0,.6);color:#fff}
button{font:inherit;border:0;border-radius:99px;padding:6px 11px;cursor:pointer;background:var(--line);color:var(--ink)}
.tile.add{border-color:var(--acc)} .tile.add .tg{background:var(--acc);color:#fff}
.tile.remove{border-color:var(--bad)} .tile.remove img{opacity:.45} .tile.remove .tg{background:var(--bad);color:#fff}
.dock{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px calc(10px + env(safe-area-inset-bottom))}
.dock .in{max-width:1100px;margin:0 auto;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.dock textarea{flex:1 1 260px;min-height:40px;max-height:120px;border:1px solid var(--line);border-radius:10px;padding:8px 10px;font:inherit;background:var(--bg);color:var(--ink)}
.count{font-size:14px;color:var(--mute);white-space:nowrap}
.send{background:var(--acc);color:#fff;font-weight:600;padding:10px 18px}
.view{position:fixed;inset:0;background:rgba(10,8,6,.94);display:none;flex-direction:column;z-index:10}
.view.on{display:flex}
.view img{flex:1;min-height:0;object-fit:contain;width:100%}
.view .ctl{display:flex;gap:10px;justify-content:center;align-items:center;padding:12px 16px calc(12px + env(safe-area-inset-bottom));flex-wrap:wrap}
.view .ctl button{background:#3a332b;color:#fff;padding:10px 16px}
.view .ctl .tg.add{background:var(--acc)} .view .ctl .tg.remove{background:var(--bad)}
.view .cap{color:#d8cfc3;font-size:14px;text-align:center;padding-top:10px}
.thanks{max-width:520px;margin:18vh auto;padding:0 16px;text-align:center}
</style></head><body>
<header>
 <h1>@@TITLE@@</h1>
 <p class="lead">Это кадры с твоего телефона. Отметь <b>★ в альбом</b> те, которые точно должны попасть в альбом.
 Если какой-то из уже взятых не нравится, нажми у него <b>✕ убрать</b>. Нажми на фото, чтобы посмотреть крупно.</p>
 <div class="legend"><span><b>@@N_IN@@</b> уже в альбоме</span><span><b>@@N_ALL@@</b> всего кадров</span></div>
</header>
<main id="m"></main>
<div class="dock"><div class="in">
 <textarea id="c" placeholder="Комментарий, если хочется (что подписать, что не показывать…)"></textarea>
 <span class="count" id="cnt"></span>
 <button class="send" id="s">Отправить</button>
</div></div>
<div class="view" id="v"><div class="cap" id="vc"></div><img id="vi" alt="">
 <div class="ctl"><button id="vp">←</button><button class="tg" id="vt"></button><button id="vn">→</button><button id="vx">Закрыть</button></div></div>
<script>
const D=@@DATA@@, K=location.pathname.replace(/\\/$/,''), st={};
const lbl=d=>d.in?(st[d.id]==='remove'?'✕ уберём':'убрать'):(st[d.id]==='add'?'★ возьмём':'☆ в альбом');
function toggle(d){ if(d.in) st[d.id]=st[d.id]==='remove'?undefined:'remove'; else st[d.id]=st[d.id]==='add'?undefined:'add'; draw(d); }
function draw(d){ const t=document.getElementById('t_'+d.id); t.className='tile '+(st[d.id]||''); t.querySelector('.tg').textContent=lbl(d);
  const a=D.filter(x=>st[x.id]==='add').length, r=D.filter(x=>st[x.id]==='remove').length;
  document.getElementById('cnt').textContent='в альбом: '+a+(r?' · убрать: '+r:''); if(cur>=0) vshow(); }
let cur=-1; const m=document.getElementById('m'); let day='';
D.forEach((d,i)=>{ if(d.day!==day){ day=d.day; const h=document.createElement('h2'); h.textContent=day; m.appendChild(h);
   const g=document.createElement('div'); g.className='grid'; m.appendChild(g); }
  const t=document.createElement('div'); t.className='tile'; t.id='t_'+d.id;
  t.innerHTML=(d.in?'<span class="badge in">✓ уже в альбоме</span>':'')+(d.video?'<span class="vid">▶ видео</span>':'')+
   '<img loading="lazy" src="'+K+'/t/'+d.id+'.jpg" alt=""><div class="bar"><span class="t">'+d.time+'</span><button class="tg"></button></div>';
  t.querySelector('img').onclick=()=>{cur=i;vshow();}; t.querySelector('.tg').onclick=()=>toggle(d);
  m.lastChild.appendChild(t); draw(d); });
const v=document.getElementById('v');
function vshow(){ const d=D[cur]; v.classList.add('on'); document.getElementById('vi').src=K+'/f/'+d.id+'.jpg';
  document.getElementById('vc').textContent=d.day+', '+d.time+(d.in?' · уже в альбоме':'')+(d.video?' · видео (кадр)':'');
  const b=document.getElementById('vt'); b.textContent=lbl(d); b.className='tg '+(st[d.id]||''); }
document.getElementById('vt').onclick=()=>toggle(D[cur]);
document.getElementById('vp').onclick=()=>{cur=(cur-1+D.length)%D.length;vshow();};
document.getElementById('vn').onclick=()=>{cur=(cur+1)%D.length;vshow();};
document.getElementById('vx').onclick=()=>{cur=-1;v.classList.remove('on');};
document.addEventListener('keydown',e=>{ if(cur<0)return; if(e.key==='Escape')document.getElementById('vx').click();
  if(e.key==='ArrowLeft')document.getElementById('vp').click(); if(e.key==='ArrowRight')document.getElementById('vn').click(); });
document.getElementById('s').onclick=async()=>{
  const add=D.filter(x=>st[x.id]==='add').map(x=>x.id), remove=D.filter(x=>st[x.id]==='remove').map(x=>x.id), comment=document.getElementById('c').value.trim();
  if(!add.length&&!remove.length&&!comment){ if(!confirm('Ничего не отмечено. Отправить «всё и так хорошо»?')) return; }
  else if(!confirm('Отправить? Изменить потом будет нельзя: страница одноразовая.')) return;
  const r=await fetch(K+'/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({add,remove,comment})});
  document.body.innerHTML='<div class="thanks"><h1>'+(r.ok?'Спасибо!':'Не получилось')+'</h1><p class="lead">'+(r.ok?'Выбор отправлен. Страницу можно закрыть.':'Попробуй ещё раз или напиши Диме.')+'</p></div>'; };
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--match", default="", help="подстрока id (например _s24): какие кадры показывать")
    ap.add_argument("--title", default=None)
    ap.add_argument("--tunnel", action="store_true")
    ap.add_argument("--minutes", type=float, default=720)
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    work = os.path.join(root, "work")
    meta = {r["id"]: r for r in json.load(open(os.path.join(work, "meta.json")))}
    album = json.load(open(os.path.join(root, "album", "album.json")))
    inalb = {it["id"] for c in album["chapters"] for it in c["items"]} | {album["cover"]}
    ids = sorted((f for f in meta if a.match in f), key=lambda f: meta[f]["time"])
    pick_dir = prepare(root, ids, meta)
    data = []
    for f in ids:
        t = datetime.fromisoformat(meta[f]["time"])
        data.append({"id": f, "day": f"{DAYS[t.weekday()]}, {t.day}.{t.month:02d}", "time": t.strftime("%H:%M"),
                     "in": f in inalb, "video": meta[f]["type"] == "video"})
    title = a.title or f"{album.get('title', '')}: какие кадры взять"
    page = (PAGE.replace("@@TITLE@@", html.escape(title)).replace("@@N_IN@@", str(sum(d["in"] for d in data)))
            .replace("@@N_ALL@@", str(len(data))).replace("@@DATA@@", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
    allowed = set(ids)
    key = secrets.token_urlsafe(18)
    done, outcome = threading.Event(), {}
    res_path = os.path.join(work, "pick_result.json")

    class H(http.server.BaseHTTPRequestHandler):
        server_version, sys_version = "pick", ""

        def log_message(self, *x):
            pass

        def send(self, code, body, ctype="text/html; charset=utf-8", cache="no-store"):
            self.send_response(code)
            for k, v in (("Content-Type", ctype), ("Cache-Control", cache), ("Referrer-Policy", "no-referrer"),
                         ("X-Robots-Tag", "noindex"), ("Content-Length", str(len(body)))):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def parts(self):
            p = urllib.parse.urlparse(self.path).path.strip("/").split("/")
            return p[1:] if p and p[0] == key and not done.is_set() else None

        def do_GET(self):
            p = self.parts()
            if p == [] or p == [""]:
                return self.send(200, page.encode())
            if p and len(p) == 2 and p[0] in ("t", "f") and p[1].endswith(".jpg") and p[1][:-4] in allowed:
                fn = os.path.join(pick_dir, p[1][:-4] + ("_t.jpg" if p[0] == "t" else ".jpg"))
                return self.send(200, open(fn, "rb").read(), "image/jpeg", "private, max-age=3600")
            self.send(404, b"Not found", "text/plain")

        def do_POST(self):
            p = self.parts()
            n = int(self.headers.get("Content-Length") or 0)
            if p != ["submit"] or not 0 < n <= MAX_BODY:
                return self.send(404, b"Not found", "text/plain")
            try:
                d = json.loads(self.rfile.read(n))
                res = {"at": datetime.now().isoformat(timespec="seconds"),
                       "add": [i for i in d.get("add", []) if i in allowed],
                       "remove": [i for i in d.get("remove", []) if i in allowed],
                       "comment": str(d.get("comment", ""))[:4000]}
            except Exception:
                return self.send(400, b"bad", "text/plain")
            json.dump(res, open(res_path, "w"), ensure_ascii=False, indent=1)
            outcome["res"] = res
            self.send(200, b"ok", "text/plain")
            done.set()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, tunnel = f"http://127.0.0.1:{srv.server_address[1]}", None
    if a.tunnel:
        tunnel = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", base],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        found = {}

        def watch():
            for line in tunnel.stderr:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and "url" not in found:
                    found["url"] = m.group(0)
        threading.Thread(target=watch, daemon=True).start()
        for _ in range(60):
            if "url" in found or tunnel.poll() is not None:
                break
            time.sleep(0.5)
        if "url" not in found:
            tunnel.terminate()
            sys.exit("cloudflared не дал адрес")
        base = found["url"]
        time.sleep(3)
    print(f"кадров: {len(ids)}, уже в альбоме: {sum(d['in'] for d in data)}", flush=True)
    print(f"open: {base}/{key}/", flush=True)
    ok = done.wait(timeout=a.minutes * 60)
    time.sleep(1)
    srv.shutdown()
    if tunnel:
        tunnel.terminate()
    if not ok:
        sys.exit("никто не отправил; остановлено")
    r = outcome["res"]
    print(f"решение → {res_path}: в альбом {len(r['add'])}, убрать {len(r['remove'])}, "
          f"комментарий: {'есть' if r['comment'] else 'нет'}", flush=True)


if __name__ == "__main__":
    main()
