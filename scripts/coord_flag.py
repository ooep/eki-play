#!/usr/bin/env python3
"""Flag (never move) the residual line-region outliers.

After the re-anchoring passes, 50 of 16,951 placed stations still sit further from their
line's median centre than max(98th percentile × 1.5, 40 km). Some of those are genuine
wrong-prefecture homonym matches whose correct node is not in our OSM station table;
many are legitimately distant stations on very long lines (鹿児島本線 小倉〜鹿児島,
予讃線). Coordinates are left untouched — each affected station gets データ品質.座標 =
"線外疑い" plus the measured distance so a consumer can filter it, and marks are
cleared again for stations that are now inside the region.
"""
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import hav
from coord_outlier import profile
from coord_pool_fix import log, save

LINES = "/data/eki-play/data/lines"
DAY = datetime.date.today().isoformat()


def main():
    flagged = cleared = 0
    report = []
    for p in sorted(glob.glob(os.path.join(LINES, "*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        pts = [((s.get("位置") or {}).get("坐标") or {}).get("纬度") and
               ((s["位置"]["坐标"]["纬度"]), (s["位置"]["坐标"]["经度"])) or None
               for s in d["车站"]]
        touched = False
        for i, st in enumerate(d["车站"]):
            if not pts[i]:
                continue
            prof = profile(pts, i)
            q = st.setdefault("データ品質", {})
            if not prof:
                continue
            dist = hav(prof[0], prof[1], *pts[i])
            if dist > prof[2]:
                if q.get("座標") != "線外疑い":
                    q["座標"] = "線外疑い"
                    touched = True
                    flagged += 1
                q["座標注記"] = {
                    "内容": f"同一线路の他駅分布（中央値＋98パーセンタイル×1.5、下限40km）から "
                            f"{round(dist / 1000)} km 外に位置。同名の別県駅と一致した可能性と、"
                            "長大線の末端駅の可能性の両方があるため座標は変更せず人手確認扱い",
                    "線内中心": [round(prof[0], 5), round(prof[1], 5)],
                    "許容半径m": round(prof[2]), "実測距離m": dist, "日付": DAY}
                report.append({"线路": (d.get("线路") or {}).get("日文"),
                               "駅": st["名称"]["日文"], "距離km": round(dist / 1000, 1),
                               "許容km": round(prof[2] / 1000), "文件": os.path.basename(p)})
            elif q.get("座標") == "線外疑い":
                q["座標"] = "線内照合済み"
                q.pop("座標注記", None)
                touched = True
                cleared += 1
        if touched:
            save(p, d)
    log(f"flagged={flagged} cleared={cleared}")
    json.dump({"generated": DAY, "count": len(report), "items": report},
              open("/data/work/stats/line_outliers.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log(f"report -> stats/line_outliers.json ({len(report)} items)")


if __name__ == "__main__":
    main()
