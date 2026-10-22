# eki-play — 日本铁路车站周边指南（観光名所 / 名所案内 / 便民公共服务）

**ja · en · zh 三语并列**的车站周边便民数据，基于 `ooep/eki-wiki`（577 条线路 / 16,955 座车站）逐站构建。

## 每站提供什么

| 分区 | 内容 | 主要来源 |
|---|---|---|
| 観光・名所案内 | 观光景点、历史文化设施、观景点、博物馆、公园 | OSM(Japan) POI、日文维基百科站条目 |
| 文化・公共施設 | 图书馆、美术馆、公会堂、体育场 | OSM、维基 |
| 買い物・商店街 | 商店街、超市、便利店 | OSM |
| 便民公共服务 | 警察、邮局、区政府、医院、药局、银行、ATM、观光案内所 | OSM、维基 |
| 交通接続 | 公交站、出租车乘车处、共享单车、换乘车站 | OSM |

每条记录带：**日文名**、**英文名**、**中文名**、类别、距车站的直线距离、**官网链接**、**维基百科链接**、以及原文引用出处。

## 数据来源与授权

- **OpenStreetMap**（日本抽取，Geofabrik `japan-latest.osm.pbf`）— © OpenStreetMap contributors, **ODbL 1.0**。用于 POI 名称、类别、坐标、距离、`name:en`/`name:zh`/`website`/`wikipedia` 标签。
- **维基百科（日文/英文/中文）** — **CC BY-SA 4.0**。用于车站条目「駅周辺」原文、景点正文与链接。
- **Wikidata** — **CC0**。用于官网、多语言标签、实体识别。
- 各设施**官网**由上述来源中的 `website` / 维基外部链接取得，并在入库前做 HTTP 可达性校验（校验结果与抓取日期记录在 `docs/SOURCES.md`）。

> ⚠️ 重要说明：本仓库为**自动化数据管线**产出，非人工撰写旅游指南。数据覆盖度与翻译完成度**逐站不同**，一律以每条记录的 `出典`／`sources` 字段为准。未翻译字段保留 `null`，不做机器填充。详见 `docs/COVERAGE.md`。

## 目录结构

```
data/lines/<线路名>.json      每条线路一个文件，内含该线路全部车站的三语周边数据
schema/station.schema.json    数据字段定义
docs/SOURCES.md               来源、抓取时间、方法与校验
docs/COVERAGE.md              覆盖率统计（逐语言 / 逐分区）
```

## 使用

```python
import json
d = json.load(open('data/lines/山手線.json', encoding='utf-8'))
for st in d['车站']:
    print(st['名称']['日文'], st['位置']['坐标'])
    for item in st['分区']['観光・名所案内']:
        print('  ', item['名称']['日文'], item['距离m'], item.get('官网'))
```
