#!/usr/bin/env python3
"""Считает расход ИИ по транскриптам Claude Code этого проекта.

Читает все *.jsonl в ~/.claude/projects/<проект>/ (включая транскрипты субагентов),
дедуплицирует сообщения по message.id, группирует по моделям, считает токены,
API-эквивалентную стоимость и время (общее и «активное» — без пауз > IDLE_GAP).

  python3 tools/ai_metrics.py                       # всё время
  python3 tools/ai_metrics.py --since 2026-10-24T21:00+02:00 --until ...
"""
import argparse, glob, json, os
from collections import defaultdict
from datetime import datetime, timedelta

PROJECT_DIR = os.path.expanduser("~/.claude/projects/-mnt-c-suth-photo-alb")
IDLE_GAP = timedelta(minutes=5)

# $ за 1M токенов: input, output, cache read. Запись в кеш: 5m = 1.25×input, 1h = 2×input.
# Источник: claude-api skill (cached 2026-10-06). Cache read у Haiku 5.5 — допущение (0.1×input).
PRICES = {
    "claude-opus-5-5":   (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5":  (0.10,  0.50, 0.01),
}

def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since"); ap.add_argument("--until")
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since) if a.since else None
    until = datetime.fromisoformat(a.until) if a.until else None

    msgs, times = {}, []
    for f in glob.glob(os.path.join(PROJECT_DIR, "**", "*.jsonl"), recursive=True):
        for line in open(f, encoding="utf-8"):
            try: d = json.loads(line)
            except ValueError: continue
            if "timestamp" not in d: continue
            t = ts(d["timestamp"])
            if (since and t < since) or (until and t > until): continue
            times.append(t)
            m = d.get("message")
            if isinstance(m, dict) and m.get("usage") and m.get("id"):
                msgs[m["id"]] = (m.get("model", "?"), m["usage"])

    agg = defaultdict(lambda: defaultdict(int))
    for model, u in msgs.values():
        g = agg[model]
        g["requests"] += 1
        g["input"] += u.get("input_tokens", 0)
        g["output"] += u.get("output_tokens", 0)
        g["cache_read"] += u.get("cache_read_input_tokens", 0)
        cc = u.get("cache_creation") or {}
        w1h = cc.get("ephemeral_1h_input_tokens", 0)
        w5m = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - w1h)
        g["cache_w5m"] += w5m; g["cache_w1h"] += w1h

    total = 0.0
    print(f"{'model':20} {'req':>5} {'input':>9} {'output':>9} {'c.read':>11} {'c.write':>10} {'$':>9}")
    for model, g in sorted(agg.items()):
        pi, po, pr = PRICES.get(model, (0, 0, 0))
        cost = (g["input"]*pi + g["output"]*po + g["cache_read"]*pr
                + g["cache_w5m"]*pi*1.25 + g["cache_w1h"]*pi*2) / 1e6
        total += cost
        flag = "" if model in PRICES else "  (нет цены)"
        print(f"{model:20} {g['requests']:>5} {g['input']:>9} {g['output']:>9} {g['cache_read']:>11} "
              f"{g['cache_w5m']+g['cache_w1h']:>10} {cost:>9.4f}{flag}")
    print(f"ИТОГО ≈ ${total:.4f} (API-эквивалент)")

    if times:
        times.sort()
        active = sum((b - a for a, b in zip(times, times[1:]) if b - a <= IDLE_GAP), timedelta())
        print(f"Время: {times[0]:%Y-%m-%d %H:%M} → {times[-1]:%H:%M} UTC; "
              f"всего {times[-1]-times[0]}, активно ~{active} (паузы > {IDLE_GAP} не считаются)")

if __name__ == "__main__":
    main()
