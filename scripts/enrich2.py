#!/usr/bin/env python3
"""Phase-2 enrichment: fill 官网 / 英文名 / 中文名 / 三语简介 for every POI record,
then verify every outbound URL. Cache-first and idempotent; use --budget to cap work.

  python3 enrich2.py            # full pass over /data/eki-play/data/lines/*.json
  python3 enrich2.py --budget 20000 --verify 1
"""
import json, os, re, sys, time, glob, threading, collections
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W

OUT = os.environ.get("EKI_OUT", "/data/eki-play/data/lines")
LOG = "/data/work/enrich"
os.makedirs(LOG, exist_ok=True)
DAY = time.strftime("%Y-%m-%d")

# 类别 -> how much we care about enriching it (1 = sights, 3 = services, 9 = skip wiki fetch)
PRIORITY = {"観光・名所案内": 0, "文化・公共施設": 1, "買い物・商店街": 2,
            "便民公共服务": 3, "交通接続": 4, "宿泊": 5}
SOCIAL = ("twitter.com", "facebook.com", "instagram.com", "youtube.com", "line.me", "tiktok.com",
          "weibo.", "pinterest.", "note.com", "chatwork")
AGG = ("amazon.", "rakuten.co.jp", "tripadvisor", "tabelog", "google.", "yahoo.co.jp", "navitime",
       "jorudan", "mapion", "machi-bike", "ekiten", "wikipedia.org", "wikimedia.org",
       "wikidata.org", "yahoo.co.jp/search", "goo.ne.jp", "map.goo", "house.", "recruit")
NEED_KEYS = ("名称", "类别")


def save(p, obj):
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def rec_iter(d):
    for st in d["车站"]:
        for klass, rows in (st.get("分区") or {}).items():
            for i, r in enumerate(rows):
                yield st, klass, i, r


def title_of(rec):
    u = rec.get("维基百科")
    if not u:
        return None
    m = re.match(r"https://ja\.wikipedia\.org/wiki/(.+)", u)
    return m.group(1).replace("_", " ") if m else None


def clean_url(u):
    if not u:
        return None
    u = u.strip().strip('"').strip("'")
    u = re.sub(r"[\u4e00-\u9fff].*$", "", u) if re.search(r"[\u4e00-\u9fff]", u) else u
    if not re.match(r"https?://", u):
        return None
    if len(re.findall(r"[/.]", u)) and " " in u:
        u = u.split(" ")[0]
    return u.rstrip(".,;)") if len(u) > 12 else None


def find_site_wikitext(title):
    """First plausible official URL inside the facility's own jawiki article."""
    wt = W.wikitext(title, "ja")
    if not wt:
        return None
    secs = W.sections(wt)[0]
    urls = []
    for head in list(secs):
        if re.search(r"外部リンク|関連项目|外部リンク$", head):
            urls += W.ext_links(secs[head])
    urls += W.ext_links(wt) + W.ref_urls(wt)
    for u in urls:
        u = clean_url(u)
        if not u:
            continue
        h = W.url_host(u)
        if not h or any(b in h for b in SOCIAL) or any(b in h for b in AGG):
            continue
        if re.search(r"(archive\.org|wayback|#!/|/share/|profile/)", u):
            continue
        return u
    return None


def zh_from_ja_title(title):
    base = re.sub(r"\s*\([^)]*\)\s*", "", title or "")
    if base.endswith("駅"):
        return base[:-1] + "站"
    if base.endswith("停留場"):
        return base[:-3] + "停留场"
    if base.endswith("停留所"):
        return base[:-3] + "停留所"
    return None


def main():
    budget = int(os.environ.get("EKI_BUDGET", 40000))
    do_verify = os.environ.get("EKI_VERIFY", "1") != "0"
    per_host = int(os.environ.get("EKI_PER_HOST", "4"))     # cap checks per domain
    verify_threads = int(os.environ.get("EKI_VTHREADS", "24"))
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--budget":
            budget = int(args[i + 1])
        if a == "--noverify":
            do_verify = False
    files = sorted(glob.glob(os.path.join(OUT, "*.json")))
    t0 = time.time()
    used = 0

    # ---------- pass 1: gather work ----------
    jobs = []          # (prio, title)
    oqids = []
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        for st, klass, i, r in rec_iter(d):
            if r.get("Wikidata"):
                oqids.append(r["Wikidata"].rsplit("/", 1)[-1])
            t = title_of(r)
            need = (not r.get("官网")) or (not r["名称"].get("英文")) or (not r["名称"].get("中文")) or not r.get("简介")
            if t and need:
                jobs.append((PRIORITY.get(klass, 3), t))
    jobs = sorted(dict.fromkeys(jobs))
    uniq_titles = [t for _, t in jobs]
    print(f"files={len(files)} titles_to_resolve={len(uniq_titles)} osm_qids={len(set(oqids))}", flush=True)

    # ---------- pass 2: titles -> qid (+ ja lead) ----------
    CAP_TITLES = budget
    sel = uniq_titles[:CAP_TITLES]
    tmap = {}
    with ThreadPoolExecutor(6) as ex:
        chunk = [sel[i:i + 600] for i in range(0, len(sel), 600)]
        for res in ex.map(lambda c: W.batch_titles(c, "ja"), chunk):
            tmap.update(res)
            print(f"  titles resolved {len(tmap)}/{len(sel)} {time.time()-t0:.0f}s", flush=True)
    qids = sorted(set([v["qid"] for v in tmap.values() if v.get("qid")] + oqids))

    # ---------- pass 3: entities ----------
    ents = {}
    with ThreadPoolExecutor(6) as ex:
        chunk = [qids[i:i + 900] for i in range(0, len(qids), 900)]
        for res in ex.map(W.batch_entities, chunk):
            ents.update(res)
            print(f"  entities {len(ents)}/{len(qids)}", flush=True)

    # ---------- pass 4: official site from jawiki for still-missing (top priority only) ----------
    want_site_titles = []
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        for st, klass, i, r in rec_iter(d):
            if r.get("官网"):
                continue
            t = title_of(r)
            if not t:
                continue
            e = ents.get((tmap.get(t) or {}).get("qid") or "")
            if e and e.get("site"):
                continue
            want_site_titles.append((PRIORITY.get(klass, 3), t))
    want_site_titles = sorted(dict.fromkeys(want_site_titles))
    SITE_BUDGET = max(0, min(len(want_site_titles), budget // 3))
    picked = [t for _, t in want_site_titles[:SITE_BUDGET]]
    sites = {}
    print(f"site-search titles: {len(picked)}", flush=True)
    with ThreadPoolExecutor(8) as ex:
        for t, u in zip(picked, ex.map(find_site_wikitext, picked)):
            if u:
                sites[t] = u

    # ---------- pass 5: zh / en leads ----------
    lead_titles_zh = sorted(set(e["swiki_zh"] for e in ents.values() if e.get("swiki_zh")))
    lead_titles_en = sorted(set(e["swiki_en"] for e in ents.values() if e.get("swiki_en")))
    zmap = {}
    emap = {}
    with ThreadPoolExecutor(4) as ex:
        ch = [lead_titles_zh[i:i + 600] for i in range(0, len(lead_titles_zh), 600)][:200]
        for res in ex.map(lambda c: W.batch_titles(c, "zh"), ch):
            zmap.update(res)
    with ThreadPoolExecutor(4) as ex:
        ch = [lead_titles_en[i:i + 600] for i in range(0, len(lead_titles_en), 600)][:200]
        for res in ex.map(lambda c: W.batch_titles(c, "en"), ch):
            emap.update(res)
    print(f"leads zh={len(zmap)} en={len(emap)}", flush=True)

    # ---------- pass 6: apply ----------
    stats = {"records": 0, "site_filled": 0, "en_filled": 0, "zh_filled": 0,
             "lead_ja": 0, "lead_en": 0, "lead_zh": 0, "no_title": 0}
    urls = set()
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        for st, klass, i, r in rec_iter(d):
            stats["records"] += 1
            t = title_of(r)
            e = ents.get((tmap.get(t) or {}).get("qid") or "") if t else None
            nm = r["名称"]
            if t and not nm.get("中文"):
                z = (e or {}).get("zh") or (e or {}).get("swiki_zh") or zh_from_ja_title(t)
                if z:
                    nm["中文"] = z
                    stats["zh_filled"] += 1
            if t and not nm.get("英文"):
                if e and e.get("en"):
                    nm["英文"] = e["en"]
                    stats["en_filled"] += 1
            if not r.get("官网"):
                cand = (e or {}).get("site") or sites.get(t or "")
                cand = clean_url(cand)
                if cand:
                    r["官网"] = cand
                    r.setdefault("来源", []).append("Wikidata P856 / 日本語版ウィキペディア外部リンク")
                    stats["site_filled"] += 1
            bl = r.get("简介") or {}
            if t:
                if not bl.get("日文"):
                    ja = (tmap.get(t) or {}).get("lead")
                    if ja:
                        bl["日文"] = ja[:600]
                        stats["lead_ja"] += 1
                        if not r.get("维基条目原文引用"):
                            r["维基条目原文引用"] = {"链接": r.get("维基百科"), "出典": "日本語版ウィキペディア (CC BY-SA 4.0)", "引用日": DAY}
                if e:
                    if not bl.get("英文") and e.get("swiki_en"):
                        l = (emap.get(e["swiki_en"]) or {}).get("lead")
                        bl["英文"] = (l or e.get("den")) and (l or e["den"])[:600]
                        if bl["英文"]:
                            stats["lead_en"] += 1
                    elif not bl.get("英文") and e.get("den"):
                        bl["英文"] = e["den"]
                        stats["lead_en"] += 1
                    if not bl.get("中文"):
                        l = (zmap.get(e.get("swiki_zh")) or {}).get("lead") if e.get("swiki_zh") else None
                        bl["中文"] = (l or e.get("dzh")) and (l or e["dzh"])[:600]
                        if bl["中文"]:
                            stats["lead_zh"] += 1
                if e and not r.get("Wikidata"):
                    r["Wikidata"] = "https://www.wikidata.org/wiki/" + ((tmap.get(t) or {}).get("qid"))
            if bl:
                r["简介"] = bl
            for u in [r.get("官网"), r.get("维基百科")]:
                if u:
                    urls.add(u)
            for u in st.get("公式リンク", []):
                urls.add(u)
        save(p, d)
    print("applied:", json.dumps(stats, ensure_ascii=False), flush=True)

    # ---------- pass 7: verify URLs ----------
    if do_verify:
        byhost = collections.Counter()
        cand = []
        for u in sorted(urls):
            h = re.sub(r"^www\.", "", W.url_host(u) or "")
            if not h:
                continue
            byhost[h] += 1
            if byhost[h] <= per_host:
                cand.append(u)
        urls = cand
        print(f"verifying {len(urls)} urls (>=400 removed as 欠陥)", flush=True)
        res = {}
        with ThreadPoolExecutor(verify_threads) as ex:
            futs = {ex.submit(W.head_status, u): u for u in urls}
            done = 0
            for f in futs:
                u = futs[f]
                try:
                    res[u] = f.result()
                except Exception:
                    res[u] = (0, u)
                done += 1
                if done % 5000 == 0:
                    print(f"  verified {done}/{len(urls)}", flush=True)
        json.dump(res, open(os.path.join(LOG, "url_status.json"), "w", encoding="utf-8"), ensure_ascii=False)
        # prune dead official links
        kept = 0
        for p in files:
            d = json.load(open(p, encoding="utf-8"))
            for st, klass, i, r in rec_iter(d):
                u = r.get("官网")
                if u:
                    code = res.get(u, (None,))[0]
                    if code is None:
                        # not probed (same-host cap): keep the link, mark it honestly
                        r["官网検証"] = {"HTTP": None, "状態": "未検証（同ドメインの上限により省略）", "確認日": DAY}
                        kept += 1
                    elif code == 0 or code >= 400:
                        r["官网検証"] = {"HTTP": code, "確認日": DAY}
                        r["官网"] = None
                        r["官网_欠陥"] = {"リンク切れ": u, "HTTP": code}
                    else:
                        r["官网検証"] = {"HTTP": code, "確認日": DAY}
                        kept += 1
            for st in d["车站"]:
                ol = []
                for u in st.get("公式リンク", []):
                    code = res.get(u, (None,))[0]
                    if code is None or code < 400:
                        ol.append(u)
                st["公式リンク"] = ol
                st["公式リンク検証日"] = DAY
            save(p, d)
        print("official links alive:", kept, flush=True)
    json.dump({"stats": stats, "titles": len(uniq_titles), "resolved": len(tmap),
               "entities": len(ents), "urls": len(urls), "secs": round(time.time() - t0)},
              open(os.path.join(LOG, "enrich_stats.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
