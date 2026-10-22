#!/usr/bin/env python3
"""Finish the OSM index: add way (polygon) POIs + the spatial indexes.

The first indexer run was stopped during pass C, so /data/osm/jp_poi.sqlite3
currently holds node POIs only (parks, castle grounds, shrine precincts and
large stations drawn as areas are missing) and has no ix_geo index.

  B  ways carrying a POI/station key -> wt table (tags) + (node,way) refs in RAM
  C  locations of those member nodes -> nloc table (one full node scan)
  D  mean-of-nodes centroid per way  -> elem/st rows (named ways only)
  E  ix_geo / ix_cls / ix_name / ix_stn, drop scratch tables

wt and nloc are persisted, so a restart skips work already done.
"""
import json, os, sqlite3, sys, time
import numpy as np
import osmium
from osmium.filter import KeyFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import RULES, classify, is_station

PBF = os.environ.get("EKI_PBF", "/data/osm/japan-latest.osm.pbf")
DB = os.environ.get("EKI_OSMDB", "/data/osm/jp_poi.sqlite3")
KEYS = list(RULES) + ["railway", "aerialway", "public_transport", "station"]
T0 = time.time()


def _v(x):
    return x() if callable(x) else x


def glat(L):
    return _v(L.lat)


def glon(L):
    return _v(L.lon)


def log(*a):
    print(f"[{time.time()-T0:6.0f}s]", *a, flush=True)


def pick(tags, *keys):
    for k in keys:
        if tags.get(k):
            return tags[k]
    return None


def emit_way(wid, lat, lon, tags, be, bs):
    """One way -> st row (station area) or elem row (named POI area)."""
    if is_station(tags):
        bs.append((f"w{wid}", tags.get("name"),
                   ",".join(f"{k}={v}" for k, v in tags.items()
                            if k in ("railway", "aerialway", "public_transport", "station")),
                   round(lat, 6), round(lon, 6), pick(tags, "operator", "company"),
                   pick(tags, "wikipedia"), pick(tags, "wikidata"),
                   pick(tags, "website", "contact:website"),
                   tags.get("name:en"), pick(tags, "name:zh", "name:zh-hans"), wid))
        return "station"
    cls = classify(tags)
    if not cls:
        return None
    if not (tags.get("name") or tags.get("brand")):
        return None          # unnamed areas would land as nameless records
    klass, cat, osmkey = cls
    extra = {k: v for k, v in tags.items() if k.startswith("addr:") or k in
             ("opening_hours", "wheelchair", "fee", "religion", "denomination", "historic",
              "tourism", "leisure", "amenity", "shop", "start_date", "ref")}
    be.append((f"w{wid}", "way", round(lat, 6), round(lon, 6),
               klass, cat, osmkey, pick(tags, "name", "brand"), tags.get("name:en"),
               pick(tags, "name:zh", "name:zh-hans", "name:zh-hant"), tags.get("brand"),
               pick(tags, "operator", "company"), pick(tags, "website", "url", "contact:website"),
               pick(tags, "wikipedia", "wikipedia:ja"), tags.get("wikidata"),
               pick(tags, "image", "commons"), tags.get("wikipedia:en"),
               pick(tags, "official_url", "official_website"), tags.get("start_date"),
               pick(tags, "email", "contact:email"), pick(tags, "phone", "contact:phone"),
               tags.get("opening_hours"), tags.get("description"), tags.get("note"),
               json.dumps(extra, ensure_ascii=False), "way", wid))
    return "poi"


def main():
    todo = sys.argv[1:] or ["B", "C", "D", "E"]
    con = sqlite3.connect(DB)
    con.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-400000;
    CREATE TABLE IF NOT EXISTS wt(wid INTEGER PRIMARY KEY, tags TEXT);
    CREATE TABLE IF NOT EXISTS nloc(nid INTEGER PRIMARY KEY, lat REAL, lon REAL);
    """)
    ins_e = "INSERT OR REPLACE INTO elem VALUES(" + ",".join("?" * 27) + ")"
    ins_s = "INSERT OR REPLACE INTO st VALUES(" + ",".join("?" * 12) + ")"
    # wt is rebuilt every run because the (node,way) refs are only held in RAM;
    # nloc is incremental, so a restart still saves the expensive full node scan.
    if "B" in todo:
        con.execute("DELETE FROM wt")
    have_ways = set()
    log(f"start: wt={con.execute('select count(*) from wt').fetchone()[0]:,} "
        f"nloc={con.execute('select count(*) from nloc').fetchone()[0]:,}")

    # ---------------- B: ways ----------------
    buf_w, buf_r = [], []

    class B(osmium.SimpleHandler):
        def __init__(s):
            super().__init__(); s.w = 0; s.kept = 0

        def way(s, wy):
            s.w += 1
            if wy.id in have_ways:
                return
            tags = dict(wy.tags) if wy.tags else None
            if not tags or not (is_station(tags) or classify(tags)):
                return
            have_ways.add(wy.id)
            s.kept += 1
            buf_w.append((wy.id, json.dumps(tags, ensure_ascii=False)))
            try:
                for nr in wy.nodes:
                    buf_r.append((nr.ref, wy.id))
            except Exception as e:
                log("B ref err", str(e)[:60])
            if len(buf_w) >= 50000:
                con.executemany("INSERT OR REPLACE INTO wt VALUES(?,?)", buf_w); buf_w.clear()
            if len(buf_r) >= 1_000_000:
                buf_r_chunks.append(np.array(buf_r, dtype=np.int64).reshape(-1, 2))
                buf_r.clear()

    buf_r_chunks = []
    if "B" in todo:
        for k in KEYS:
            b = B()
            b.apply_file(PBF, locations=False, filters=[KeyFilter(k)])
            con.executemany("INSERT OR REPLACE INTO wt VALUES(?,?)", buf_w); buf_w.clear()
            con.commit()
            log(f"B[{k:16s}] scanned={b.w:>9,} new_ways={b.kept:>8,} wt={len(have_ways):,}")
    if buf_r:
        buf_r_chunks.append(np.array(buf_r, dtype=np.int64).reshape(-1, 2)); buf_r.clear()
    rp = np.vstack(buf_r_chunks) if buf_r_chunks else np.zeros((0, 2), dtype=np.int64)
    buf_r_chunks = None
    log(f"B: refs raw={len(rp):,}")
    order = np.lexsort((rp[:, 1], rp[:, 0]))
    rp = rp[order]
    if len(rp) > 1:
        dup = (rp[1:, 0] == rp[:-1, 0]) & (rp[1:, 1] == rp[:-1, 1])
        rp = rp[np.concatenate(([True], ~dup))]
    rp_ids, rp_way = rp[:, 0].copy(), rp[:, 1].copy()
    rp = None
    log(f"B: unique (node,way) pairs={len(rp_ids):,}")

    # ---------------- C: member node locations ----------------
    if "C" in todo:
        have = {r[0] for r in con.execute("SELECT nid FROM nloc").fetchall()}
        wanted = set(int(i) for i in np.unique(rp_ids)) - have
        log(f"C: want {len(wanted):,} node locations (have {len(have):,})")

        class C(osmium.SimpleHandler):
            def __init__(s):
                super().__init__(); s.n = 0; s.got = 0; s.buf = []

            def node(s, nd):
                s.n += 1
                nid = nd.id
                if nid not in wanted:
                    return
                L = nd.location
                if not L.valid():
                    return
                s.buf.append((nid, glat(L), glon(L)))
                s.got += 1
                if len(s.buf) >= 300000:
                    con.executemany("INSERT OR REPLACE INTO nloc VALUES(?,?,?)", s.buf); s.buf.clear()
                    con.commit()
                    log(f"C: scanned={s.n:,} stored={s.got:,}")

        c = C()
        c.apply_file(PBF, locations=False)
        con.executemany("INSERT OR REPLACE INTO nloc VALUES(?,?,?)", c.buf); c.buf.clear()
        con.commit()
        log(f"C done: scanned={c.n:,} new={c.got:,} "
            f"nloc={con.execute('select count(*) from nloc').fetchone()[0]:,}")

    # ---------------- D: centroids -> elem/st ----------------
    if "D" in todo:
        ids, las, los = [], [], []
        while True:
            rows = con.execute("SELECT nid, lat, lon FROM nloc ORDER BY nid").fetchmany(1_000_000) \
                if not ids else con.execute("SELECT nid, lat, lon FROM nloc WHERE nid>? ORDER BY nid",
                                            (ids[-1],)).fetchmany(1_000_000)
            if not rows:
                break
            ids.extend(r[0] for r in rows); las.extend(r[1] for r in rows); los.extend(r[2] for r in rows)
        loc_ids = np.array(ids, dtype=np.int64)
        loc_lat = np.array(las); loc_lon = np.array(los)
        ids = las = los = None
        log(f"D: {len(loc_ids):,} node locations loaded")

        order = np.argsort(rp_way, kind="mergesort")
        wid_sorted, nid_sorted = rp_way[order], rp_ids[order]
        starts = np.flatnonzero(np.concatenate(([True], wid_sorted[1:] != wid_sorted[:-1])))
        idx = np.searchsorted(loc_ids, nid_sorted)
        ok = (idx < len(loc_ids)) & (loc_ids[np.minimum(idx, len(loc_ids) - 1)] == nid_sorted)
        la = np.zeros(len(nid_sorted)); lo = np.zeros(len(nid_sorted)); cnt = np.zeros(len(nid_sorted), np.int32)
        la[ok] = loc_lat[idx[ok]]; lo[ok] = loc_lon[idx[ok]]; cnt[ok] = 1
        g_lat, g_lon, g_cnt = np.add.reduceat(la, starts), np.add.reduceat(lo, starts), np.add.reduceat(cnt, starts)
        wids = wid_sorted[starts]
        good = g_cnt >= 2
        log(f"D: ways with >=2 resolved nodes: {int(good.sum()):,} of {len(starts):,}")

        be, bs, placed, skipped = [], [], 0, 0
        con_r = sqlite3.connect(DB)          # read-only cursor alongside the writes
        it = iter(con_r.execute("SELECT wid, tags FROM wt ORDER BY wid"))
        nxt = next(it, None)
        for wid, sla, slo, n, g in zip(wids, g_lat, g_lon, g_cnt, good):
            if not g:
                continue
            while nxt is not None and nxt[0] < wid:
                nxt = next(it, None)
            if nxt is None or nxt[0] != wid:
                skipped += 1
                continue
            tags = json.loads(nxt[1])
            if emit_way(int(wid), sla / n, slo / n, tags, be, bs):
                placed += 1
            if len(be) + len(bs) >= 20000:
                con.executemany(ins_e, be); be.clear()
                con.executemany(ins_s, bs); bs.clear()
        con.executemany(ins_e, be); be.clear()
        con.executemany(ins_s, bs); bs.clear()
        con.commit()
        con_r.close()
        log(f"D done: placed={placed:,} no_tags={skipped:,}")

    # ---------------- E: indexes, drop scratch ----------------
    if "E" in todo:
        con.executescript("""
        CREATE INDEX IF NOT EXISTS ix_geo ON elem(lat, lon);
        CREATE INDEX IF NOT EXISTS ix_cls ON elem(klass);
        CREATE INDEX IF NOT EXISTS ix_name ON elem(name);
        CREATE INDEX IF NOT EXISTS ix_stn ON st(name);
        DROP TABLE IF EXISTS wt;
        DROP TABLE IF EXISTS nloc;
        """)
        con.commit()
        log("E done: elem=%s st=%s ways=%s size=%.0fMB" % (
            con.execute("select count(*) from elem").fetchone()[0],
            con.execute("select count(*) from st").fetchone()[0],
            con.execute("select count(*) from elem where kind='way'").fetchone()[0],
            os.path.getsize(DB) / 2 ** 20))
    con.close()


if __name__ == "__main__":
    main()
