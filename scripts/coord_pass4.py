#!/usr/bin/env python3
"""Fourth geocode pass — final resolution for the stations coord_pass3 rejected.

Root cause found while reviewing the rejects: eki-wiki's 车站 arrays are in
*alphabetical* order, not in line order, so the "distance to the neighbouring entry"
guard used in pass 2/3 compared a station with unrelated stations (保土ケ谷 next to
三ケ根 next to 三ノ宮). Every correct ヶ/ヶ match was thrown out as implausible.

This pass therefore validates identity, not geography:
  1. one Japanese-Wikipedia article per name (full-text search, glyph-normalised title
     must read as the same station),
  2. that article's own page coordinate, or its Wikidata P625,
  3. if the OSM station table has a node with the same normalised name within 3 km of
     the wiki point, the OSM node is preferred (keeps every pool on OSM geometry).

Nothing here is accepted on name similarity alone.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coord_pass3 import page_geo, search_article
from coord_pool_fix import add_pois, collect_missing, hav, log, norm, save

DAY = datetime.date.today().isoformat()


def osm_node(osmdb, entry, lat, lon):
    """OSM station node with the same normalised name within 3 km of the wiki point."""
    key = norm(entry)
    for r in osmdb.con.execute(
            "SELECT rid,name,kind,lat,lon FROM st WHERE name IN (?,?)",
            (entry, entry + "駅")):
        if norm(r["name"]) == key and hav(lat, lon, r["lat"], r["lon"]) <= 3000:
            return r
    rows = [r for r in osmdb.con.execute(
        "SELECT rid,name,kind,lat,lon FROM st WHERE name LIKE ?", (key[:2] + "%",))
        if norm(r["name"]) == key and hav(lat, lon, r["lat"], r["lon"]) <= 3000]
    return rows[0] if rows else None


def main():
    missing = collect_missing()
    log(f"remaining without coordinates: {len(missing)}")
    osmdb = OsmDb()
    by_file, fails = {}, []
    for m in missing:
        arts = []
        if m["title"]:
            t = m["title"].replace("_", " ")
            if norm(t) == norm(m["entry"]):
                arts.append(t)
        for e in (m["entry"], m["orig"]):
            if e:
                a = search_article(e)
                if a and a not in arts:
                    arts.append(a)
        for a in arts:
            g = page_geo(a)
            if not g:
                continue
            la, lo, src, url = g
            n = osm_node(osmdb, m["entry"], la, lo)
            if n:
                src = f"OpenStreetMap {n['rid']} ({n['kind'] or 'n/a'})、ウィキ座標照合"
                url = "https://ja.wikipedia.org/wiki/" + a.replace(" ", "_")
                kind, la, lo = "OSM", n["lat"], n["lon"]
            else:
                kind = "Wikidata" if src.startswith("Wikidata") else "Wikipedia"
            by_file.setdefault(m["file"], []).append((m["idx"], la, lo, src, url, kind, a))
            break
        else:
            fails.append((m["line"], m["entry"], m["orig"]))

    total = sum(len(v) for v in by_file.values())
    log(f"resolved={total} across {len(by_file)} files; unresolved={len(fails)}")

    n_poi = 0
    for p, items in sorted(by_file.items()):
        d = json.load(open(p, encoding="utf-8"))
        for idx, la, lo, src, url, kind, art in items:
            st = d["车站"][idx]
            pos = st.setdefault("位置", {})
            pos["坐标"] = {"纬度": round(la, 6), "经度": round(lo, 6), "出处": src}
            pos["坐标可用"] = True
            pos["坐标来源"] = {
                "来源": src, "出典": url,
                "記事": f"https://ja.wikipedia.org/wiki/{art.replace(' ', '_')}",
                "取得日": DAY,
                "理由": "OSM駅ノード表記とeki-wiki表記の glyphs 差（ヶ⇄ケ 等）を1件ずつ"
                        "ウィキペディア記事検索で同定し、記事座標±3km内のOSMノードを優先採用"}
            note = {"OSM": "OpenStreetMap (Japan extract, ODbL)",
                    "Wikidata": "Wikidata (CC0)",
                    "Wikipedia": "日本語版ウィキペディア (CC BY-SA 4.0)"}[kind] + f"（後補座標・{src}）"
            n_poi += add_pois(st, la, lo, osmdb, note)
        save(p, d)
    log(f"new POI records={n_poi}")
    for f in fails:
        log("  unresolved:", *f)
    osmdb.close()


if __name__ == "__main__":
    from guide_core import OsmDb
    main()
