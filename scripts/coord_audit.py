#!/usr/bin/env python3
"""Audit every station coordinate against the Japanese Wikipedia article coordinate.

Why this exists: the Phase-A builder ranked OSM candidate nodes by distance to the
previously placed station, but eki-wiki lists 车站 in *alphabetical* order, so that
"previously placed station" was usually a station on the other side of Japan. For
unique names nothing changes, but for homonyms (大森, 金山, 中町 …) a node in the
wrong prefecture could have won. The article coordinate is an independent, per-station
source, so agreement between the two is the check.

Step 1 (this script) only measures: it writes /data/work/stats/coord_audit.json with
  agree / mismatch(>5km) / no_wiki_geo, and never touches the repository.
"""
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W
from guide_core import hav

LINES = "/data/eki-play/data/lines"
OUT = "/data/work/stats/coord_audit.json"


def log(*a):
    print(f"[{datetime.datetime.now():%H:%M:%S}]", *a, flush=True)


def main():
    rows = []
    for p in sorted(os.listdir(LINES)):
        if not p.endswith(".json"):
            continue
        d = json.load(open(os.path.join(LINES, p), encoding="utf-8"))
        for i, st in enumerate(d["车站"]):
            c = (st.get("位置") or {}).get("坐标")
            if not c:
                continue
            ln = (st.get("车站条目") or {}).get("链接") or ""
            m = re.search(r"ja\.wikipedia\.org/wiki/([^?#]+)", ln)
            if not m:
                continue
            rows.append((p, i, st["名称"]["日文"], c["纬度"], c["经度"],
                         m.group(1)))
    log(f"stations with coords and an article link: {len(rows)}")

    titles = sorted({r[5] for r in rows})
    geo, redir = {}, {}
    for i in range(0, len(titles), 50):
        ch = titles[i:i + 50]
        code, d = W._get(W.API["ja"], {"action": "query", "titles": "|".join(ch),
                                       "prop": "coordinates", "redirects": "1",
                                       "converttitles": "1", "colimit": "50",
                                       "format": "json", "formatversion": "2"})
        if code != 200 or not d:
            log("  batch failed", code, len(ch))
            continue
        pages = d.get("query", {}).get("pages", [])
        for rd in d.get("query", {}).get("redirects") or []:
            if rd.get("from") and rd.get("to"):
                redir.setdefault(rd["from"], rd["to"])
        for pg in pages:
            co = (pg.get("coordinates") or [None])[0]
            if pg.get("missing") or not co:
                continue
            geo[pg["title"]] = (float(co["lat"]), float(co["lon"]))
        if i % 2000 == 0:
            log(f"  {i}/{len(titles)} titles, geo so far {len(geo)}")
        time.sleep(0.1)

    # normalise the requested/returned title difference (underscores, redirects)
    def lookup(t):
        for k in (t, t.replace("_", " "), redir.get(t), redir.get(t.replace("_", " "))):
            if k and k in geo:
                return geo[k]
        return None

    agree = mism = nogeo = 0
    bad = []
    for p, i, name, la, lo, title in rows:
        g = lookup(title)
        if not g:
            nogeo += 1
            continue
        dist = hav(la, lo, g[0], g[1])
        if dist <= 5000:
            agree += 1
        else:
            mism += 1
            bad.append({"file": p, "idx": i, "name": name, "title": title,
                        "osm": [la, lo], "wiki": [round(g[0], 6), round(g[1], 6)],
                        "dist_m": dist})
    bad.sort(key=lambda x: -x["dist_m"])
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M"), "checked": len(rows),
               "agree": agree, "mismatch": mism, "no_wiki_geo": nogeo,
               "worst": bad[:400]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"agree={agree} mismatch={mism} no_wiki_geo={nogeo} -> {OUT}")


def pages_titles(pages):
    return [pg.get("title") for pg in pages]


if __name__ == "__main__":
    main()
