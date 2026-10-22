# -*- coding: utf-8 -*-
"""加精層レコードの出典検証：
 1) JSON 妥当性・重複キー
 2) 引用URL（官网／介绍页／出典／維基词条）が 検索记录.使用した出典URL に存在するか
 3) 「本文を開かず…」に挙げたURLを実際に引用していないか
 4) 英文フィールドへの日本語混入
 5) 简介・出典の空欄や極短文
用法: python3 audit_curated.py <json> [<json> ...]"""
import io, json, re, sys, collections

CJK = re.compile(u"[\u3040-\u30ff\u4e00-\u9fff\uff01-\uff5e\u300c\u300d\u3001]")
SECS = ("观光景点", "名所案内", "公共服务", "交通", "购物", "美食")


def urls_of(v, acc):
    if isinstance(v, dict):
        for x in v.values():
            urls_of(x, acc)
    elif isinstance(v, list):
        for x in v:
            urls_of(x, acc)
    elif isinstance(v, str) and v.startswith("http"):
        acc.append(v.split(u"（")[0].strip().rstrip(u"。"))


for path in sys.argv[1:]:
    raw = io.open(path, encoding="utf-8").read()
    dup = []

    def hook(pairs):
        ks = [k for k, _ in pairs]
        d = [k for k in set(ks) if ks.count(k) > 1]
        if d:
            dup.append(d)
        return collections.OrderedDict(pairs)

    d = json.loads(raw, object_pairs_hook=hook)
    opened = list(d.get(u"検索记录", {}).get(u"使用した出典URL", []))
    print(u"=== %s (%d bytes) ===" % (path, len(raw)))
    print(u"  重複キー:", dup or u"なし")
    print(u"  レコード:", {k: len(d.get(k, [])) for k in SECS if k in d},
          u"合計", sum(len(d.get(k, [])) for k in SECS if k in d))
    print(u"  人手確認:", d.get(u"人手確認"), u"座標:", bool(d.get(u"座標")))

    cited = []
    urls_of({k: v for k, v in d.items() if k != u"検索记录"}, cited)
    missing = [u for u in dict.fromkeys(cited) if u not in opened]
    print(u"  引用URL(重複込):", len(cited), u"→使用した出典URLに無い:", missing or u"なし")
    unused = [u for u in opened if u not in cited]
    print(u"  開いたが未引用:", len(unused), unused[:8])

    notopen = d.get(u"検索记录", {}).get(u"本文を開かずリンクのみで記載したURL", [])
    clash = [x for x in (notopen if isinstance(notopen, list) else []) if x in cited]
    print(u"  『本文を開かず』 listed:", len(notopen), u"そのうち引用済み(要修正):", clash or u"なし")

    bad = []
    short = []
    def walk(v, p):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, p + u"/" + k)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, p + u"[%d]" % i)
        elif isinstance(v, str):
            if p.endswith(u"/英文") and CJK.search(v):
                bad.append((p, v[:80]))
            if p.endswith(u"/简介/日文") and len(v) < 40:
                short.append((p, len(v), v[:60]))
            if p.endswith(u"/原文要旨") and len(v) < 15:
                short.append((p, len(v), v[:60]))
    walk({k: v for k, v in d.items() if k != u"検索记录"}, u"")
    for p, s in bad:
        print(u"  英語欄に日本語:", p, u"|", s)
    for p, n, s in short:
        print(u"  短文:", p, n, u"|", s)
    for sec in SECS:
        for i, r in enumerate(d.get(sec, [])):
            if not r.get(u"出典"):
                print(u"  出典なし:", sec, i, r.get(u"名称", {}).get(u"日文"))
    print(u"  検索记录 keys:", list(d.get(u"検索记录", {}).keys()))
