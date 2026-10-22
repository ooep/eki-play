# -*- coding: utf-8 -*-
"""加精層（data/curated）の出典自己申告を一括検証し docs/qa/curated_audit.json に書く。
 人手確認 true の駅（Phase B）は「引用URL ⊆ 使用した出典URL／本文を開かず宣言」を必須とする。
 自動抽出層は 検索记录 の語彙が揺れているため、その実態（空欄・旧キー・Wikipedia 未登録・
 英語欄の日本語混入・短文出典）を個数で記録し、品質目標ではなく既知の限界として公開する。"""
import io, json, glob, re, collections, os

CJK = re.compile(u"[\u3040-\u30ff\u4e00-\u9fff\uff01-\uff5e\u300c\u300d\u3001]")
SECS = (u"观光景点", u"名所案内", u"公共服务", u"交通", u"购物", u"美食")


def urls_of(v, acc):
    if isinstance(v, dict):
        for x in v.values():
            urls_of(x, acc)
    elif isinstance(v, list):
        for x in v:
            urls_of(x, acc)
    elif isinstance(v, str) and v.startswith("http"):
        acc.append(v.split(u"（")[0].strip().rstrip(u"。"))


res = collections.OrderedDict()
phase, auto = [], []
files = sorted(glob.glob("data/curated/*/*.json"))
schema = collections.Counter()
wiki_not_listed = 0
en_mix_files = en_mix_hits = 0
short_hits = 0
empty_log = []
for f in files:
    d = json.load(io.open(f, encoding="utf-8"))
    sr = d.get(u"検索记录", {}) or {}
    schema[u"+".join(sorted(sr.keys())) or u"(空)"] += 1
    opened = list(sr.get(u"使用した出典URL") or []) + list(sr.get(u"実際に開いた出典") or [])
    declared = []
    npl = sr.get(u"本文を開かずリンクのみで記載したURL", [])
    for x in (npl if isinstance(npl, list) else []):
        if isinstance(x, dict) and x.get(u"URL"):
            declared.append(x[u"URL"])
        elif isinstance(x, str):
            declared.append(x)
    cited = []
    urls_of({k: v for k, v in d.items() if k != u"検索记录"}, cited)
    cited = list(dict.fromkeys(cited))
    missing = [u for u in cited if u not in opened and u not in declared]
    recs = sum(len(d.get(k, [])) for k in SECS if k in d)
    cnt = [0, 0]

    def walk(v, p):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, p + u"/" + k)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, p + u"/[%d]" % i)
        elif isinstance(v, str):
            if p.endswith(u"/英文") and CJK.search(v):
                cnt[0] += 1
            if (p.endswith(u"/原文要旨") or p.endswith(u"/简介/日文")) and len(v) < 15:
                cnt[1] += 1

    walk(d, u"")
    mix, short = cnt
    st = collections.OrderedDict([
        ("file", f),
        ("人手確認", bool(d.get(u"人手確認"))),
        ("レコード数", recs),
        ("開いたURL数", len(opened)),
        ("引用URL数", len(cited)),
        ("根拠未登録URL", missing),
    ])
    if st["人手確認"]:
        phase.append(st)
    else:
        auto.append(st)
        if not opened:
            empty_log.append(f)
    wiki_not = [u for u in missing if u.startswith("https://ja.wikipedia.org/")]
    if wiki_not:
        wiki_not_listed += 1
    en_mix_files += 1 if mix else 0
    en_mix_hits += mix
    short_hits += short

out = collections.OrderedDict([
    ("検証日", "2026-10-22"),
    ("検証スクリプト", "scripts/audit_curated.py（本ファイルは同一ロジックの一括集計）"),
    ("加精層ファイル数", len(files)),
    ("検索记录スキーマ分布", dict(schema.most_common())),
    ("人手確認true", collections.OrderedDict([
        ("駅数", len(phase)),
        ("レコード数", sum(p["レコード数"] for p in phase)),
        ("引用URLが未登録の駅", [p["file"] for p in phase if p["根拠未登録URL"]]),
        ("内訳", [collections.OrderedDict([
            ("file", p["file"]), ("レコード", p["レコード数"]),
            ("開いたURL", p["開いたURL数"]), ("引用URL", p["引用URL数"])]) for p in phase]),
    ])),
    ("自動抽出層", collections.OrderedDict([
        ("駅数", len(auto)),
        ("検索记录が空のファイル", len(empty_log)),
        ("その一覧", empty_log),
        ("Wikipedia を 引用していながら使用した出典URL に無い駅", wiki_not_listed),
        ("英語欄に日本語を含む駅", en_mix_files),
        ("同 該当フィールド数", en_mix_hits),
        ("15字未満の 简介／原文要旨", short_hits),
    ])),
])
os.makedirs("docs/qa", exist_ok=True)
io.open("docs/qa/curated_audit.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in out.items() if k not in (u"自動抽出層",)}, ensure_ascii=False, indent=1)[:900])
print(u"自動抽出層:", {k: v for k, v in out[u"自動抽出層"].items() if k != u"その一覧"})
