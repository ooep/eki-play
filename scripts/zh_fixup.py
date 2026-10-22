#!/usr/bin/env python3
"""Backfill Chinese (and English) names / blurbs that the first enrichment pass missed.

Cause: MediaWiki title lookups need converttitles=1 for zhwiki (ja 漢字 title -> zh
simplified title). With that fixed, we can resolve
  facility/station -> Wikidata entity -> zhwiki/enwiki sitelink -> 首段 extract
and store it as 简介.中文 / 车站条目.首段.中文. Nothing is machine-translated: if no
zh/en article exists, the field stays null (Wikidata zh description is used as a
short fallback for facilities, marked with its source).
"""
import json, os, sys, glob, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikimedia as W

OUT = "/data/eki-play/data/lines"
DAY = time.strftime("%Y-%m-%d")
t0 = time.time()


def log(*a):
    print(f"[{time.time()-t0:6.0f}s]", *a, flush=True)


def save(p, obj):
    tmp = f"{p}.zh.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def q_of(url):
    return (url or "").rsplit("/", 1)[-1] if url and "wikidata" in url else None


def main():
    files = sorted(glob.glob(os.path.join(OUT, "*.json")))
    st_titles, rec_qids = [], set()
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        for st in d["车站"]:
            w = st.get("车站条目") or {}
            if w.get("链接") and not (w.get("首段") or {}).get("中文"):
                t = w["链接"].rsplit("/", 1)[-1].replace("_", " ")
                st_titles.append(t)
            for rows in (st.get("分区") or {}).values():
                for r in rows:
                    if not (r.get("简介") or {}).get("中文") and r.get("Wikidata"):
                        rec_qids.add(q_of(r["Wikidata"]))
                    if not (r.get("简介") or {}).get("英文") and r.get("Wikidata"):
                        rec_qids.add(q_of(r["Wikidata"]))
    st_titles = list(dict.fromkeys(st_titles))
    log(f"stations to resolve={len(st_titles):,} record qids={len(rec_qids):,}")

    stmap = {}
    with ThreadPoolExecutor(6) as ex:
        ch = [st_titles[i:i + 600] for i in range(0, len(st_titles), 600)]
        for res in ex.map(lambda c: W.batch_titles(c, "ja", tag="t2q"), ch):
            stmap.update(res)
    log(f"ja station pages={len(stmap):,}")

    qids = sorted(set(rec_qids | {v["qid"] for v in stmap.values() if v.get("qid")}))
    ents = {}
    with ThreadPoolExecutor(6) as ex:
        ch = [qids[i:i + 900] for i in range(0, len(qids), 900)]
        for res in ex.map(W.batch_entities, ch):
            ents.update(res)
    log(f"entities={len(ents):,}")

    zh_titles = sorted({e["swiki_zh"] for e in ents.values() if e.get("swiki_zh")})
    en_titles = sorted({e["swiki_en"] for e in ents.values() if e.get("swiki_en")})
    log(f"zh sitelinks={len(zh_titles):,} en sitelinks={len(en_titles):,}")
    zmap, emap = {}, {}
    with ThreadPoolExecutor(6) as ex:
        ch = [zh_titles[i:i + 600] for i in range(0, len(zh_titles), 600)]
        for res in ex.map(lambda c: W.batch_titles(c, "zh", tag="t2qz"), ch):
            zmap.update(res)
    log(f"zh leads={len(zmap):,} {time.time()-t0:.0f}s")
    with ThreadPoolExecutor(6) as ex:
        ch = [en_titles[i:i + 600] for i in range(0, len(en_titles), 600)]
        for res in ex.map(lambda c: W.batch_titles(c, "en", tag="t2qe"), ch):
            emap.update(res)
    log(f"en leads={len(emap):,}")

    n = {"zh_rec": 0, "en_rec": 0, "zh_st": 0, "en_st": 0, "zh_name": 0, "files": 0}
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        for st in d["车站"]:
            w = st.get("车站条目") or {}
            t = (w.get("链接") or "").rsplit("/", 1)[-1].replace("_", " ")
            q = (stmap.get(t) or {}).get("qid")
            e = ents.get(q or "") or {}
            if not (w.get("首段") or {}).get("中文"):
                l = (zmap.get(e.get("swiki_zh") or "") or {}).get("lead")
                if l:
                    w.setdefault("首段", {})["中文"] = l[:900]
                    w["中文链接"] = "https://zh.wikipedia.org/wiki/" + e["swiki_zh"].replace(" ", "_")
                    w.setdefault("首段出典", {})["中文"] = {"リンク": w["中文链接"],
                                                  "ライセンス": "CC BY-SA 4.0", "取得日": DAY}
                    n["zh_st"] += 1
            if not (w.get("首段") or {}).get("英文"):
                l = (emap.get(e.get("swiki_en") or "") or {}).get("lead")
                if l:
                    w.setdefault("首段", {})["英文"] = l[:900]
                    w["英文链接"] = "https://en.wikipedia.org/wiki/" + e["swiki_en"].replace(" ", "_")
                    w.setdefault("首段出典", {})["英文"] = {"リンク": w["英文链接"],
                                                  "ライセンス": "CC BY-SA 4.0", "取得日": DAY}
                    n["en_st"] += 1
            nm = st.get("名称") or {}
            if not nm.get("中文") and e.get("zh"):
                nm["中文"] = e["zh"]
                n["zh_name"] += 1
            for rows in (st.get("分区") or {}).values():
                for r in rows:
                    q2 = q_of(r.get("Wikidata")) or ((stmap.get((r.get("维基百科") or "")
                                  .rsplit("/", 1)[-1].replace("_", " ")) or {}).get("qid"))
                    ee = ents.get(q2 or "") or {}
                    bl = r.get("简介") or {}
                    if not bl.get("中文"):
                        l = (zmap.get(ee.get("swiki_zh") or "") or {}).get("lead")
                        v = l or ee.get("dzh")
                        if v:
                            bl["中文"] = v[:600]
                            r["简介"] = bl
                            if l:
                                r.setdefault("维基条目原文引用", {})["中文"] = {
                                    "链接": "https://zh.wikipedia.org/wiki/" +
                                            ee["swiki_zh"].replace(" ", "_"),
                                    "出典": "中文维基百科 (CC BY-SA 4.0)", "取得日": DAY}
                            else:
                                r.setdefault("维基条目原文引用", {})["中文"] = {
                                    "链接": r.get("Wikidata"),
                                    "出典": "Wikidata 中文说明 (CC0)", "取得日": DAY}
                            n["zh_rec"] += 1
                    if not bl.get("英文"):
                        l = (emap.get(ee.get("swiki_en") or "") or {}).get("lead")
                        v = l or ee.get("den")
                        if v:
                            bl["英文"] = v[:600]
                            r["简介"] = bl
                            n["en_rec"] += 1
                    if not (r.get("名称") or {}).get("中文") and ee.get("zh"):
                        r["名称"]["中文"] = ee["zh"]
                        n["zh_name"] += 1
        n["files"] += 1
        save(p, d)
        if n["files"] % 100 == 0:
            log(f"applied {n['files']}/{len(files)} {json.dumps(n, ensure_ascii=False)}")
    log(f"DONE {json.dumps(n, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
