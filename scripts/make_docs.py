#!/usr/bin/env python3
"""Regenerate docs/COVERAGE.md + docs/SOURCES.md and the top-level index from data/lines/*.json."""
import json, os, re, glob, sys, time, collections

OUT = "/data/eki-play"
LINES = os.path.join(OUT, "data", "lines")
DAY = time.strftime("%Y-%m-%d")

CLASS_ORDER = ["観光・名所案内", "文化・公共施設", "買い物・商店街", "便民公共服务", "交通接続", "宿泊"]


def main():
    files = sorted(glob.glob(os.path.join(LINES, "*.json")))
    n_st = n_rec = n_nocoord = 0
    by_class = collections.Counter()
    has_site = has_wiki = has_qid = 0
    en_name = zh_name = 0
    lead_ja = lead_en = lead_zh = 0
    verified_ok = verified_dead = 0
    coord_src = collections.Counter()
    per_prefecture = collections.Counter()
    stations_total = 0
    langs = {"ja": 0, "en": 0, "zh": 0}
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        for st in d["车站"]:
            stations_total += 1
            if not st["位置"]["坐标可用"]:
                n_nocoord += 1
            pos = st.get("位置") or {}
            if pos.get("坐标修正"):
                coord_src["修正"] += 1
            elif pos.get("坐标来源"):
                coord_src["後補"] += 1
            elif pos.get("坐标"):
                coord_src["OSM"] += 1
            if (st.get("データ品質") or {}).get("座標") == "線外疑い":
                coord_src["線外疑い"] += 1
            for klass, rows in st["分区"].items():
                for r in rows:
                    n_rec += 1
                    by_class[r["类别"]["日文"]] += 1
                    if r.get("官网"):
                        has_site += 1
                        v = (r.get("官网検証") or {}).get("HTTP")
                        if v and v < 400:
                            verified_ok += 1
                        elif v:
                            verified_dead += 1
                    if r.get("维基百科"):
                        has_wiki += 1
                    if r.get("Wikidata"):
                        has_qid += 1
                    if r["名称"].get("英文"):
                        en_name += 1
                    if r["名称"].get("中文"):
                        zh_name += 1
                    bl = r.get("简介") or {}
                    lead_ja += bool(bl.get("日文"))
                    lead_en += bool(bl.get("英文"))
                    lead_zh += bool(bl.get("中文"))
                    langs["ja"] += bool(r["名称"].get("日文"))
                    langs["en"] += bool(r["名称"].get("英文"))
                    langs["zh"] += bool(r["名称"].get("中文"))
    pct = lambda a, b: f"{100.0 * a / b:.1f}%" if b else "-"
    CUR = os.path.join(OUT, "data", "curated")
    cur_files = sorted(glob.glob(os.path.join(CUR, "*", "*.json")))
    cur_recs = 0
    cur_dirs = collections.Counter()
    cur_site = 0
    for p in cur_files:
        try:
            c = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        n = sum(len(c.get(k) or []) for k in
                ("观光景点", "名所案内", "公共服务", "交通", "购物", "美食", "无障碍")
                if isinstance(c.get(k), list))
        cur_recs += n
        cur_dirs[os.path.basename(os.path.dirname(p))] += 1
        for k in ("观光景点", "名所案内", "公共服务", "交通", "购物", "美食", "无障碍"):
            for r in c.get(k) or []:
                if isinstance(r, dict) and r.get("官网"):
                    cur_site += 1
    lines = [
        f"# データカバー率 / 数据覆盖率 (auto-generated {DAY})",
        "",
        f"| 指標 / 指标 / Metric | 数値 / 数值 / Value |",
        "|---|---|",
        f"| 線路ファイル / 线路文件 / Line files | {len(files)} |",
        f"| 駅 / 车站 / Stations | {stations_total:,} |",
        f"| 座標未取得 / 未获坐标 / without coordinates | {n_nocoord:,} ({pct(n_nocoord, stations_total)}) |",
        f"| ├ OSM駅ノード一致 / OSM 节点直接命中 | {coord_src['OSM']:,} |",
        f"| ├ 表記差を後補（Wikidata／Wikipedia）/ 表记差异后补 | {coord_src['後補']:,} |",
        f"| ├ 同名他県駅を線内照合で差替え / 同名异地站已按线路域校验替换 | {coord_src['修正']:,} |",
        f"| └ 線外疑いフラグ（座標は変更せず）/ 线外疑似标记（坐标未改动） | {coord_src['線外疑い']:,} |",
        f"| 周辺施設レコード / 周边设施记录 / POI records | {n_rec:,} |",
        "",
        "## 分類別 / 按类别 / By section",
        "",
        "| 日文 | 中文 | English | records | share |",
        "|---|---|---|---|---|",
    ]
    CN = {"観光・名所案内": "观光·名胜", "文化・公共施設": "文化·公共设施", "買い物・商店街": "购物·商店街",
          "便民公共服务": "便民公共服务", "交通接続": "交通换乘", "宿泊": "住宿", "除外": "（已排除）",
          "一般名詞": "（已排除）", "周辺施設": "周边设施"}
    EN = {"観光・名所案内": "Sights & Landmarks", "文化・公共施設": "Culture & Public Facilities",
          "買い物・商店街": "Shopping", "便民公共服务": "Public Services", "交通接続": "Transport",
          "宿泊": "Lodging", "除外": "Excluded", "一般名詞": "Excluded", "周辺施設": "Facilities nearby"}
    for c, v in by_class.most_common():
        lines.append(f"| {c} | {CN.get(c, c)} | {EN.get(c, c)} | {v:,} | {pct(v, n_rec)} |")
    lines += [
        "",
        "## フィールド充足率 / 字段填充率 / Field coverage",
        "",
        "| フィールド / 字段 / Field | 件数 | 比率 |",
        "|---|---|---|",
        f"| 名称・日文 / 日文名 / name (ja) | {langs['ja']:,} | {pct(langs['ja'], n_rec)} |",
        f"| 名称・英文 / 英文名 / name (en) | {en_name:,} | {pct(en_name, n_rec)} |",
        f"| 名称・中文 / 中文名 / name (zh) | {zh_name:,} | {pct(zh_name, n_rec)} |",
        f"| 官网 / 官网 / official website | {has_site:,} | {pct(has_site, n_rec)} |",
        f"| ├ HTTP検証済み / 已通过可达性校验 | {verified_ok:,} | {pct(verified_ok, n_rec)} |",
        f"| └ リンク切れとして除去 / 已判定失效并移除 | {verified_dead:,} | {pct(verified_dead, n_rec)} |",
        f"| 维基百科リンク / 维基百科链接 / Wikipedia link | {has_wiki:,} | {pct(has_wiki, n_rec)} |",
        f"| Wikidataエンティティ / Wikidata 实体 / Wikidata entity | {has_qid:,} | {pct(has_qid, n_rec)} |",
        f"| 紹介文・日文 / 日文简介 / blurb (ja) | {lead_ja:,} | {pct(lead_ja, n_rec)} |",
        f"| 紹介文・英文 / 英文简介 / blurb (en) | {lead_en:,} | {pct(lead_en, n_rec)} |",
        f"| 紹介文・中文 / 中文简介 / blurb (zh) | {lead_zh:,} | {pct(lead_zh, n_rec)} |",
        "",
        "",
        "## 加精层（人工检索・逐条出典）/ 加精层（人工检索、逐条出典）/ Curated tier",
        "",
        f"`data/curated/<区域>/<车站>.json` — {len(cur_files)} 駅 / {cur_recs:,} レコード（うち `官网` 付き {cur_site:,}）。",
        "自動層とは別ファイルで、実在ページを直接開いて確認した观光・名所案内・便民公共服务のみを収録します。",
        "",
        "| 区域 / 区域 / Cluster | 车站 / Stations |",
        "|---|---|",
    ] + [f"| {k} | {v} |" for k, v in sorted(cur_dirs.items())] + [
        "",
        "## 既知の限界 / 已知局限 / Known limitations",
        "",
        "- **自動生成データ**です。人間が書いた旅行ガイドではありません。各レコードは `来源` フィールドに出典を明記しています。",
        "- 自動層の公式サイトは **Wikidata P856・OSM の website タグ・日本語版ウィキペディアの外部リンク**から抽出し、HTTP の応答codeで到達確認しました（存在しないサイトの能動検索は自動層では行っていません。手動検索は上の加精層の範囲です）。未収録の施設では `官网` が `null` になります。",
        "- 英文名・中文名は OSM の `name:en` / `name:zh`、Wikidata の多言語ラベル、および中・英ウィキペディアの見出しから取得。**これらに無い場合の機械翻訳は行っていません**（`null` のまま）。",
        "- 距離は駅座標からの**直線距離**（步行距離ではありません）。",
        "- 座標は OpenStreetMap の駅ノード照合が基本ですが、**同名異県の駅**（森駅・神田駅・北野駅・中郡駅 等）が別県のノードに一致していた事例を線内分布テストで検出し、{fix} 件を正しいノードへ差替え、{flag} 件は差替え根拠が無いため `データ品質.座標 = 線外疑い` として**座標はそのままで警告付**にしています。差替え・後補の履歴はすべて `位置.坐标修正` / `位置.坐标来源` に残しています。".format(fix=coord_src["修正"], flag=coord_src["線外疑い"]),
        "- POI の検索半径は 900 m。範囲外の観光地は本データには含まれません。",
    ]
    os.makedirs(os.path.join(OUT, "docs"), exist_ok=True)
    open(os.path.join(OUT, "docs", "COVERAGE.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    src = [
        "# 来源与验证方法 / 出典と検証 / Sources & verification",
        "",
        f"更新日 / {DAY}",
        "",
        "## 三层出典",
        "",
        "| 层 / Layer | 来源 | 授权 | 记录内的出处字段 |",
        "|---|---|---|---|",
        "| 自動層 `data/lines/` | OpenStreetMap 日本抽出（Geofabrik `japan-latest.osm.pbf`） | ODbL 1.0 | `来源: [\"OpenStreetMap (Japan extract, ODbL)\"]` |",
        "| 自動層（面データ） | 同上 way のノード平均重心（公園・境内・城郭敷地など点に無い施設） | ODbL 1.0 | `OSM编号: way/<id>` |",
        "| 自動層（記事中リンク） | 日本語版ウィキペディア各駅記事の「駅周辺」欄 | CC BY-SA 4.0 | `来源: 日本語ウィキペディア「…」記事「駅周辺」欄` + `维基百科` |",
        "| 三語名称・简介 | OSM `name:en/zh` → Wikidata ラベル → 中英ウィキペディア見出し | ODbL / CC0 / CC BY-SA 4.0 | `维基条目原文引用` |",
        "| 加精層 `data/curated/` | 自治体・施設・鉄道事業者の**公式サイトを実際に開いて**摘録 | 各サイト © | `出典[].{標題, URL, 原文要旨, ライセンス}` |",
        "",
        "## 座標の同定と検証（自動層）",
        "",
        f"1. OSM 駅ノード照合（種別と運営事業者でランク付け）→ {coord_src['OSM']:,} 駅。",
        f"2. 表記差（ヶ⇄ケ・挾⇄挟 等）で一致しなかった {coord_src['後補']:,} 駅は、日本語版ウィキペディア記事→Wikidata P625、"
        "または記事自身の座標で後補し、`位置.坐标来源` に出典を残しました。",
        f"3. 線内分布テスト（同一线路の他駅中央値＋98パーセンタイル×1.5）で {coord_src['修正']:,} 駅が**別県の同名駅**と"
        "一致していたことを検出し、線内の正しいノードへ差替えました（`位置.坐标修正` に旧値・偏差・根拠）。",
        f"4. 差替え根拠が無い {coord_src['線外疑い']:,} 駅は座標を変更せず `データ品質.座標 = 線外疑い` で標記しています。",
        f"5. 座標が取得できなかった {n_nocoord} 駅（廃止ケーブルカーの停留場で記事・OSM・Wikidata すべてに個別座標無し）は "
        "`データ品質.未得理由` に理由を記載しています。",
        "",
        "### 失敗した検証の記録（透明性のため残す）",
        "",
        "当初 `名称.词条` から導したウィキペディア座標を正として 86 駅を一括置換しましたが、`词条` は eki-wiki が同じ駅名の記事に"
        "解決した**派生リンク**にすぎず、鹿児島市電の中郡駅が JR 米坂線（山形県）の記事に化けているような例がありました。"
        "置換は誤りだったため 77 駅は旧座標へ復元し、線内分布テストを通過した 9 駅のみ修正を維持しています。"
        "各駅の結果は `位置.坐标修正.判定`（旧座標のみ線内／新座標のみ線内／両者線内＝要人手）に保存しました。",
        "",
        "## 官网リンクの検証",
        "",
        f"- 自動層：抽出した官网 {has_site:,} 件を HTTP ステータスで到達確認。応答なし・4xx/5xx は `官网` を `null` にし、"
        f"`官网_欠陥` に理由を保存（{verified_dead:,} 件）。検証済み {verified_ok:,} 件は `官网検証.HTTP` に code と日付。",
        "- 加精層：`人手確認: true` の記録は **実際に本文を読むまで開いたページのみ** を `出典` に記載。到達できなかった"
        "公式（鶴岡八幡宮・久岐神社・鎌倉国宝館・名古屋城 等）と 403 を返すサイト（JR東日本・東京メトロ）は出典にしていません。",
        "",
        "## 再現実行",
        "",
        "```",
        "python3 scripts/build_osm_index.py        # japan-latest.osm.pbf → jp_poi.sqlite3（駅・POI・面の重心）",
        "python3 scripts/run_lines.py              # 577 路線 → data/lines/*.json",
        "python3 scripts/enrich2.py                # 官网・Wikidata・三語名称の補強とHTTP検証",
        "python3 scripts/zh_fixup.py               # converttitles=1 で中文ウィキ見出しを解決",
        "python3 scripts/coord_pool_fix.py         # 表記差駅の座標後補＋POIプール再生成",
        "python3 scripts/coord_outlier.py --apply  # 線内分布テスト（同名異県駅の差替え）",
        "python3 scripts/coord_flag.py             # 差替え根拠の無い駅を警告のみ付与",
        "python3 scripts/curate_gen.py --per-cluster 8",
        "python3 scripts/make_docs.py              # docs/ と index.json の再生成",
        "```",
    ]
    open(os.path.join(OUT, "docs", "SOURCES.md"), "w", encoding="utf-8").write("\n".join(src) + "\n")

    idx = {"生成日": DAY, "線路ファイル数": len(files), "駅数": stations_total,
           "施設レコード数": n_rec,
           "座標": {"OSM一致": coord_src["OSM"], "後補（Wikidata／Wikipedia）": coord_src["後補"],
                   "線内照合で差替え": coord_src["修正"], "線外疑い（未変更・警告のみ）": coord_src["線外疑い"],
                   "未取得": n_nocoord},
           "加精層": {"駅": len(cur_files), "レコード": cur_recs, "官网付き": cur_site,
                     "区域": {k: v for k, v in sorted(cur_dirs.items())}},
           "内訳": {k: v for k, v in by_class.most_common()},
           "フィールド充足率": {"英文名": pct(en_name, n_rec), "中文名": pct(zh_name, n_rec),
                              "官网": pct(has_site, n_rec), "简介(日文)": pct(lead_ja, n_rec),
                              "简介(英文)": pct(lead_en, n_rec), "简介(中文)": pct(lead_zh, n_rec)},
           "線路": [{"文件": os.path.basename(p), "线路中文": d0["线路"]["中文"], "线路日文": d0["线路"]["日文"],
                     "车站数": d0["车站数"]} for p in files for d0 in [json.load(open(p, encoding="utf-8"))]]
           }
    json.dump(idx, open(os.path.join(OUT, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in idx.items() if k != "線路"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
