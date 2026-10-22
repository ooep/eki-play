#!/usr/bin/env python3
"""Build /data/osm/jp_poi.sqlite3 from the Japan PBF.

The pyosmium wheel here has no node-location index types and no osmium sort, and
Geofabrik extracts store all nodes before all ways. So we use the fast C++
SimpleHandler path three times instead:

  A  node():  tagged nodes -> POI/station rows + compact location store
  B  way():   POI/station ways -> tag set + member node ids
  C  node():  locations for the member ids collected in B (walked in id order)
Ways are then reduced to their centroid.
"""
import json, os, sys, time
import osmium
import numpy as np
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import RULES, classify, is_station

PBF = os.environ.get("EKI_PBF", "/data/osm/japan-latest.osm.pbf")
DB = os.environ.get("EKI_OSMDB", "/data/osm/jp_poi.sqlite3")
WANT = set(RULES) | {"railway", "aerialway", "public_transport", "station", "historic",
                     "leisure", "amenity", "shop", "office", "craft"}
DAY = time.strftime("%Y-%m-%d")
CAP = 24_000_000


class LocStore:
    """Append-only id/lat/lon arrays, then a sorted index for bisect lookups."""

    def __init__(self):
        self.ids = np.empty(CAP, dtype=np.int64)
        self.lat = np.empty(CAP, dtype=np.float32)
        self.lon = np.empty(CAP, dtype=np.float32)
        self.n = 0
        self.order = None

    def add(self, nid, la, lo):
        if self.n >= CAP:
            return
        self.ids[self.n] = nid
        self.lat[self.n] = la
        self.lon[self.n] = lo
        self.n += 1

    def finalize(self):
        self.ids = self.ids[:self.n]; self.lat = self.lat[:self.n]; self.lon = self.lon[:self.n]
        self.order = np.argsort(self.ids, kind="stable")
        self.ids_sorted = self.ids[self.order]
        self.lat_sorted = self.lat[self.order]
        self.lon_sorted = self.lon[self.order]
        self.ids = self.lat = self.lon = None

    def get(self, nid):
        i = int(np.searchsorted(self.ids_sorted, nid))
        if i < len(self.ids_sorted) and self.ids_sorted[i] == nid:
            return float(self.lat_sorted[i]), float(self.lon_sorted[i])
        return None


def interesting(tags):
    for k in tags:
        if k == "name" or k.split(":")[0] in WANT:
            return True
    return False


def main():
    t0 = time.time()
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
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
        if len(buf_e) >= 20000:
            con.executemany(ins_e, buf_e); buf_e.clear()
        return "poi"

    loc = LocStore()
    poi_ways = {}

    # ---------------- A: tagged nodes ----------------
    class A(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.n = 0
            self.tagged = 0

        def node(self, nd):
            self.n += 1
            if not nd.tags:
                return
            tags = dict(nd.tags)
            if not interesting(tags):
                return
            self.tagged += 1
            L = nd.location
            if not L.valid():
                return
            la, lo = L.lat(), L.lon()
            loc.add(nd.id, la, lo)
            if is_station(tags) or classify(tags):
                emit("node", nd.id, la, lo, tags)

    a = A()
    a.apply_file(PBF, locations=False, node="pass", way="skip", relation="skip",
                 changeset="skip", area="skip")
    print(f"A: nodes={a.n:,} tagged={a.tagged:,} buf_e={len(buf_e)} {time.time()-t0:.0f}s", flush=True)
    con.executemany(ins_e, buf_e); buf_e.clear()
    con.executemany(ins_s, buf_s); buf_s.clear()
    loc.finalize()
    print(f"   loc store: {loc.n:,} entries {time.time()-t0:.0f}s", flush=True)

    # ---------------- B: ways ----------------
    class B(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.w = 0
            self.kept = 0

        def way(self, wy):
            self.w += 1
            if not wy.tags:
                return
            tags = dict(wy.tags)
            if not interesting(tags):
                return
            if not (is_station(tags) or classify(tags)):
                return
            self.kept += 1
            try:
                poi_ways[wy.id] = (tags, list(wy.references()))
            except Exception:
                poi_ways[wy.id] = (tags, [])

    b = B()
    b.apply_file(PBF, locations=False, node="skip", way="pass", relation="skip",
                 changeset="skip", area="skip")
    print(f"B: ways={b.w:,} kept={b.kept:,} {time.time()-t0:.0f}s", flush=True)

    # ---------------- C: extra locations for unmatched members ----------------
    need = set()
    for wid, (tags, refs) in poi_ways.items():
        for nid in refs:
            if loc.get(nid) is None:
                need.add(nid)
    print(f"C: need {len(need):,} locations", flush=True)
    got = 0
    if need:
        nl = sorted(need)
        ptr = 0

        class C(osmium.SimpleHandler):
            def node(self, nd):
                nonlocal ptr, got
                if ptr >= len(nl):
                    return
                while ptr < len(nl) and nl[ptr] < nd.id:
                    ptr += 1
                if ptr < len(nl) and nl[ptr] == nd.id:
                    L = nd.location
                    if L.valid():
                        loc.add(nd.id, L.lat(), L.lon())
                        got += 1
                    ptr += 1

        c = C()
        c.apply_file(PBF, locations=False, node="pass", way="skip",
                     relation="skip", changeset="skip", area="skip")
    print(f"C: resolved {got:,} ({time.time()-t0:.0f}s)", flush=True)
    loc.finalize()

    # ---------------- ways -> centroid ----------------
    placed = miss = 0
    for wid, (tags, refs) in poi_ways.items():
        pts = [p for p in (loc.get(r) for r in refs) if p]
        if len(pts) < max(3, len(refs) // 2):
            miss += 1
            continue
        la = sum(p[0] for p in pts) / len(pts)
        lo = sum(p[1] for p in pts) / len(pts)
        if emit("way", wid, la, lo, tags, way_geom=True):
            placed += 1
    print(f"ways placed={placed:,} skipped={miss:,} {time.time()-t0:.0f}s", flush=True)
    poi_ways.clear()
    con.executemany(ins_e, buf_e); buf_e.clear()
    con.executemany(ins_s, buf_s); buf_s.clear()
    con.executescript("""
    CREATE INDEX ix_geo ON elem(lat, lon);
    CREATE INDEX ix_cls ON elem(klass);
    CREATE INDEX ix_name ON elem(name);
    CREATE INDEX ix_stn ON st(name);
    """)
    con.commit()
    print("FINAL elem:", con.execute("select count(*) from elem").fetchone()[0],
          "st:", con.execute("select count(*) from st").fetchone()[0],
          f"{time.time()-t0:.0f}s size={os.path.getsize(DB)/2**20:.0f}MB", flush=True)
    con.close()


if __name__ == "__main__":
    main()
