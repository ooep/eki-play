#!/usr/bin/env python3
"""Regenerate docs/COVERAGE.md + docs/SOURCES.md and the top-level index from data/lines/*.json."""
import json, os, re, glob, sys, time, collections

OUT = "/data/eki-play"
LINES = os.path.join(OUT, "data", "lines")
DAY = time.strftime("%Y-%m-%d")

LG_FLAG = 30   # linegap.py が疑い判定した駅数
LG_FIX = 23    # linegap.py が差替えを実施した駅数
CLASS_ORDER = ["観光・名所案内", "文化・公共施設", "買い物・商店街", "便民公共服务", "交通接続", "宿泊"]


def main():
    files = sorted(glob.glob(os.path.join(LINES, "*.json")))
    n_st = n_rec = n_nocoord = 0
    by_class = collections.Counter()
    has_site = has_wiki = has_qid = 0
    en_name = zh_name = 0
    lead_ja = lead_en = lead_zh = 0
    verified_ok = verified_dead = 0
    site_defect = 0
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
                    if r.get("官网_欠陥"):
                        site_defect += 1
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
    cur_hand = 0
    cur_hand_recs = 0
    cur_hand_rows = []
    _qa = os.path.join(OUT, "docs", "qa", "curated_audit.json")
    qauto = json.load(open(_qa, encoding="utf-8")).get("自動抽出層", {}) if os.path.exists(_qa) else {}
    for p in cur_files:
        try:
            c = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        n = sum(len(c.get(k) or []) for k in
                ("观光景点", "名所案内", "公共服务", "交通", "购物", "美食", "无障碍")
                if isinstance(c.get(k), list))
        cur_recs += n
        if c.get("人手確認") is True:
            cur_hand += 1
            cur_hand_recs += n
            _sr = c.get("検索记录") or {}
            _op = _sr.get("使用した出典URL") or _sr.get("実際に開いた出典") or []
            cur_hand_rows.append((os.path.join(os.path.basename(os.path.dirname(p)),
                                               os.path.basename(p)), n, len(_op)))
        cur_dirs[os.path.basename(os.path.dirname(p))] += 1
        for k in ("观光景点", "名所案内", "公共服务", "交通", "购物", "美食", "无障碍"):
            for r in c.get(k) or []:
                if isinstance(r, dict) and r.get("官网"):
                    cur_site += 1
    hand_names = "・".join(os.path.splitext(os.path.basename(r[0]))[0] for r in cur_hand_rows)
    hand_detail = "／".join("%s：%d レコード・開いた URL %d 件" % (os.path.splitext(os.path.basename(r[0]))[0], r[1], r[2])
                           for r in cur_hand_rows)
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
        f"| ├ 未検証（同一ドメインの上限で打査省略）/ 未校验 | {has_site - verified_ok - verified_dead:,} | {pct(has_site - verified_ok - verified_dead, n_rec)} |",
        f"| └ 到達不可と判定し除去 / 判定失效后移除（官网=null） | {site_defect:,} | {pct(site_defect, n_rec)} |",
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
        f"うち **`人手確認: true`（官网本文を人手で読んで抄録した駅）は {cur_hand} 駅 / {cur_hand_recs:,} レコード**。残りは自動抽出＋出典 URL の HTTP 到達検証のみ（`加精方式` 参照）。",
        "自動層とは別ファイルで、実在ページを直接開いて確認した观光・名所案内・便民公共服务のみを収録します。",
        "",
        "| 区域 / 区域 / Cluster | 车站 / Stations |",
        "|---|---|",
    ] + [f"| {k} | {v} |" for k, v in sorted(cur_dirs.items())] + [
        "",
        "## 既知の限界 / 已知局限 / Known limitations",
        "",
        "- **自動生成データ**です。人間が書いた旅行ガイドではありません。各レコードは `来源` フィールドに出典を明記しています。",
        '- 加精層の自己申告検証（`scripts/audit_curated.py`／一括集計 `scripts/qa_curated.py` → `docs/qa/curated_audit.json`）の実測値を公開しています。'
        ' `人手確認: true` の %d 駅（%s、計 %s レコード）は、引用した全 URL が実際に開いた URL 一覧（または本文を開かず宣言）と対応することを確認済みです。内訳：%s。' % (
            cur_hand, hand_names, "{:,}".format(cur_hand_recs), hand_detail) +
        ' 一方、自動抽出＋到達検証みの %s 駅には既知の未整理が残ります：検索记录 が空のファイル %s、Wikipedia を引用しているのに 使用した出典URL へ未登録の駅 %s、英語欄に日本語表記が混じる駅 %s（該当フィールド %s）、15字未満の 简介／原文要旨 %s。これらは『検証済み』ではなく『未手当て』として数値で公開し、Phase B で駅ごとに潰します。' % (
            qauto.get("駅数", "-"), qauto.get("検索记录が空のファイル", "-"),
            qauto.get("Wikipedia を 引用していながら使用した出典URL に無い駅", "-"),
            qauto.get("英語欄に日本語を含む駅", "-"), qauto.get("同 該当フィールド数", "-"),
            qauto.get("15字未満の 简介／原文要旨", "-")),
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
        f"3. 線内の他駅分布を使う検証を2度失敗させ（下記 その2・その3）、最終的に採用した `linegap.py` の最近傍ギャップ判定"
        f"（同一ファイル内で最も近い他駅までの距離が 100 km 超）で遠方同名駅の疑いを {LG_FLAG} 駅に付け、"
        f"そのうち根拠を満たした {LG_FIX} 駅を線内の同名ノードへ差替えました。差替え総数は {coord_src['修正']:,} 駅"
        "（内訳：線内分布テストで残った 217 駅＋最近傍ギャップ 23 駅）で、旧値・偏差・判定はすべて `位置.坐标修正` に残しています。",
        f"4. 差替え根拠が無い {coord_src['線外疑い']:,} 駅は座標を変更せず `データ品質.座標 = 線外疑い` で標記しています"
        "（最近傍ギャップ判定で疑いのまま残った 7 駅を含む）。",
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
        "その2（中央値＋パーセンタイル方式／方式差し替え）：同一ファイル内の他駅中央値＋95・98パーセンタイル×1.4〜1.5 を閾値に "
        "差替えを実施し、現在 217 駅の `坐标修正` がこの方式由来ですが、**同名異県駅が混入したファイルでは中心も分散も膨らみ、混入駅が自己正当化して失明**します。"
        "鹿児島市電唐湊線は 6 駅中 2 駅が山形県の JR 米坂線・愛知県の名鉄のノードに化けていたのに、閾値が膨らんだため「通過」と判定されていました（`scripts/coord_outlier*.py`）。",
        "",
        "その3（30 km 単連結クラスタ方式／不採用）：`cluster_audit.py`／`cluster_fix.py` で 30 km 単連結のコアから外れた駅を疑う方式を試みましたが、"
        "駅間が 30 km を超える路線で誤検知が支配的でした。東海道新幹線は 33 駅中 27 駅がコア外となり、東京駅ですら「500〜600 km 離れている」と判定されます。"
        "このスクリプトには `guide_core.hav()` がメートル返却なのに `*1000` を掛けて実質 30 m 閾値にしていた単位バグもあり（16,266 駅がコア外に化けた）、"
        "修正後の再実行でも誤検知は解消しなかったため**方式そのものを不採用**としました。スクリプトは判断過程の記録としてリポジトリに残しています。",
        "",
        "採用した検証（`linegap.py`）：クラスタを作らず「最も近い他駅までの距離」だけを見る方式に変更しました。国内の路線では 100 km 超の隔たりは原則ありえないため 30 駅を疑い判定し、"
        "差替え先は ①同名 OSM ノード、②路線内の隣接駅中央値×3（10〜40 km にクランプ）以内に収まる、③同じ半径内に他駅が2つ以上ある、④ `railway`／`public_transport` タグ付き、"
        "をすべて満たすものだけとする条件を課しました。結果 23 駅を差替え（POI 併せて再構築）、7 駅は座標を動かさず `線外疑い` のままにしています。"
        "緩い条件だった当初は 松山市電 本町五丁目停留場 を**渋谷区コミュニティバスの同名プラットフォーム**へ、山陰本線 大江駅を タグのない同名ノードへ 差替えようとしたため、いずれ flag へ格下げしました（実測で捕まった誤り）。"
        "既知の限界：同じ市内の同名駅（近鉄木幡駅と JR 木幡駅 等）はこの方式では検出できません。路線ラベル自体の混在（山手线.json に 237 駅、高山駅が 东海道本线.json に混入 等）は座標とは別問題なので "
        "`docs/qa/line_membership.json` にファイル単位の記録として公開しています。",
        "",
        "## 官网リンクの検証",
        "",
        f"- 自動層：抽出した官网 {has_site:,} 件を HTTP ステータスで到達確認。応答なし・4xx/5xx は `官网` を `null` にし、"
        f"`官网_欠陥` に理由を保存（{site_defect:,} 件）。検証済み {verified_ok:,} 件は `官网検証.HTTP` に code と日付。"
        f"同ドメインの打査上限により未検証 {has_site - verified_ok - verified_dead:,} 件は、削除せず**未検証のまま**保持しています。",
        "- 加精層：`人手確認: true` の記録は **実際に本文を読むまで開いたページのみ** を `出典` に記載。到達できなかった"
        "公式（鶴岡八幡宮・久岐神社・鎌倉国宝館・名古屋城 等）と 403 を返すサイト（JR東日本・東京メトロ）は出典にしていません。",
        " 内訳（人手で本文を開いた公式ページ数）：%s。函館駅は函館市公式観光サイト「はこぶら」と市公式多言語サイト Travel Hakodate（English／简体中文の原文ページ）を併用し、到達不可の運営サイト（函館山ロープウェイ・トラピスチヌ修道院・函館バス・道南バス・函館朝市組合・函館市企業局交通部＝HTTP 000、JR北海道の駅情報ページ＝Soft-404）と、200 を返すが本文がないページ（スクリプト描画のレンタサイクル、単数形 `/spot/` のはこぶらURL）は `検索记录.採用しなかった公式候補` / `本文を開かずリンクのみで記載したURL` に区別して記録しています。" % hand_detail,
        " 鎌倉駅は市公式観光サイト Visit Kamakura（`/things-to-do/` の English ページと `zh-hans/…_cn/` の简体中文ページの**原文**）と市・観光協会・広域案内所・路線各社・タクシー・レンタサイクル・駐車場・寺院の各運営サイトを併用し、JR東日本の駅情報ページとバス・タクシー運営の複数ページが 403 を返したため 官网 を空欄にして紹介ページのみ掲載しています。公式同士が食い違う箇所は**統一せず併記**しています（湘南モノレール1日フリー券 日文 750／380 円 ⇄ 英・中 610／310 円、京急バス鎌倉フリーエリア 日文「販売終了」⇄ 英・中で継続掲載、江ノ電のハンドル型車いす欄は日本語ページのみ、山海堂の営業時間、檑亭の住所 等）。自動層が引いていた URL は `自動層が引用していたURL（本セッションで未開封）` に 7 件、200 でも本文が空・JS 描画・別対象ドメイン（`www.hasedera.or.jp`＝奈良の総本山長谷寺）だったものは `本文を開かず…`／`採用しなかった公式候補` に分離しています。",
 " 長野駅（54 レコード）は 202 ページを取得し 178 ページを HTTP 200 で本文開封、引用した 113 URL はすべて開封済みページです。自動層の長野駅レコードは 8 ファイル（信越本线#96・北陆新干线#18・上越新干线#16・信浓铁道线#29・北信浓线#42・大糸线#61・篠之井线#120・长野电铁长野线#22）すべて `公式リンク` が空だったため `自動層が引用していたURL（本セッションで未開封）` は『対象なし』を宣言しています。到達できなかった公式は JR東日本の駅情報ページ（日・英・中）とアルピコ交通タクシーの英語ページ＝403、nagaden-nagano.jp/station・convention（会議施設）・nagano-saijiki.jp・eki-midori.com＝503、midori-nagano.jp・ncp-ca.go.jp＝000、長野県公式の多言語 area-guide・県立美術館 `/en/`・市の library／kanko・善光寺 `/access/parking/`＝404、そば処やぶは初見 429（同一URLの再取得で 200、その本文のみ引用）で、いずれも `採用しなかった公式候補` に記録しました。200 で開いたが時刻表本体がスクリプト描画だった 長電バス `route.php?route=NGN01` は `本文を開かずリンクのみで記載したURL` に分離しています。長野市は多言語ページを Google 機械翻訳として提供し「機械的に翻訳されますので、正しい翻訳になっていない場合もございます…長野市では一切の責任を負うことができません」と自己宣言しているため、市の English／中文ページは引用せず English・中文は当倉庫の人手執筆としました（URL で多言語化に見える長野電鉄の駅ページは ja／en／zh の 3 がバイト一致で、実体は日本語のみでした）。公式同士が食い違う箇所は同じく**統一せず併記**しています（びんずる号＝10分/190円 ⇄ 仲見世商店街 10分/150円 ⇄ 県観光サイト 約13分、そば亭油やの営業時間・電話、善光寺の創建 642⇄644、県立美術館の収蔵 980余点⇄970点、城山動物園の住所表記、七福神めぐり 2時間10分⇄2時間半、真田邸の電話と徒歩分数 等）。",
 " 姫路駅（77 レコード）は 190 ページを取得し、HTTP 200 が 153 ページ（重複込）・一意で開封した URL は 146 件、引用した 107 件はすべて開封済みページです。200 以外の一意 36 件（000×23／404×11／503×1／403×1／302×1）は `採用しなかった公式候補` に全件を逐件記載し、それらからの本文引用は 0 件であることを書き出しゲートで照合しています。自動層では姫路駅が 11 ファイル（山阳本线#104・山阳新干线#4・播但线#4・姬新线#14・东海道本线#151・东海道新干线#8・九州新干线#4・伯备线#33・北陆本线#29・山阴本线#73・赤穂线#42）に重複収録され、同一の 公式リンク6件と同一の座標をそのままコピーしていたため、`线路注記` で実際に乗り入れる 4 線（山陽本線・山陽新幹線・播但線・姫新線）と推定由来を分けて宣言しました。座標は自動層の `出处` が「OpenStreetMap n8092170172」単独名義なのに値は当駅の OSM 2ノード n265895565（34.8264048, 134.6907972）と n8092170172（34.8269005, 134.6902424）の算術平均（34.82665265, 134.69051980）と一致するという不整合を実測で特定し、採用値は Wikipedia 日本語 infobox の {{Coord}}（34.826750, 134.690917）、OSM 2ノード値と英・中ウィキペディアの値は `座標.注記` に併記しています（在来線3面8線＋新幹線2面3線の高架駅を単一点で示せる前提自体を疑っています）。JR西日本の駅ページは `https://www.jrwest.co.jp/eki/s-tec/40101`＝000、`jr-odekake.net/eki/top.php?id=0610619`＝503、`/eki/top?id=…`・`/eki/premise?id=…`＝404 で構内図・多機能トイレ・駅売り情報を確認できないため一切引用していません。一方でJR西日本のニュースリリース 2017年11月17日付「姫路駅の案内サインと中央改札口リニューアルのお知らせ」は 200 で開封し、駅係員前の改札通路 0.9 m→1.5 m・ウォークスルー型の窓口・車いすの待合空間・案内サインの4カ国語表記を `无障碍` と `车站信息` で原文引用しました。この2件は 2026-10-22 の初回取得で Shift-JIS を UTF-8 にデコードして文字化けしていたため、2026-10-23 に charset を見て cp932 で復号し直した版のみを引用しており、その経緯も `HTTP検証` に残しています（同じ形式で取得した 2015年3月11日付の入線警告音リリースは 200 で開いたが姫路固有の記載がなく不採用）。200 で開いても本文テキストが2文字だけの `local-prime.com/monzen/`（SITE_ISOLATED のスクリプト描画）は `本文を開かずリンクのみで記載したURL` に分離し、MONZEN の施設情報は神姫バス公式と姫路観光ナビの 200 ページから引用しました。姫路城公式サイトが自ら公開する 日本語／English／中文（簡体）／中文（繁体）／한국어／français と、visit-himeji.com・神姫バス shinki.bus-japan.net・ピオレ姫路の English／简体中文は公表訳をそのまま引用し、姫路市・姫路観光ナビ「ひめのみち」・書寫山圓教寺・太陽公園・各店舗サイトは日本語表記のみのため English・中文を人手執筆として `言語注記` に宣言しています。公式間の差は**統一せず併記**しました（姫路城公式FAQ の介助者＝日本語「介助者2人から3人」⇄English「about three experienced caregivers」⇄中文「至少三名」、百間廊下⇄百閒廊下⇄百聞廊下、入城料と共通券・好古園 400円⇄310円、高田の馬場の「徒歩10分／15分」「100名／200名／50名／150名」「大手門駐車場／大手前駐車場」、ピオレの日本語版「地下1階飲食 11:00〜22:00」⇄English版「Restaurants 11:00〜20:00」、しろのまちめぐり2DAYきっぷの価格・系統数、姫路城公式 English ページの www⇄非www 版）。公式側の誤記（平均予算「1.000円」、住所「〒070-0926」、「前獲れ／前どれ」、SHIRO TERRACE の「1階」と「西棟1階」）も訂正せず原文のままです。Wikipedia は日本語・English・中文の 3 版を開封し、5面13線⇄「5 island platforms plus 1 side platform」、営業キロ、乗車人員、在来線高架化の完成年、座標の差を `车站信息.注記` に列挙しています。",
 " 富山駅（81 レコード）は 126 本を取得し、HTTP 200 が 93 本（重複込）・一意で開いた URL は 92 件、引用した 83 件はすべて 200 で本文を開いたページです。200 以外しか返さなかった一意 30 件（000×22／503×3／401×2／404×3）は全件を `採用しなかった公式候補` に逐件記載し、書き出しゲートで「引用 ⊆ 開封200」と「非200 の全掲出」を照合しています。富山は到達不可の幅が尤其広く、あいの風とやま鉄道（トップ 000・`/station/toyama/` 503）、富山地方鉄道トップ以外の各ページの一部、駅ビル運営（toyama-stationcity.jp・maruito.co.jp・toyama-esta.com＝000）、富山市立図書館（000）、ます寿し協同組合（000）、富山県美術館（401）、富山県公式 lg.jp 版（000）、旧観光ポータル info-toyama.com（503）、オーバード・ホールの別ドメイン（000）、郵便局の変更情報ページ（000）、立山黒部の別ドメイン（000）、富山空港の `/facility`・`/terminal`（404）などを開けず、200 で開いた JRおでかけネット・富山地方鉄道・富山市・富山県・富山市医師会・公益社団法人富山市観光協会（とやまナビ）の各ページに絞ったため、`未実施` は 12 項目を明記しています。200 で開いても本文が描画されなかったソフト404 を区別していて、JRおでかけネットの `eki/top?id=0541465` と `eki/connection?id=0541465`（www 側の `top.php` は 503）は共通枠しか返らず、JR の一次情報として引用したのは `barrierfree` と `premises` の 2 本だけです（バリアフリーは「中央改札口（1階）南口・北口・西口＝段差『必要なし』」「11〜14番のりば＝エレベーター・エスカレーター『あり』」「トイレ＝改札内 あり／改札外 なし」のように表の値をそのまま原文引用し、構内図 PDF の時点表記 2026年3月27日も併記）。富山地方鉄道の `/english` と `/english/cn` は 200 で開いたものの本文は観光地名を並べたリンク集で、駅の施設・運行情報の多言語本文ではなかったため翻訳文として引用していません。OpenStreetMap は 2 ノード（新幹線側 n3977968053 と在来線側 n3977703970、南北約 37 m）で、自動層の座標 36.701591／137.213411 が両ノードの算術平均でありながら `出处` が片側単独名義だったことを生 XML の再取得で実測特定しました（姫路駅と同じ型の欠陥。初回は XML タグ除去で本文が消えたため、タグ付きの生 XML を買い直して `name=富山`・`operator`・`highspeed=yes`・`wikidata=Q1053070` を確認しています）。自動層が当駅を「ハピラインふくい線#20」と別 ID で重複収録していた問題は、本ファイルを「北陸新幹線#7」を正本とし `线路注記` で実乗り入れ（北陸新幹線・高山本線・あいの風とやま鉄道線・地鉄本線／軌道線／港線）と分けて宣言しました。富山市はトップが city.toyama.toyama.jp、下位ページが city.toyama.lg.jp の二重ドメインで、とやまナビが掲出する「公式サイト …/etc/muse/index.html」は本セッションで開いていないため引用 URL に含めていません。公式間の差は**統一せず併記**しています（まちなか観光案内所の開所 8:00⇄9:00、松川の桜の本数・延長、山王まつり 5/31〜6/2 ⇄ 5/25〜6/3、休日急患センターの日当番表（市⇄医師会）、現住所 明輪町1番225号（JR 公表）⇄ 1番227号（中文ウィキ infobox）、Wikipedia 各言語版のホーム数・乗車人員・座標）。Wikipedia は日本語・English・中文の 3 版を別々に開封し（CC BY-SA 4.0）、1899年3月20日の開業から 1952年の民衆駅・まると百貨店、1960年の火災と待合室、2015年のあいの風転換、2020年の路面電車南北接続までの沿革補完にのみ用いています。",
 " 京都駅（32 レコード）は 106 本を取得し、HTTP 200 が 73 本（重複込）・一意で開いた URL は 72 件、本ファイルが引用する 58 件（本文内の引用カウントは重複込 135 件）はすべて 200 で本文を開いたページです。200 以外しか返さなかった一意 33 件（000×12／404×17／503×2／403×1／301×1）は全件を `採用しなかった公式候補` に逐件記載し、それらからの本文引用が 0 件であることを書き出しゲートで照合しました。当駅から検証を5段に増やしています（① 引用URL ⊆ 開封200、② 原文要旨を「／」で割った断片＝4字以上 396 片を保存ダンプ本文と全件照合し不一致 0、③ 非200 の全掲出と未補間マーカーの不在、④ `scripts/audit_curated.py`、⑤ 記述欄 216 欄の「…」引用を**そのレコードが出典として掲げたダンプ**と全文照合して不一致 0）。5段目で初めて、各出典の 标题 に使っていた title 要素の語が本文ダンプから除去されているため本文に存在しないことを突き止め、東寺トップの title「東寺 – 世界遺産 真言宗総本山 教王護国寺」、観光協会 FAQ のサイト名「京なびオンライン」、関西空港交通トップの title「関西国際空港のエアポートリムジンバス」は記述欄の「」を外して title 由来と明記しました（JRおでかけネットの駅情報は表組が平坦化されてラベルと値が連続するため「／」で区切り、西本願寺は本文にふりがなが混在しているので「御影堂ごえいどう・阿弥陀堂あみだどう」のまま写しています）。多言語は、東本願寺（真宗本廟）が公式英語を別ドメイン english.higashihonganji.or.jp で運用していたためトップ・/access/・渉成園 /shosei-en/ を 200 で開き、Hours・Admission Free・Getting Here・Thirteen Views・West Gate などの公表訳をそのまま引用しました（日本語側の /access/ は 404 で、現行パスは /worship/access/）。京都国立博物館も公式英語 /eng/ と 中文（簡体）/zh-s/（visit/access 込み）を 200 で開いたので English・中文は公表訳です。京都市観光協会は ja／en／cn／tw のトップをすべて開きましたが、施設個別ページの下位（kyoto.travel/en|cn/tourism/single01.php?…・/en/access）は 404 で、京都鉄道博物館の English・中文は人手執筆になりました。関西空港交通の公式英語・繁体字トップは 200 で本文を開いたものの路線表が JavaScript 描画で、確認できたのは Urgent Notice No／List of Lines (Schedule/Fares)／Site map／Company Info と 緊急通知／無緊急通知／各路線一覽（時刻表・車費）／網站地圖／公司簡介 の欄名だけだったため、運賃は日本語ページ（200）の数値を引き、英語・中文欄が薄い表記である旨を 言語注記 に書きました。200 で開いても本文が空白1文字だけのページは区別していて、京都鉄道博物館の自ドメイン（kyotorailwaymuseum.jp と /guide/、ログ行を含め 59・65 バイト）は `本文を開かずリンクのみで記載したURL` に回して 官网 欄を空欄にし、住所・電話・営業時間・観覧料は本文を開けた観光協会の施設詳細から引用しました。https://www.krm.or.jp/ は 200 で開けた別主体（医療法人社団KRM）なので不採用です。自動層は当駅を15ファイルへ分散し（同一 node 表記7・別 node 表記8）、既存の加精層ファイルは 车站ID「jr奈良線#7」に対し 线路「北陆本线」・座標 null だったため、所属線ID「東海道本線#48」と実測ノード値へ置き換えました。座標は OpenStreetMap の3ノード（267316272／7780637510／3340028686、ばらつき約 121〜272m）を生 XML で開封し、自動層の15ファイル共通値 34.985222／135.758731 がどちらのノード生値でも2ノードの算術平均でもない（`出处` ラベルは 3340028686 名義8・7780637510 名義7）ことを実測で特定しました（姫路・富山と同じ型の欠陥）。併せて、自動層の 车站ID 末尾の数字が路線上の順序ではなくファイル内配列の指数であること（東海道本线#47＝亘理駅・#49＝仙台駅）も `座標.注記` に残しました。公式間の差は**統一せず併記**しています（Wikipedia 日本語「1日平均の乗降人員は約60万人」⇄中文（簡体）「1日平均上下車人次達70萬人」、日本語「1997年に完成した4代目駅舎」⇄ English 版 summary「third-largest station building … under one 15-story roof」⇄中文「總高60公尺」（120m 高さ規制の説明は中文側のみ）、近鉄 B01・地下鉄 K11 は自社駅ページの本文を開いていないため OpenStreetMap の ref タグと中文版表記による裏付け併記、市バスのりば D2 の系統表記が施設ページで日本語「206・208号系統」⇄英語「206, 208」⇄中文「206、208号」など）。`未実施` は 13 項目を明示しています（三十三間堂＝http 301／https 000、京都タワー・ヨドバシ・駅ビル旧候補ドメイン＝000、郵便局と府の医療・福祉系下位＝404、近鉄・市営地下鉄の自社駅ページ、観光協会の English／中文施設詳細、リムジンバスの英語・繁体字路線表、自動層の加精ファイルが引いていた 22 URL のうち本セッションで開けなかった 20 件 等）。",
        "",
        "## 再現実行",
        "```",
        "python3 scripts/build_osm_index.py        # japan-latest.osm.pbf → jp_poi.sqlite3（駅・POI・面の重心）",
        "python3 scripts/run_lines.py              # 577 路線 → data/lines/*.json",
        "python3 scripts/enrich2.py                # 官网・Wikidata・三語名称の補強とHTTP検証",
        "python3 scripts/zh_fixup.py               # converttitles=1 で中文ウィキ見出しを解決",
        "python3 scripts/coord_pool_fix.py         # 表記差駅の座標後補＋POIプール再生成",
        "python3 scripts/coord_outlier.py --apply  # 線内分布テスト（同名異県駅の差替え）",
        "python3 scripts/cluster_audit.py          # （不採用）30 km 単連結クラスタ監査の実験",
        "python3 scripts/cluster_fix.py            # （不採用）同上の差替え試行・単位バグ修正後の記録",
        "python3 scripts/linegap.py --apply        # 採用した最近傍ギャップ監査＋同名ノード差替え",
        "python3 scripts/line_membership.py        # 路線ラベル混在の監査（docs/qa/line_membership.json）",
        "python3 scripts/coord_flag.py             # 差替え根拠の無い駅を警告のみ付与",
        "python3 scripts/curate_gen.py --per-cluster 8",
        "python3 scripts/audit_curated.py 'data/curated/*/*.json'   # 加精層の重複キー・引用URLと開いたURLの対応・英語欄の日本語混入・短文出典を検証",
        "python3 scripts/qa_curated.py             # 加精層の一括検証 → docs/qa/curated_audit.json（下の make_docs がこの数値を引用）",
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
                     "人手確認駅": cur_hand, "人手確認レコード": cur_hand_recs,
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
