#!/usr/bin/env python3
"""Build the trilingual area guide for one line. Usage: build_line.py <线路中文名>"""
import json, os, sys, time, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import OsmDb, build_station, CLASS_ORDER, META_LABEL, CAT_LABEL, CAT_LABEL2
import wikimedia as W

WIKI = "/data/eki-wiki/data"
OUT = os.environ.get("EKI_OUT", "/data/eki-play/data/lines")
STATS = "/data/work/stats"
os.makedirs(OUT, exist_ok=True); os.makedirs(STATS, exist_ok=True)

NEIGHBOUR_KEYS = ("隣の駅", "隣の駅・停留場", "相邻车站", "隣の停留場", "隣接駅")


def load_line(line):
    p = os.path.join(WIKI, line + ".json")
    if not os.path.exists(p):
        cands = [f for f in os.listdir(WIKI) if f[:-5] == line]
        if not cands:
            return None
        p = cands[0]
    return json.load(open(p, encoding="utf-8"))


def main(line):
    t0 = time.time()
    d = load_line(line)
    if not d:
        print("!! no such line:", line); return 2
    line_ja = (d.get("线路", {}).get("名称") or {}).get("日文") or line
    osmdb = OsmDb()
    stations = []
    n_nocoord = n_poi = n_wiki = 0
    # pre-seed coordinates from the OSM station table; then resolve ambiguous ones by
    # walking the line in order (a station is usually near its already-placed neighbours)
    raw = d.get("车站", [])
    hints_all = {}
    for st in raw:
        nm = st.get("名称") or {}
        si = st.get("结构化信息") or {}
        hints_all[nm.get("词条") or nm.get("日文原名")] = (si.get("所属公司") or []) + (si.get("分类") or [])[:3]
    seed = None
    order = raw
    for st in order:
        nm = st.get("名称") or {}
        entry = nm.get("词条") or nm.get("日文原名")
        rec, _ = build_station({**st, "词条": entry, "日文原名": nm.get("日文原名"),
                                "英文": nm.get("英文"), "中文": nm.get("中文"),
                                "读音": (st.get("结构化信息") or {}).get("读音"),
                                "罗马字": (st.get("结构化信息") or {}).get("罗马字")},
                               line_ja, osmdb, seed=seed, hints=hints_all.get(entry))
        if rec and rec["位置"]["坐标"]:
            c = rec["位置"]["坐标"]
            seed = (c["纬度"], c["经度"])
        if not rec:
            continue
        stations.append(rec)
        if not rec["位置"]["坐标可用"]:
            n_nocoord += 1
        n_poi += rec["データ品質"]["OSM施設数"]
        n_wiki += rec["データ品質"]["ウィキ_only数"]
    out = {
        "线路": {"中文": line, "日文": line_ja,
                 "英文": (d.get("线路", {}).get("名称") or {}).get("英文"),
                 "链接": "https://ja.wikipedia.org/wiki/" + re.sub(r"\s+", "_", line_ja)},
        "车站数": len(stations),
        "生成": {"脚本": "build_line.py", "日期": time.strftime("%Y-%m-%d"),
                 "半径m": 900,
                 "来源": ["日本語版ウィキペディア (CC BY-SA 4.0)", "OpenStreetMap Japan extract (ODbL)",
                          "Wikidata (CC0)"]},
        "品質": {"無座標": n_nocoord, "OSM施設合計": n_poi, "ウィキのみ合計": n_wiki},
        "分区定義": [{"日文": c, "英文": META_LABEL[c]["en"], "中文": META_LABEL[c]["zh"]} for c in CLASS_ORDER],
        "车站": stations,
    }
    fn = os.path.join(OUT, line + ".json")
    json.dump(out, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"line": line, "stations": len(stations), "no_coord": n_nocoord,
               "osm_pois": n_poi, "wiki_only": n_wiki, "secs": round(time.time() - t0, 1),
               "bytes": os.path.getsize(fn)}, open(os.path.join(STATS, line + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{line}: 駅{len(stations)} 無座標{n_nocoord} OSM{n_poi} wiki{n_wiki} {time.time()-t0:.1f}s {os.path.getsize(fn)//1024}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
