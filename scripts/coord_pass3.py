#!/usr/bin/env python3
"""Third geocode pass — the stubborn remainder after coord_pool_fix.py.

37 more stations (42 remain) fail the batch title lookup because their eki-wiki
spelling is a *non-article* form: 茅ケ崎駅 (the article is 茅ヶ崎駅), 飛騨古川駅
(飛騨古川駅 exists but 飛騨細江 has no coordinate on Wikidata), or a Hiroden stop
written "広島港・宇品" where the article is 広島港停留場. Chunked title lookups
plus fuzzy normalisation mapped some of those onto the wrong homonym, which the
neighbour-distance guard rejected — so this pass works strictly one title at a time:

  search the Japanese Wikipedia for the name → canonical article → pageprops +
  page coordinates → Wikidata P625 if the article has no coordinate of its own.

Every candidate point must lie within 120 km of an already-placed station on the
same line before it is written, then the station gets the same 900 m OSM POI pool.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W
from coord_pool_fix import add_pois, collect_missing, hav, log, norm, save

DAY = datetime.date.today().isoformat()
API = W.API["ja"]


def search_article(entry):
    """Best Japanese-Wikipedia article for a station name, via full-text search."""
    q = re.sub(r"[・の\s]+$", "", re.sub(r"駅$", "", entry))
    for sr in (f'"{entry}"', f'"{q}" 停留場 OR 駅 OR 停留所', f'"{q}"'):
        code, d = W._get(API, {"action": "query", "list": "search", "srsearch": sr,
                               "srlimit": "5", "format": "json", "formatversion": "2"})
        if code != 200 or not d:
            continue
        hits = d.get("query", {}).get("search") or []
        for h in hits:
            t = h.get("title") or ""
            if not t:
                continue
            # a station article must read as the same place once glyphs/brackets go
            a, b = norm(t), norm(entry)
            if a and b and (a == b or a.startswith(b) or b.startswith(a)):
                return t
    return None


def page_geo(title):
    """(lat, lon, source, url) from the article's own coordinate or its Wikidata P625."""
    code, d = W._get(API, {"action": "query", "titles": title, "redirects": "1",
                           "prop": "pageprops|coordinates", "ppprop": "wikibase_item",
                           "format": "json", "formatversion": "2"})
    if code != 200 or not d:
        return None
    qid = None
    for pg in d.get("query", {}).get("pages", []):
        if pg.get("missing"):
            continue
        title = pg.get("title") or title
        qid = (pg.get("pageprops") or {}).get("wikibase_item")
        co = (pg.get("coordinates") or [None])[0]
        if co and co.get("lat") is not None:
            return (float(co["lat"]), float(co["lon"]),
                    f"日本語版ウィキペディア「{title}」座標",
                    "https://ja.wikipedia.org/wiki/" + title.replace(" ", "_"))
    if not qid:
        return None
    code, d = W._get("https://www.wikidata.org/w/api.php",
                    {"action": "wbgetentities", "ids": qid, "format": "json",
                     "props": "claims"})
    if code != 200 or not d:
        return None
    e = (d.get("entities") or {}).get(qid) or {}
    for sn in e.get("claims", {}).get("P625", []):
        mv = (((sn or {}).get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
        if mv.get("latitude") is not None:
            return (float(mv["latitude"]), float(mv["longitude"]),
                    f"Wikidata ({qid}) P625", f"https://www.wikidata.org/wiki/{qid}")
    return None


def main():
    missing = collect_missing()
    log(f"remaining stations without coordinates: {len(missing)}")
    applied = poi_total = 0
    fails = []
    by_file = {}
    for m in missing:
        anchor = m["anchor"]
        arts = []
        if m["title"]:
            t = m["title"].replace("_", " ")
            if norm(t) == norm(m["entry"]):
                arts.append(t)              # batch pass had the right article
        for e in (m["entry"], m["orig"]):
            if e:
                a = search_article(e)
                if a and a not in arts:
                    arts.append(a)
        hit = cand = None
        for a in arts:
            g = page_geo(a)
            if not g:
                continue
            la, lo, src, url = g
            if anchor and not all(hav(x, y, la, lo) <= 120000 for x, y in anchor):
                fails.append((m["line"], m["entry"], f"{a}: point off this line"))
                continue
            hit, cand = g, a
            break
        if not hit:
            if not cand and m["entry"] not in " ".join(str(x) for x in fails):
                fails.append((m["line"], m["entry"], "no usable article/coordinate"))
            continue
        by_file.setdefault(m["file"], []).append(
            (m["idx"], hit[0], hit[1], hit[2], hit[3], cand))

    log(f"resolvable now: {len(by_file)} files, {sum(len(v) for v in by_file.values())} stations")
    for p, items in sorted(by_file.items()):
        d = json.load(open(p, encoding="utf-8"))
        for idx, la, lo, src, url, cand in items:
            st = d["车站"][idx]
            kind = "Wikidata" if src.startswith("Wikidata") else "Wikipedia"
            pos = st.setdefault("位置", {})
            pos["坐标"] = {"纬度": round(la, 6), "经度": round(lo, 6), "出处": src}
            pos["坐标可用"] = True
            pos["坐标来源"] = {"来源": src, "出典": url, "記事": cand, "取得日": DAY,
                              "理由": "表記差でOSM駅ノードと一致しなかったため、1件ずつウィキペディア記事"
                                      "を全文検索して座標を確定（隣駅からの距離で検証済み）"}
            note = {"Wikidata": "Wikidata (CC0)",
                    "Wikipedia": "日本語版ウィキペディア (CC BY-SA 4.0)"}[kind] + f"（後補座標・{src}）"
            poi_total += add_pois(st, la, lo, OSMDB, note)
            applied += 1
        save(p, d)
    log(f"applied={applied}  new POI records={poi_total}")
    for f in fails:
        log("  unresolved:", *f)
    log(f"unresolved count={len(fails)}")


OSMDB = None
if __name__ == "__main__":
    from guide_core import OsmDb
    OSMDB = OsmDb()
    try:
        main()
    finally:
        OSMDB.close()
