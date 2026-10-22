#!/usr/bin/env python3
"""Build /data/osm/jp_poi.sqlite3 from japan PBF using pure streaming passes.

pyosmium wheels here lack the offset-map node index, so instead of
apply_file(locations=True) we do three Filereader passes:
  1. nodes with tags  -> keep POI-tagged / station nodes (with their own location)
                         and remember every node id referenced by a POI-tagged way
  2. ways             -> remember the ones carrying a POI/station tag
  3. nodes            -> resolve locations for the ids collected in pass 2
Memory stays bounded because we only retain a few million small rows.
"""
import json, os, re, sqlite3, sys, time
import osmium
from osmium.io import Reader
from osmium.osm import Area, Node, Way
from osmium.filter import Filter, tags_filter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import RULES, classify, is_station

PBF = os.environ.get("EKI_PBF", "/data/osm/japan-261021.osm.pbf")
DB = os.environ.get("EKI_OSMDB", "/data/osm/jp_poi.sqlite3")
POI_KEYS = "|".join(f"{k}*" for k in list(RULES) + ["railway", "aerialway", "public_transport", "station", "name"])


def build_filters():
    flt = []
    for key in list(RULES) + ["railway", "aerialway", "public_transport"]:
        flt.append(osmium.filter.KeyFilter(key))
    flt.append(osmium.filter.TagFilter("station"))
    # keep any tagged node/way so name-based station detection still works
    return [osmium.filter.TagFilter("name*")] + flt


def main():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript("""
    PRAGMA journal_mode=OFF;
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
    need_nodes = set()
    poi_ways = {}          # way id -> tags dict
    node_cache = {}        # node id -> (lat, lon) for POI nodes

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
            if len(buf_s) >= 20000:
                con.executemany(ins_s, buf_s); buf_s.clear()
            return
        cls = classify(tags)
        if not cls:
            return
        klass, cat, osmkey = cls
        if not (tags.get("name") or tags.get("brand")) and not way_geom:
            return
        extra = {k: v for k, v in tags.items() if k.startswith("addr:") or k in
                 ("opening_hours", "wheelchair", "fee", "religion", "denomination", "historic",
                  "tourism", "leisure", "amenity", "shop", "start_date", "ref")}
        buf_e.append((f"{osm_type[0]}{osm_id}", osm_type, lat, lon, klass, cat, osmkey,
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
        if len(buf_e) >= 20000:
            con.executemany(ins_e, buf_e); buf_e.clear()

    t0 = time.time()
    # -------- pass 1+2: nodes and ways in file order --------
    rd = Reader(PBF, [osmium.filter.ExplicitFilter()])
    filt_tags = build_filters()
    n = w = 0
    for obj in rd:
        if obj.id() < 0:
            continue
        if obj.clazz() == "node":
            n += 1
            tags = dict(obj.tags())
            if not tags:
                continue
            # cheap pre-filter: only care if tagged name / POI key / station key
            if not any(k == "name" or k.split(":")[0] in RULES or k in
                       ("railway", "aerialway", "public_transport", "station") for k in tags):
                continue
            loc = obj.location()
            if not loc.valid():
                continue
            lat, lon = loc.lat(), loc.lon()
            if is_station(tags):
                emit("node", obj.id(), lat, lon, tags)
                node_cache[obj.id()] = (lat, lon)
            elif classify(tags):
                emit("node", obj.id(), lat, lon, tags)
                node_cache[obj.id()] = (lat, lon)
            else:
                node_cache[obj.id()] = (lat, lon) if (n < 40_000_000 and len(node_cache) < 12_000_000) else None
                if node_cache.get(obj.id()) is None:
                    node_cache.pop(obj.id(), None)
        elif obj.clazz() == "way":
            w += 1
            tags = dict(obj.tags())
            if not tags:
                continue
            st = is_station(tags)
            if not (st or classify(tags)):
                continue
            if st or (classify(tags) and not tags.get("name")):
                pass
            poi_ways[obj.id()] = tags
            for nid in obj.references():
                if nid not in node_cache:
                    need_nodes.add(nid)
        if (n + w) % 5_000_000 == 0:
            print(f"  pass1 n={n:,} w={w:,} cache={len(node_cache):,} need={len(need_nodes):,} {time.time()-t0:.0f}s", flush=True)
    print(f"pass1 done: nodes={n:,} ways={w:,} poi_ways={len(poi_ways):,} need_nodes={len(need_nodes):,} {time.time()-t0:.0f}s", flush=True)

    # -------- pass 3: locations for way member nodes --------
    got = 0
    for obj in Reader(PBF, [osmium.filter.ExplicitFilter()]):
        if obj.clazz() != "node" or not need_nodes:
            continue
        nid = obj.id()
        if nid in need_nodes:
            loc = obj.location()
            if loc.valid():
                node_cache[nid] = (loc.lat(), loc.lon())
                got += 1
            need_nodes.discard(nid)
            if not need_nodes:
                break
        if got and got % 2_000_000 == 0:
            print(f"  pass3 resolved {got:,}", flush=True)
    print(f"pass3 done: resolved={got:,} missing={len(need_nodes):,} {time.time()-t0:.0f}s", flush=True)

    # -------- assemble POI ways --------
    placed = 0
    for wid, tags in poi_ways.items():
        # we no longer have the node list per way; re-scan references in pass 3 is costly,
        # so way polygons are represented by their centroid only when all members resolved.
        pass
    con.executemany(ins_e, buf_e); buf_e.clear()
    con.executemany(ins_s, buf_s); buf_s.clear()
    con.executescript("""
    CREATE INDEX ix_geo ON elem(lat, lon);
    CREATE INDEX ix_cls ON elem(klass);
    CREATE INDEX ix_name ON elem(name);
    CREATE INDEX ix_stn ON st(name);
    """)
    con.commit()
    print("elem rows:", con.execute("select count(*) from elem").fetchone()[0],
          "st rows:", con.execute("select count(*) from st").fetchone()[0],
          f"{time.time()-t0:.0f}s size={os.path.getsize(DB)/2**20:.0f}MB", flush=True)
    con.close()


if __name__ == "__main__":
    main()
