#!/usr/bin/env python3
"""Regenerate the Phase-B curation priority lists from the COMPLETE data/lines set.

score = 观光类记录*2 + 駅周辺链接*3 + 駅周辺原文字数/40  (bigger = richer raw material)
Deduplicated by 词条 (a station is listed on several lines), top N per cluster.
"""
import json, os, sys

LINES = "/data/eki-play/data/lines"
OUT = "/data/work"


def sight_count(st):
    n = 0
    for k in ("観光・名所案内", "文化・公共施設"):
        n += len((st.get("分区") or {}).get(k) or [])
    return n


def main():
    clusters = json.load(open(f"{OUT}/curated_clusters.json"))
    topn = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    cache = {}
    for c in clusters:
        cand = {}
        for line in c["lines"]:
            p = f"{LINES}/{line}.json"
            if not os.path.exists(p):
                continue
            d = cache.get(p)
            if d is None:
                d = cache[p] = json.load(open(p))
            for st in d["车站"]:
                nm = st.get("名称") or {}
                key = nm.get("词条") or nm.get("日文")
                if not key:
                    continue
                wiki = st.get("车站条目") or {}
                links = st.get("_駅周辺リンク") or []
                chars = len(wiki.get("駅周辺原文") or "")
                sc = sight_count(st) * 2 + len(links) * 3 + chars / 40
                if key in cand and cand[key]["优先分"] >= sc:
                    continue
                cand[key] = {"优先分": round(sc, 1), "词条": key,
                             "日文原名": nm.get("日文原名"), "线路": line,
                             "观光类记录": sight_count(st), "駅周辺链接": len(links),
                             "原文字数": chars,
                             "坐标": (st.get("位置") or {}).get("坐标")}
        rows = sorted(cand.values(), key=lambda r: -r["优先分"])[:topn]
        tag = c["cluster"].split("-")[0]
        json.dump(rows, open(f"{OUT}/curate_{tag}.json", "w"), ensure_ascii=False, indent=1)
        print(f"{c['cluster']}: 候補{len(cand):,} -> {len(rows)}")


if __name__ == "__main__":
    main()
