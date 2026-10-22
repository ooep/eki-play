#!/usr/bin/env python3
"""Build /data/osm/jp_poi.sqlite3 from the Japan PBF with two streaming passes.

The pyosmium wheel in this sandbox has no node-location index and no osmium sort,
and a Geofabrik extract stores all nodes before all ways. So:

  pass A  nodes: keep every tagged node we care about (POI / station) with its
               location; stop at the first way.
          ways : remember POI/station ways and collect the node ids they use.
  pass B  nodes (again): resolve locations for the ids collected in pass A
               by walking them in ascending order (PBF nodes are id-sorted).
Then ways are reduced to their centroid.
"""
import json, os, re, sqlite3, sys, time
import osmium
from osmium.io import Reader

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import RULES, classify, is_station

PBF = os.environ.get("EKI_PBF", "/data/osm/japan-261021.osm.pbf")
DB = os.environ.get("EKI_OSMDB", "/data/osm/jp_poi.sqlite3")
WANT = set(RULES) | {"railway", "aerialway", "public_transport", "station"}
DAY = time.strftime("%Y-%m-%d")


def interesting(tags):
    if not tags:
        return False
    for k in tags:
        base = k.split(":")[0]
        if base in WANT or k == "name":
            return True
    return False


def main():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript("""
    PRAGMA journal_mode=OFF;
    PRAGMA synchronous=OFF;
    CREATE TABLE elem(rid TEXT PRIMARY KEY, kind TEXT, lat REAL, lon REAL,
                      klass TEXT, cat TEXT, osmkey TEXT,
                      name TEXT, name_en TEXT, name_zh TEXT, brand TEXT, operator TEXT,
                      website TEXT, wiki TEXT, wikidata TEXT, image TEXT,
                      wikipedia_tag TEXT, official_website TEXT, start_wiki TEXT,
                      email TEXT, phone TEXT, opening TEXT, description TEXT,
                      note TEXT, extra TEXT, osm_type TEXT, osm_id INTEGER);
    CREATE TABLE st(rid TEXT PRIMARY KEY, name TEXT, kind TEXT, lat REAL, lon REAL,
                    operator TEXT, wikidata TEXT, website TEXT,
                    name_en TEXT, name_zh TEXT, osm_id INTEGER);
    """)
    ins_e = "INSERT OR REPLACE INTO elem VALUES(" + ",".join("?" * 26) + ")"
    ins_s = "INSERT OR REPLACE INTO st VALUES(" + ",".join("?" * 10) + ")"
    buf_e, buf_s = [], []
    poi_nodes = {}      # id -> (lat, lon, tags)  (kept ones only, small)
    all_tagged = {}     # id -> (lat,lon) for every tagged node (bounded)
    poi_ways = {}       # id -> (tags, [node ids])
    t0 = time.time()

    def pick(tags, *keys):
        for k in keys:
            if tags.get(k):
                return tags[k]
        return None

    def emit(osm_type, osm_id, lat, lon, tags, way_geom=False):
        if is_station(tags):
            buf_s.append((f"{osm_type[0]}{osm_id}", tags.get("name"),
                          ",".join(f"{k}={v}" for k, v in tags.items()
                                   if k in ("railway", "aerialway", "public_transport", "station")),
                          lat, lon, pick(tags, "operator", "company"), pick(tags, "wikidata"),
                          pick(tags, "website", "contact:website"), pick(tags, "name:en"),
                          pick(tags, "name:zh", "name:zh-hans"), osm_id))
            if len(buf_s) >= 50000:
                con.executemany(ins_s, buf_s); buf_s.clear()
            return "station"
        cls = classify(tags)
        if not cls:
            return None
        klass, cat, osmkey = cls
        if not (tags.get("name") or tags.get("brand")) and not way_geom:
            return None
        extra = {k: v for k, v in tags.items() if k.startswith("addr:") or k in
                 ("opening_hours", "wheelchair", "fee", "religion", "denomination", "historic",
                  "tourism", "leisure", "amenity", "shop", "start_date", "ref")}
        buf_e.append((f"{osm_type[0]}{osm_id}", osm_type, round(lat, 6), round(lon, 6),
                      klass, cat, osmkey,
                      pick(tags, "name", "brand"), tags.get("name:en"),
                      pick(tags, "name:zh", "name:zh-hans", "name:zh-hant"),
                      tags.get("brand"), pick(tags, "operator", "company"),
                      pick(tags, "website", "url", "contact:website"),
                      pick(tags, "wikipedia", "wikipedia:ja"), tags.get("wikidata"),
                      pick(tags, "image", "commons"), tags.get("wikipedia:en"),
                      pick(tags, "official_url", "official_website"), tags.get("start_date"),
                      pick(tags, "email", "contact:email"), pick(tags, "phone", "contact:phone"),
                      tags.get("opening_hours"), tags.get("description"), tags.get("note"),
                      json.dumps(extra, ensure_ascii=False), osm_type, osm_id))
        if len(buf_e) >= 50000:
            con.executemany(ins_e, buf_e); buf_e.clear()
        return "poi"

    # ---------------- pass A ----------------
    n_nodes = n_ways = skipped = 0
    seen_way = False
    for obj in Reader(PBF):
        c = obj.clazz()
        if c == "node":
            if seen_way:
                break
            n_nodes += 1
            if obj.id() < 0:
                continue
            tags = dict(obj.tags())
            if not interesting(tags):
                continue
            loc = obj.location()
            if not loc.valid():
                continue
            lat, lon = loc.lat(), loc.lon()
            if len(all_tagged) < 9_000_000:
                all_tagged[obj.id()] = (lat, lon)
            if is_station(tags) or classify(tags):
                emit("node", obj.id(), lat, lon, tags)
        elif c == "way":
            seen_way = True
            n_ways += 1
            tags = dict(obj.tags())
            if not interesting(tags):
                continue
            st = is_station(tags)
            if not (st or classify(tags)):
                continue
            try:
                refs = list(obj.references())
            except Exception:
                refs = []
            poi_ways[obj.id()] = (tags, refs)
        if (n_nodes + n_ways) % 10_000_000 == 0:
            print(f"  A: n={n_nodes:,} w={n_ways:,} ways_kept={len(poi_ways):,} "
                  f"tagged={len(all_tagged):,} {time.time()-t0:.0f}s", flush=True)
    print(f"pass A: nodes={n_nodes:,} ways={n_ways:,} poi_ways={len(poi_ways):,} "
          f"tagged_nodes={len(all_tagged):,} {time.time()-t0:.0f}s", flush=True)

    # ---------------- pass B: resolve way member locations ----------------
    need = set()
    waymap = {}
    for wid, (tags, refs) in poi_ways.items():
        for nid in refs:
            if nid not in all_tagged:
                need.add(nid)
        waymap[wid] = refs
    need_sorted = sorted(need)
    ptr = 0
    got = 0
    for obj in Reader(PBF):
        if obj.clazz() != "node":
            break
        if not need_sorted or ptr >= len(need_sorted):
            break
        nid = obj.id()
        while ptr < len(need_sorted) and need_sorted[ptr] < nid:
            ptr += 1
        if ptr < len(need_sorted) and need_sorted[ptr] == nid:
            loc = obj.location()
            if loc.valid():
                all_tagged[nid] = (loc.lat(), loc.lon())
                got += 1
            ptr += 1
        if got and got % 1_000_000 == 0:
            print(f"  B: resolved {got:,}/{len(need):,} {time.time()-t0:.0f}s", flush=True)
    print(f"pass B: resolved {got:,} of {len(need):,} needed ({time.time()-t0:.0f}s)", flush=True)

    # ---------------- assemble way centroids ----------------
    placed = miss = 0
    for wid, (tags, refs) in poi_ways.items():
        pts = [all_tagged[r] for r in refs if r in all_tagged]
        if len(pts) < max(3, len(refs) // 2):
            miss += 1
            continue
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        if emit("way", wid, lat, lon, tags, way_geom=True):
            placed += 1
    print(f"ways placed={placed:,} skipped={miss:,} ({time.time()-t0:.0f}s)", flush=True)

    con.executemany(ins_e, buf_e); buf_e.clear()
    con.executemany(ins_s, buf_s); buf_s.clear()
    con.execute("insert or replace into st values('__meta__','__meta__',?,?,?,NULL,NULL,NULL,NULL,NULL,NULL)",
                (DAY, time.time(), 0))
    con.executescript("""
    CREATE INDEX ix_geo ON elem(lat, lon);
    CREATE INDEX ix_cls ON elem(klass);
    CREATE INDEX ix_name ON elem(name);
    CREATE INDEX ix_stn ON st(name);
    """)
    con.commit()
    print("FINAL elem:", con.execute("select count(*) from elem").fetchone()[0],
          " st:", con.execute("select count(*) from st").fetchone()[0],
          f"{time.time()-t0:.0f}s size={os.path.getsize(DB)/2**20:.0f}MB", flush=True)
    con.close()


if __name__ == "__main__":
    main()
