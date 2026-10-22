"""線ファイル内の空間クラスタリング監査（書き込みなし）。

ekiw-wiki の「線路」ファイルは駅名を五十音順に並べたもので、同一ファイル内に
同名の別路線駅が混ざると、中央値＋95パーセンタイル方式の線内テストは
基準自体が膨らんで失明する（鹿児島市電唐湊線に福島の中郡駅・長崎圏の神田駅が
入り、対角3,258km になっていた）。

そこで「1本の路線＝地理的に連結した塊」という前提に戻り、
単連結クラスタ（しきい 30km）で最大の塊をその路線のコアとし、
コアに入らない駅を疑い候補として数え上げる。
"""
import collections
import glob
import json
import math
import os

SRC = "/data/eki-play/data/lines"
OUT = "/data/work/stats/cluster_audit.json"
LINK_KM = 30.0


def hav(a, b, c, d):
    r = math.radians
    h = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2) ** 1
    h = min(1.0, max(0.0, h))
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def clusters(items, thr):
    """items: [(idx, lat, lon)] -> list of cluster index-lists (single linkage)."""
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if hav(items[i][1], items[i][2], items[j][1], items[j][2]) <= thr:
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


rows = []
outside_total = 0
per_file = {}
for path in sorted(glob.glob(os.path.join(SRC, "*.json"))):
    d = json.load(open(path, encoding="utf-8"))
    items = []
    for i, s in enumerate(d["车站"]):
        c = (s.get("位置") or {}).get("坐标")
        if c:
            items.append((i, c["纬度"], c["经度"]))
    if len(items) < 5:
        continue
    gs = clusters(items, LINK_KM)
    core = max(gs, key=len)
    core_pts = [items[i] for i in core]
    strays = [g for g in gs if g is not core]
    stray_idx = [i for g in strays for i in g]
    n_out = len(stray_idx)
    outside_total += n_out
    recs = []
    for i in stray_idx:
        idx, la, lo = items[i]
        dist = min(hav(la, lo, y[1], y[2]) for y in core_pts)
        st = d["车站"][idx]
        recs.append({"駅": (st.get("名称") or {}).get("日文"), "座標": [la, lo],
                     "コア最近距離km": round(dist, 1),
                     "品質": (st.get("データ品質") or {}).get("座標")})
    if n_out:
        per_file[os.path.basename(path)] = {"駅": len(d["车站"]), "座標": len(items),
                                            "コア駅数": len(core), "群れ数": len(gs),
                                            "コア外": n_out, "明細": recs}
    rows.extend([{"ファイル": os.path.basename(path), **r} for r in recs])

rows.sort(key=lambda r: -r["コア最近距離km"])
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump({"しきいkm": LINK_KM, "コア外駅計": outside_total,
           "影響ファイル数": len(per_file),
           "ファイル上位": sorted(((k, v["コア外"], v["コア駅数"], v["座標"]) for k, v in per_file.items()),
                                key=lambda x: -x[1])[:40],
           "駅明細": rows},
          open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("core-outside stations:", outside_total, "| files:", len(per_file))
for f, n, core, tot in sorted(((k, v["コア外"], v["コア駅数"], v["座標"]) for k, v in per_file.items()),
                              key=lambda x: -x[1])[:20]:
    print(f"  {f[:44]:46} コア外 {n:4} / {tot:4}（コア {core}）")
print("--- worst 10 by distance ---")
for r in rows[:10]:
    print(f"  {r['駅']:14} {r['コア最近距離km']:8}km  {r['ファイル'][:34]}  {r['品質']}")
