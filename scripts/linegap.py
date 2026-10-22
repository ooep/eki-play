#!/usr/bin/env python3
"""路線ファイル内の「全駅中最も近い他駅」距離で同名混入を疑う監査／修復。

経緯（検証の失敗と改良）:
  1. coord_outlier.py = 中央値＋98パーセンタイル×1.5 -> 混入駅が中心と分散自体を
     膨らませるため自己正当化して失明。
  2. cluster_audit.py / cluster_fix.py = 30 km 単連結クラスタのコア外 -> 稀疏な路線で
     誤検知。東海道新幹線は駅間 40-60 km の区間があり、東京・品川・新横浜・小田原・熱海が
     「コア外 500-600 km」化した（クラスタが切れるだけで、実際は正位置）。
     ※ cluster_fix.py は guide_core.hav（メートル返却）に *1000 を掛けてしまい、
       しきい値が実質 30 m になっていた不具合も途中で修正済み。その版でも 453 駅中出现 27 駅が
       東海道新幹線、86 駅が鹿児島本線というように稀疏路線の誤検知が dominant。
  3. 本スクリプト = 「同じファイル内の最も近い他駅までの距離」d_nearest。
     同じ路線の駅なら隣接駅まで必ず近い（日本最長の駅間も 60 km 未満）。
     d_nearest > GAP_M (100 km) の駅だけを同名混入の疑いとして扱う。
     -> 連結性を前提にしないので新幹線・長距離線でも誤検知しない。

Usage:
  linegap.py            -> 監査のみ（stats/linegap.json / docs/qa/linegap.json）
  linegap.py --apply    -> 差替え（線内に収まる同名OSMノードがある場合）＋警告付与
"""
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
STATS = "/data/work/stats/linegap.json"
QA = "/data/eki-play/docs/qa/linegap.json"
DAY = datetime.date.today().isoformat()
GAP_M = 100000.0      # この距離より近い他駅が1つも無ければ疑い
FIT_MIN_M = 10000.0   # 受入半径の下限（市電など隣接間隔が極端に短い路線用）
FIT_MAX_M = 40000.0   # 受入半径の上限（新幹線など隣接間隔が長い路線用）
MIN_ST = 3            # 駅数が少ないファイルは母数不足で判定しない


def main(apply):
    osmdb = OsmDb()
    audit, fixed, marked = [], 0, 0
    changed = set()
    for path in sorted(glob.glob(os.path.join(LINES, "*.json"))):
        d = json.load(open(path, encoding="utf-8"))
        pts, idxs = [], []
        for i, st in enumerate(d["车站"]):
            c = (st.get("位置") or {}).get("坐标") or {}
            if c.get("纬度"):
                pts.append((c["纬度"], c["经度"]))
                idxs.append(i)
        if len(pts) >= MIN_ST:
            gaps = []
            for i in range(len(pts)):
                best, bj = None, None
                for j in range(len(pts)):
                    if j == i:
                        continue
                    dl = hav(pts[i][0], pts[i][1], pts[j][0], pts[j][1])
                    if best is None or dl < best:
                        best, bj = dl, j
                gaps.append((best, bj))
            # この路線の「隣接駅间距」の実測中央値。同じ路線の正当な駅は原則このオーダーに
            # 収まるので、差替え先ノードの受入半径はここから決める（固定 60km にすると
            # 越生線の 一本松駅 に対して長崎電鉄の同名駅を「線内」と誤認する）。
            normal = sorted(g for g, _ in gaps if g is not None and g <= GAP_M)
            med = normal[len(normal) // 2] if normal else GAP_M
            fit = min(FIT_MAX_M, max(FIT_MIN_M, med * 3.0))
            for i, (best, bj) in enumerate(gaps):
                if best is not None and best > GAP_M:
                    st = d["车站"][idxs[i]]
                    name = st["名称"]["日文"]
                    others = [pts[j] for j in range(len(pts)) if j != i]
                    cand = None
                    for r in osmdb.con.execute(
                            "SELECT rid,name,kind,lat,lon FROM st WHERE name LIKE ?",
                            (norm(name)[:2] + "%",)):
                        if norm(r["name"]) != norm(name):
                            continue
                        k = (r["kind"] or "")
                        if "railway" not in k and "public_transport" not in k:
                            continue  # 単なる named node（バス停表記等）は使わない
                        near_pts = [hav(r["lat"], r["lon"], p[0], p[1]) for p in others]
                        dl = min(near_pts)
                        # 線内の「最も近い他駅1点」だけに寄せると、ファイル自体が
                        # 混在名前の池（山手线.json 等）のときに汚染駅へ引き寄せられる。
                        # 渋谷のコミュニティバス「本町五丁目」を松山市電の本町五丁目停留場
                        # として差替える誤りが実際に起きたため、
                        # 同じ受入半径内に 2 駅以上の裏付けを求める。
                        if dl <= fit and sum(1 for x in near_pts if x <= fit) >= 2 \
                                and (cand is None or dl < cand[0]):
                            cand = (dl, r)
                    audit.append({"file": os.path.basename(path),
                                  "line": (d.get("线路") or {}).get("日文"),
                                  "idx": idxs[i], "name": name,
                                  "d_nearest_m": round(best),
                                  "nearest_station": d["车站"][idxs[bj]]["名称"]["日文"],
                                  "total": len(pts),
                                  "reanchor": bool(cand),
                                  "reanchor_rid": cand[1]["rid"] if cand else None,
                                  "reanchor_fit_m": round(cand[0]) if cand else None,
                                  "隣接駅中央値m": round(med), "受入半径m": round(fit)})
                    if not apply:
                        continue
                    pos = st["位置"]
                    if cand:
                        old = dict(pos["坐标"])
                        strip_osm_pool(st)
                        got = add_pois(st, cand[1]["lat"], cand[1]["lon"], osmdb,
                                       f"OpenStreetMap (Japan extract, ODbL)"
                                       f"（線内最近駅照合で再確定・{cand[1]['rid']}）")
                        st["データ品質"]["OSM施設数"] = got
                        pos["坐标"] = {"纬度": round(cand[1]["lat"], 6),
                                      "经度": round(cand[1]["lon"], 6),
                                      "出处": f"OpenStreetMap {cand[1]['rid']} "
                                             f"({cand[1]['kind'] or 'n/a'})、線内最近駅照合"}
                        pos["坐标修正"] = {
                            "旧坐标": old, "旧最近平駅距離m": round(best),
                            "新最近平駅距離m": round(cand[0]), "結果": "keep-fix",
                            "日付": DAY,
                            "判定": {"結果": "同名ノードのうち線内に収まるものが存在",
                                     "根拠": f"このファイル {len(pts)} 駅中、最も近い他駅"
                                             f"（{d['车站'][idxs[bj]]['名称']['日文']}）まで "
                                             f"{round(best / 1000)} km。同名の別路線駅が混入した"
                                             f"と判断し、線内の最も近い他駅まで {round(cand[0] / 1000)} km "
                                             "に収まる正しいノードへ差替え",
                                     "疑い閾値m": round(GAP_M), "受入閾値m": round(fit),
                                     "隣接駅中央値m": round(med),
                                     "日付": DAY}}
                        fixed += 1
                        changed.add(path)
                    else:
                        st["データ品質"]["座標"] = "線外疑い"
                        st["データ品質"]["座標注記"] = {
                            "内容": f"この路線ファイル {len(pts)} 駅のうち最も近い他駅"
                                    f"（{d['车站'][idxs[bj]]['名称']['日文']}）まで "
                                    f"{round(best / 1000)} km 離れており、上流 eki-wiki が同名の"
                                    "別路線駅をこの路線に混入させた疑いが強い。ただし線内に同名の"
                                    "OSM 駅ノードが存在しない（または線内から遠い）ため座標は"
                                    "動かさず、人手確認までこの位置を保持する。",
                            "最寄り他駅": d["车站"][idxs[bj]]["名称"]["日文"],
                            "最寄り他駅までm": round(best),
                            "疑い閾値m": round(GAP_M), "隣接駅中央値m": round(med), "日付": DAY}
                        marked += 1
                        changed.add(path)
        if apply and path in changed:
            save(path, d)
        del d
    audit.sort(key=lambda x: -x["d_nearest_m"])
    log(f"flagged={len(audit)} | re-anchor candidates={sum(1 for a in audit if a['reanchor'])}"
        f" | files={len({a['file'] for a in audit})}")
    if apply:
        log(f"applied: re-anchored={fixed} flagged-only={marked}")
    payload = {"generated": DAY, "方式": "d_nearest（ファイル内で最も近い他駅までの距離）",
               "疑い閾値m": GAP_M, "受入閾値": "路線内の隣接駅中央値×3（10-40km にクランプ）", "count": len(audit),
               "reanchor_candidates": sum(1 for a in audit if a["reanchor"]),
               "items": audit}
    json.dump(payload, open(STATS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if apply:
        json.dump(payload, open(QA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for a in audit[:25]:
        log("  ", a["line"], a["name"], f"{round(a['d_nearest_m'] / 1000)}km",
            f" nearest={a['nearest_station']}", "reanchor" if a["reanchor"] else "flag",
            a["file"])
    osmdb.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
