#!/usr/bin/env python3
"""Triage the 86 audit mismatches with a geography test that actually holds.

coord_fix_mismatch.py assumed the article named by 词条 describes the same station.
It does not always: eki-wiki resolves some tram stops to a same-named JR station
(中郡駅 → JR米坂線 in 山形県 although 日文原名 says 中郡(鹿児島県)), so the "wiki
coordinate" can be the *wrong* one and the stored OSM point the right one.

The reliable invariant is the line itself: all stations of one line sit in one region.
So for every file we take the median centre of its placed stations and the 95th
percentile of their spread, then judge each contested station by which candidate
coordinate lies inside that region.

  old inside, old-only   -> revert to OSM point (article was a homonym)
  new inside, old-only   -> keep the fix (OSM had matched another prefecture)
  both inside            -> revert, record as 要人手確認
  neither inside         -> revert, record as 要人手確認
"""
import datetime
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import OsmDb, hav
from coord_fix_mismatch import strip_osm_pool
from coord_pool_fix import add_pois, log, save

DAY = datetime.date.today().isoformat()
LINES = "/data/eki-play/data/lines"


def line_profile(d, skip_idx):
    xs = [c for j, st in enumerate(d["车站"])
          if j != skip_idx and (st.get("位置") or {}).get("坐标")
          for c in [(st["位置"]["坐标"]["纬度"], st["位置"]["坐标"]["经度"])]]
    if len(xs) < 6:
        return None
    la = statistics.median([p[0] for p in xs])
    lo = statistics.median([p[1] for p in xs])
    ds = sorted(hav(la, lo, *p) for p in xs)
    spread = ds[int(0.95 * (len(ds) - 1))]
    return (la, lo, max(spread * 1.4, 25000))


def main():
    rep = json.load(open("/data/work/stats/coord_audit.json", encoding="utf-8"))
    worst = rep["worst"]
    by_file = {}
    for x in worst:
        by_file.setdefault(os.path.join(LINES, x["file"]), []).append(x)
    log(f"triaging {len(worst)} contested stations in {len(by_file)} files")
    osmdb = OsmDb()
    tally = {"revert": 0, "keep": 0, "review": 0}

    for p, items in sorted(by_file.items()):
        d = json.load(open(p, encoding="utf-8"))
        cache = {}
        for x in items:
            st = d["车站"][x["idx"]]
            pos = st.get("位置") or {}
            m = pos.get("坐标修正")
            if not m:
                continue
            old = m.get("旧坐标") or {}
            prof = line_profile(d, x["idx"])
            if not prof or not old.get("纬度"):
                verdict, why = "revert", "線内分布を取れないため旧座標を維持"
            else:
                cla, clo, lim = prof
                do = hav(cla, clo, old["纬度"], old["经度"])
                dn = hav(cla, clo, pos["坐标"]["纬度"], pos["坐标"]["经度"])
                old_in, new_in = do <= lim, dn <= lim
                if old_in and not new_in:
                    verdict, why = "revert", f"旧座標のみ線内({do}m≤{lim:.0f}m)、記事座標は{dn}m=別県の同名駅"
                elif new_in and not old_in:
                    verdict, why = "keep", f"新座標のみ線内({dn}m≤{lim:.0f}m)、旧OSMは{do}m=別県の同名駅"
                elif old_in and new_in:
                    verdict, why = "revert", "両座標とも線内（旧OSMノードを正として維持）"
                    tally["review"] += 1
                else:
                    verdict, why = "revert", f"両座標とも線外({do}m/{dn}m) — 旧座標を維持して人手確認"
                    tally["review"] += 1
            tally[verdict] += 1
            m["判定"] = {"結果": verdict, "根拠": why, "線内中心": [cla, clo] if prof else None,
                        "許容半径m": round(prof[2]) if prof else None, "日付": DAY}
            if verdict == "revert":
                la, lo = old["纬度"], old["经度"]
                strip_osm_pool(st)
                got = add_pois(st, la, lo, osmdb,
                               f"OpenStreetMap (Japan extract, ODbL)（復元座標・{old.get('出处')}）")
                st["データ品質"]["OSM施設数"] = got
                pos["坐标"] = old
            else:
                got = st["データ品質"].get("OSM施設数")
            m["旧POOL除去件数"] = m.get("旧POOL除去件数")
            m["現POOL件数"] = got
        save(p, d)
    log("verdicts: " + json.dumps(tally, ensure_ascii=False))
    osmdb.close()


if __name__ == "__main__":
    main()
