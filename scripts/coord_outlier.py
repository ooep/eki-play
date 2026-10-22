#!/usr/bin/env python3
"""Line-region outlier check for all station coordinates (report first).

The article-based audit was unreliable because eki-wiki's 词条 links are derived from the
name, so same-name stations can point at another prefecture's article. What *is*
reliable is that every station of one line lies in one region. For each file we take
the median centre of its placed stations (computed while excluding the station under
test) and the 98th percentile of that line's own spread; a station further away than
max(98pct × 1.5, 40 km) is an outlier that almost certainly matched a same-name node
somewhere else.

Usage:
  coord_outlier.py            -> writes stats/coord_outliers.json, changes nothing
  coord_outlier.py --apply    -> for each outlier, adopt an OSM node with the same
                                 normalised name that lies inside the line region;
                                 otherwise keep the point but mark データ品質.座標="線外疑い"
"""
import datetime
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import OsmDb, hav
from coord_fix_mismatch import strip_osm_pool
from coord_pool_fix import add_pois, log, norm, save

LINES = "/data/eki-play/data/lines"
OUT = "/data/work/stats/coord_outliers.json"
DAY = datetime.date.today().isoformat()


def profile(coords, skip):
    xs = [c for i, c in enumerate(coords) if i != skip and c]
    if len(xs) < 8:
        return None
    la = statistics.median([p[0] for p in xs])
    lo = statistics.median([p[1] for p in xs])
    ds = sorted(hav(la, lo, *p) for p in xs)
    return (la, lo, max(ds[int(0.98 * (len(ds) - 1))] * 1.5, 40000.0))


def main(apply):
    files = sorted(glob.glob(os.path.join(LINES, "*.json")))
    osmdb = OsmDb()
    flags = []
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        coords = [((st.get("位置") or {}).get("坐标") or {}) for st in d["车站"]]
        pts = [(c.get("纬度"), c.get("经度")) if c.get("纬度") else None for c in coords]
        for i, st in enumerate(d["车站"]):
            if not pts[i]:
                continue
            prof = profile(pts, i)
            if not prof:
                continue
            dist = hav(prof[0], prof[1], *pts[i])
            if dist <= prof[2]:
                continue
            flags.append({"file": os.path.basename(p), "path": p, "idx": i,
                          "name": st["名称"]["日文"], "orig": st["名称"].get("日文原名"),
                          "line": (d.get("线路") or {}).get("日文"),
                          "dist_m": dist, "limit_m": round(prof[2]),
                          "centre": [prof[0], prof[1]]})
        del d
    flags.sort(key=lambda x: -x["dist_m"])
    log(f"outliers={len(flags)} of 16951 placed stations")

    fixed = marked = 0
    if apply:
        by_file = {}
        for f in flags:
            by_file.setdefault(f["path"], []).append(f)
        for p, items in sorted(by_file.items()):
            d = json.load(open(p, encoding="utf-8"))
            prof_cache = {}
            for f in items:
                st = d["车站"][f["idx"]]
                key = (f["centre"][0], f["centre"][1])
                if key not in prof_cache:
                    prof_cache[key] = profile(
                        [((s.get("位置") or {}).get("坐标") or {}).get("纬度") and
                         ((s["位置"]["坐标"]["纬度"]), (s["位置"]["坐标"]["经度"]))
                         for s in d["车站"]], f["idx"])
                cen = prof_cache[key]
                la_c, lo_c, lim = cen
                cand = None
                for r in osmdb.con.execute(
                        "SELECT rid,name,kind,lat,lon FROM st WHERE name LIKE ?",
                        (norm(f["name"])[:2] + "%",)):
                    if norm(r["name"]) != norm(f["name"]):
                        continue
                    dl = hav(la_c, lo_c, r["lat"], r["lon"])
                    if dl <= min(lim, 60000) and (cand is None or dl < cand[0]):
                        cand = (dl, r)
                pos = st["位置"]
                if cand:
                    old = dict(pos["坐标"])
                    strip_osm_pool(st)
                    got = add_pois(st, cand[1]["lat"], cand[1]["lon"], osmdb,
                                   f"OpenStreetMap (Japan extract, ODbL)（線内照合で再確定・{cand[1]['rid']}）")
                    st["データ品質"]["OSM施設数"] = got
                    pos["坐标"] = {"纬度": round(cand[1]["lat"], 6),
                                  "经度": round(cand[1]["lon"], 6),
                                  "出处": f"OpenStreetMap {cand[1]['rid']} ({cand[1]['kind'] or 'n/a'})、線内照合"}
                    pos["坐标修正"] = {"旧坐标": old, "旧偏差m": f["dist_m"],
                                     "線内距離m": cand[0], "結果": "keep-fix", "日付": DAY,
                                     "根拠": "同一线路の他駅分布（中央値＋98パーセンタイル）から外れていた"
                                             "同名ノードを、線内に存在する正しいノードへ差替え"}
                    fixed += 1
                else:
                    st["データ品質"]["座標"] = "線外疑い"
                    st["データ品質"]["座標注記"] = {
                        "内容": f"同一线路の他駅分布から {round(f['dist_m']/1000)} km 外れているが、"
                                "線内に同名のOSM駅ノードが存在しないため差替えない（要人手確認）",
                        "中央値中心": [round(la_c, 5), round(lo_c, 5)], "日付": DAY}
                    marked += 1
            save(p, d)
        log(f"applied: re-anchored={fixed} marked-for-review={marked}")

    json.dump({"generated": DAY, "count": len(flags),
               "items": [{k: v for k, v in f.items() if k != "path"} for f in flags[:300]]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for f in flags[:25]:
        log("  ", f["line"], f["name"], f"{round(f['dist_m'] / 1000)}km", f["file"])
    osmdb.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
