#!/usr/bin/env python3
"""Final pass for the 31 stations still marked データ品質.座標 = 線外疑い.

coord_outlier.py refused to swap them because no same-name OSM node lay within 60 km of
the line centre; coord_outlier2.py refused the Wikipedia point because those plain
titles are the *other* 森駅 / 高瀬駅 (same name, different prefecture). Both refusals
were correct behaviour, so what remains is to test same-name nodes against the line's
own region and confirm the prefecture with a reverse geocode — one Nominatim call per
candidate, cached, and a station is only moved when the candidate's 都道府県 is the
same as the majority of its line.
"""
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W
from guide_core import OsmDb, hav
from coord_fix_mismatch import strip_osm_pool
from coord_outlier import profile
from coord_pool_fix import add_pois, log, norm, save

LINES = "/data/eki-play/data/lines"
DAY = datetime.date.today().isoformat()
PREF_CACHE = "/data/work/cache/pref_cache.json"
os.makedirs(os.path.dirname(PREF_CACHE), exist_ok=True)

def load_pref():
    try:
        return json.load(open(PREF_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def prefecture(lat, lon, cache):
    key = f"{round(lat, 2)},{round(lon, 2)}"
    if key in cache:
        return cache[key]
    code, d = W.curl("https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat="
                     f"{lat:.5f}&lon={lon:.5f}&zoom=10&accept_language=ja", timeout=25)
    pref = None
    if isinstance(d, dict):
        ad = d.get("address") or {}
        pref = ad.get("province") or ad.get("state") or None
    cache[key] = pref
    json.dump(cache, open(PREF_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    __import__("time").sleep(1.1)          # Nominatim usage policy: max 1 request/s
    return pref


def main():
    cache = load_pref()
    targets = []
    for p in sorted(glob.glob(os.path.join(LINES, "*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        pts = [((s.get("位置") or {}).get("坐标") or {}).get("纬度") and
               ((s["位置"]["坐标"]["纬度"]), (s["位置"]["坐标"]["经度"])) or None
               for s in d["车站"]]
        for i, st in enumerate(d["车站"]):
            if (st.get("データ品質") or {}).get("座標") != "線外疑い":
                continue
            prof = profile(pts, i)
            if not prof:
                continue
            line_pref = prefecture(prof[0], prof[1], cache)
            targets.append((p, i, st, prof, line_pref))
        del d
    log(f"flagged stations: {len(targets)}")

    osmdb = OsmDb()
    solved = {}
    for p, i, st, (la_c, lo_c, lim), line_pref in targets:
        key = norm(st["名称"]["日文"])
        best = None
        for r in osmdb.con.execute("SELECT rid,name,kind,lat,lon FROM st WHERE name LIKE ?",
                                   (key[:2] + "%",)):
            if norm(r["name"]) != key:
                continue
            dl = hav(la_c, lo_c, r["lat"], r["lon"])
            if dl > lim:
                continue
            if (best is None or dl < best[0]):
                best = (dl, r)
        if not best:
            continue
        dl, r = best
        pref = prefecture(r["lat"], r["lon"], cache)
        if not pref or pref != line_pref:
            log(f"  skip {st['名称']['日文']}: candidate {round(r['lat'],3)},"
                f"{round(r['lon'],3)} pref={pref} != line {line_pref}")
            continue
        solved[(p, i)] = (r, dl, pref, line_pref)

    log(f"validated candidates: {len(solved)}")
    by_file = {}
    for (p, i) in solved:
        by_file.setdefault(p, []).append(i)
    n_poi = 0
    for p, idxs in sorted(by_file.items()):
        d = json.load(open(p, encoding="utf-8"))
        for i in idxs:
            r, dl, pref, line_pref = solved[(p, i)]
            st = d["车站"][i]
            pos = st["位置"]
            old = dict(pos["坐标"])
            strip_osm_pool(st)
            src = f"OpenStreetMap {r['rid']} ({r['kind'] or 'n/a'})、線内＋都道府県照合"
            got = add_pois(st, r["lat"], r["lon"], osmdb,
                           f"OpenStreetMap (Japan extract, ODbL)（線内照合・{src}）")
            st["データ品質"]["OSM施設数"] = got
            st["データ品質"]["座標"] = "OSM（線内＋都道府県照合で確定）"
            st["データ品質"].pop("座標注記", None)
            pos["坐标"] = {"纬度": round(r["lat"], 6), "经度": round(r["lon"], 6), "出处": src}
            pos["坐标修正"] = {"旧坐标": old, "結果": "keep-fix", "線内距離m": dl,
                             "都道府県": pref, "日付": DAY,
                             "根拠": "旧座標は同一线路の駅分布と都道府県が不一致。同名ノードのうち"
                                     "線内で、かつ线路の都道府県と一致するものを採用"}
            n_poi += got
        save(p, d)
    log(f"moved={len(solved)}  new POI records={n_poi}")
    osmdb.close()


if __name__ == "__main__":
    main()
