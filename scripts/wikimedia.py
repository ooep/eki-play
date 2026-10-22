#!/usr/bin/env python3
"""Wikimedia helpers: raw wikitext, section slicing, red-link detection, page views,
Wikidata labels/claims. All responses disk-cached under EKI_CACHE."""
import json, os, re, sys, time, threading, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("EKI_CACHE", "/data/work/cache")
os.makedirs(CACHE, exist_ok=True)
UA = "eki-play-research/1.0 (https://github.com/ooep/eki-play)"
_lock = threading.Lock()

API = {"ja": "https://ja.wikipedia.org/w/api.php",
       "en": "https://en.wikipedia.org/w/api.php",
       "zh": "https://zh.wikipedia.org/w/api.php"}


def _k(*p):
    import hashlib
    return hashlib.sha1("||".join(p).encode()).hexdigest()[:24]


def cached(fn, ttl=86400 * 20):
    p = os.path.join(CACHE, fn)
    if os.path.exists(p) and (ttl is None or time.time() - os.path.getmtime(p) < ttl):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def store(fn, obj):
    p = os.path.join(CACHE, fn)
    with _lock:
        tmp = f"{p}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, p)
    return obj


def curl(url, timeout=45, headers=None):
    import subprocess
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-A", UA, "-w", "\n@@%{http_code}", "-G"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    base, _, qs = url.partition("?")
    cmd.append(base)
    for part in qs.split("&"):
        if part:
            cmd += ["--data-urlencode", part]
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n@@")
    try:
        return int(code or 0), json.loads(body)
    except Exception:
        return int(code or 0), None


def post_json(url, fields, timeout=60):
    import subprocess
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-A", UA, "-w", "\n@@%{http_code}"]
    for k, v in fields.items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n@@")
    try:
        return int(code or 0), json.loads(body)
    except Exception:
        return int(code or 0), None


def wikitext(title, lang="ja"):
    """Raw wikitext of a page (cache-persistent, no TTL to keep cost down)."""
    fn = "wt_" + _k(lang, title)
    hit = cached(fn, ttl=None)
    if hit is not None:
        return hit.get("text")
    code, d = post_json(API[lang], {"action": "parse", "page": title, "prop": "wikitext",
                                    "redirects": "1", "format": "json", "formatversion": "2"})
    text = None
    if code == 200 and d and "parse" in d:
        text = d["parse"].get("wikitext")
    store(fn, {"text": text, "code": code, "missing": text is None})
    return text


def exists(title, lang="ja"):
    fn = "ex_" + _k(lang, title)
    hit = cached(fn, ttl=None)
    if hit is not None:
        return hit.get("ok")
    code, d = post_json(API[lang], {"action": "query", "titles": title, "redirects": 1,
                                    "format": "json", "formatversion": "2"})
    ok = False
    norm = None
    if code == 200 and d:
        pages = d.get("query", {}).get("pages", [])
        ok = bool(pages) and "missing" not in pages[0]
        if ok:
            norm = pages[0].get("title")
    store(fn, {"ok": ok, "title": norm})
    return ok


def page_exists_batch(titles, lang="ja"):
    """Batch existence check; returns dict title->bool."""
    todo = [t for t in titles if cached("ex_" + _k(lang, t), ttl=None) is None]
    for i in range(0, len(todo), 40):
        chunk = todo[i:i + 40]
        code, d = post_json(API[lang], {"action": "query", "titles": "|".join(chunk),
                                       "redirects": 1, "format": "json", "formatversion": "2"})
        seen = {}
        if code == 200 and d:
            for p in d.get("query", {}).get("pages", []):
                seen[p.get("title")] = "missing" not in p
        for t in chunk:
            store("ex_" + _k(lang, t), {"ok": bool(seen.get(t)), "title": t})
        time.sleep(0.25)
    return {t: cached("ex_" + _k(lang, t), ttl=None).get("ok") for t in titles}


def page_views(title, lang="ja", months=6):
    fn = "pv_" + _k(lang, title)
    hit = cached(fn, ttl=None)
    if hit is not None:
        return hit.get("views")
    t = title.replace(" ", "_")
    from datetime import date, timedelta
    end = date(2026, 10, 1)
    start = end - timedelta(days=30 * months)
    u = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
         f"{lang}.wikipedia/all-access/user/{t}/monthly/{start:%Y%m%d00}/{end:%Y%m%d00}")
    code, d = curl(u, timeout=30)
    views = 0
    if code == 200 and d:
        views = sum(x.get("views", 0) for x in d.get("items", []))
    store(fn, {"views": views if views else None, "code": code})
    return views or None


# ---------------- section slicing ----------------
SEC_RE = re.compile(r"^(={2,3})\s*(.+?)\s*\1\s*$", re.M)


def sections(wt):
    """Return {heading: body} for == / === level sections."""
    out, order = {}, []
    marks = list(SEC_RE.finditer(wt or ""))
    for i, m in enumerate(marks):
        level, head = len(m.group(1)), m.group(2).strip()
        end = len(wt)
        for n in marks[i + 1:]:
            if len(n.group(1)) <= level:
                end = n.start()
                break
        out.setdefault(head, wt[m.end():end].strip())
        order.append(head)
    return out, order


LINK_RE = re.compile(r"\[\[([^\]|#]*)(?:\|([^\]]*))?\]\]")


def links_in(body, limit=120):
    """[[Target|Label]] -> list of (target, label). Deduplicated, order kept."""
    seen, out = set(), []
    for m in LINK_RE.finditer(body or ""):
        tgt = m.group(1).strip()
        lab = (m.group(2) or tgt).strip()
        if not tgt or tgt in seen or ":" in tgt.split("|")[0]:
            continue
        if re.search(r"^\d|^[A-Za-z]{1,2}$", tgt):
            continue
        seen.add(tgt)
        out.append((tgt, lab))
        if len(out) >= limit:
            break
    return out


EXT_RE = re.compile(r"(?m)^\s*(?:#\s*)?\*\s*\[([^\]\s]+)[^\]]*\]\s*([^\n]*)")


def ext_links(wt, host_filter=None):
    """Collect bulleted external links: [url label] and {{Cite web|url=...}}."""
    urls = []
    for m in re.finditer(r"https?://[^\s\"'<>{}\]\[]+", wt or ""):
        u = m.group(0).rstrip(".,;)")
        if host_filter and host_filter not in u:
            continue
        if any(b in u for b in ("wikimedia.org", "wikipedia.org/wiki/Template", "navitime",
                                "archive.org/wayback")):
            continue
        if u not in urls:
            urls.append(u)
    return urls


REF_URL_RE = re.compile(r"\{\{[Cc]ite\s+\w+[^}]*?url\s*=\s*(https?://[^\s|}]+)")


def ref_urls(wt):
    return [m.group(1).rstrip(".,;)") for m in REF_URL_RE.finditer(wt or "")]


# ---------------- Wikidata ----------------
def wd_entities(qids):
    """labels+descs+website for a batch of QIDs via wbgetentities."""
    if not qids:
        return {}
    qids = [q for q in qids if re.fullmatch(r"Q\d+", q or "")]
    fn = "wd_" + _k(",".join(sorted(qids)))
    hit = cached(fn, ttl=None)
    if hit is not None:
        return hit
    code, d = post_json("https://www.wikidata.org/w/api.php",
                        {"action": "wbgetentities", "ids": "|".join(qids), "format": "json",
                         "props": "labels|descriptions|claims|sitelinks"}, timeout=60)
    out = {}
    if code == 200 and d:
        for q, e in (d.get("entities") or {}).items():
            def lb(langs):
                for L in langs:
                    v = (e.get("labels") or {}).get(L, {}).get("value")
                    if v:
                        return v
                return None

            def ds(langs):
                for L in langs:
                    v = (e.get("descriptions") or {}).get(L, {}).get("value")
                    if v:
                        return v
                return None

            claims = e.get("claims") or {}

            def claim(pid):
                for sn in claims.get(pid, []):
                    mv = (sn.get("mainsnak") or {}).get("datavalue")
                    if mv:
                        v = mv.get("value")
                        if isinstance(v, dict):
                            return v.get("id")
                        return v
                return None

            out[q] = {"label_ja": lb(["ja"]), "label_en": lb(["en"]), "label_zh": lb(["zh-cn", "zh-hans", "zh"]),
                      "desc_ja": ds(["ja"]), "desc_en": ds(["en"]), "desc_zh": ds(["zh-cn", "zh-hans", "zh"]),
                      "website": claim("P856"), "wikidata_ja": (e.get("sitelinks") or {}).get("jawiki"),
                      "wikidata_en": (e.get("sitelinks") or {}).get("enwiki"),
                      "wikidata_zh": (e.get("sitelinks") or {}).get("zhwiki"),
                      "instance": claim("P31"), "located": claim("P131")}
    store(fn, out)
    return out


def wd_search(name, lang="ja", radius_km=None, lat=None, lon=None, limit=1):
    """Search Wikidata entities by label; optional geo-proximity filter via geosearch."""
    fn = "wds_" + _k(name, lang, str(lat), str(lon))
    hit = cached(fn, ttl=None)
    if hit is not None:
        return hit
    fields = {"action": "wbsearchentities", "search": name, "language": lang,
              "uselang": lang, "format": "json", "limit": "10", "type": "item"}
    if lat is not None and lon is not None:
        fields.update({"geo_lat": f"{lat:.5f}", "geo_lon": f"{lon:.5f}"})
    code, d = post_json("https://www.wikidata.org/w/api.php", fields, timeout=45)
    res = []
    if code == 200 and d:
        for it in d.get("search", []):
            res.append({"qid": it.get("id"), "label": it.get("label"), "desc": it.get("description")})
    store(fn, res[:limit] if res else res)
    return res


def wd_geosearch(lat, lon, radius_m=1500, limit=500):
    """All Wikidata items within radius (great circle) -> [{qid, lon, lat, dist_m}]"""
    fn = "wdg_" + _k(str(round(lat, 4)), str(round(lon, 4)), str(radius_m // 100))
    hit = cached(fn, ttl=None)
    if hit is not None:
        return hit
    u = "https://www.wikidata.org/w/api.php"
    code, d = post_json(u, {"action": "query", "list": "geosearch",
                            "gscoord": f"{lat}|{lon}", "gsradius": min(radius_m, 10000),
                            "gslimit": str(min(limit, 500)), "format": "json", "formatversion": "2"},
                        timeout=60)
    rows = []
    if code == 200 and d:
        for g in d.get("query", {}).get("geosearch", []):
            rows.append({"qid": g.get("title"), "lat": g.get("lat"), "lon": g.get("lon"),
                         "dist": g.get("dist")})
    store(fn, rows)
    return rows


if __name__ == "__main__":
    wt = wikitext("すすきの駅")
    ss, order = sections(wt)
    print("sections:", list(ss)[:20])
    body = ss.get("駅周辺", "")
    lk = links_in(body)
    print("駅周辺 links:", len(lk))
    for t, l in lk[:15]:
        print("   ", t, "->", l)
    print("ext urls:", ext_links(wt)[:6])
    print("ref urls:", ref_urls(wt)[:4])


def url_host(u):
    try:
        return re.sub(r"^www\.", "", (u.split("//", 1)[1].split("/", 1)[0]).lower())
    except Exception:
        return ""


WIKI_HOST = ("wikipedia.org", "wikimedia.org", "wikidata.org")
BAD_HOST = ("archive.org", "web.archive.org", "twitter.com", "facebook.com", "instagram.com",
            "youtube.com", "line.me", "yahoo.co.jp", "google.com")


# ---------------- batched, globally-cached helpers ----------------
def _get(path, params):
    return post_json(path, params, timeout=90)


def batch_titles(titles, lang="ja", props="pageprops|extracts", extra=None, tag="t2q"):
    """Fetch page data for many titles in chunks of 50; each chunk cached globally
    under a key derived from its member titles, so any partition order is stable."""
    titles = list(dict.fromkeys(t for t in titles if t))
    out = {}
    todo = []
    for i in range(0, len(titles), 50):
        ch = titles[i:i + 50]
        fn = f"{tag}_" + _k(lang, ",".join(sorted(ch)))
        hit = cached(fn, ttl=None)
        if hit is not None:
            out.update(hit)
        else:
            todo.append((ch, fn))
    for ch, fn in todo:
        f = {"action": "query", "titles": "|".join(ch), "prop": props, "redirects": "1",
             "converttitles": "1",
             "format": "json", "formatversion": "2", "exintro": "1", "explaintext": "1",
             "ppprop": "wikibase_item"}
        if extra:
            f.update(extra)
        code, d = _get(API[lang], f)
        res = {}
        if code == 200 and d:
            for pg in d.get("query", {}).get("pages", []):
                if pg.get("missing"):
                    continue
                res[pg.get("title")] = {
                    "qid": (pg.get("pageprops") or {}).get("wikibase_item"),
                    "lead": (pg.get("extract") or "")[:900] or None,
                    "title": pg.get("title")}
        store(fn, res)
        out.update(res)
        time.sleep(0.15)
    return out


def batch_entities(qids):
    qids = sorted(set(q for q in qids if re.fullmatch(r"Q\d+", q or "")))
    out = {}
    todo = []
    for i in range(0, len(qids), 45):
        ch = qids[i:i + 45]
        fn = "ent_" + _k(",".join(ch))
        hit = cached(fn, ttl=None)
        if hit is not None:
            out.update(hit)
        else:
            todo.append((ch, fn))
    for ch, fn in todo:
        code, d = _get("https://www.wikidata.org/w/api.php",
                       {"action": "wbgetentities", "ids": "|".join(ch), "format": "json",
                        "props": "labels|descriptions|claims|sitelinks"})
        res = {}
        if code == 200 and d:
            for q, e in (d.get("entities") or {}).items():
                def lb(langs, node):
                    for L in langs:
                        v = (e.get(node) or {}).get(L, {}).get("value")
                        if v:
                            return v
                    return None
                claims = e.get("claims") or {}
                site = None
                for sn in claims.get("P856", []):
                    mv = (((sn or {}).get("mainsnak") or {}).get("datavalue") or {}).get("value")
                    if isinstance(mv, str) and mv.startswith("http"):
                        site = mv
                        break
                def sid(lang):
                    sl = (e.get("sitelinks") or {}).get(lang)
                    return sl.get("title") if sl else None
                res[q] = {"ja": lb(["ja"], "labels"), "en": lb(["en"], "labels"),
                          "zh": lb(["zh-cn", "zh-hans", "zh-hk", "zh-tw", "zh"], "labels"),
                          "dja": lb(["ja"], "descriptions"), "den": lb(["en"], "descriptions"),
                          "dzh": lb(["zh-cn", "zh-hans", "zh"], "descriptions"),
                          "site": site, "swiki_ja": sid("jawiki"), "swiki_zh": sid("zhwiki"),
                          "swiki_en": sid("enwiki")}
        store(fn, res)
        out.update(res)
        time.sleep(0.15)
    return out


def search_entities(name, lang="ja", geo=None):
    """wbsearchentities; optional geo=(lat,lon) biases results."""
    fn = "wds2_" + _k(name, lang, str(geo))
    hit = cached(fn, ttl=None)
    if hit is not None:
        return hit
    f = {"action": "wbsearchentities", "search": name, "language": lang, "uselang": lang,
         "format": "json", "limit": "8", "type": "item"}
    if geo:
        f.update({"geo_lat": f"{geo[0]:.5f}", "geo_lon": f"{geo[1]:.5f}"})
    code, d = _get("https://www.wikidata.org/w/api.php", f)
    res = []
    if code == 200 and d:
        res = [{"qid": it.get("id"), "label": it.get("label"), "desc": it.get("description")}
               for it in d.get("search", [])]
    store(fn, res)
    time.sleep(0.12)
    return res


def head_status(url, timeout=15):
    """Return final HTTP status after redirects, plus resolved URL."""
    import subprocess
    r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-L", "--max-time", str(timeout),
                        "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                        "-w", "%{http_code} %{url_effective} %{redirect_url}", url],
                       capture_output=True, text=True)
    parts = (r.stdout or "").split()
    try:
        code = int(parts[0]) if parts else 0
    except Exception:
        code = 0
    return code, (parts[1] if len(parts) > 1 else url)
