#!/usr/bin/env python3
"""Phase-2 global enrichment over every generated line file.

Stages
  sites    : official website for POI records (OSM tag -> Wikidata P856 -> ja-wiki 外部リンク)
  names    : English / Chinese names (OSM name:en|zh -> Wikidata labels -> zh-wiki title -> en-wiki)
  leads    : short trilingual blurb for sight-class records (ja/zh/en wiki extracts, Wikidata desc)
  verify   : HEAD/GET check on every 官网 / 公式リンク, records http status + checked date

All stages are cache-first and idempotent, so reruns only pay for new work.
"""
import json, os, re, sys, time, glob, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W

OUT = os.environ.get("EKI_OUT", "/data/eki-play/data/lines")
WORK = "/data/work/enrich"
os.makedirs(WORK, exist_ok=True)

BATCH = 50


def iter_lines():
    for p in sorted(glob.glob(os.path.join(OUT, "*.json"))):
        yield p, json.load(open(p, encoding="utf-8"))


def save(p, obj):
    tmp = p + f".{os.getpid()}.tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def all_records(d):
    for st in d["车站"]:
        for klass, rows in st["分区"].items():
            for r in rows:
                yield st, klass, r


# ------------------------------------------------- title -> QID (batch) ----
def titles_to_qids(titles):
    """action=query&titles=... on jawiki -> pageprops.wikibase_item."""
    out = W.cached("t2q_" + W._k(",".join(sorted(titles))), ttl=None)
    if out is not None:
        return out
    res = {}
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        code, d = W.post_json(W.API["ja"], {"action": "query", "titles": "|".join(chunk),
                                            "prop": "pageprops|extracts", "ppprop": "wikibase_item",
                                            "exintro": "1", "explaintext": "1", "redirects": "1",
                                            "format": "json", "formatversion": "2"}, timeout=90)
        if code == 200 and d:
            for pg in d.get("query", {}).get("pages", []):
                if pg.get("missing"):
                    continue
                res[pg.get("title")] = {
                    "qid": (pg.get("pageprops") or {}).get("wikibase_item"),
                    "lead_ja": (pg.get("extract") or "")[:700] or None,
                }
        time.sleep(0.2)
    W.store("t2q_" + W._k(",".join(sorted(titles))), res)
    return res


def qids_to_entities(qids):
    out = W.cached("e2q_" + W._k(",".join(sorted(qids))), ttl=None)
    if out is not None:
        return out
    res = {}
    for i in range(0, len(qids), 45):
        chunk = qids[i:i + 45]
        code, d = W.post_json("https://www.wikidata.org/w/api.php",
                             {"action": "wbgetentities", "ids": "|".join(chunk), "format": "json",
                              "props": "labels|descriptions|claims|sitelinks"}, timeout=90)
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
                    if mv:
                        site = mv
                        break
                tl = claims.get("P31", [])
                inst = None
                if tl:
                    inst = (((tl[0] or {}).get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id")
                res[q] = {"ja": lb(["ja"], "labels"), "en": lb(["en"], "labels"),
                          "zh": lb(["zh-cn", "zh-hans", "zh-hk", "zh"], "labels"),
                          "dja": lb(["ja"], "descriptions"), "den": lb(["en"], "descriptions"),
                          "dzh": lb(["zh-cn", "zh-hans", "zh"], "descriptions"),
                          "site": site, "instance": inst,
                          "ja_wiki": (e.get("sitelinks") or {}).get("jawiki", {}).get("title") if (e.get("sitelinks") or {}).get("jawiki") else None,
                          "zh_wiki": (e.get("sitelinks") or {}).get("zhwiki", {}).get("title") if (e.get("sitelinks") or {}).get("zhwiki") else None,
                          "en_wiki": (e.get("sitelinks") or {}).get("enwiki", {}).get("title") if (e.get("sitelinks") or {}).get("enwiki") else None}
        time.sleep(0.2)
    W.store("e2q_" + W._k(",".join(sorted(qids))), res)
    return res


ZH_SUF = re.compile(r"(駅|停留場|停留所|站|信号場)$")


def zh_title_for(ja_title, ent):
    """Chinese name for a Japanese facility title: zhwiki sitelink, else strip 駅 -> 站."""
    if ent and ent.get("zh_wiki"):
        return ent["zh_wiki"]
    if ent and ent.get("zh"):
        return ent["zh"]
    if ja_title and re.search(r"駅$", ja_title):
        base = re.sub(r"\s*\(.*?\)\s*", "", ja_title)
        if base.endswith("駅"):
            return base[:-1] + "站"
    return None


def en_lead(title):
    fn = "enlead_" + W._k(title)
    hit = W.cached(fn, ttl=None)
    if hit is not None:
        return hit.get("lead")
    code, d = W.post_json(W.API["en"], {"action": "query", "titles": title, "prop": "extracts",
                                        "exintro": "1", "explaintext": "1", "redirects": "1",
                                        "format": "json", "formatversion": "2"}, timeout=60)
    lead = None
    if code == 200 and d:
        for pg in d.get("query", {}).get("pages", []):
            lead = (pg.get("extract") or "")[:700] or None
    W.store(fn, {"lead": lead})
    return lead


def zh_lead(title):
    fn = "zhlead_" + W._k(title)
    hit = W.cached(fn, ttl=None)
    if hit is not None:
        return hit.get("lead")
    lead = None
    if title:
        code, d = W.post_json(W.API["zh"], {"action": "query", "titles": title, "prop": "extracts",
                                            "exintro": "1", "explaintext": "1", "redirects": "1",
                                            "format": "json", "formatversion": "2"}, timeout=60)
        if code == 200 and d:
            for pg in d.get("query", {}).get("pages", []):
                if not pg.get("missing"):
                    lead = (pg.get("extract") or "")[:700] or None
    W.store(fn, {"lead": lead})
    return lead


SOCIAL = ("twitter.com", "facebook.com", "instagram.com", "youtube.com", "line.me", "tiktok.com",
          "weibo.com", "weixin.qq.com")
AGG = ("amazon.", "rakuten.co.jp/gold", "yahoo.co.jp/search", "google.", "tripadvisor", "tabelog",
       "ekiben", "jorudan", "navitime", "mapion", "machi-map", "goo.ne.jp", "hitachi", "twitter")


def site_from_wiki(title):
    """First plausible official URL inside the facility's own jawiki article."""
    wt = W.wikitext(title, "ja")
    if not wt:
        return None
    urls = W.ext_links(wt) + W.ref_urls(wt)
    ext_sec = W.sections(wt)[0].get("外部リンク", "")
    urls = W.ext_links(ext_sec) + urls
    for u in urls:
        h = W.url_host(u) if hasattr(W, "url_host") else ""
        if not h or any(b in h for b in SOCIAL) or any(b in h for b in WIKI_HOST):
            continue
        if re.search(r"(profile|share|ref=|/search|bookmark|twitter|facebook)", u):
            continue
        return u
    return None


# ------------------------------------------------------------- stages -----
def stage_titles(path):
    """Collect unique jawiki titles needing QID resolution."""
    d = json.load(open(path, encoding="utf-8"))
    titles = set()
    for st, klass, r in all_records(d):
        t = r.get("_title") or (r.get("维基百科") or "")
        if r.get("维基百科") and not r.get("Wikidata"):
            t = re.sub(r"^https://ja\.wikipedia\.org/wiki/", "", r["维基百科"]).replace("_", " ")
            if t:
                titles.add(t)
        elif t:
            titles.add(t)
    for st in d["车站"]:
        for u in st.get("公式リンク", []):
            pass
    return d, sorted(titles)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "titles"
    files = sorted(glob.glob(os.path.join(OUT, "*.json")))
    if stage == "titles":
        allt = set()
        for p in files:
            _, t = stage_titles(p)
            allt.update(t)
        json.dump(sorted(allt), open(os.path.join(WORK, "titles.json"), "w", encoding="utf-8"), ensure_ascii=False)
        print("unique jawiki titles:", len(allt))


if __name__ == "__main__":
    main()
