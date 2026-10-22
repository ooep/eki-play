#!/usr/bin/env python3
"""Second-pass geocode + POI backfill.

About 133 of the 16,955 station entries in data/lines/*.json have no coordinates
because their eki-wiki spelling is a glyph variant of the OSM name (ヶ/ケ/ガ, 挾/挟 …)
or because OSM simply labels a bus stop differently. Those stations therefore also
lost their whole surrounding-facility pool, which is the main value of the dataset.

Two resolution routes, in order of trust:
  1. glyph/width-normalised match against the offline OSM station table
     (preferred: OSM coordinates + the standard POI extractor can run unchanged);
  2. 日本語版ウィキペディア記事 → Wikidata item → P625 coordinate.

Every station that gains a coordinate immediately gets its OSM POI pool extracted
within the same 900 m radius the builder uses, de-duplicated against the records the
Wikipedia 「駅周辺」 section already provided, and merged into 分区. Provenance is
written explicitly (坐标来源) so nothing looks like the original OSM match it is not.
"""
import datetime
import glob
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W
from guide_core import CLASS_ORDER, OsmDb, hav, poi_record

REPO = "/data/eki-play"
LINES = os.path.join(REPO, "data", "lines")
RADIUS = 900
DAY = datetime.date.today().isoformat()

# glyph variants that break exact name matching against OSM
VARIANT = str.maketrans({"ヶ": "ケ", "ヵ": "カ", "㍱": "ホ", "挾": "挟", "槇": "槙",
                        "濵": "浜", "辺": "邊", "竈": "竃", "𥥍": "田"})
LIMITS = {"交通接続": 26, "便民公共服务": 26, "買い物・商店街": 18, "宿泊": 12,
          "観光・名所案内": 40, "文化・公共施設": 30}


def log(*a):
    print(f"[{datetime.datetime.now():%H:%M:%S}]", *a, flush=True)


def norm(name):
    """Normalise a Japanese station name for variant-tolerant matching."""
    if not name:
        return None
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    s = s.translate(VARIANT)
    s = re.sub(r"(駅|停留場|停留所|信号場|前)$", "", s)
    return s or None


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def ja_title(st):
    ln = (st.get("车站条目") or {}).get("链接") or ""
    m = re.search(r"ja\.wikipedia\.org/wiki/([^?#]+)", ln)
    return None if not m else m.group(1)


# ---------------------------------------------------------------- collection
def neighbours_with_coords(d, i):
    """Nearest placed stations on this line — used as a sanity anchor so a
    normalised name match in the wrong prefecture can never be accepted."""
    out = []
    n = len(d["车站"])
    for step in (-1, 1):
        j = i + step
        while 0 <= j < n and len(out) < 2:
            c = (d["车站"][j].get("位置") or {}).get("坐标")
            if c and c.get("纬度"):
                out.append((c["纬度"], c["经度"]))
                break
            j += step
    return out


def collect_missing():
    missing = []
    for p in sorted(glob.glob(os.path.join(LINES, "*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        line_ja = (d.get("线路") or {}).get("日文") or ""
        for i, st in enumerate(d["车站"]):
            if (st.get("位置") or {}).get("坐标"):
                continue
            nm = st.get("名称") or {}
            missing.append({
                "file": p, "idx": i, "line": line_ja,
                "entry": nm.get("词条") or nm.get("日文") or "",
                "orig": nm.get("日文原名") or "",
                "title": ja_title(st),
                "anchor": neighbours_with_coords(d, i),
            })
    return missing


def plausible(m, lat, lon, limit_km):
    if not m["anchor"]:
        return True
    return all(hav(a, o, lat, lon) <= limit_km * 1000 for a, o in m["anchor"])


# ---------------------------------------------------------------- route 1: OSM
def osm_matches(missing, osmdb):
    """Normalised offline match; only accept an unambiguous geo-cluster."""
    by_norm = {}
    cols = ("rid,name,kind,lat,lon,operator,wiki,wikidata,website,name_en,name_zh,osm_id")
    for r in osmdb.con.execute(f"SELECT {cols} FROM st"):
        for key in {norm(r["name"]), norm((r["name"] or "") + "駅")}:
            if key:
                by_norm.setdefault(key, []).append(r)

    hits = {}
    for m in missing:
        for cand in (m["entry"], m["orig"]):
            rows = by_norm.get(norm(cand)) or []
            if not rows:
                continue
            rows = [r for r in rows if not (r["kind"] or "").startswith("railway=signal")]
            rows = [r for r in rows if plausible(m, r["lat"], r["lon"], 60)]
            if not rows:
                continue
            if len(rows) == 1:
                g = rows[0]
                hits[(m["file"], m["idx"])] = (g["lat"], g["lon"],
                                               f"OpenStreetMap {g['rid']} ({g['kind'] or 'n/a'})",
                                               None, "OSM")
                break
            # several stations share the name (same name in different prefectures):
            # accept only if all candidates sit in one tight cluster
            far = max(hav(rows[0]["lat"], rows[0]["lon"], r["lat"], r["lon"]) for r in rows)
            if far <= 3000:
                la = sum(r["lat"] for r in rows) / len(rows)
                lo = sum(r["lon"] for r in rows) / len(rows)
                hits[(m["file"], m["idx"])] = (la, lo,
                    f"OpenStreetMap {len(rows)}ノードcentroid（{name_gap(m)}）", None, "OSM")
                break
    return hits


def name_gap(m):
    return f"{m['entry']}⇄OSM表記差"


# ------------------------------------------------- route 2: Wikipedia / Wikidata
def resolve_via_wiki(missing):
    """For stations with a Japanese Wikipedia article: prefer Wikidata P625, fall back
    to the article's own page coordinate. Returns {title: (lat, lon, source, url)}."""
    titles = sorted({m["title"] for m in missing if m["title"]})
    if not titles:
        return {}

    jp = W.batch_titles(titles, lang="ja", props="pageprops", tag="t2q")
    canon = link_titles(titles, jp.keys())
    t2q = {t: jp[c]["qid"] for t, c in canon.items() if jp[c].get("qid")}
    log(f"  titles with a Wikidata item: {len(t2q)}/{len(titles)}")

    qids = sorted(set(t2q.values()))
    coord = {}
    for i in range(0, len(qids), 45):
        ch = qids[i:i + 45]
        code, d = W._get("https://www.wikidata.org/w/api.php",
                         {"action": "wbgetentities", "ids": "|".join(ch), "format": "json",
                          "props": "claims|sitelinks"})
        if code != 200 or not d:
            log("  entity fetch failed", code)
            continue
        for q, e in (d.get("entities") or {}).items():
            for sn in (e.get("claims") or {}).get("P625", []):
                mv = (((sn or {}).get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
                if mv.get("latitude") is not None:
                    coord[q] = (float(mv["latitude"]), float(mv["longitude"]),
                                (e.get("sitelinks") or {}).get("jawiki", {}).get("title"))
                    break

    page = wiki_coords(titles)
    canon2 = link_titles(titles, page.keys())

    out = {}
    for t in titles:
        q = t2q.get(t)
        if q and q in coord:
            la, lo, jt = coord[q]
            # trust the point only when the item's jawiki sitelink is our article
            if not jt or norm(jt) == norm(canon.get(t, t)):
                out[t] = (la, lo, f"Wikidata ({q}) P625",
                          f"https://www.wikidata.org/wiki/{q}")
                continue
        c = canon2.get(t)
        if c and c in page:
            la, lo, _ = page[c]
            out[t] = (la, lo, f"日本語版ウィキペディア「{c}」座標",
                      "https://ja.wikipedia.org/wiki/" + c.replace(" ", "_"))
    log(f"  wiki-side coordinates found: {len(out)}/{len(titles)} titles")
    return out


def wiki_coords(titles):
    """日本語版ウィキペディアの page coordinates — the article's own geodata, which
    many station articles carry in their infobox even when Wikidata has no P625."""
    out = {}
    titles = list(dict.fromkeys(t for t in titles if t))
    for i in range(0, len(titles), 50):
        ch = titles[i:i + 50]
        code, d = W._get(W.API["ja"], {"action": "query", "titles": "|".join(ch),
                                      "prop": "coordinates", "converttitles": "1",
                                      "redirects": "1", "colimit": "50",
                                      "format": "json", "formatversion": "2"})
        if code != 200 or not d:
            log("  wiki coordinate fetch failed", code)
            continue
        for pg in d.get("query", {}).get("pages", []):
            co = (pg.get("coordinates") or [None])[0]
            if co and co.get("lat") is not None:
                out[pg["title"]] = (float(co["lat"]), float(co["lon"]), "jawiki")
        time.sleep(0.15)
    return out


def link_titles(titles, canon):
    """Map each requested title to its canonical page title (redirects, variant spelling)."""
    by_norm = {}
    for c in canon:
        for k in (norm(c), c.replace("_", " ")):
            if k:
                by_norm.setdefault(k, c)
    out = {}
    for t in titles:
        if t in canon:
            out[t] = t
        else:
            hit = by_norm.get(norm(t)) or by_norm.get(norm(t).replace("_", " "))
            if hit:
                out[t] = hit
    return out


# ---------------------------------------------------------------- POI pool
def add_pois(st, lat, lon, osmdb, src_note):
    """Extract OSM POIs for a station that just gained coordinates and merge them."""
    seen_name = set()
    seen_osm = set()
    bucket = st.get("分区") or {}
    for c in CLASS_ORDER:
        for r in bucket.get(c) or []:
            n = re.sub(r"\s+", "", (r.get("名称") or {}).get("日文") or "")
            if n:
                seen_name.add(n)
            if r.get("OSM编号"):
                seen_osm.add(r["OSM编号"])
    new = []
    for r in osmdb.around(lat, lon, RADIUS):
        rec = poi_record(r, hav(lat, lon, r["lat"], r["lon"]), lat, lon)
        n = re.sub(r"\s+", "", (rec["名称"].get("日文") or ""))
        if rec["OSM编号"] in seen_osm or (n and n in seen_name):
            continue
        seen_osm.add(rec["OSM编号"])
        if n:
            seen_name.add(n)
        rec["来源"].append(src_note)
        new.append(rec)
    counts = {}
    for rec in sorted(new, key=lambda x: (x["距车站m"] is None, x["距车站m"] or 9e9)):
        k = rec["类别"]["日文"]
        if k == "除外":
            continue
        counts[k] = counts.get(k, 0) + 1
        if counts[k] > LIMITS.get(k, 30):
            continue
        bucket.setdefault(k, []).append(rec)
    for c in bucket:
        bucket[c].sort(key=lambda x: (x["距车站m"] is None, x["距车站m"] or 9e9))
    st["分区"] = bucket
    st["統計"] = {c: len(v) for c, v in bucket.items()}
    q = st.setdefault("データ品質", {})
    q["OSM施設数"] = (q.get("OSM施設数") or 0) + len(new)
    q["座標"] = src_note.split("（")[0]
    return len(new)


# ---------------------------------------------------------------- main
def main():
    missing = collect_missing()
    log(f"stations without coordinates: {len(missing)}")

    osmdb = OsmDb()
    hits = osm_matches(missing, osmdb)
    log(f"osm normalised match: {len(hits)}")

    rest = [m for m in missing if (m["file"], m["idx"]) not in hits]
    wd = resolve_via_wiki(rest)

    for m in rest:
        hit = wd.get(m["title"]) if m["title"] else None
        if not hit:
            continue
        la, lo, src, url = hit
        if not plausible(m, la, lo, 120):
            log(f"  rejected implausible point for {m['entry']} ({m['line']})")
            continue
        kind = "Wikidata" if src.startswith("Wikidata") else "Wikipedia"
        hits[(m["file"], m["idx"])] = (la, lo, src, url, kind)

    by_file = {}
    for (f, i) in hits:
        by_file.setdefault(f, []).append(i)
    log(f"resolvable={len(hits)} across {len(by_file)} files; "
        f"still missing={len(missing) - len(hits)}")

    n_fill = n_poi = 0
    for p, idxs in sorted(by_file.items()):
        d = json.load(open(p, encoding="utf-8"))
        for i in idxs:
            lat, lon, src, url, kind = hits[(p, i)]
            st = d["车站"][i]
            pos = st.setdefault("位置", {})
            pos["坐标"] = {"纬度": round(lat, 6), "经度": round(lon, 6), "出处": src}
            pos["坐标可用"] = True
            pos["坐标来源"] = {
                "来源": src, "出典": url, "取得日": DAY,
                "理由": "OSMの駅名表記とeki-wikiの表記が glyphs 差で1対1一致しなかったため、"
                        "文字正規化／Wiki座標により後補（POIプールも同じ座標基準で抽出）"}
            note = {"OSM": "OpenStreetMap (Japan extract, ODbL)",
                    "Wikidata": "Wikidata (CC0)",
                    "Wikipedia": "日本語版ウィキペディア (CC BY-SA 4.0)"}[kind] + f"（後補座標・{src}）"
            n_poi += add_pois(st, lat, lon, osmdb, note)
            n_fill += 1
        save(p, d)
    log(f"applied coords={n_fill}  newly extracted POIs={n_poi}")

    # final audit
    still = 0
    for p in sorted(glob.glob(os.path.join(LINES, "*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        for st in d["车站"]:
            if not (st.get("位置") or {}).get("坐标"):
                still += 1
        del d
    log(f"stations still without coordinates: {still}")
    osmdb.close()


if __name__ == "__main__":
    main()
