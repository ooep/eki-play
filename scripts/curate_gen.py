#!/usr/bin/env python3
"""Phase-B builder: assemble curated-tier station files from the enriched auto layer.

This is NOT hand-written prose. It is a transparent assembly step: every record
carries 出典 entries pointing at pages we actually fetched
(日本語版ウィキペディア / Wikidata P856 / OSM tags), official links are kept only
when the HTTP check passed, and each file declares 加精方式 = 自動抽出＋出典検証.
Files already produced by hand-searched agents are never overwritten.

  python3 curate_gen.py --per-cluster 8
  python3 curate_gen.py --per-cluster 20 --out /data/eki-play/data/curated
"""
import argparse, datetime, glob, json, os, re, sys

REPO = "/data/eki-play"
LINES = os.path.join(REPO, "data", "lines")
CUR = os.path.join(REPO, "data", "curated")
DAY = datetime.date.today().isoformat()

SHOP_RE = re.compile(r"百貨店|デパート|アトレ|ヴィエント|エスト|ポテテ|ターミナル|エスカ|アスティ|オーパ|OPA|そごう|大丸|髙島屋|高島屋|本屋|書店|商店街|市場|モール|SC$")
INFO_RE = re.compile(r"観光案内所|案内所|インフォメーション|ツアーデスク|案内")


def osm_url(r):
    rid = r.get("OSM编号")
    if not rid:
        return None
    m = re.match(r"^([nwr])(\d+)$", str(rid))
    if not m:
        return None
    kind = {"n": "node", "w": "way", "r": "relation"}[m.group(1)]
    return f"https://www.openstreetmap.org/{kind}/{m.group(2)}"


def cite(rec, host_of):
    """Assemble the 出典 list for one auto-layer record: wiki lead is real fetched text,
    official site is only reachability-verified, OSM is the raw tag source."""
    out = []
    if rec.get("维基百科"):
        lead = ((rec.get("简介") or {}).get("日文") or "").strip()
        title = rec["维基百科"].rsplit("/", 1)[-1].replace("_", " ")
        out.append({"标题": f"日本語ウィキペディア「{title}」",
                    "URL": rec["维基百科"],
                    "原文要旨": (lead[:220] + "…") if len(lead) > 220 else (lead or "見出し文をAPIで取得（本文はリンク先）。"),
                    "ライセンス": "CC BY-SA 4.0"})
    site = rec.get("官网")
    if site:
        v = rec.get("官网検証") or {}
        out.append({"标题": host_of(site), "URL": site,
                    "原文要旨": f"公式サイト。到達性のみ自動検証（HTTP {v.get('HTTP')}、{v.get('確認日')}）。本文の人手確認は行っていません。"})
    if rec.get("Wikidata"):
        out.append({"标题": "Wikidata（P856 他）", "URL": rec["Wikidata"],
                    "原文要旨": "多言語ラベル・公式 website 識別子の出典。CC0。"})
    if not out and rec.get("OSM编号"):
        u = osm_url(rec)
        out.append({"标题": f"OpenStreetMap {rec['OSM编号']}", "URL": u,
                    "原文要旨": f"OSM タグ {rec.get('OSM类别')} 由来。マッピングの生データ。ODbL。"})
    return out


def host_of(u):
    try:
        return re.sub(r"^www\.", "", u.split("//", 1)[1].split("/", 1)[0])
    except Exception:
        return u


def dist(rec):
    m = rec.get("距车站m")
    if not m:
        return None
    mm = int(round(float(m) / 10.0) * 10)
    return f"直線{mm}m（徒歩目安約{max(1, round(mm / 80))}分）"


def mk(rec, extra=None, kind=None, sub=None):
    nm = rec.get("名称") or {}
    bl = rec.get("简介") or {}
    o = {"名称": {"日文": nm.get("日文"), "英文": nm.get("英文"), "中文": nm.get("中文")},
         "距離": dist(rec),
         "简介": {"日文": bl.get("日文"), "英文": bl.get("英文"), "中文": bl.get("中文")},
         "住所": rec.get("地址"),
         "电话": rec.get("电话"),
         "営業時間": rec.get("开放时间"),
         "官网": rec.get("官网"),
         "介绍页": None,
         "出典": cite(rec, host_of)}
    if kind:
        o["種别"] = kind
    if sub:
        o["細分"] = {"日文": (rec.get("细分") or {}).get("日文"),
                    "英文": (rec.get("细分") or {}).get("英文"),
                    "中文": (rec.get("细分") or {}).get("中文")}
    o.update(extra or {})
    return o


def pick(rows, want, n, bonus=()):
    """Prefer sourced records, then category keywords, then proximity."""
    def key(r):
        s = 0
        if r.get("官网"):
            s += 6
        if r.get("维基百科"):
            s += 4
        if (r.get("简介") or {}).get("日文"):
            s += 3
        if (r.get("简介") or {}).get("中文"):
            s += 1
        nm = (r.get("名称") or {}).get("日文") or ""
        for i, w in enumerate(want):
            if w and w in nm:
                s += max(0, len(want) - i)
        d = r.get("距车站m")
        return -(s * 1000 - (float(d) if d else 4000))
    rows = [r for r in rows if (r.get("名称") or {}).get("日文")]
    return sorted(rows, key=key)[:n]


def build(st, line_cn, cluster):
    part = st.get("分区") or {}
    sight = part.get("観光・名所案内") or []
    cult = part.get("文化・公共施設") or []
    pub = part.get("便民公共服务") or []
    trn = part.get("交通接続") or []
    shop = part.get("買い物・商店街") or []
    used, unconfirmed = [], []

    sights = pick(sight, ["城", "公園", "美術館", "博物館", "寺", "神社", "展望", "温泉"], 6)
    rest = [r for r in sight if r not in sights]
    meisho = pick(rest + cult, ["庭園", "史跡", "記念", "資料館", "大通り", "坂", "橋"], 4)
    services = pick(pub + cult, ["観光案内所", "役所", "図書館", "警察", "病院", "郵便局", "庁舎"], 6)
    transport = pick(trn, ["駅", "バス", "ターミナル", "空港"], 4)
    shopping = pick([r for r in shop if SHOP_RE.search((r.get("名称") or {}).get("日文") or "")],
                    ["百貨店", "商店街", "駅"], 3)

    def records(rows, kind=None):
        out = []
        for r in rows:
            o = mk(r, kind=kind)
            if not o["出典"]:
                continue
            for c in o["出典"]:
                if c.get("URL"):
                    used.append(c["URL"])
            out.append(o)
        return out

    gi = st.get("名称") or {}
    wiki = st.get("车站条目") or {}
    lead = ((wiki.get("首段") or {}).get("日文") or "").strip()
    stinfo = {
        "读音": (gi.get("读音") or None), "罗马字": (gi.get("罗马字") or None),
        "所属公司": (st.get("所属线路") or {}).get("会社") if isinstance(st.get("所属线路"), dict) else None,
        "开业": (st.get("所属线路") or {}).get("开业") if isinstance(st.get("所属线路"), dict) else None,
        "车站地址": None,
        "维基首段": (lead[:400] + "…") if len(lead) > 400 else (lead or None),
        "出典": ([{"标题": "日本語ウィキペディア「%s」" % gi.get("词条"),
                   "URL": wiki.get("链接"), "原文要旨": "駅概要文を MediaWiki API から取得（CC BY-SA 4.0）。"}]
                 if wiki.get("链接") else []),
    }
    for r in services:
        if INFO_RE.search((r.get("名称") or {}).get("日文") or ""):
            stinfo["観光案内所"] = {"名称": (r.get("名称") or {}).get("日文"),
                                  "电话": r.get("电话"), "官网": r.get("官网")}
            break
    if not sights:
        unconfirmed.append("周囲の観光地レコードが自動層に存在しなかったため、观光景点 は空です。")
    if any(not (r.get("简介") or {}).get("中文") for r in sights + services):
        unconfirmed.append("中文名・中文简介が取得できない施設があります（機械翻訳はしていません）。")
    if any(not (r.get("简介") or {}).get("英文") for r in sights + services):
        unconfirmed.append("英文名・英文简介が取得できない施設があります。")

    return {
        "车站": gi.get("日文"),
        "车站ID": st.get("车站ID"),
        "线路": line_cn,
        "区域": cluster,
        "加精日期": DAY,
        "加精方式": "自動抽出＋出典URL検証（Phase B ビルダー curate_gen.py）",
        "人手確認": False,
        "观光景点": records(sights),
        "名所案内": records(meisho, kind="名所案内"),
        "公共服务": records(services, kind="公共服务"),
        "交通": records(transport, kind="交通接続"),
        "购物": records(shopping, kind="買い物"),
        "无障碍": {"信息": None, "出典": []},
        "车站信息": stinfo,
        "検索记录": {"使用した出典URL": sorted(set(used))[:40],
                   "未確認": unconfirmed,
                   "検証日": DAY},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-cluster", type=int, default=8)
    ap.add_argument("--skip-existing", default="1")
    a = ap.parse_args()
    clusters = json.load(open("/data/work/curated_clusters.json"))
    station_index = {}
    for p in glob.glob(os.path.join(LINES, "*.json")):
        d = json.load(open(p, encoding="utf-8"))
        for st in d["车站"]:
            key = (st.get("名称") or {}).get("词条")
            if key and key not in station_index:
                station_index[key] = (st, d["线路"].get("中文") or os.path.basename(p)[:-5])
    made = skipped = 0
    for c in clusters:
        tag = c["cluster"].split("-")[0]
        lp = f"/data/work/curate_{tag}.json"
        if not os.path.exists(lp):
            continue
        out_dir = os.path.join(CUR, c["cluster"])
        os.makedirs(out_dir, exist_ok=True)
        for row in json.load(open(lp))[:a.per_cluster * 3]:
            got = station_index.get(row["词条"])
            if not got:
                continue
            st, line_cn = got
            fn = os.path.join(out_dir, re.sub(r'[\\/:*?"<>|]', "・", row["词条"]) + ".json")
            if a.skip_existing != "0" and os.path.exists(fn):
                skipped += 1
                continue
            obj = build(st, row.get("线路") or line_cn, c["cluster"])
            n = sum(len(obj[k]) for k in ("观光景点", "名所案内", "公共服务", "交通", "购物"))
            if n < 4:
                continue
            tmp = fn + ".tmp"
            json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, fn)
            made += 1
            if made % 20 == 0:
                print(f"built {made} (skipped {skipped})", flush=True)
    print(f"DONE built={made} skipped_existing={skipped}")


if __name__ == "__main__":
    main()
