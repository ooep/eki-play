#!/usr/bin/env python3
"""Extract a compact candidate pool per station for the Phase-B curation agents.

Agents get ONE small file instead of paging through 5-15 MB line JSON:
station coords, the ja-wiki 「駅周辺」 raw text, and the top OSM/wiki candidates
with whatever website / wikipedia / phone / hours fields we already have.

usage: pool.py <cluster-tag A..H> <rank-from> <rank-to>
"""
import json, os, sys

LINES = "/data/eki-play/data/lines"
OUT = "/data/work"
TAG, FR, TO = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

rows = json.load(open(f"{OUT}/curate_{TAG}.json"))[FR:TO]
pool = []
line_cache = {}
for r in rows:
    p = f"{LINES}/{r['线路']}.json"
    d = line_cache.get(p)
    if d is None:
        d = line_cache[p] = json.load(open(p))
    st = next((s for s in d["车站"] if (s.get("名称") or {}).get("词条") == r["词条"]), None)
    if not st:
        continue
    wiki = st.get("车站条目") or {}
    part = st.get("分区") or {}
    cands = []
    for k in ("観光・名所案内", "文化・公共施設", "便民公共服务", "買い物・商店街", "交通接続", "宿泊"):
        for e in (part.get(k) or []):
            cands.append({
                "名称": (e.get("名称") or {}).get("日文"),
                "类别": (e.get("类别") or {}).get("日文"),
                "细分": (e.get("细分") or {}).get("日文"),
                "距m": e.get("距车站m"),
                "官网": e.get("官网"),
                "维基": e.get("维基百科"),
                "电话": e.get("电话"),
                "开放时间": e.get("开放时间"),
            })
    cands.sort(key=lambda c: (c["距m"] is None, c["距m"] or 9e9))
    pool.append({
        "车站": r["词条"], "日文原名": r.get("日文原名"), "线路": r["线路"],
        "坐标": r.get("坐标"),
        "优先分": r.get("优先分"),
        "维基駅周辺原文": ((wiki.get("駅周辺原文") or {}).get("日文") or "")[:2500],
        "维基条目链接": wiki.get("链接"),
        "维基首段": (((wiki.get("首段") or {}).get("日文") or "").split("}}")[-1] or "")[:600],
        "駅周辺链接": [{"target": t, "label": l} for t, l in (st.get("_駅周辺リンク") or [])][:60],
        "候选设施": cands[:70],
        "站内公式链接": [u for u in (st.get("公式リンク") or []) if "|" not in u][:12],
    })
    line_cache = {} if len(line_cache) > 3 else line_cache

fn = f"{OUT}/pool_{TAG}_{FR}-{TO}.json"
json.dump(pool, open(fn, "w"), ensure_ascii=False, indent=1)
print(fn, "stations=", len(pool), "bytes=", os.path.getsize(fn))
