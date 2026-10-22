#!/usr/bin/env python3
"""Build per-station trilingual area guide: OSM POIs + ja-wiki 駅周辺/バス路線 + Wikidata sites.

Usage: python3 build_guide.py <line_name>
Reads : /data/osm/jp_poi.sqlite3, /data/work/stations.json, eki-wiki/data/<line>.json
Writes: /data/eki-play/data/lines/<line>.json, /data/work/guide/<line>.ndjson (stats)
"""
import json, math, os, re, sys, sqlite3, time

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



from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W

OSMDB = os.environ.get("EKI_OSMDB", "/data/osm/jp_poi.sqlite3")
WIKI = "/data/eki-wiki/data"
OUT = os.environ.get("EKI_OUT", "/data/eki-play/data/lines")
STATS = os.environ.get("EKI_STATS", "/data/work/guide")
os.makedirs(OUT, exist_ok=True)
os.makedirs(STATS, exist_ok=True)

R_EARTH = 6371000.0


def hav(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * R_EARTH * math.asin(math.sqrt(a)))


def norm_station(name):
    if not name:
        return None
    s = re.sub(r"\s+", "", name)
    s = re.sub(r"(駅|停留場|停留所|信号場|前)$", "", s)
    return s or None


CLASS_ORDER = ["観光・名所案内", "文化・公共施設", "買い物・商店街", "便民公共服务", "交通接続", "宿泊"]

# English / Chinese renderings for OSM class + category labels (curated, not machine-translated)
META_LABEL = {
    "観光・名所案内": {"en": "Sights & Landmarks", "zh": "观光·名胜"},
    "文化・公共施設": {"en": "Culture & Public Facilities", "zh": "文化·公共设施"},
    "買い物・商店街": {"en": "Shopping & Shopping Streets", "zh": "购物·商店街"},
    "便民公共服务": {"en": "Public Services", "zh": "便民公共服务"},
    "交通接続": {"en": "Transport Connections", "zh": "交通换乘"},
    "宿泊": {"en": "Lodging", "zh": "住宿"},
}
CAT_LABEL = {
    "名所": ("Tourist attraction", "旅游景点"), "歴史遺産": ("Historic site", "历史遗产"),
    "観光案内所": ("Tourist information", "旅游咨询处"), "公園": ("Park / garden", "公园·绿地"),
    "スポーツ": ("Sports facility", "体育设施"), "劇場映画": ("Theatre / cinema", "影院剧场"),
    "文化施設": ("Cultural facility", "文化设施"), "市場": ("Market", "市场"),
    "店舗": ("Shop", "商铺"), "工房": ("Workshop", "工坊"),
    "行政警察": ("Town hall / police", "区政府·警察"), "郵便局": ("Post office", "邮局"),
    "医療": ("Medical", "医疗"), "金融": ("Bank / ATM", "银行·ATM"),
    "オフィス": ("Government office", "政务办公"), "その他公共": ("Other public amenity", "其他公共设施"),
    "自動車関連": ("Parking / fuel / bike hire", "停车·加油·共享单车"), "タクシー": ("Taxi stand", "出租车乘车处"),
    "宿": ("Lodging", "住宿"),
}


class OsmDb:
    def __init__(self, path=OSMDB):
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.cache = {}
        self.lock = __import__("threading").Lock()

    @property
    def COLS(self):
        if not getattr(self, "_cols", None):
            rs = [r["name"] for r in self.con.execute("PRAGMA table_info(elem)")]
            drop = {"image", "wikipedia_tag", "official_website", "start_wiki", "note", "description"}
            self._cols = ", ".join(c for c in rs if c not in drop)
        return self._cols

    def around(self, lat, lon, radius_m, klass=None):
        key = ("around", round(lat, 4), round(lon, 4), radius_m, klass)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        q = f"SELECT {self.COLS} FROM elem WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        dlat = radius_m / 111320.0
        dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 0.2))
        args = [lat - dlat, lat + dlat, lon - dlon, lon + dlon]
        if klass:
            q += " AND klass=?"; args.append(klass)
        rows = [r for r in self.con.execute(q, args) if hav(lat, lon, r["lat"], r["lon"]) <= radius_m]
        with self.lock:
            self.cache[key] = rows
        return rows

    def by_name(self, name):
        q = f"SELECT {self.COLS} FROM st WHERE name=? UNION ALL SELECT {self.COLS.replace('st.', 'elem.').replace('rid, kind', 'rid, kind')} FROM elem WHERE name=? LIMIT 5"
        rows = list(self.con.execute(f"SELECT {self.COLS} FROM elem WHERE name=? LIMIT 5", (name,)))
        rows += list(self.con.execute("SELECT rid,name,kind,lat,lon,operator,wiki,wikidata,website,name_en,name_zh,osm_id FROM st WHERE name=? LIMIT 8", (name,)))
        return rows

    BAD_PREFIX = ("railway=subway_entrance", "public_transport=platform",
                  "public_transport=stop_position", "railway=signal", "railway=crossing",
                  "railway=buffer_stop", "railway=abandoned", "railway=disused", "railway=stop")

    def _rank(self, kind):
        """0 mainline station node · 1 pt=station · 2 halt/tram/funicular · 3 unknown ·
        4 entrance / platform / stop-position (not a usable station centre)."""
        k = (kind or "").strip()
        if not k:
            return 3
        pairs = [p.strip() for p in k.split(",") if p.strip()]
        bad = ("railway=subway_entrance", "railway=train_station_entrance", "railway=station_entrance",
               "public_transport=platform", "public_transport=stop_position", "railway=signal",
               "railway=crossing", "railway=buffer_stop", "railway=abandoned", "railway=disused",
               "railway=stop", "railway=tram")
        good2 = ("railway=halt", "railway=tram_stop", "railway=funicular", "railway=monorail",
                 "railway=light_rail", "railway=narrow_gauge", "aerialway=station",
                 "station=light_rail", "station=train_station")
        if any(p.startswith("railway=station") or p.startswith("railway=train_station") for p in pairs):
            return 0
        if any(p.startswith("public_transport=station") for p in pairs):
            return 1
        if any(p.startswith(x) for p in pairs for x in good2):
            return 2
        if any(p.startswith(x) for p in pairs for x in bad):
            return 4
        return 3

    def find_station(self, name, like=None, near=None, operator=None):
        """Ranked OSM matches for a Japanese station name.

        OSM station nodes are conventionally named WITHOUT the trailing 駅, while
        entrances/platforms often carry the full "XX駅" label — so the de-suffixed
        base name is tried first and rank-0/1 matches always beat rank-4 junk.
        Returns [best] + cluster; best.lat/lon is the cluster centroid.
        """
        base = re.sub(r"\s*\(.*?\)\s*", "", (name or "").strip())
        stem = norm_station(base)
        cands = []
        for cand in (stem, base, stem + "駅", base.replace("停留場", "").replace("停留所", ""),
                     (like or "").rstrip("%")):
            if cand and cand not in cands:
                cands.append(cand)
        pool = []
        for nm in cands:
            rows = list(self.con.execute(
                "SELECT rid,name,kind,lat,lon,operator,wiki,wikidata,website,name_en,name_zh,osm_id"
                " FROM st WHERE name=? LIMIT 40", (nm,)).fetchall())
            if rows:
                pool = rows
                break
        if not pool and like:
            pool = list(self.con.execute(
                "SELECT rid,name,kind,lat,lon,operator,wiki,wikidata,website,name_en,name_zh,osm_id"
                " FROM st WHERE name LIKE ? LIMIT 40", (like,)).fetchall())
        if not pool:
            return []

        def score(r):
            rank = self._rank(r["kind"])
            d = hav(near[0], near[1], r["lat"], r["lon"]) if near else 0.0
            if near and d > 30000:
                rank += 100
            op = 0
            if operator and r["operator"]:
                op = 0 if any(o and o in (r["operator"] or "") for o in operator) else 1
            return (rank, op, round(d / 1000.0, 2))

        pool.sort(key=score)
        best_rank = self._rank(pool[0]["kind"])
        if best_rank >= 4:
            good = [r for r in pool if self._rank(r["kind"]) <= 2]
            if good:
                pool = good
                best_rank = self._rank(good[0]["kind"])
        # cluster only nodes of the same station (within 400 m of the best one)
        anchor = pool[0]
        cluster = [r for r in pool if self._rank(r["kind"]) == best_rank
                   and hav(anchor["lat"], anchor["lon"], r["lat"], r["lon"]) <= 400][:16]
        la = sum(x["lat"] for x in cluster) / len(cluster)
        lo = sum(x["lon"] for x in cluster) / len(cluster)
        head = dict(anchor); head["lat"], head["lon"] = la, lo; head["n"] = len(cluster)
        return [head] + cluster

    def geolookup(self, qid):
        r = self.con.execute(f"SELECT {self.COLS} FROM elem WHERE wikidata=? LIMIT 1", (qid,)).fetchall()
        return r[0] if r else None

    def close(self):
        self.con.close()


def strip_refs(t):
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t or "", flags=re.S)
    t = re.sub(r"<ref[^/]*/>", "", t)
    t = re.sub(r"\{\{[Cc]ite[^\n]*?\}\}", "", t, flags=re.S)
    t = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"'''?", "", t)
    t = re.sub(r"</?br\s*/?>", " ", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


def url_host(u):
    try:
        return re.sub(r"^www\.", "", (u.split("//", 1)[1].split("/", 1)[0]))
    except Exception:
        return ""


BAD_HOST = ("archive.org", "web.archive.org", "twitter.com", "facebook.com", "instagram.com",
            "youtube.com", "line.me", "yahoo.co.jp", "google.com", "amap.com", "mhlw.go.jp/statistics")
WIKI_HOST = ("wikipedia.org", "wikimedia.org", "wikidata.org")


def poi_record(row, dist, station_lat, station_lon):
    extra = {}
    try:
        extra = json.loads(row["extra"] or "{}")
    except Exception:
        pass
    site = row["website"]
    wiki_urls = [u for u in [row["wiki"]] if u]
    return {
        "名称": {"日文": row["name"] or row["brand"], "英文": row["name_en"], "中文": row["name_zh"]},
        "类别": {"日文": row["klass"], "英文": META_LABEL[row["klass"]]["en"], "中文": META_LABEL[row["klass"]]["zh"]},
        "细分": {"日文": row["cat"], "英文": CAT_LABEL.get(row["cat"], (row["cat"], row["cat"]))[0],
                 "中文": CAT_LABEL.get(row["cat"], (row["cat"], row["cat"]))[1]},
        "OSM类别": row["osmkey"],
        "距车站m": dist,
        "坐标": {"纬度": round(row["lat"], 6), "经度": round(row["lon"], 6)},
        "地址": extra.get("addr:street") and "".join(filter(None, [extra.get("addr:city", ""), extra.get("addr:street", "")])),
        "官网": site,
        "维基百科": wiki_urls[0] if wiki_urls else None,
        "Wikidata": f"https://www.wikidata.org/wiki/{row['wikidata']}" if row["wikidata"] else None,
        "电话": row["phone"],
        "开放时间": row["opening"],
        "OSM编号": f"{row['osm_type']}/{row['osm_id']}",
        "来源": ["OpenStreetMap (Japan extract, ODbL)"],
    }


# --------------------------------------------------------------------------
#  Wikipedia side: 駅周辺 / バス路線 / 外部リンク  →  structured records
# --------------------------------------------------------------------------

SCHOOL_RE = re.compile(r"(小学校|中学校|高等学校|大学|大学院|高等専修|養護学校|支援学校|特別支援|保育所|幼稚園)")
RELIGION_RE = re.compile(r"(神社|寺院|教会|仏閣|廟$|大社|八幡宮|天神社|稲荷|観音|仏教|霊場|石仏|宝塔|塔婆|チャーチ)")
TRANSIT_RE = re.compile(r"(駅$|停留場|停留所|信号場|港$|フェリー|ターミナル|空港|パーキングエリア|バスストップ|バスターミナル|自動車道|IC$|JCT$|道路|国道|県道|市道|通り$|橋$|トンネル|車庫|車両基地)")
SHOP_RE = re.compile(r"(商店街|百貨店|デパート|センター街|ショッピング|アミュプラザ|ポルタ|地下街|.market|Market|見世|商店館|モール)")
CIVIC_RE = re.compile(r"(警察署|交番|郵便局|消防署|消防団|図書館|公民館|文化センター|文庫$|資料館|博物館|美術館|図書館|役所|役場|市庁舎|区庁|保健所|年金|ハローワーク|労働基準|銀行|金庫|信用組合|信用金庫|保険|病院|クリニック|診療所|医療センター|庁舎|会館|ホール|記念館|体育館|運動場|公園$|庁$|署$|局$)")
SIGHT_RE = re.compile(r"(城$|城址|公園|庭園|美術館|博物館|資料館|記念館|史跡|遺跡|古墳|温泉|滝|渓谷|溪|浜$|海岸|島$|山$|岳$|峯|峠|湖$|ダム|橋$|隧道|桜|藤$|蓮|祭$|名所|景勝|観光|動物園|植物園|水族館|プラネタリウム|遊園地|-slider|$|跡$|展望|自然|森林|緑地|親水|ふ头)")
# Generic concepts / district names that are NOT a concrete nearby facility.
GENERIC = re.compile(r"^(官公庁|路線バス|バス|商店街$|歓楽街|風俗店|周辺|地理|概要|歴史$|文化$|"
                     r".{0,4}(町|丁字|丁目|条|条\d|西\d+|東\d+|南\d+|北\d+)$)")
PLACE_SUFFIX_ONLY = re.compile(r"^[\u3000-\u9fff]{1,4}(駅|局|署|所)$")


def guess_class(label, target):
    """Route a wiki link to one of our guide sections using surface patterns only."""
    s = f"{label} {target}".strip()
    bare = re.sub(r"\s+", "", (target or "").split("(")[0])
    if GENERIC.match(s) or len(s) < 2:
        return "除外", "一般名詞"
    if SCHOOL_RE.search(s):
        return "便民公共服务", "学校"
    if CIVIC_RE.search("郵便局" if "郵便局" in s else s) and "郵便局" in s:
        return "便民公共服务", "郵便局"
    if RELIGION_RE.search(s):
        return "観光・名所案内", "歴史遺産"
    if "図書館" in s or "公民館" in s or "文化センター" in s or "会館" in s or "ホール" in s:
        return "文化・公共施設", "文化施設"
    if "博物館" in s or "美術館" in s or "資料館" in s or "記念館" in s or "プラネタリウム" in s:
        return "文化・公共施設", "文化施設"
    if "公園" in s or "庭園" in s or "緑地" in s or "運動場" in s or "体育館" in s or "スポーツ" in s:
        return "文化・公共施設", "公園" if ("公園" in s or "庭園" in s or "緑地" in s) else "スポーツ"
    if re.search(r"(警察署|交番|署$)", s):
        return "便民公共服务", "行政警察"
    if re.search(r"(役所|役場|庁舎|市庁|区庁|出張所|支所|保健所|年金|ハローワーク|労働基準)", s):
        return "便民公共服务", "行政警察"
    if re.search(r"(病院|診療所|クリニック|医療|衛生|健康センター|消防)", s):
        return "便民公共服务", "医療" if "消防" not in s else "その他公共"
    if re.search(r"(銀行|金庫|信用組合|証券|ATM|保険$)", s):
        return "便民公共服务", "金融"
    if TRANSIT_RE.search(s):
        return "交通接続", "鉄道駅" if re.search(r"(駅|停留場|停留所|信号場)$", bare) else "その他交通"
    if SHOP_RE.search(s) or re.search(r"(百貨店|デパート|モール|商店)$", bare):
        return "買い物・商店街", "商店街"
    if re.search(r"(ホテル|旅館|Hostel|宿泊|グランドプリンス|プリンス$|レーベン|ビューホテル)", s):
        return "宿泊", "宿"
    if SIGHT_RE.search(s):
        return "観光・名所案内", "名所"
    if re.search(r"^(大学|学校)", bare):
        return "便民公共服务", "学校"
    return "除外", "一般名詞"


def guess_class(label, target):
    """Route a wiki link to one of our guide sections using surface patterns only."""
    s = f"{label} {target}"
    if SCHOOL_RE.search(s):
        return "便民公共服务", "学校"
    if RELIGION_RE.search(s):
        return "観光・名所案内", "歴史遺産"
    if CIVIC_RE.search(s):
        return "便民公共服务", "行政警察" if "警察" in s or "役所" in s or "役場" in s or "庁" in s else \
               ("郵便局" if "郵便局" in s else ("金融" if ("銀行" in s or "金庫" in s or "信用" in s) else
                ("医療" if ("病" in s or "診療" in s or "クリニック" in s or "保険" in s) else
                 ("文化施設" if ("図書館" in s or "会館" in s or "ホール" in s or "公民館" in s) else "その他公共"))))
    if TRANSIT_RE.search(s):
        return "交通接続", "鉄道駅" if re.search(r"駅|停留場|停留所", s) else "その他交通"
    if SHOP_RE.search(s):
        return "買い物・商店街", "商店街"
    if SIGHT_RE.search(s):
        return "観光・名所案内", "名所"
    if re.search(r"ホテル|旅館|Hostel|宿泊", s):
        return "宿泊", "宿"
    return "観光・名所案内", "周辺施設"


CAT_LABEL2 = {
    "学校": ("School", "学校"), "その他交通": ("Other transport", "其他交通"),
    "商店街": ("Shopping street", "商店街"), "周辺施設": ("Facilities nearby", "周边设施"),
    "鉄道駅": ("Railway station", "铁路车站"),
}


def wiki_poi_record(target, label, station, dist=None, coords=None, src_extra=None):
    title = re.sub(r"\s+", " ", target.split("#")[0]).strip()
    url = "https://ja.wikipedia.org/wiki/" + title.replace(" ", "_")
    rec = {
        "名称": {"日文": label or title, "英文": None, "中文": None},
        "类别": None, "细分": None,
        "距车站m": dist,
        "坐标": ({"纬度": round(coords[0], 6), "经度": round(coords[1], 6)} if coords else None),
        "地址": None, "官网": None,
        "维基百科": url,
        "Wikidata": None, "电话": None, "开放时间": None,
        "OSM编号": None,
        "来源": ["日本語ウィキペディア「%s」記事「駅周辺」欄（CC BY-SA 4.0）" % station],
    }
    klass, cat = guess_class(label, title)
    rec["类别"] = {"日文": klass, "英文": META_LABEL[klass]["en"], "中文": META_LABEL[klass]["zh"]}
    en, zh = CAT_LABEL.get(cat) or CAT_LABEL2.get(cat) or (cat, cat)
    rec["细分"] = {"日文": cat, "英文": en, "中文": zh}
    if src_extra:
        rec["来源"] += src_extra
    rec["_title"] = title
    return rec


def build_station(st, line_ja, osmdb, radius=900, seed=None, hints=None):
    """Assemble one station record. budget: dict of remaining API-call credits (mutated)."""
    name_ja = st.get("词条") or ((st.get("名称") or {}).get("词条"))
    disp = (st.get("名称") or {}).get("日文原名") or st.get("日文原名")
    entry = st.get("词条") or st.get("日文原名") or ""
    if not entry:
        return None, {"no_title": 1}
    wt = W.wikitext(entry, "ja")
    secs, order = W.sections(wt) if wt else ({}, [])

    # --- coordinates: OSM station table (offline), ranked by kind/nearness/operator ---
    lat = lon = None
    coord_src = None
    cand = osmdb.find_station(entry, like=(norm_station(disp) + "%") if disp else None,
                              near=seed, operator=hints)
    if not cand and disp:
        cand = osmdb.find_station(disp, near=seed, operator=hints)
    if cand:
        lat, lon = cand[0]["lat"], cand[0]["lon"]
        coord_src = f"OpenStreetMap {cand[0]['rid']} ({cand[0]['kind'] or 'n/a'})"

    stat = {}
    lead_ja = strip_refs(secs.get("__lead__") or "") if False else None
    # lead = text before first section
    first = wt.split("==")[0] if wt else ""
    lead_ja = strip_refs(re.sub(r"\{\{[^{}]*\}\}|\[\[[^\]]*\]\]|<!--.*?-->", " ", first, flags=re.S))[:900]

    around_txt = strip_refs(secs.get("駅周辺", ""))[:3000]
    bus_txt = strip_refs(secs.get("バス路線", ""))[:2500]
    # 名前 changed 名前->词条 for links helper
    links = []
    for tgt, lab in W.links_in(secs.get("駅周辺", "")):
        links.append((tgt, lab))

    # --- OSM POIs ---
    pois = []
    seen_rid = set()
    if lat is not None:
        for r in osmdb.around(lat, lon, radius):
            if r["rid"] in seen_rid:
                continue
            seen_rid.add(r["rid"])
            rec = poi_record(r, hav(lat, lon, r["lat"], r["lon"]), lat, lon)
            pois.append(rec)

    # --- merge wiki 駅周辺 links into POI set (match by name), else add wiki-only records ---
    byname = {}
    for p in pois:
        n = (p["名称"]["日文"] or "").strip()
        if n:
            byname.setdefault(n, []).append(p)
        n2 = re.sub(r"\s+", "", n)
        byname.setdefault(n2, []).append(p)
    wiki_only = []
    for tgt, lab in links:
        key = re.sub(r"\s+", "", lab)
        key2 = re.sub(r"\s+", "", tgt.split("(")[0].strip())
        hit = None
        for k in (key, key2, re.sub(r"駅$", "", key2) + "駅"):
            if k in byname:
                hit = byname[k][0]
                break
        if hit:
            hit["维基百科"] = "https://ja.wikipedia.org/wiki/" + re.sub(r"\s+", "_", tgt.split("(")[0].strip())
            hit["来源"].append("日本語ウィキペディア「%s」記事「駅周辺」欄（CC BY-SA 4.0）" % entry)
            continue
        wr = wiki_poi_record(tgt, lab, entry)
        if wr["类别"]["日文"] != "除外":
            wiki_only.append(wr)

    # --- official links from the station article itself ---
    ext = W.ext_links(secs.get("外部リンク", "") + " " + (wt or ""))
    refs = W.ref_urls(wt or "")
    official = []
    for u in dict.fromkeys(ext + refs):
        h = url_host(u)
        if not h or any(b in h for b in BAD_HOST) or any(b in h for b in WIKI_HOST):
            continue
        if any(x in h for x in ("navitime", "jorudan", "yahoo", "google", "twitter", "facebook",
                                        "youtube", "instagram", "line.me", "archive.org", "wayback",
                                        "rakuten.co.jp", "tripadvisor", "tabelog", "mapion", "amap")):
            continue
        official.append(u)
    official = official[:12]

    recs = (pois + wiki_only)
    # cap per class to keep files sane: sights unlimited-ish, transit bus_stop trimmed
    def cap(rr):
        out, cnt = [], {}
        for p in sorted(rr, key=lambda x: (x["距车站m"] is None, x["距车站m"] or 9e9)):
            k = p["类别"]["日文"] if p.get("类别") else "?"
            lim = {"交通接続": 26, "便民公共服务": 26, "買い物・商店街": 18, "宿泊": 12,
                   "観光・名所案内": 40, "文化・公共施設": 30}.get(k, 30)
            if cnt.get(k, 0) >= lim:
                continue
            cnt[k] = cnt.get(k, 0) + 1
            out.append(p)
        return out
    recs = cap(recs)

    # sections bucket (excluding generic/non-facility wiki links)
    bucket = {c: [] for c in CLASS_ORDER}
    excluded = 0
    for p in recs:
        k = p["类别"]["日文"]
        if k == "除外":
            excluded += 1
            continue
        bucket.setdefault(k, []).append(p)
    global LAST_EXCLUDED
    LAST_EXCLUDED = excluded
    for c in bucket:
        bucket[c].sort(key=lambda x: (x["距车站m"] is None, x["距车站m"] or 9e9))

    out = {
        "车站ID": f"{line_ja}#{st.get('序号')}",
        "名称": {"日文": entry, "日文原名": disp, "英文": st.get("英文"), "中文": st.get("中文"),
                 "词条": entry, "读音": st.get("读音"), "罗马字": st.get("罗马字")},
        "所属线路": {"日文": line_ja},
        "位置": {"坐标": ({"纬度": round(lat, 6), "经度": round(lon, 6), "出处": coord_src} if lat is not None else None),
                 "坐标可用": lat is not None},
        "车站条目": {"链接": "https://ja.wikipedia.org/wiki/" + entry.replace(" ", "_"),
                     "首段": {"日文": lead_ja or None, "英文": None, "中文": None},
                     "駅周辺原文": {"日文": around_txt or None}},
        "交通接続メモ": {"バス路線原文": {"日文": bus_txt or None}},
        "公式リンク": official,
        "分区": bucket,
        "統計": {c: len(v) for c, v in bucket.items()},
        "_駅周辺リンク": [[t, l] for t, l in links],
        "データ品質": {"座標": "OSM" if lat is not None else "未得",
                     "OSM施設数": len(pois), "ウィキ_only数": len(wiki_only),
                     "駅周辺リンク数": len(links)},
    }
    return out, stat
