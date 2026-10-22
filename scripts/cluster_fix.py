#!/usr/bin/env python3
"""単連結クラスタで各路線ファイルの「コア」を確定し、コア外駅を再配置または警告する。

背景（前回の検証の失敗）: coord_outlier.py は「中央値＋98パーセンタイル×1.5」で線内半径を
決めたが、同名の別路線駅が混ざったファイルでは中心も分散基準自体が膨らみ、混入駅が
自己正当化して失明した（鹿児島市電唐湊線 6 駅中 2 駅が福島・長崎圏の同名駅だった例）。

本スクリプトは基準を「同じ路線の駅は地理的に連結している」に置き換える:
  1. 各ファイル内で 30km 単連結クラスタを形成
  2. 最大の塊＝その路線のコア
  3. コアに入らない駅のみを疑い対象にする（長期路線は連鎖で1塊になるため誤検知しない）
  4. 疑い駅は、線内に存在する同名 OSM ノードへ差替えるか、差替え先がなければ
     座標を動かさず データ品質.座標="線外疑い" ＋クラスタ根拠を記録

Usage:
  cluster_fix.py            -> 監査のみ（stats/cluster_fix.json）
  cluster_fix.py --apply    -> 差替え・警告を data/lines に書き込み
"""
import collections
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_core import OsmDb, hav
from coord_fix_mismatch import strip_osm_pool
from coord_pool_fix import add_pois, log, norm, save

LINES = "/data/eki-play/data/lines"
OUT = "/data/work/stats/cluster_fix.json"
DAY = datetime.date.today().isoformat()
LINK_M = 30000.0


def groups(items, thr):
    """items: [(idx, (lat, lon))] -> list of index-lists, single-linkage at thr metres.

    注意: guide_core.hav はメートル単位で返す（cluster_audit.py のローカル hav は km）。
    以前ここで hav()*1000 を使ったためしきい値が実質 30 m になり、ほぼ全駅が
    「コア外」に化けた（16,266 駅）。単位は必ず m で揃える。
    """
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if hav(items[i][1][0], items[i][1][1], items[j][1][0], items[j][1][1]) <= thr:
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
    g = collections.defaultdict(list)
    for i in range(len(items)):
        g[find(i)].append(i)
    return list(g.values())


def find_core(osmdb, name, core_pts):
    """コア（30km で連鎖して合流できる位置）に同名 OSM 駅ノードがあれば最寄りを変える。"""
    best = None
    for r in osmdb.con.execute("SELECT rid,name,kind,lat,lon FROM st WHERE name LIKE ?",
                               (norm(name)[:2] + "%",)):
        if norm(r["name"]) != norm(name):
            continue
        for clat, clon in core_pts:
            dl = hav(clat, clon, r["lat"], r["lon"])
            if dl <= LINK_M:
                if best is None or dl < best[0]:
                    best = (dl, r, (clat, clon))
                break
    return best


def main(apply):
    osmdb = OsmDb()
    audit, fixed, marked = [], 0, 0
    for path in sorted(glob.glob(os.path.join(LINES, "*.json"))):
        d = json.load(open(path, encoding="utf-8"))
        pts, idxs = [], []
        for i, st in enumerate(d["车站"]):
            c = (st.get("位置") or {}).get("坐标") or {}
            if c.get("纬度"):
                pts.append((c["纬度"], c["经度"]))
                idxs.append(i)
        if len(pts) >= 5:
            gs = groups(list(enumerate(pts)), LINK_M)
            core = max(gs, key=len)
            core_pts = [pts[i] for i in core]
            for g in gs:
                if g is core:
                    continue
                for i in g:
                    st = d["车站"][idxs[i]]
                    la, lo = pts[i]
                    near = min(core_pts, key=lambda p: hav(p[0], p[1], la, lo))
                    dist = round(hav(near[0], near[1], la, lo))
                    cand = find_core(osmdb, st["名称"]["日文"], core_pts)
                    audit.append({"file": os.path.basename(path),
                                  "line": (d.get("线路") or {}).get("日文"),
                                  "idx": idxs[i], "name": st["名称"]["日文"],
                                  "dist_m": dist, "core": len(core), "total": len(pts),
                                  "reanchor": bool(cand)})
                    if not apply:
                        continue
                    pos = st["位置"]
                    if cand:
                        old = dict(pos["坐标"])
                        strip_osm_pool(st)
                        got = add_pois(st, cand[1]["lat"], cand[1]["lon"], osmdb,
                                       f"OpenStreetMap (Japan extract, ODbL)（線内クラスタ照合で再確定・{cand[1]['rid']}）")
                        st["データ品質"]["OSM施設数"] = got
                        pos["坐标"] = {"纬度": round(cand[1]["lat"], 6),
                                      "经度": round(cand[1]["lon"], 6),
                                      "出处": f"OpenStreetMap {cand[1]['rid']} ({cand[1]['kind'] or 'n/a'})、線内クラスタ照合"}
                        pos["坐标修正"] = {
                            "旧坐标": old, "旧偏差m": dist, "線内距離m": round(cand[0]),
                            "結果": "keep-fix", "日付": DAY,
                            "判定": {"結果": "線内に正しい同名ノードが存在",
                                     "根拠": f"30km単連結クラスタ（この路線のコア {len(core)}駅／ファイル内 {len(pts)}駅）から "
                                             f"{round(dist / 1000, 1)} km 外れていた同名ノードを、"
                                             "コアに連鎖する位置の正しいノードへ差替え",
                                     "許容半径m": round(LINK_M), "日付": DAY}}
                        fixed += 1
                    else:
                        st["データ品質"]["座標"] = "線外疑い"
                        st["データ品質"]["座標注記"] = {
                            "内容": f"この路線の駅群（30km単連結コア {len(core)}駅／ファイル内 {len(pts)}駅）から "
                                    f"{round(dist / 1000, 1)} km 離れているが、線内に同名のOSM駅ノードが無く差替えられない（要人手確認）。"
                                    "上流の eki-wiki が同名の別路線駅をこの路線に混入させている可能性が高い。",
                            "線内中心": [round(near[0], 5), round(near[1], 5)],
                            "許容半径m": round(LINK_M), "実測距離m": dist, "日付": DAY}
                        marked += 1
        if apply:
            save(path, d)
        del d
    audit.sort(key=lambda x: -x["dist_m"])
    log(f"cluster-outside={len(audit)} | re-anchor candidates={sum(1 for a in audit if a['reanchor'])}")
    if apply:
        log(f"applied: re-anchored={fixed} flagged={marked}")
    json.dump({"generated": DAY, "link_m": LINK_M, "count": len(audit),
               "items": audit[:400]}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    for a in audit[:20]:
        log("  ", a["line"], a["name"], f"{round(a['dist_m'] / 1000)}km",
            "core", a["core"], "/", a["total"], "reanchor" if a["reanchor"] else "flag", a["file"])
    osmdb.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
