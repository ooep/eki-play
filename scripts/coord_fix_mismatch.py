#!/usr/bin/env python3
"""Fix the coordinate mismatches found by coord_audit.py.

Each entry in stats/coord_audit.json['worst'] is a station whose stored OSM coordinate
is more than 5 km from the coordinate in its own Japanese Wikipedia article — in
practice a same-name station in another prefecture (中郡駅 Kagoshima matched to the
Chiba node, 新町駅 Kumamoto matched to Hiroshima …). Because the POI pool is built
around that coordinate, the whole record set for those stations was describing the
wrong city, so the pool is rebuilt, not just the point.

Resolution order per station:
  1. an OSM station node with the same normalised name within 2 km of the article
     coordinate (keeps the pool anchored on OSM geometry),
  2. otherwise the article coordinate itself, labelled as Wikipedia-derived.
The previous value and the deviation are kept in 位置.坐标修正.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import OsmDb, hav
from coord_pool_fix import add_pois, log, norm, save

LINES = "/data/eki-play/data/lines"
REPORT = "/data/work/stats/coord_audit.json"
DAY = datetime.date.today().isoformat()


def strip_osm_pool(st):
    """Remove OSM-derived records (they belong to the old, wrong coordinate)."""
    b = st.get("分区") or {}
    removed = 0
    out = {}
    for k, v in b.items():
        keep = []
        for r in v:
            if any(str(s).startswith("OpenStreetMap") for s in (r.get("来源") or [])):
                removed += 1
                continue
            keep.append(r)
        out[k] = keep
    st["分区"] = out
    st["統計"] = {k: len(v) for k, v in out.items()}
    return removed


def nearest_osm(osmdb, entry, la, lo):
    key = norm(entry)
    best = None
    for r in osmdb.con.execute("SELECT rid,name,kind,lat,lon FROM st WHERE name LIKE ?",
                               (key[:2] + "%",)):
        if norm(r["name"]) != key:
            continue
        d = hav(la, lo, r["lat"], r["lon"])
        if d <= 2000 and (best is None or d < best[0]):
            best = (d, r)
    return best[1] if best else None


def main():
    rep = json.load(open(REPORT, encoding="utf-8"))
    worst = rep["worst"]
    log(f"mismatches to fix: {len(worst)}")
    osmdb = OsmDb()
    by_file = {}
    for x in worst:
        by_file.setdefault(os.path.join(LINES, x["file"]), []).append(x)

    fixed = dropped = 0
    for p, items in sorted(by_file.items()):
        d = json.load(open(p, encoding="utf-8"))
        for x in items:
            st = d["车站"][x["idx"]]
            old = (st.get("位置") or {}).get("坐标") or {}
            la, lo = x["wiki"]
            n = nearest_osm(osmdb, x["name"], la, lo)
            if n:
                src = f"OpenStreetMap {n['rid']} ({n['kind'] or 'n/a'})、Wikipedia座標±2kmで照合"
                kind, la, lo = "OSM", n["lat"], n["lon"]
                url = x["title"]
            else:
                src = f"日本語版ウィキペディア「{x['title']}」座標"
                kind = "Wikipedia"
                url = x["title"]
            removed = strip_osm_pool(st)
            note = {"OSM": "OpenStreetMap (Japan extract, ODbL)",
                    "Wikipedia": "日本語版ウィキペディア (CC BY-SA 4.0)"}[kind] + f"（座標修正後・{src}）"
            got = add_pois(st, la, lo, osmdb, note)
            st["データ品質"]["OSM施設数"] = got
            pos = st["位置"]
            pos["坐标"] = {"纬度": round(la, 6), "经度": round(lo, 6), "出处": src}
            pos["坐标可用"] = True
            pos.pop("坐标来源", None)
            pos["坐标修正"] = {
                "旧坐标": old, "偏差m": x["dist_m"],
                "根拠": "同名の別県駅と一致していたため、記事座標を基準に再確定",
                "検証元": f"https://ja.wikipedia.org/wiki/{url.replace(' ', '_')}",
                "旧POOL除去件数": removed, "新POOL件数": got, "検証日": DAY}
            dropped += removed
            fixed += 1
        save(p, d)
    log(f"fixed={fixed}  old POI records removed={dropped}")
    osmdb.close()


if __name__ == "__main__":
    main()
