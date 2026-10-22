"""線ファイルの収録駅がはたして同じ路線の上にあるかを、座標分布だけで検査する。

eki-wiki 側の「線路」ラベルは一部ファイルで収録範囲が壊れており（例: 山手线.json に
上諏訪・鬼怒川温泉・松本が混在）、線内の地理連続性テストの前提そのものを崩す。
このスクリプトは各ファイルの座標から
  - 統計的分散（中央値からの距離の95パーセンタイル）
  - 外接矩形の対角長
を求め、分散が異常に大きいファイルを「線路ラベル疑い」としてlistingする。
座標そのものは各駅のOSM/Wikipedia照合で確定済みなので、周辺施設プールは正しい。
壊れているのは『所属線路』という見出しだけ、という整理になる。
"""
import glob
import json
import math
import os
import statistics

SRC = "/data/eki-play/data/lines"
OUT = "/data/eki-play/docs/qa/line_membership.json"


def hav(a, b, c, d):
    r = math.radians
    h = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2)
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def spread(pts, lat, lon):
    return statistics.quantiles([hav(lat, lon, p[0], p[1]) for p in pts], n=100)[94]


rows = []
weird = []
for path in sorted(glob.glob(os.path.join(SRC, "*.json"))):
    d = json.load(open(path, encoding="utf-8"))
    pts = []
    for s in d["车站"]:
        c = (s.get("位置") or {}).get("坐标")
        if c:
            pts.append((c["纬度"], c["经度"]))
            if not (24 <= c["纬度"] <= 46 and 122 <= c["经度"] <= 146):
                weird.append({"ファイル": os.path.basename(path),
                              "駅": (s.get("名称") or {}).get("日文"),
                              "座標": [c["纬度"], c["经度"]]})
    if len(pts) < 5:
        continue
    mlat = statistics.median(p[0] for p in pts)
    mlon = statistics.median(p[1] for p in pts)
    sp = spread(pts, mlat, mlon)
    diag = hav(min(p[0] for p in pts), min(p[1] for p in pts),
               max(p[0] for p in pts), max(p[1] for p in pts))
    rows.append({"ファイル": os.path.basename(path), "駅": len(d["车站"]), "座標": len(pts),
                 "分散km": round(sp, 1), "対角km": round(diag, 1)})

rows.sort(key=lambda r: -r["分散km"])
suspect = [r for r in rows if r["分散km"] > 40]
out = {
    "生成日": "2026-10-22",
    "検査対象ファイル": len(rows),
    "手順": "各ファイルの座標中央値からの距離の95パーセンタイル（分散km）と外接対角（対角km）を計算。"
            "実路線で 40km を大きく超えるものは「1本の路線」として地理的に連続していない＝上流の線路ラベルが混在。",
    "線内連続性40km超": suspect,
    "分布（分散km）": {
        "10km以下": sum(1 for r in rows if r["分散km"] <= 10),
        "10〜20km": sum(1 for r in rows if 10 < r["分散km"] <= 20),
        "20〜40km": sum(1 for r in rows if 20 < r["分散km"] <= 40),
        "40km超": len(suspect),
    },
    "日本域外の座標": weird,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("files checked:", len(rows), "| 40km+ :", len(suspect), "| 日本域外:", len(weird))
for r in suspect[:15]:
    print(" ", r["ファイル"], r["駅"], "分散", r["分散km"], "対角", r["対角km"])
