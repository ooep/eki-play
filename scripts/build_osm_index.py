#!/usr/bin/env python3
"""Stream japan-latest.osm.pbf -> compact SQLite (pois + stations) for local spatial queries.

Only POI classes that matter for a station-area guide are kept, so the whole
Japan extract is reduced to a few million rows that fit in RAM.
"""
import json, os, re, sqlite3, sys, time

DB = os.environ.get("EKI_OSMDB", "/data/osm/jp_poi.sqlite3")
PBF = os.environ.get("EKI_PBF", "/data/osm/japan-latest.osm.pbf")

# value whitelist per OSM key -> (class, category)
RULES = {
    "tourism": [
        (r"attraction|viewpoint|artwork|museum|gallery|zoo|theme_park|aquarium|theme_park", "観光・名所案内", "名所"),
        (r"information", "観光・名所案内", "観光案内所"),
        (r"hotel|hostel|guest_house|apartment|motel|ryokan", "宿泊", "宿"),
    ],
    "historic": [(r".", "観光・名所案内", "歴史遺産")],
    "leisure": [
        (r"park|garden|dog_park", "文化・公共施設", "公園"),
        (r"stadium|sports_centre|swimming_pool|ice_rink|bowling_alley|golf_course", "文化・公共施設", "スポーツ"),
        (r"theatre|cinema", "文化・公共施設", "劇場映画"),
    ],
    "amenity": [
        (r"community_centre|library|arts_centre|planetarium|observatory", "文化・公共施設", "文化施設"),
        (r"theatre|cinema", "文化・公共施設", "劇場映画"),
        (r"marketplace", "買い物・商店街", "市場"),
        (r"police|townhall", "便民公共服务", "行政警察"),
        (r"post_office", "便民公共服务", "郵便局"),
        (r"hospital|clinic|dentist|doctors|pharmacy|social_facility", "便民公共服务", "医療"),
        (r"bank|atm|currency_exchange", "便民公共服务", "金融"),
        (r"fuel|car_wash|charging_station|bicycle_rental|vehicle_inspection", "交通接続", "自動車関連"),
        (r"taxi", "交通接続", "タクシー"),
        (r"public_bookcase|toilets|drinking_water|bench|place_of_worship", "便民公共服务", "その他公共"),
    ],
    "shop": [
        (r"department_store|mall|supermarket|convenience|variety_store|books|chemist", "買い物・商店街", "店舗"),
    ],
    "office": [(r"government|tourism|company", "便民公共服务", "オフィス")],
    "craft": [(r"carpenter|pottery|confectionery", "買い物・商店街", "工房")],
}
POI_KEYS = list(RULES)

STATION_OK = re.compile(r"^(railway|aerialway|public_transport|station)$")


def classify(tags):
    for key in POI_KEYS:
        val = tags.get(key)
        if not val:
            continue
        for pat, klass, cat in RULES[key]:
            if re.fullmatch(pat, val):
                return klass, cat, f"{key}={val}"
    return None


def is_station(tags):
    if tags.get("railway") in ("station", "halt", "tram_stop", "subway_entrance", "light_rail",
                               "funicular", "monorail", "narrow_gauge", "disused"):
        return True
    if tags.get("aerialway") in ("station",):
        return True
    if tags.get("public_transport") in ("station", "stop_position", "platform"):
        return True
    if "station" in tags:
        return True
    n = tags.get("name") or ""
    if re.search(r"駅|停留所|停留場|信号場|車庫", n) and len(n) <= 12:
        return True
    return False


def main():
    import osmium
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
                    operator TEXT, wiki TEXT, wikidata TEXT, website TEXT,
                    name_en TEXT, name_zh TEXT, osm_id INTEGER);
    """)
    ins_e = ("INSERT OR REPLACE INTO elem VALUES(" + ",".join("?" * 26) + ")")
    ins_s = ("INSERT OR REPLACE INTO st VALUES(" + ",".join("?" * 11) + ")")

    COLS = ["name", "name:en", "name:zh", "name:zh-hans", "name:zh-hant", "brand", "operator",
            "website", "url", "contact:website", "wikipedia", "wikidata", "image", "commons",
            "wikipedia:ja", "wikipedia:en", "official_url", "official_website", "start_date",
            "email", "phone", "contact:phone", "opening_hours", "description", "note", "comment"]

    def pick(tags, *keys):
        for k in keys:
            if tags.get(k):
                return tags[k]
        return None

    buf_e, buf_s = [], []

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.nodes = 0
            self.ways = 0
            self.kept_poi = 0
            self.kept_st = 0

        def _row(self, osm_type, osm_id, lat, lon, tags, way_geom=None):
            st = is_station(tags)
            if st:
                self.kept_st += 1
                buf_s.append((f"{osm_type[0]}{osm_id}", tags.get("name"),
                              ",".join(f"{k}={v}" for k, v in tags.items()
                                        if k in ("railway", "aerialway", "public_transport", "station")),
                              lat, lon, pick(tags, "operator", "company"),
                              pick(tags, "wikidata"), None, pick(tags, "website", "contact:website"),
                              pick(tags, "name:en"), pick(tags, "name:zh", "name:zh-hans"), osm_id))
                if len(buf_s) >= 20000:
                    con.executemany(ins_s, buf_s); buf_s.clear()
                return
            cls = classify(tags)
            if not cls:
                return
            klass, cat, osmkey = cls
            if not (tags.get("name") or tags.get("brand") or way_geom):
                return
            self.kept_poi += 1
            extra = {k: v for k, v in tags.items()
                     if k in ("addr:street", "addr:housenumber", "addr:city", "addr:postcode",
                              "denomination", "religion", "historic", "tourism", "leisure",
                              "amenity", "shop", "office", "layer", "level", "covered",
                              "wheelchair", "wifi", "internet_access", "fee", "duration",
                              "opening_hours", "survey_date", "ref")}
            buf_e.append((f"{osm_type[0]}{osm_id}", osm_type, lat, lon, klass, cat, osmkey,
                          pick(tags, "name", "brand"), pick(tags, "name:en"),
                          pick(tags, "name:zh", "name:zh-hans", "name:zh-hant"),
                          tags.get("brand"), pick(tags, "operator", "company"),
                          pick(tags, "website", "url", "contact:website"),
                          pick(tags, "wikipedia", "wikipedia:ja"), pick(tags, "wikidata"),
                          pick(tags, "image", "commons"), pick(tags, "wikipedia:en"),
                          pick(tags, "official_url", "official_website"), pick(tags, "start_date"),
                          pick(tags, "email", "contact:email"), pick(tags, "phone", "contact:phone"),
                          tags.get("opening_hours"), tags.get("description"), tags.get("note"),
                          json.dumps(extra, ensure_ascii=False), osm_type, osm_id))
            if len(buf_e) >= 20000:
                con.executemany(ins_e, buf_e); buf_e.clear()

        def node(self, n):
            self.nodes += 1
            if not n.tags:
                return
            loc = n.location
            if not loc.valid():
                return
            self._row("node", n.id, loc.lat(), loc.lon(), dict(n.tags))

        def way(self, w):
            self.ways += 1
            tags = dict(w.tags)
            if not tags:
                return
            if not (classify(tags) or is_station(tags)):
                return
            try:
                if not w.closed:
                    return
                lat = sum(n.location.lat() for n in w.nodes) / len(w.nodes)
                lon = sum(n.location.lon() for n in w.nodes) / len(w.nodes)
            except Exception:
                return
            self._row("way", w.id, lat, lon, tags, way_geom=True)

    t0 = time.time()
    h = H()
    print("streaming", PBF, flush=True)
    h.apply_file(PBF, locations=True, idx="nodelocations_offset_map")
    if buf_e:
        con.executemany(ins_e, buf_e)
    if buf_s:
        con.executemany(ins_s, buf_s)
    con.executescript("""
    CREATE INDEX ix_elem_geo ON elem(lat, lon);
    CREATE INDEX ix_elem_class ON elem(klass);
    CREATE INDEX ix_st_name ON st(name);
    """)
    con.commit()
    n1 = con.execute("SELECT count(*) FROM elem").fetchone()[0]
    n2 = con.execute("SELECT count(*) FROM st").fetchone()[0]
    print(f"nodes={h.nodes:,} ways={h.ways:,} poi_kept={h.kept_poi:,} st_kept={h.kept_st:,}")
    print(f"db rows: elem={n1:,} st={n2:,} in {time.time()-t0:.0f}s  size={os.path.getsize(DB)/2**20:.0f}MB")
    con.close()


if __name__ == "__main__":
    main()
