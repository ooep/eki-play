"""Shared fetch helpers: Overpass, Nominatim, MediaWiki, Wikidata. Disk-cached, retry-safe."""
import json, os, re, time, hashlib, urllib.parse, subprocess, threading
CACHE = os.environ.get("EKI_CACHE", "/data/work/cache")
os.makedirs(CACHE, exist_ok=True)
_lock = threading.Lock()

def _key(*parts):
    return hashlib.sha1("||".join(parts).encode()).hexdigest()[:24]

def cached(name, ttl=None):
    p = os.path.join(CACHE, name)
    if os.path.exists(p) and (ttl is None or time.time() - os.path.getmtime(p) < ttl):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None

def store(name, obj):
    p = os.path.join(CACHE, name)
    with _lock:
        tmp = p + f".{os.getpid()}.tmp"
        json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, p)
    return obj

def curl(url, post=None, timeout=90, headers=None):
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-A", "eki-wiki-research/1.0 (ooep)", "-w", "\n@@%{http_code}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if post is not None:
        cmd += ["--data-urlencode", "data=" + post]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n@@")
    return int(code or 0), body

# ---------- Nominatim (station coordinates) ----------
def geocode_station(q):
    cn = "geo_" + _key(q)
    hit = cached(cn, ttl=86400 * 30)
    if hit is not None:
        return hit
    u = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "jp", "addressdetails": 1})
    code, body = curl(u, headers={"Accept": "application/json"}, timeout=60)
    out = None
    if code == 200:
        try:
            rows = json.loads(body)
        except Exception:
            rows = []
        st = [x for x in rows if x.get("category") == "railway" and x.get("type") in ("station", "subway", "tram_stop", "halt", "tram")]
        pick = (st or rows or [None])[0]
        out = {"lat": float(pick["lat"]), "lon": float(pick["lon"]), "osm": pick.get("osm_id"),
               "name": pick.get("display_name", ""), "kind": f"{pick.get('category')}/{pick.get('type')}" } if pick else None
    store(cn, out)
    time.sleep(1.05)
    return out

# ---------- Wikipedia (any Wikimedia MW via its own api.php) ----------
MW_API = "https://ja.wikipedia.org/w/api.php"

def wiki_wikitext(title, lang="ja"):
    cn = "wt_" + _key(lang, title)
    hit = cached(cn, ttl=86400 * 14)
    if hit is not None:
        return hit
    api = {"ja": "https://ja.wikipedia.org/w/api.php", "en": "https://en.wikipedia.org/w/api.php",
           "zh": "https://zh.wikipedia.org/w/api.php"}[lang]
    code, body = curl(api, post=None, timeout=60, headers={"Accept": "application/json"})
    u = api + "?" + urllib.parse.urlencode({"action": "parse", "page": title, "prop": "wikitext",
                                            "redirects": 1, "format": "json", "formatversion": 2})
    code, body = curl(u, timeout=60)
    out = None
    if code == 200:
        try:
            out = body.split("||")[0] if False else json.loads(body)["parse"].get("wikitext")
        except Exception:
            out = None
    store(cn, out)
    return out

# ---------- Overpass POIs around a point ----------
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
CAT = {
 "観光・名所": ['node["tourism"~"attraction|viewpoint|artwork|museum|gallery|zoo|theme_park|aquarium|iwi|hotel|hostel|guest_house|information"]',
               'node["historic"~"memorial|monument|ruins|castle|archaeological_site|battlefield"]'],
 "文化・公園": ['node["leisure"~"park|garden|sports_centre|stadium|swimming_pool"]', 'node["amenity"~"theatre|cinema|library|community_centre|museum"]'],
 "商店街・買い物": ['node["shop"~"convenience|supermarket|department_store"]', 'node["amenity"~"marketplace"]', 'way["shop"~"department_store"]'],
 "公共服务": ['node["amenity"~"police|townhall|post_office|hospital|clinic|pharmacy|bank|atm|public_bookcase"]',
             'node["office"~"government|tourism"]', 'node["emergency"~"assembly_point"]'],
 "交通": ['node["highway"~"bus_stop"]', 'node["public_transport"~"platform"]', 'node["amenity"~"bicycle_rental|taxi"]', 'node["railway"~"station|halt|tram_stop"]'],
}

def overpass_around(lat, lon, radius_m=900, cats=None, key=None, tries=4):
    cn = "op_" + _key(str(round(lat, 4)), str(round(lon, 4)), str(radius_m), ",".join(cats or ["ALL"]))
    if key:
        cn = "op_" + key
    hit = cached(cn, ttl=86400 * 7)
    if isinstance(hit, dict) and "elements" in hit:
        return hit
    if isinstance(hit, dict) and hit.get("_fail"):
        return None
    sel = "\n".join(c.rstrip().rstrip(";") + ";" for k, v in CAT.items() if (cats is None or k in cats) for c in v)
    q = f"""[out:json][timeout:25];
(
{sel}
(around:{radius_m},{lat},{lon});
);
out tags 300;"""
    for attempt in range(tries):
        for ep in OVERPASS:
            code, body = curl(ep, post=q, timeout=60)
            if code == 200 and body.lstrip().startswith("{"):
                try:
                    d = json.loads(body)
                    d["_endpoint"] = ep
                    store(cn, d)
                    return d
                except Exception:
                    pass
            time.sleep(3 + 7 * attempt)
    store(cn, {"_fail": 1, "elements": [], "attempts": tries})
    return None
