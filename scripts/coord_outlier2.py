#!/usr/bin/env python3
"""Second attempt at the 31 outliers coord_outlier.py could only flag.

Those stations sit far outside their own line's station distribution, but no OSM node
with the identical normalised name exists inside the region — usually because OSM names
the node slightly differently (森駅 vs 森) or maps it as a way rather than a node.
For each of them we ask the Japanese Wikipedia article for that station (plus the
prefecture-qualified variants eki-wiki uses) and accept the point only when it falls
inside the line region. Nothing outside the region is ever adopted, which is exactly
the mistake coord_fix_mismatch.py made.
"""
import datetime
import glob
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W
from guide_core import OsmDb, hav
from coord_fix_mismatch import strip_osm_pool
from coord_outlier import profile
from coord_pool_fix import add_pois, collect_missing, log, norm, save

DAY = datetime.date.today().isoformat()
LINES = "/data/eki-play/data/lines"


def article_candidates(st, orig):
    out = []
    ln = (st.get("车站条目") or {}).get("链接") or ""
    m = re.search(r"ja\.wikipedia\.org/wiki/([^?#]+)", ln)
    if m:
        out.append(m.group(1))
    name = st["名称"]["日文"]
    for suf in ("", "(岐阜県)", "(鹿児島県)", "(兵庫県)", "(静岡県)"):
        out.append(name + suf)
    base = re.sub(r"\s*\((.*?)\)\s*", "", orig or "")
    if base and base != name:
        out.append(base)
    return list(dict.fromkeys(out))


def page_coord(title):
    code, d = W._get(W.API["ja"], {"action": "query", "titles": title, "redirects": "1",
                                  "prop": "coordinates", "format": "json",
                                  "formatversion": "2"})
    if code != 200 or not d:
        return None, None
    for pg in d.get("query", {}).get("pages", []):
        if pg.get("missing"):
            continue
        co = (pg.get("coordinates") or [None])[0]
        if co and co.get("lat") is not None:
            return (float(co["lat"]), float(co["lon"])), pg["title"]
    return None, None


def osm_near_any(osmdb, name, la, lo, lim):
    """Any OSM station-ish node close to the verified article point."""
    key = norm(name)
    best = None
    rows = list(osmdb.con.execute("SELECT rid,name,kind,lat,lon FROM st"))
    for r in rows:
        n = norm(r["name"])
        if not n or (n != key and not (key and n.startswith(key))):
            continue
        d = hav(la, lo, r["lat"], r["lon"])
        if d <= min(lim, 8000) and (best is None or d < best[0]):
            best = (d, r)
    return best


def main():
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
            if prof:
                targets.append((p, i, st, prof))
        del d
    log(f"flagged stations to retry: {len(targets)}")
    if not targets:
        return

    osmdb = OsmDb()
    by_file, solved = {}, {}
    for p, i, st, (la_c, lo_c, lim) in targets:
        name = st["名称"]["日文"]
        for t in article_candidates(st, st["名称"].get("日文原名")):
            g, canon = page_coord(t)
            if not g:
                continue
            if hav(la_c, lo_c, *g) > lim:
                continue                      # wrong-prefecture homonym again — skip
            n = osm_near_any(osmdb, name, g[0], g[1], lim)
            solved[(p, i)] = ((n[1]["lat"], n[1]["lon"],
                               f"OpenStreetMap {n[1]['rid']} ({n[1]['kind'] or 'n/a'})、記事座標±8km照合",
                               "OSM", canon) if n else
                              (g[0], g[1], f"日本語版ウィキペディア「{canon}」座標", "Wikipedia", canon))
            by_file.setdefault(p, []).append(i)
            break
    log(f"resolved inside line region: {len(solved)}")

    n_poi = 0
    for p, idxs in sorted(by_file.items()):
        d = json.load(open(p, encoding="utf-8"))
        for i in idxs:
            la, lo, src, kind, canon = solved[(p, i)]
            st = d["车站"][i]
            pos = st["位置"]
            old = dict(pos["坐标"])
            strip_osm_pool(st)
            note = {"OSM": "OpenStreetMap (Japan extract, ODbL)",
                    "Wikipedia": "日本語版ウィキペディア (CC BY-SA 4.0)"}[kind] + f"（線内照合・{src}）"
            got = add_pois(st, la, lo, osmdb, note)
            st["データ品質"]["OSM施設数"] = got
            st["データ品質"]["座標"] = kind + "（線内照合で確定）"
            st["データ品質"].pop("座標注記", None)
            pos["坐标"] = {"纬度": round(la, 6), "经度": round(lo, 6), "出处": src}
            pos["坐标修正"] = {"旧坐标": old, "結果": "keep-fix", "記事": canon, "日付": DAY,
                             "根拠": "線内分布（中央値＋98パーセンタイル）内で記事座標を確認し、"
                                     "その地点に最も近いOSMノードを再採用"}
            n_poi += got
        save(p, d)
    log(f"new POI records={n_poi}  files={len(by_file)}")
    osmdb.close()


if __name__ == "__main__":
    main()
