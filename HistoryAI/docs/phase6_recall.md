# 第六阶段 · 搜索召回基线报告

生成时间：2026-09-13 23:41:47

由 `python tests/recall.py --report docs/phase6_recall.md` 生成，请勿手改。

## 运行环境

- 数据库：`D:\MyCode\HistoryProject\HistoryAI\data\database\history.db`
- 用例集：`tests/search_cases/`（4 个时代文件，360 条）
- 繁简转换：**可用**

繁简转换是否可用是全局开关——脚本 `search/zh.py` 依赖 Windows 的 `LCMapStringEx`，非 Windows 或调用失败会静默返回原文。

## 分类器自检

**通过。** 注入已知故障后，分类器能把「繁体原形有命中却返回 0」判为 `Search`、把「只有转换形才有命中」判为 `Normalization`、把「语料确实没有」判为 `Coverage`，并在繁简回退时如实报 `UNKNOWN`。

这一步是必需的：全绿的跑分报告本身证明不了任何事——一个永远返回 `PASS`/`Coverage` 的分类器看起来一模一样。

## 汇总

| 结果 | 条数 |
|---|---|
| 通过 | 358 |
| 注意 | 2 |
| 失败 | 0 |
| 合计 | 360 |

### 八维分布

| 维度 | 条数 | 含义 |
|---|---|---|
| `Coverage` | 37 | 语料里确实没有（检索列 instr 计数为 0） |
| `Pagination` | 2 | 触发组装上限，或末页翻不到、has_more 与实际不符 |

## 逐条结果

| id | 分组 | 查询 | 转换后 | 参数 | 语料段 | 命中段 | 结果块 | 命中形态 | 路径 | 机械审计 | 出处 | 维度 | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nb-person-01 | person | 檀道濟 | 檀道濟 | 默认 | 103 | 103 | 73 | text | fts | Passage:通过、Provenance:通过 | 南史、宋書、魏書 |  | PASS |
| nb-person-02 | person | 沈約 | 沈約 | 默认 | 226 | 226 | 184 | text | bigram | Passage:通过、Provenance:通过 | 南史、南齊書、宋書… |  | PASS |
| nb-person-03 | person | 王琳 | 王琳 | 默认 | 262 | 262 | 155 | text | bigram | Passage:通过、Provenance:通过 | 南史、周書、陳書 |  | PASS |
| nb-person-04 | person | 崔浩 | 崔浩 | 默认 | 135 | 135 | 114 | text | bigram | Passage:通过、Provenance:通过 | 北史、魏書 |  | PASS |
| nb-person-05 | person | 楊愔 | 楊愔 | 默认 | 87 | 87 | 57 | text | bigram | Passage:通过、Provenance:通过 | 北史、北齊書、魏書 |  | PASS |
| nb-person-06 | person | 蘇綽 | 蘇綽 | 默认 | 22 | 22 | 19 | text | bigram | Passage:通过、Provenance:通过 | 北史、周書、隋書 |  | PASS |
| nb-person-07 | person | 楊素 | 楊素 | 默认 | 121 | 121 | 85 | text | bigram | Passage:通过、Provenance:通过 | 北史、周書、隋書 |  | PASS |
| nb-person-08 | person | 宇文泰 | 宇文泰 | 默认 | 25 | 25 | 13 | text | fts | Passage:通过、Provenance:通过 | 北史、南史、周書… |  | PASS |
| nb-person-09 | person | 蕭子顯 | 蕭子顯 | 默认 | 17 | 17 | 16 | text | fts | Passage:通过、Provenance:通过 | 南史、南齊書、晉書… |  | PASS |
| nb-person-10 | person | 侯景 | 侯景 | 默认 | 899 | 899 | 556 | text | bigram | Passage:通过、Provenance:通过 | 北史、北齊書、南史… |  | PASS |
| nb-person-11 | person | 祖沖之 | 祖沖之 | 默认 | 21 | 21 | 17 | text | fts | Passage:通过、Provenance:通过 | 南史、南齊書、宋書… |  | PASS |
| nb-person-12 | person | 斛律光 | 斛律光 | 默认 | 43 | 43 | 29 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書 |  | PASS |
| nb-person-13 | person | 高熲 | 高熲 | 默认 | 13 | 13 | 11 | text | bigram | Passage:通过、Provenance:通过 | 北史、周書、隋書 |  | PASS |
| nb-person-14 | person | 韋孝寛 | 韋孝寛 | 默认 | 64 | 64 | 59 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書、周書… |  | PASS |
| nb-person-15 | person | 陳霸先 | 陳霸先 | 默认 | 21 | 21 | 15 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書、南史… |  | PASS |
| nb-person-16 | person | 髙熲 | 髙熲 | 默认 | 84 | 84 | 51 | text | bigram | Passage:通过、Provenance:通过 | 北史、周書、隋書 |  | PASS |
| nb-alias-01 | alias | 宋武帝 | 宋武帝 | 默认 | 129 | 129 | 116 | text | fts | Passage:通过、Provenance:通过 | 南史、宋書、隋書 |  | PASS |
| nb-alias-02 | alias | 梁武帝 | 梁武帝 | 默认 | 241 | 241 | 209 | text | fts | Passage:通过、Provenance:通过 | 南史、周書、陳書… |  | PASS |
| nb-alias-03 | alias | 陳武帝 | 陳武帝 | 默认 | 103 | 103 | 83 | text | fts | Passage:通过、Provenance:通过 | 北齊書、南史 |  | PASS |
| nb-alias-04 | alias | 齊神武 | 齊神武 | 默认 | 202 | 202 | 115 | text | fts | Passage:通过、Provenance:通过 | 北史、周書 |  | PASS |
| nb-alias-05 | alias | 隋文帝 | 隋文帝 | 默认 | 137 | 137 | 82 | text | fts | Passage:通过、Provenance:通过 | 北史、南史、周書 |  | PASS |
| nb-alias-06 | alias | 周文帝 | 周文帝 | 默认 | 49 | 49 | 43 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書 |  | PASS |
| nb-alias-07 | alias | 梁元帝 | 梁元帝 | 默认 | 151 | 151 | 113 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書、南史… |  | PASS |
| nb-alias-08 | alias | 煬帝 | 煬帝 | 默认 | 137 | 137 | 118 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、周書… |  | PASS |
| nb-alias-09 | alias | 昭明太子 | 昭明太子 | 默认 | 58 | 58 | 50 | text | fts | Passage:通过、Provenance:通过 | 前漢書、南史、梁書… |  | PASS |
| nb-alias-10 | alias | 蘭陵王 | 蘭陵王 | 默认 | 30 | 30 | 19 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書、史記… |  | PASS |
| nb-script-01 | script | 侯景之乱 | 侯景之亂 | 默认 | 117 | 117 | 111 | text | fts | Passage:通过、Provenance:通过 | 南史、梁書、陳書… |  | PASS |
| nb-script-02 | script | 侯景之亂 | 侯景之亂 | 默认 | 117 | 117 | 111 | text | fts | Passage:通过、Provenance:通过 | 南史、梁書、陳書… |  | PASS |
| nb-script-03 | script | 陈庆之 | 陳慶之 | 默认 | 31 | 31 | 28 | text | fts | Passage:通过、Provenance:通过 | 北齊書、南史、周書… |  | PASS |
| nb-script-04 | script | 陳慶之 | 陳慶之 | 默认 | 31 | 31 | 28 | text | fts | Passage:通过、Provenance:通过 | 北齊書、南史、周書… |  | PASS |
| nb-script-05 | script | 高颎 | 高颎 | 默认 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-script-06 | script | 高熲 | 高熲 | 默认 | 13 | 13 | 11 | text | bigram | Passage:通过、Provenance:通过 | 北史、周書、隋書 |  | PASS |
| nb-script-07 | script | 韋孝寛 | 韋孝寛 | 默认 | 64 | 64 | 59 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書、周書… |  | PASS |
| nb-script-08 | script | 韋孝寬 | 韋孝寬 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-script-09 | script | 高祖 | 高祖 | 默认 | 2080 | 2080 | 997 | text | bigram | Passage:通过、Provenance:通过 | 北史、史記、宋書… |  | PASS |
| nb-script-10 | script | 髙祖 | 髙祖 | 默认 | 3492 | 3492 | 1522 | text | bigram | Passage:通过、Provenance:通过 | 北齊書、宋書、梁書… |  | PASS |
| nb-section-01 | section | 宋本紀上第一 | 宋本紀上第一 | recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 南史 |  | PASS |
| nb-section-02 | section | 魏本紀第一 | 魏本紀第一 | recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 北史 |  | PASS |
| nb-section-03 | section | 志第一 | 志第一 | book=隋書、recall=section | 0 | 0 | 2 | section | fts | Passage:通过、Provenance:通过 | 隋書 |  | PASS |
| nb-section-04 | section | 帝紀第一 | 帝紀第一 | book=魏書、recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 魏書 |  | PASS |
| nb-section-05 | section | 帝紀第一 | 帝紀第一 | book=周書、recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 周書 |  | PASS |
| nb-section-06 | section | 列傳第一 | 列傳第一 | book=梁書、recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 梁書 |  | PASS |
| nb-section-07 | section | 本紀第六 | 本紀第六 | book=陳書、recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 陳書 |  | PASS |
| nb-section-08 | section | 本紀第一 | 本紀第一 | book=宋書、recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 宋書 |  | PASS |
| nb-section-09 | section | 夲紀第二 | 夲紀第二 | book=南齊書、recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 南齊書 |  | PASS |
| nb-section-10 | section | 本紀第二 | 本紀第二 | book=南齊書 | 1 | 1 | 1 | text | fts | Passage:通过、Provenance:通过 | 南齊書 |  | PASS |
| nb-single-01 | single | 之 | 之 | 默认 | 158417 | 158417 | 40566 | text | like | — | 三國志、南史、南齊書… | Pagination | WARN：命中 158417 段，触发组装上限，结果不完整 |
| nb-single-02 | single | 帝 | 帝 | 默认 | 34807 | 34807 | 14003 | both、text | like | Passage:通过、Provenance:通过 | 北史、北齊書、南史… |  | PASS |
| nb-single-03 | single | 州 | 州 | 默认 | 30601 | 30601 | 10171 | text | like | Passage:通过、Provenance:通过 | 南齊書、宋書、晉書… |  | PASS |
| nb-single-04 | single | 元年 | 元年 | 默认 | 7673 | 7673 | 4249 | text | bigram | Passage:通过、Provenance:通过 | 史記、宋書 |  | PASS |
| nb-multi-01 | multi | 侯景 王僧辯 | 侯景 王僧辯 | 默认 | 25 | 25 | 24 | text | fts | Passage:通过、Provenance:通过 | 南史、梁書、陳書… |  | PASS |
| nb-multi-02 | multi | 宇文泰 高歡 | 宇文泰 高歡 | 默认 | 1 | 1 | 1 | text | fts | Passage:通过、Provenance:通过 | 魏書 |  | PASS |
| nb-book-01 | book | 崔浩 | 崔浩 | book=魏書 | 96 | 96 | 79 | text | bigram | Passage:通过、Provenance:通过 | 魏書 |  | PASS |
| nb-book-02 | book | 楊愔 | 楊愔 | book=北齊書 | 65 | 65 | 39 | text | bigram | Passage:通过、Provenance:通过 | 北齊書 |  | PASS |
| nb-book-03 | book | 楊素 | 楊素 | book=隋書 | 82 | 82 | 54 | text | bigram | Passage:通过、Provenance:通过 | 隋書 |  | PASS |
| nb-book-04 | book | 宇文泰 | 宇文泰 | book=北史 | 16 | 16 | 6 | text | fts | Passage:通过、Provenance:通过 | 北史 |  | PASS |
| nb-book-05 | book | 侯景 | 侯景 | book=南史 | 295 | 295 | 171 | text | bigram | Passage:通过、Provenance:通过 | 南史 |  | PASS |
| nb-book-06 | book | 侯景 | 侯景 | book=陳書 | 153 | 153 | 98 | text | bigram | Passage:通过、Provenance:通过 | 陳書 |  | PASS |
| nb-book-07 | book | 沈約 | 沈約 | book=梁書 | 60 | 60 | 45 | text | bigram | Passage:通过、Provenance:通过 | 梁書 |  | PASS |
| nb-book-08 | book | 檀道濟 | 檀道濟 | book=宋書 | 49 | 49 | 35 | text | fts | Passage:通过、Provenance:通过 | 宋書 |  | PASS |
| nb-edition-01 | edition | 崔浩 | 崔浩 | edition=WYG | 135 | 135 | 114 | text | bigram | Passage:通过、Provenance:通过 | 北史、魏書 |  | PASS |
| nb-edition-02 | edition | 楊素 | 楊素 | edition=WYG | 121 | 121 | 85 | text | bigram | Passage:通过、Provenance:通过 | 北史、周書、隋書 |  | PASS |
| nb-edition-03 | edition | 崔浩 | 崔浩 | edition=tls | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-place-01 | place | 建康 | 建康 | 默认 | 387 | 387 | 334 | text | bigram | Passage:通过、Provenance:通过 | 南史、南齊書、宋書… |  | PASS |
| nb-place-02 | place | 平城 | 平城 | 默认 | 311 | 311 | 266 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、北史、史記… |  | PASS |
| nb-place-03 | place | 江陵 | 江陵 | 默认 | 925 | 925 | 624 | text | bigram | Passage:通过、Provenance:通过 | 南史、南齊書、周書… |  | PASS |
| nb-place-04 | place | 鄴城 | 鄴城 | 默认 | 109 | 109 | 99 | text | bigram | Passage:通过、Provenance:通过 | 三國志、北史、北齊書… |  | PASS |
| nb-place-05 | place | 鍾離 | 鍾離 | 默认 | 257 | 257 | 199 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、南齊書… |  | PASS |
| nb-place-06 | place | 廣陵 | 廣陵 | 默认 | 774 | 774 | 608 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、北史、南史… |  | PASS |
| nb-war-01 | war | 玉壁 | 玉壁 | 默认 | 54 | 54 | 43 | text | bigram | Passage:通过、Provenance:通过 | 北齊書、周書、魏書 |  | PASS |
| nb-war-02 | war | 沙苑 | 沙苑 | 默认 | 105 | 105 | 94 | text | bigram | Passage:通过、Provenance:通过 | 北史、北齊書、周書… |  | PASS |
| nb-war-03 | war | 邙山 | 邙山 | 默认 | 85 | 85 | 73 | text | bigram | Passage:通过、Provenance:通过 | 北齊書、周書、陳書… |  | PASS |
| nb-office-01 | office | 尚書令 | 尚書令 | 默认 | 1282 | 1282 | 1052 | text | fts | Passage:通过、Provenance:通过 | 北史、北齊書、南史… |  | PASS |
| nb-office-02 | office | 中書監 | 中書監 | 默认 | 358 | 358 | 304 | text | fts | Passage:通过、Provenance:通过 | 三國志、南史、南齊書… |  | PASS |
| nb-office-03 | office | 領軍將軍 | 領軍將軍 | 默认 | 245 | 245 | 211 | text | fts | Passage:通过、Provenance:通过 | 宋書、晉書、梁書… |  | PASS |
| nb-office-04 | office | 都督中外諸軍事 | 都督中外諸軍事 | 默认 | 88 | 88 | 86 | text | fts | Passage:通过、Provenance:通过 | 三國志、南史、宋書… |  | PASS |
| nb-event-01 | event | 河陰 | 河陰 | 默认 | 29 | 29 | 26 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、北史、周書… |  | PASS |
| nb-event-02 | event | 元嘉 | 元嘉 | 默认 | 1104 | 1104 | 645 | text | bigram | Passage:通过、Provenance:通过 | 宋書 |  | PASS |
| nb-event-03 | event | 開皇 | 開皇 | 默认 | 1171 | 1171 | 1089 | text | bigram | Passage:通过、Provenance:通过 | 北史、隋書 |  | PASS |
| nb-event-04 | event | 大業 | 大業 | 默认 | 920 | 920 | 834 | text | bigram | Passage:通过、Provenance:通过 | 北史、北齊書、南史… |  | PASS |
| nb-page-01 | pagination | 沈約 | 沈約 | page_size=100 | 226 | 226 | 184 | text | bigram | Pagination:通过、Passage:通过、Provenance:通过 | 前漢書、南史、南齊書… |  | PASS |
| nb-page-02 | pagination | 尚書令 | 尚書令 | page_size=100 | 1282 | 1282 | 1052 | text | fts | Pagination:通过、Passage:通过、Provenance:通过 | 前漢書、北史、北齊書… |  | PASS |
| nb-passage-01 | passage | 檀道濟 | 檀道濟 | mode=long | 103 | 103 | 62 | text | fts | Passage:通过、Provenance:通过 | 北史、南史、宋書… |  | PASS |
| nb-passage-02 | passage | 崔浩 | 崔浩 | mode=long | 135 | 135 | 102 | text | bigram | Passage:通过、Provenance:通过 | 北史、魏書 |  | PASS |
| nb-neg-01 | negative | 安祿山 | 安祿山 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-neg-02 | negative | 趙匡胤 | 趙匡胤 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-neg-03 | negative | 岳飛 | 岳飛 | 默认 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-neg-04 | negative | 王安石 | 王安石 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-neg-05 | negative | 朱元璋 | 朱元璋 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-neg-06 | negative | 忽必烈 | 忽必烈 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| nb-neg-07 | negative | 桃園結義 | 桃園結義 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| person-01 | person | 齐桓公 | 齊桓公 | 默认 | 159 | 159 | 133 | text | fts | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| person-02 | person | 管仲 | 管仲 | 默认 | 219 | 219 | 170 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| person-03 | person | 楚庄王 | 楚莊王 | 默认 | 57 | 57 | 47 | text | fts | Passage:通过、Provenance:通过 | 史記 |  | PASS |
| person-04 | person | 郑庄公 | 鄭莊公 | 默认 | 21 | 21 | 19 | text | fts | Passage:通过、Provenance:通过 | 前漢書、北史、史記… |  | PASS |
| person-05 | person | 秦始皇 | 秦始皇 | 默认 | 141 | 141 | 138 | both、text | fts | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| person-06 | person | 晋文公 | 晉文公 | 默认 | 112 | 112 | 90 | text | fts | Passage:通过、Provenance:通过 | 史記、國語、春秋左傳 |  | PASS |
| person-07 | person | 重耳 | 重耳 | 默认 | 238 | 238 | 142 | text | bigram | Passage:通过、Provenance:通过 | 史記、春秋左傳 |  | PASS |
| person-08 | person | 秦穆公 | 秦穆公 | 默认 | 50 | 50 | 48 | text | fts | Passage:通过、Provenance:通过 | 前漢書、史記、國語… |  | PASS |
| person-09 | person | 百里奚 | 百里奚 | 默认 | 29 | 29 | 26 | text | fts | Passage:通过、Provenance:通过 | 三國志、前漢書、南史… |  | PASS |
| person-10 | person | 伍子胥 | 伍子胥 | 默认 | 69 | 69 | 59 | both、text | fts | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| person-11 | person | 孙武 | 孫武 | 默认 | 40 | 40 | 33 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、北齊書、史記… |  | PASS |
| person-12 | person | 孔子 | 孔子 | 默认 | 1585 | 1585 | 1226 | both、text | bigram | Passage:通过、Provenance:通过 | 史記、宋書、晉書 |  | PASS |
| person-13 | person | 墨子 | 墨子 | 默认 | 58 | 58 | 50 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| person-14 | person | 孟子 | 孟子 | 默认 | 227 | 227 | 184 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| person-15 | person | 荀子 | 荀子 | 默认 | 18 | 18 | 17 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南史、宋書… |  | PASS |
| person-16 | person | 商鞅 | 商鞅 | 默认 | 32 | 32 | 31 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| person-17 | person | 苏秦 | 蘇秦 | 默认 | 235 | 235 | 167 | both、text | bigram | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| person-18 | person | 张仪 | 張儀 | 默认 | 357 | 357 | 230 | both、text | bigram | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| place-01 | place | 城濮 | 城濮 | 默认 | 40 | 40 | 38 | text | bigram | Passage:通过、Provenance:通过 | 三國志、史記、國語… |  | PASS |
| place-02 | place | 長勺 | 長勺 | 默认 | 8 | 8 | 7 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、國語、春秋左傳… |  | PASS |
| place-03 | place | 邯鄲 | 邯鄲 | 默认 | 446 | 446 | 352 | text | bigram | Passage:通过、Provenance:通过 | 三國志、史記、後漢書… |  | PASS |
| place-04 | place | 曲沃 | 曲沃 | 默认 | 155 | 155 | 105 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、春秋左傳 |  | PASS |
| place-05 | place | 葵丘 | 葵丘 | 默认 | 29 | 29 | 26 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、國語… |  | PASS |
| place-06 | place | 滎陽 | 滎陽 | 默认 | 517 | 517 | 428 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| place-07 | place | 垓下 | 垓下 | 默认 | 28 | 28 | 24 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書 |  | PASS |
| state-01 | state | 齊國 | 齊國 | 默认 | 199 | 199 | 182 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、北齊書、南史… |  | PASS |
| state-02 | state | 晉國 | 晉國 | 默认 | 200 | 200 | 180 | text | bigram | Passage:通过、Provenance:通过 | 史記、周書、國語… |  | PASS |
| state-03 | state | 楚國 | 楚國 | 默认 | 178 | 178 | 164 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| state-04 | state | 秦國 | 秦國 | 默认 | 53 | 53 | 49 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、戰國策… |  | PASS |
| state-05 | state | 宋國 | 宋國 | 默认 | 53 | 53 | 48 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、南史… |  | PASS |
| state-06 | state | 越國 | 越國 | 默认 | 48 | 48 | 46 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、國語… |  | PASS |
| war-01 | war | 城濮之戰 | 城濮之戰 | 默认 | 1 | 1 | 1 | text | fts | Passage:通过、Provenance:通过 | 春秋左傳 |  | PASS |
| war-02 | war | 鄢陵 | 鄢陵 | 默认 | 93 | 93 | 91 | text | bigram | Passage:通过、Provenance:通过 | 史記、國語、戰國策… |  | PASS |
| war-03 | war | 長平 | 長平 | 默认 | 188 | 188 | 176 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| war-04 | war | 鉅鹿 | 鉅鹿 | 默认 | 312 | 312 | 281 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| war-05 | war | 韓原 | 韓原 | 默认 | 10 | 10 | 10 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、國語… |  | PASS |
| office-01 | office | 丞相 | 丞相 | 默认 | 2348 | 2348 | 1732 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| office-02 | office | 令尹 | 令尹 | 默认 | 222 | 222 | 176 | text | bigram | Passage:通过、Provenance:通过 | 史記、春秋左傳 |  | PASS |
| office-03 | office | 太尉 | 太尉 | 默认 | 2360 | 2360 | 1776 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、史記… |  | PASS |
| office-04 | office | 大夫 | 大夫 | 默认 | 6512 | 6512 | 4901 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、史記、周書… |  | PASS |
| office-05 | office | 將軍 | 將軍 | 默认 | 17625 | 17625 | 6600 | text | bigram | Passage:通过、Provenance:通过 | 史記、宋書、晉書… |  | PASS |
| chapter-01 | chapter | 秦本紀 | 秦本紀 | 默认 | 9 | 9 | 10 | section、text | fts | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| chapter-02 | chapter | 周本紀 | 周本紀 | 默认 | 5 | 5 | 7 | section、text | fts | Passage:通过、Provenance:通过 | 前漢書、北史、史記… |  | PASS |
| chapter-03 | chapter | 晉世家 | 晉世家 | 默认 | 3 | 3 | 4 | section、text | fts | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| chapter-04 | chapter | 十二諸侯年表 | 十二諸侯年表 | 默认 | 2 | 2 | 3 | section、text | fts | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| event-01 | event | 焚書 | 焚書 | 默认 | 22 | 22 | 20 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南史、史記… |  | PASS |
| event-02 | event | 三家分晉 | 三家分晉 | 默认 | 2 | 2 | 2 | text | fts | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| single-01 | single | 尧 | 堯 | 默认 | 1183 | 1183 | 956 | text | like | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| single-02 | single | 舜 | 舜 | 默认 | 1193 | 1193 | 935 | both、text | like | Passage:通过、Provenance:通过 | 三國志、史記、宋書… |  | PASS |
| single-03 | single | 禹 | 禹 | 默认 | 1454 | 1454 | 1156 | both、text | like | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| single-04 | single | 郢 | 郢 | 默认 | 1287 | 1287 | 853 | text | like | Passage:通过、Provenance:通过 | 南史、南齊書、史記… |  | PASS |
| single-05 | single | 絳 | 絳 | 默认 | 715 | 715 | 550 | text | like | Passage:通过、Provenance:通过 | 三國志、南齊書、史記… |  | PASS |
| multi-01 | multi | 齊桓公 管仲 | 齊桓公 管仲 | 默认 | 11 | 11 | 11 | text | fts | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| multi-02 | multi | 蘇秦 張儀 | 蘇秦 張儀 | 默认 | 21 | 21 | 19 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| multi-03 | multi | 秦穆公 百里奚 | 秦穆公 百里奚 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| script-01 | script | 齐桓公 | 齊桓公 | 默认 | 159 | 159 | 133 | text | fts | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| script-02 | script | 齊桓公 | 齊桓公 | 默认 | 159 | 159 | 133 | text | fts | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| script-03 | script | 苏秦 | 蘇秦 | 默认 | 235 | 235 | 167 | both、text | bigram | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| script-04 | script | 蘇秦 | 蘇秦 | 默认 | 235 | 235 | 167 | both、text | bigram | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| script-05 | script | 张仪 | 張儀 | 默认 | 357 | 357 | 230 | both、text | bigram | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| script-06 | script | 張儀 | 張儀 | 默认 | 357 | 357 | 230 | both、text | bigram | Passage:通过、Provenance:通过 | 史記、戰國策 |  | PASS |
| script-07 | script | 郑庄公 | 鄭莊公 | 默认 | 21 | 21 | 19 | text | fts | Passage:通过、Provenance:通过 | 前漢書、北史、史記… |  | PASS |
| script-08 | script | 鄭莊公 | 鄭莊公 | 默认 | 21 | 21 | 19 | text | fts | Passage:通过、Provenance:通过 | 前漢書、北史、史記… |  | PASS |
| script-09 | script | 晋文公 | 晉文公 | 默认 | 112 | 112 | 90 | text | fts | Passage:通过、Provenance:通过 | 史記、國語、春秋左傳 |  | PASS |
| script-10 | script | 晉文公 | 晉文公 | 默认 | 112 | 112 | 90 | text | fts | Passage:通过、Provenance:通过 | 史記、國語、春秋左傳 |  | PASS |
| section-01 | section | 秦始皇本紀 | 秦始皇本紀 | recall=section | 2 | 2 | 3 | section、text | fts | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| section-02 | section | 大禹謨 | 大禹謨 | recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 前漢書、尚書 |  | PASS |
| section-03 | section | 五帝本紀 | 五帝本紀 | recall=section | 2 | 2 | 3 | section、text | fts | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| section-04 | section | 秦本紀 | 秦本紀 | recall=section | 9 | 9 | 10 | section、text | fts | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| section-05 | section | 尚書逸文 | 尚書逸文 | recall=section | 0 | 0 | 11 | section | fts | Passage:通过、Provenance:通过 | 尚書 |  | PASS |
| section-06 | section | 晉語 | 晉語 | recall=section | 8 | 8 | 16 | section、text | bigram | Passage:通过、Provenance:通过 | 前漢書、國語、戰國策 |  | PASS |
| pagination-01 | pagination | 之 | 之 | page_size=100 | 158417 | 158417 | 40566 | text | like | — | 三國志、北史、北齊書… | Pagination | WARN：命中 158417 段，触发组装上限，结果不完整 |
| pagination-02 | pagination | 大夫 | 大夫 | 默认 | 6512 | 6512 | 4901 | text | bigram | Pagination:通过、Passage:通过、Provenance:通过 | 南齊書、史記、周書… |  | PASS |
| pagination-03 | pagination | 齊 | 齊 | 默认 | 14918 | 14918 | 8891 | text | like | Pagination:通过、Passage:通过、Provenance:通过 | 北史、南史、史記… |  | PASS |
| pagination-04 | pagination | 將軍 | 將軍 | 默认 | 17625 | 17625 | 6600 | text | bigram | Pagination:通过、Passage:通过、Provenance:通过 | 史記、宋書、晉書… |  | PASS |
| passage-01 | passage | 齊桓公 | 齊桓公 | mode=long | 159 | 159 | 122 | text | fts | Passage:通过、Provenance:通过 | 史記 |  | PASS |
| passage-02 | passage | 管仲 | 管仲 | mode=long、page_size=50 | 219 | 219 | 162 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| neg-02 | negative | 坑儒 | 坑儒 | book=史記 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| neg-03 | negative | 商鞅變法 | 商鞅變法 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| neg-04 | negative | 秦策一 | 秦策一 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| neg-05 | negative | 燕策 | 燕策 | 默认 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| neg-06 | negative | 重耳 秦穆公 | 重耳 秦穆公 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-person-01 | person | 李斯 | 李斯 | 默认 | 112 | 112 | 90 | both、text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| qh-person-02 | person | 赵高 | 趙高 | 默认 | 99 | 99 | 70 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、魏書 |  | PASS |
| qh-person-03 | person | 项羽 | 項羽 | 默认 | 496 | 496 | 326 | text | bigram | Passage:通过、Provenance:通过 | 南史、史記 |  | PASS |
| qh-person-04 | person | 韩信 | 韓信 | 默认 | 304 | 304 | 246 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| qh-person-05 | person | 萧何 | 蕭何 | 默认 | 204 | 204 | 175 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、北史… |  | PASS |
| qh-person-06 | person | 张良 | 張良 | 默认 | 140 | 140 | 120 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、宋書 |  | PASS |
| qh-person-07 | person | 司马迁 | 司馬遷 | 默认 | 92 | 92 | 93 | text | fts | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| qh-person-08 | person | 班超 | 班超 | 默认 | 31 | 31 | 26 | text | bigram | Passage:通过、Provenance:通过 | 後漢書 |  | PASS |
| qh-person-09 | person | 卫青 | 衛青 | 默认 | 54 | 54 | 49 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| qh-person-10 | person | 霍去病 | 霍去病 | 默认 | 45 | 45 | 45 | text | fts | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| qh-person-11 | person | 张骞 | 張騫 | 默认 | 55 | 55 | 52 | both、text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、隋書 |  | PASS |
| qh-person-12 | person | 苏武 | 蘇武 | 默认 | 45 | 45 | 40 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南史、史記… |  | PASS |
| qh-person-13 | person | 王莽 | 王莽 | 默认 | 629 | 629 | 595 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、宋書、後漢書 |  | PASS |
| qh-person-14 | person | 刘秀 | 劉秀 | 默认 | 47 | 47 | 41 | text | bigram | Passage:通过、Provenance:通过 | 宋書、後漢書、晉書 |  | PASS |
| qh-person-15 | person | 吕后 | 呂后 | 默认 | 186 | 186 | 127 | text | bigram | Passage:通过、Provenance:通过 | 史記、宋書 |  | PASS |
| qh-person-16 | person | 董卓 | 董卓 | 默认 | 254 | 254 | 220 | both、text | bigram | Passage:通过、Provenance:通过 | 三國志、後漢書 |  | PASS |
| qh-alias-01 | alias | 高祖 | 高祖 | 默认 | 2080 | 2080 | 997 | text | bigram | Passage:通过、Provenance:通过 | 北史、史記、宋書… |  | PASS |
| qh-alias-02 | alias | 沛公 | 沛公 | 默认 | 435 | 435 | 222 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| qh-alias-03 | alias | 汉武帝 | 漢武帝 | 默认 | 122 | 122 | 106 | text | fts | Passage:通过、Provenance:通过 | 南齊書、宋書、後漢書… |  | PASS |
| qh-alias-04 | alias | 武帝 | 武帝 | 默认 | 4024 | 4024 | 2450 | text | bigram | Passage:通过、Provenance:通过 | 南史、宋書、晉書 |  | PASS |
| qh-alias-05 | alias | 汉文帝 | 漢文帝 | 默认 | 37 | 37 | 35 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、晉書… |  | PASS |
| qh-alias-06 | alias | 文帝 | 文帝 | 默认 | 2359 | 2359 | 1567 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、史記… |  | PASS |
| qh-alias-07 | alias | 景帝 | 景帝 | 默认 | 493 | 493 | 385 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南齊書、史記 |  | PASS |
| qh-alias-08 | alias | 光武 | 光武 | 默认 | 946 | 946 | 792 | both、text | bigram | Passage:通过、Provenance:通过 | 宋書、後漢書 |  | PASS |
| qh-alias-09 | alias | 元后 | 元后 | 默认 | 101 | 101 | 80 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、北齊書… |  | PASS |
| qh-alias-10 | alias | 吕太后 | 呂太后 | 默认 | 61 | 61 | 51 | both、text | fts | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| qh-place-01 | place | 马邑 | 馬邑 | 默认 | 134 | 134 | 107 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、北齊書、史記… |  | PASS |
| qh-place-02 | place | 河西 | 河西 | 默认 | 390 | 390 | 312 | text | bigram | Passage:通过、Provenance:通过 | 北史、史記、宋書… |  | PASS |
| qh-place-03 | place | 西域 | 西域 | 默认 | 491 | 491 | 388 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、北史、後漢書… |  | PASS |
| qh-war-01 | war | 白登 | 白登 | 默认 | 51 | 51 | 44 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、北史… |  | PASS |
| qh-event-01 | event | 党锢 | 黨錮 | 默认 | 25 | 25 | 25 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、宋書、後漢書 |  | PASS |
| qh-event-02 | event | 黄巾 | 黃巾 | 默认 | 28 | 28 | 26 | text | bigram | Passage:通过、Provenance:通过 | 三國志、後漢書、魏書 |  | PASS |
| qh-event-03 | event | 推恩 | 推恩 | 默认 | 26 | 26 | 25 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南史、史記… |  | PASS |
| qh-office-01 | office | 骠骑将军 | 驃騎將軍 | 默认 | 332 | 332 | 270 | text | fts | Passage:通过、Provenance:通过 | 南史、史記、宋書… |  | PASS |
| qh-office-02 | office | 西域都护 | 西域都護 | 默认 | 7 | 7 | 7 | text | fts | Passage:通过、Provenance:通过 | 前漢書、後漢書 |  | PASS |
| qh-office-03 | office | 侍中 | 侍中 | 默认 | 3757 | 3757 | 2431 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、南齊書… |  | PASS |
| qh-section-01 | section | 光武帝纪第一上 | 光武帝紀第一上 | recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 後漢書 |  | PASS |
| qh-section-02 | section | 艺文志第十 | 藝文志第十 | recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 前漢書 |  | PASS |
| qh-section-03 | section | 地理志第八上 | 地理志第八上 | recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 前漢書 |  | PASS |
| qh-section-04 | section | 班梁列传第三十七 | 班梁列傳第三十七 | recall=section | 0 | 0 | 1 | section | fts | Passage:通过、Provenance:通过 | 後漢書 |  | PASS |
| qh-section-05 | section | 高帝纪第一上 | 高帝紀第一上 | recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 前漢書 |  | PASS |
| qh-section-06 | section | 西周 | 西周 | book=戰國策、recall=section | 73 | 73 | 54 | both、text | bigram | Passage:通过、Provenance:通过 | 戰國策 |  | PASS |
| qh-section-07 | section | 东周 | 東周 | book=戰國策、page_size=100、recall=section | 68 | 68 | 51 | section、text | bigram | Passage:通过、Provenance:通过 | 戰國策 |  | PASS |
| qh-multi-01 | multi | 卫青 霍去病 | 衛青 霍去病 | 默认 | 4 | 4 | 4 | text | fts | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書 |  | PASS |
| qh-multi-02 | multi | 王莽 光武 | 王莽 光武 | 默认 | 13 | 13 | 13 | text | bigram | Passage:通过、Provenance:通过 | 宋書、後漢書、晉書… |  | PASS |
| qh-multi-03 | multi | 苏武 匈奴 | 蘇武 匈奴 | 默认 | 8 | 8 | 8 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、後漢書 |  | PASS |
| qh-multi-04 | multi | 匈奴 西域 | 匈奴 西域 | 默认 | 48 | 48 | 45 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書 |  | PASS |
| qh-multi-05 | multi | 萧何 韩信 | 蕭何 韓信 | 默认 | 6 | 6 | 6 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、後漢書 |  | PASS |
| qh-multi-06 | multi | 高祖 项羽 | 高祖 項羽 | 默认 | 8 | 8 | 8 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、後漢書 |  | PASS |
| qh-script-01 | script | 卫青 | 衛青 | 默认 | 54 | 54 | 49 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| qh-script-02 | script | 衛青 | 衛青 | 默认 | 54 | 54 | 49 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記 |  | PASS |
| qh-script-03 | script | 张骞 | 張騫 | 默认 | 55 | 55 | 52 | both、text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、隋書 |  | PASS |
| qh-script-04 | script | 張騫 | 張騫 | 默认 | 55 | 55 | 52 | both、text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、隋書 |  | PASS |
| qh-script-05 | script | 苏武 | 蘇武 | 默认 | 45 | 45 | 40 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南史、史記… |  | PASS |
| qh-script-06 | script | 蘇武 | 蘇武 | 默认 | 45 | 45 | 40 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南史、史記… |  | PASS |
| qh-script-07 | script | 吕后 | 呂后 | 默认 | 186 | 186 | 127 | text | bigram | Passage:通过、Provenance:通过 | 史記、宋書 |  | PASS |
| qh-script-08 | script | 呂后 | 呂后 | 默认 | 186 | 186 | 127 | text | bigram | Passage:通过、Provenance:通过 | 史記、宋書 |  | PASS |
| qh-script-09 | script | 刘秀 | 劉秀 | 默认 | 47 | 47 | 41 | text | bigram | Passage:通过、Provenance:通过 | 宋書、後漢書、晉書 |  | PASS |
| qh-script-10 | script | 劉秀 | 劉秀 | 默认 | 47 | 47 | 41 | text | bigram | Passage:通过、Provenance:通过 | 宋書、後漢書、晉書 |  | PASS |
| qh-script-11 | script | 党锢 | 黨錮 | 默认 | 25 | 25 | 25 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、宋書、後漢書 |  | PASS |
| qh-script-12 | script | 黨錮 | 黨錮 | 默认 | 25 | 25 | 25 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、宋書、後漢書 |  | PASS |
| qh-single-01 | single | 羌 | 羌 | 默认 | 1398 | 1398 | 990 | text | like | Passage:通过、Provenance:通过 | 三國志、前漢書、北齊書… |  | PASS |
| qh-single-02 | single | 鲜卑 | 鮮卑 | 默认 | 433 | 433 | 316 | text | bigram | Passage:通过、Provenance:通过 | 三國志、宋書、後漢書… |  | PASS |
| qh-pagination-01 | pagination | 汉 | 漢 | 默认 | 15434 | 15434 | 9836 | text | like | Pagination:通过、Passage:通过、Provenance:通过 | 南齊書、史記、宋書… |  | PASS |
| qh-pagination-02 | pagination | 单于 | 單于 | 默认 | 1454 | 1454 | 919 | text | bigram | Pagination:通过、Passage:通过、Provenance:通过 | 前漢書、史記、後漢書 |  | PASS |
| qh-pagination-03 | pagination | 大将军 | 大將軍 | 默认 | 3678 | 3678 | 2214 | text | fts | Pagination:通过、Passage:通过、Provenance:通过 | 北史、史記、周書… |  | PASS |
| qh-passage-01 | passage | 王莽 | 王莽 | mode=long | 629 | 629 | 591 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、宋書、後漢書… |  | PASS |
| qh-passage-02 | passage | 匈奴 | 匈奴 | mode=long、page_size=50 | 2208 | 2208 | 1458 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| qh-neg-01 | negative | 巨鹿 | 巨鹿 | 默认 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-02 | negative | 刘邦 | 劉邦 | book=史記 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-03 | negative | 王政君 | 王政君 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-04 | negative | 文景之治 | 文景之治 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-05 | negative | 党锢之祸 | 黨錮之禍 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-06 | negative | 黄巾之乱 | 黃巾之亂 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-07 | negative | 推恩令 | 推恩令 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-08 | negative | 丝绸之路 | 絲綢之路 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-09 | negative | 河西之战 | 河西之戰 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| qh-neg-10 | negative | 刘邦 项羽 | 劉邦 項羽 | 默认 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-person-01 | person | 诸葛亮 | 諸葛亮 | 默认 | 92 | 92 | 82 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、晉書… |  | PASS |
| sz-person-02 | person | 关羽 | 關羽 | 默认 | 33 | 33 | 29 | text | bigram | Passage:通过、Provenance:通过 | 三國志、宋書、晉書… |  | PASS |
| sz-person-03 | person | 曹操 | 曹操 | 默认 | 186 | 186 | 155 | text | bigram | Passage:通过、Provenance:通过 | 宋書、後漢書 |  | PASS |
| sz-person-04 | person | 周瑜 | 周瑜 | 默认 | 10 | 10 | 10 | text | bigram | Passage:通过、Provenance:通过 | 三國志、周書、宋書… |  | PASS |
| sz-person-05 | person | 司马懿 | 司馬懿 | 默认 | 22 | 22 | 14 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、後漢書… |  | PASS |
| sz-person-06 | person | 王祥 | 王祥 | 默认 | 28 | 28 | 22 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、宋書… |  | PASS |
| sz-person-07 | person | 陶侃 | 陶侃 | 默认 | 34 | 34 | 24 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、宋書、晉書… |  | PASS |
| sz-person-08 | person | 王导 | 王導 | 默认 | 82 | 82 | 67 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、宋書、晉書… |  | PASS |
| sz-person-09 | person | 祖逖 | 祖逖 | 默认 | 11 | 11 | 8 | text | bigram | Passage:通过、Provenance:通过 | 南史、宋書、晉書… |  | PASS |
| sz-person-10 | person | 陆逊 | 陸遜 | 默认 | 13 | 13 | 13 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南齊書、宋書… |  | PASS |
| sz-person-11 | person | 邓艾 | 鄧艾 | 默认 | 65 | 65 | 50 | text | bigram | Passage:通过、Provenance:通过 | 三國志、宋書、晉書… |  | PASS |
| sz-person-12 | person | 羊祜 | 羊祜 | 默认 | 31 | 31 | 29 | text | bigram | Passage:通过、Provenance:通过 | 宋書、晉書、隋書 |  | PASS |
| sz-person-13 | person | 嵇康 | 嵇康 | 默认 | 25 | 25 | 25 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、南齊書… |  | PASS |
| sz-person-14 | person | 谢安 | 謝安 | 默认 | 56 | 56 | 52 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南齊書、史記… |  | PASS |
| sz-person-15 | person | 张辽 | 張遼 | 默认 | 37 | 37 | 28 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南齊書 |  | PASS |
| sz-person-16 | person | 马谡 | 馬謖 | 默认 | 7 | 7 | 7 | text | bigram | Passage:通过、Provenance:通过 | 三國志、宋書、戰國策… |  | PASS |
| sz-person-17 | person | 贾充 | 賈充 | 默认 | 73 | 73 | 61 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、宋書… |  | PASS |
| sz-person-18 | person | 石崇 | 石崇 | 默认 | 13 | 13 | 13 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南齊書、宋書… |  | PASS |
| sz-person-19 | person | 王羲之 | 王羲之 | 默认 | 11 | 11 | 10 | text | fts | Passage:通过、Provenance:通过 | 北史、南史、晉書… |  | PASS |
| sz-person-20 | person | 刘禅 | 劉禪 | 默认 | 33 | 33 | 31 | text | bigram | Passage:通过、Provenance:通过 | 三國志、宋書、晉書 |  | PASS |
| sz-person-21 | person | 刘备 | 劉備 | 默认 | 187 | 187 | 157 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、後漢書… |  | PASS |
| sz-person-22 | person | 孙权 | 孫權 | 默认 | 238 | 238 | 189 | text | bigram | Passage:通过、Provenance:通过 | 三國志、宋書、晉書 |  | PASS |
| sz-person-23 | person | 华佗 | 華佗 | 默认 | 32 | 32 | 29 | text | bigram | Passage:通过、Provenance:通过 | 三國志、後漢書、隋書 |  | PASS |
| sz-person-24 | person | 陈寿 | 陳壽 | 默认 | 7 | 7 | 4 | text | bigram | Passage:通过、Provenance:通过 | 三國志、隋書、魏書 |  | PASS |
| sz-alias-01 | alias | 孔明 | 孔明 | 默认 | 19 | 19 | 19 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、周書、宋書… |  | PASS |
| sz-alias-02 | alias | 雲長 | 雲長 | 默认 | 10 | 10 | 10 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南史、南齊書… |  | PASS |
| sz-alias-03 | alias | 孟德 | 孟德 | 默认 | 1 | 1 | 1 | text | bigram | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-alias-04 | alias | 仲達 | 仲達 | 默认 | 11 | 11 | 11 | text | bigram | Passage:通过、Provenance:通过 | 南史、宋書、後漢書… |  | PASS |
| sz-alias-05 | alias | 玄德 | 玄德 | 默认 | 2 | 2 | 2 | text | bigram | Passage:通过、Provenance:通过 | 尚書、晉書 |  | PASS |
| sz-alias-06 | alias | 太祖 | 太祖 | 默认 | 3963 | 3963 | 1623 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南齊書、周書… |  | PASS |
| sz-alias-07 | alias | 先主 | 先主 | 默认 | 36 | 36 | 29 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、史記… |  | PASS |
| sz-alias-08 | alias | 後主 | 後主 | 默认 | 648 | 648 | 253 | text | bigram | Passage:通过、Provenance:通过 | 南史、陳書 |  | PASS |
| sz-alias-09 | alias | 昭烈 | 昭烈 | 默认 | 36 | 36 | 31 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、宋書… |  | PASS |
| sz-alias-10 | alias | 晋宣帝 | 晉宣帝 | 默认 | 11 | 11 | 11 | text | fts | Passage:通过、Provenance:通过 | 三國志、南史、周書… |  | PASS |
| sz-script-01 | script | 诸葛亮 | 諸葛亮 | 默认 | 92 | 92 | 82 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、晉書… |  | PASS |
| sz-script-02 | script | 諸葛亮 | 諸葛亮 | 默认 | 92 | 92 | 82 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、晉書… |  | PASS |
| sz-script-03 | script | 司马懿 | 司馬懿 | 默认 | 22 | 22 | 14 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、後漢書… |  | PASS |
| sz-script-04 | script | 司馬懿 | 司馬懿 | 默认 | 22 | 22 | 14 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、後漢書… |  | PASS |
| sz-script-05 | script | 张飞 | 張飛 | 默认 | 15 | 15 | 15 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、後漢書 |  | PASS |
| sz-script-06 | script | 張飛 | 張飛 | 默认 | 15 | 15 | 15 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、後漢書 |  | PASS |
| sz-script-07 | script | 华佗 | 華佗 | 默认 | 32 | 32 | 29 | text | bigram | Passage:通过、Provenance:通过 | 三國志、後漢書、隋書 |  | PASS |
| sz-script-08 | script | 華佗 | 華佗 | 默认 | 32 | 32 | 29 | text | bigram | Passage:通过、Provenance:通过 | 三國志、後漢書、隋書 |  | PASS |
| sz-script-09 | script | 陆逊 | 陸遜 | 默认 | 13 | 13 | 13 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南齊書、宋書… |  | PASS |
| sz-script-10 | script | 陸遜 | 陸遜 | 默认 | 13 | 13 | 13 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南齊書、宋書… |  | PASS |
| sz-script-11 | script | 谢安 | 謝安 | 默认 | 56 | 56 | 52 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南齊書、史記… |  | PASS |
| sz-script-12 | script | 謝安 | 謝安 | 默认 | 56 | 56 | 52 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、南齊書、史記… |  | PASS |
| sz-script-13 | script | 荊州 | 荊州 | 默认 | 17 | 17 | 15 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、史記… |  | PASS |
| sz-script-14 | script | 荆州 | 荊州 | 默认 | 17 | 17 | 15 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、史記… |  | PASS |
| sz-section-01 | section | 魏志卷一 | 魏志卷一 | recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-section-02 | section | 魏志卷三十 | 魏志卷三十 | recall=section | 0 | 0 | 2 | section | fts | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-section-03 | section | 魏志卷十 | 魏志卷十 | recall=section | 21 | 21 | 27 | both、section、text | fts | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-section-04 | section | 魏志卷十六 | 魏志卷十六 | recall=section | 2 | 2 | 2 | both、text | fts | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-section-05 | section | 魏志卷二十八 | 魏志卷二十八 | recall=section | 4 | 4 | 5 | both、section、text | fts | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-section-06 | section | 帝紀第一 | 帝紀第一 | book=晉書、recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 晉書 |  | PASS |
| sz-section-07 | section | 志第十六 | 志第十六 | book=晉書、recall=section | 1 | 1 | 2 | section、text | fts | Passage:通过、Provenance:通过 | 晉書 |  | PASS |
| sz-section-08 | section | 列傳第三 | 列傳第三 | book=晉書、recall=section | 11 | 11 | 5 | section、text | fts | Passage:通过、Provenance:通过 | 晉書 |  | PASS |
| sz-single-01 | single | 蜀 | 蜀 | 默认 | 1916 | 1916 | 1357 | text | like | Passage:通过、Provenance:通过 | 三國志、南史、史記… |  | PASS |
| sz-single-02 | single | 吳 | 吳 | 默认 | 4764 | 4764 | 2849 | text | like | Passage:通过、Provenance:通过 | 史記、宋書、春秋左傳… |  | PASS |
| sz-single-03 | single | 魏 | 魏 | 默认 | 12877 | 12877 | 7468 | both、text | like | Passage:通过、Provenance:通过 | 北史、南史、史記… |  | PASS |
| sz-single-04 | single | 晉 | 晉 | 默认 | 12828 | 12828 | 7643 | text | like | Passage:通过、Provenance:通过 | 北史、北齊書、史記… |  | PASS |
| sz-multi-01 | multi | 曹操 袁紹 | 曹操 袁紹 | 默认 | 9 | 9 | 9 | text | bigram | Passage:通过、Provenance:通过 | 後漢書、魏書 |  | PASS |
| sz-multi-02 | multi | 周瑜 赤壁 | 周瑜 赤壁 | 默认 | 2 | 2 | 2 | text | bigram | Passage:通过、Provenance:通过 | 周書、後漢書 |  | PASS |
| sz-multi-03 | multi | 王導 王敦 | 王導 王敦 | 默认 | 2 | 2 | 2 | text | bigram | Passage:通过、Provenance:通过 | 晉書 |  | PASS |
| sz-multi-04 | multi | 諸葛亮 先主 | 諸葛亮 先主 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-multi-05 | multi | 關羽 荊州 | 關羽 荊州 | 默认 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-multi-06 | multi | 諸葛亮 北伐 | 諸葛亮 北伐 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-multi-07 | multi | 司馬懿 公孫淵 | 司馬懿 公孫淵 | book=晉書 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-book-01 | book | 诸葛亮 | 諸葛亮 | book=三國志 | 37 | 37 | 34 | text | fts | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-book-02 | book | 王祥 | 王祥 | book=晉書 | 16 | 16 | 10 | text | bigram | Passage:通过、Provenance:通过 | 晉書 |  | PASS |
| sz-book-03 | book | 关羽 | 關羽 | book=三國志 | 24 | 24 | 21 | text | bigram | Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-book-04 | book | 謝安 | 謝安 | book=晉書 | 17 | 17 | 14 | text | bigram | Passage:通过、Provenance:通过 | 晉書 |  | PASS |
| sz-book-05 | book | 曹操 | 曹操 | book=晉書 | 2 | 2 | 2 | text | bigram | Passage:通过、Provenance:通过 | 晉書 |  | PASS |
| sz-edition-01 | edition | 管仲 | 管仲 | edition=tls | 80 | 80 | 38 | text | bigram | Passage:通过、Provenance:通过 | 史記、春秋左傳 |  | PASS |
| sz-edition-02 | edition | 管仲 | 管仲 | edition=sbck | 22 | 22 | 22 | text | bigram | Passage:通过、Provenance:通过 | 國語、戰國策 |  | PASS |
| sz-edition-03 | edition | 諸葛亮 | 諸葛亮 | edition=sbck | 1 | 1 | 1 | text | fts | Passage:通过、Provenance:通过 | 戰國策 |  | PASS |
| sz-edition-04 | edition | 王祥 | 王祥 | edition=WYG | 28 | 28 | 22 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、宋書… |  | PASS |
| sz-edition-05 | edition | 管仲 | 管仲 | edition=WYG | 219 | 219 | 170 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、後漢書… |  | PASS |
| sz-place-01 | place | 汉中 | 漢中 | 默认 | 572 | 572 | 472 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南齊書、史記… |  | PASS |
| sz-place-02 | place | 许都 | 許都 | 默认 | 13 | 13 | 12 | text | bigram | Passage:通过、Provenance:通过 | 三國志、北齊書、宋書… |  | PASS |
| sz-place-03 | place | 建康 | 建康 | 默认 | 387 | 387 | 334 | text | bigram | Passage:通过、Provenance:通过 | 南史、南齊書、宋書… |  | PASS |
| sz-place-04 | place | 洛阳 | 洛陽 | 默认 | 1271 | 1271 | 1027 | text | bigram | Passage:通过、Provenance:通过 | 北史、北齊書、南史… |  | PASS |
| sz-place-05 | place | 五丈原 | 五丈原 | 默认 | 5 | 5 | 4 | text | fts | Passage:通过、Provenance:通过 | 晉書、隋書、魏書 |  | PASS |
| sz-place-06 | place | 合肥 | 合肥 | 默认 | 169 | 169 | 128 | text | bigram | Passage:通过、Provenance:通过 | 三國志、南史、南齊書… |  | PASS |
| sz-place-07 | place | 成都 | 成都 | 默认 | 330 | 330 | 252 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、南齊書… |  | PASS |
| sz-place-08 | place | 街亭 | 街亭 | 默认 | 4 | 4 | 4 | text | bigram | Passage:通过、Provenance:通过 | 三國志、隋書 |  | PASS |
| sz-war-01 | war | 官渡 | 官渡 | 默认 | 67 | 67 | 51 | text | bigram | Passage:通过、Provenance:通过 | 三國志、宋書、後漢書… |  | PASS |
| sz-war-02 | war | 赤壁 | 赤壁 | 默认 | 14 | 14 | 14 | text | bigram | Passage:通过、Provenance:通过 | 三國志、周書、後漢書… |  | PASS |
| sz-war-03 | war | 淝水 | 淝水 | 默认 | 7 | 7 | 7 | text | bigram | Passage:通过、Provenance:通过 | 北齊書、宋書、晉書 |  | PASS |
| sz-office-01 | office | 丞相 | 丞相 | 默认 | 2348 | 2348 | 1732 | text | bigram | Passage:通过、Provenance:通过 | 前漢書、史記、宋書… |  | PASS |
| sz-office-02 | office | 都督 | 都督 | 默认 | 3321 | 3321 | 1732 | text | bigram | Passage:通过、Provenance:通过 | 北齊書、南史、南齊書… |  | PASS |
| sz-office-03 | office | 侍中 | 侍中 | 默认 | 3757 | 3757 | 2431 | text | bigram | Passage:通过、Provenance:通过 | 北史、南史、南齊書… |  | PASS |
| sz-office-04 | office | 录尚书事 | 錄尚書事 | 默认 | 162 | 162 | 132 | text | fts | Passage:通过、Provenance:通过 | 三國志、北史、南史… |  | PASS |
| sz-event-01 | event | 永嘉 | 永嘉 | 默认 | 391 | 391 | 336 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、宋書、後漢書… |  | PASS |
| sz-event-02 | event | 八王 | 八王 | 默认 | 19 | 19 | 20 | section、text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、北史… |  | PASS |
| sz-event-03 | event | 建安 | 建安 | 默认 | 814 | 814 | 701 | text | bigram | Passage:通过、Provenance:通过 | 三國志、前漢書、南史… |  | PASS |
| sz-event-04 | event | 太康 | 太康 | 默认 | 728 | 728 | 357 | text | bigram | Passage:通过、Provenance:通过 | 宋書 |  | PASS |
| sz-page-01 | pagination | 蜀 | 蜀 | page_size=100 | 1916 | 1916 | 1357 | text | like | Pagination:通过、Passage:通过、Provenance:通过 | 三國志、前漢書、南史… |  | PASS |
| sz-page-02 | pagination | 将军 | 將軍 | book=三國志 | 818 | 818 | 417 | text | bigram | Pagination:通过、Passage:通过、Provenance:通过 | 三國志 |  | PASS |
| sz-page-03 | pagination | 王導 | 王導 | page_size=10 | 82 | 82 | 67 | text | bigram | Pagination:通过、Passage:通过、Provenance:通过 | 宋書、晉書、魏書 |  | PASS |
| sz-passage-01 | passage | 諸葛亮 | 諸葛亮 | mode=long | 92 | 92 | 80 | text | fts | Passage:通过、Provenance:通过 | 三國志、宋書、晉書… |  | PASS |
| sz-passage-02 | passage | 曹操 | 曹操 | mode=long | 186 | 186 | 155 | text | bigram | Passage:通过、Provenance:通过 | 宋書、後漢書 |  | PASS |
| sz-passage-03 | passage | 王導 | 王導 | mode=long | 82 | 82 | 55 | text | bigram | Passage:通过、Provenance:通过 | 南齊書、宋書、晉書… |  | PASS |
| sz-neg-01 | negative | 安祿山 | 安祿山 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-neg-02 | negative | 三顧茅廬 | 三顧茅廬 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-neg-03 | negative | 桃園結義 | 桃園結義 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-neg-04 | negative | 魏志卷三十一 | 魏志卷三十一 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-neg-05 | negative | 赤壁之戰 | 赤壁之戰 | 默认 | 0 | 0 | 0 | — | fts | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-neg-06 | negative | 姜維 陳壽 | 姜維 陳壽 | 默认 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |
| sz-neg-07 | negative | 臥龍 | 臥龍 | book=晉書 | 0 | 0 | 0 | — | bigram | — | — | Coverage | PASS：预期为空，归因 Coverage 相符 |

## 0 命中清单（48 条）

每条都写明归因。`Coverage` 是语料问题（该扩语料），`Search` 才是引擎问题（该改代码）——两者绝不能混。

| id | 查询 | 语料段 | 归因 | 说明 |
|---|---|---|---|---|
| nb-script-05 | 高颎 | 0 | Coverage | **已知收窄（B 向，待用户决定是否修）**：简体「颎」（U+988E）→ 转换器不动它，于是查询串是「高颎」，而语料写的是「髙熲」（U+9AD9+U+71B2，主形，84 段）与「高熲」（13 段）→ 用户拿 0。这不是缺书（隋書卷四十一本传在库内），是**字没转过去**。同源问题全库 131 个字符，见文件头 note 第 5 条；对照 nb-script-06 与 nb-person-13/16 |
| nb-script-08 | 韋孝寬 | 0 | Coverage | **已知收窄（异体字，待用户决定是否修）**：港台通行繁体写「寬」（U+5BEC），而语料（WYG，日韩字形）写「寛」（U+5BDB）64 段 → 查「韋孝寬」全库 0 段。简体输入「韦孝宽」同样 0（宽→寬 转得对，但语料里没有 寬）。不是缺书：周書卷三十一本传在库内，见 nb-person-14 |
| nb-section-02 | 魏本紀第一 | 0 | - | **纯篇名命中**：hit_total=0（正文里没有「魏本紀第一」这个串），一块 match_type=section（北史魏本紀的正文首行「魏之先岀自黄帝軒轅氏…」）。这条专门证明「命中段数=0 不等于没结果」，所以不设 top_needle（篇题在 label 字段里，不在块 text 里） |
| nb-section-03 | 志第一 | 0 | - | 隋書篇名（志第一 = 禮儀一）：2 块都是 section —— 一块在 KR2a0023_000（目録，label 取自目録里的「禮儀一」），一块在 _006（正文）。**隋書 136 个 section 多于 50 个文件**就是这种「一文件多志」的体例 |
| nb-section-05 | 帝紀第一 | 0 | - | 周書篇名（帝紀第一 = 文帝上）：纯篇名命中，正文首行「文帝上¶太祖文皇帝姓宇文氏諱泰…」 |
| nb-section-08 | 本紀第一 | 0 | - | 宋書篇名（本紀第一 = 武帝上）：宋書 107 个 section 全部由 title 模式建立（置信 0.9） |
| nb-section-09 | 夲紀第二 | 0 | - | **语料里的异体字**：「本」写作「夲」（U+5932），南齊書 2/3/6 号文件的篇题就是「夲紀第二」。section 建得起来（label 就是 夲紀第二），且能按原样查到。与 nb-section-10 是一对 |
| nb-edition-03 | 崔浩 | 0 | Coverage | edition=tls 在 19 部语料上必然 0：tls 家族只有 尚書/史記/春秋左傳 三个，崔浩的 135 段一段都不在其中，空得合理 —— 期望归因 **Coverage**。写这条时归因器还只看全库计数（135）而看不见 edition 参数，会误判成 Search，所以当初故意不设期望值；第六点三阶段把 `book`/`edition` 传进 `diagnose.corpus_count` / `classify_empty` 之后（同一处修复也治好了 4 条被扩容证伪后收窄到单书的负例），地面真值与检索范围一致了，这条也就有了确定的期望值，并在 `tests/recall.py --selftest` 里由 ③b 守着不再回退。 |
| nb-neg-01 | 安祿山 | 0 | Coverage | 负例标杆（与 sz-neg-01 同一个词，这里是**扩容后**的复核）：南北朝 10 部 + 隋書（止于 618）都写不到他，全库 0 段；对应的舊唐書/新唐書在 catalog 里是 planned。这是「书未收录」这一类 Coverage 的标准探针 |
| nb-neg-02 | 趙匡胤 | 0 | Coverage | 宋史（planned，尚未下载）的人物，全库 0 段。归因应当是 Coverage 且能报到《宋史》这本书 —— 6.3-H 诊断链要区分「书未收录」与「文本确无此串」 |
| nb-neg-03 | 岳飛 | 0 | Coverage | 宋史（planned）人物，全库 0 段 |
| nb-neg-04 | 王安石 | 0 | Coverage | 宋史（planned）人物，全库 0 段。**注意别与 貞觀 混用**：实测「貞觀」在 19 部里有 41 段（梁書7/後漢書5/宋書5/隋書4/魏書3/陳書3…）——唐代年号会经唐人所修的注/考證漏进南北朝各书，所以它**不能**当负例 |
| nb-neg-05 | 朱元璋 | 0 | Coverage | 明史（planned）人物，全库 0 段 |
| nb-neg-06 | 忽必烈 | 0 | Coverage | 元史（planned）人物，全库 0 段 |
| nb-neg-07 | 桃園結義 | 0 | Coverage | 演义（三國演義）情节，任何正史都没有，全库 0 段 —— 这条证明负例归因不看「像是史书里的词」，只看语料实际有没有 |
| multi-03 | 秦穆公 百里奚 | 0 | Coverage | 实测共段 = 0（单独各 25 / 11）。两词分处不同段，AND 语义下必须返回空——预期为空的对照 |
| section-05 | 尚書逸文 | 0 | - | 同一个篇名在 24 个区间上重复（尚書逸文横跨 24 个文件）。实测 11 块——多个区间指向同一条首记录时只产出一块 |
| neg-02 | 坑儒 | 0 | Coverage | **第六点三阶段第二批扩容后本条收窄到史記**：原是全库负例（先秦 5 部时代实测 0 段），扩容后新入库的 宋書/陳書/隋書 各有 1 段「坑儒」（宋書「接秦坑儒之後」、陳書「秦始皇焚書坑儒」、隋書「坑儒士」），全库不再是 0 —— 成语化的说法在南北朝史论里用，在秦汉语料里不用。史記 内仍是 0：史記 写「阬術士」1 段（异体「阬」），另有「焚書」2 段。舊 note 那句「异体字 阬儒 亦为 0」是 6.2 之前的口径，实际 前漢書2/後漢書4 段，一并更正。戰國策 亦 0 段（成书早于前 213 年，本不该有）。 |
| neg-03 | 商鞅變法 | 0 | Coverage | 现代合成词，古籍正文不会以此四字连写；商鞅 单独有 9 段 |
| neg-04 | 秦策一 | 0 | Coverage | 本底本（SBCK 鮑彪校注本）**没有「秦策一」式编次**：戰國策 的篇题粒度为「卷第一~卷第十，每卷一个国别」，12 个国别标题是 東周/西周/秦/齊/楚/趙/魏/韓/燕/宋/衛/中山。第六点二阶段已把国别抽成 sections（正例见 qin_han 的 qh-section-06/07），所以这条负例现在考的是另一件事：**「秦策一」这个写法在本底本不存在**，正文 0 段、篇名 0 条。不是覆盖缺口，是用词与底本不符 |
| neg-05 | 燕策 | 0 | Coverage | 与 neg-04 同一原因：本底本只有国别篇题（燕），没有「燕策」式篇名。正例是 qin_han 的 qh-section-06（西周） |
| neg-06 | 重耳 秦穆公 | 0 | Coverage | 实测共段 = 0（单独各 198 / 25）。多词 AND 语义正确工作，属预期为空的对照 |
| qh-section-01 | 光武帝纪第一上 | 0 | - | 後漢書篇名，正文 0 段。简体输入经转换命中 WYG 底本的篇题行 |
| qh-section-02 | 艺文志第十 | 0 | - | 前漢書篇名，正文 0 段 |
| qh-section-03 | 地理志第八上 | 0 | - | 前漢書篇名；WYG 底本作「地理志第八上」，卷次带「上」 |
| qh-section-04 | 班梁列传第三十七 | 0 | - | 後漢書篇名（班超、梁慬合传），正文 0 段 |
| qh-neg-01 | 巨鹿 | 0 | Coverage | 正字/异体字不通用：語料作 鉅鹿 147 段（正例见 preqin war-04），「巨鹿」0 段。繁简转换不管异体字 |
| qh-neg-02 | 刘邦 | 0 | Coverage | **第六点三阶段第二批扩容后本条收窄到史記**：原是全库负例，扩容后梁書 有 1 段直书「劉邦」（檄文「赤泉未賞劉邦尚曰漢王」），全库不再是 0。史記 内仍 0 段：史記 一律作 高祖 299 段 / 沛公 243 段（正例 qh-alias-01/02，同书对照），检索不做别名映射；前漢書 也是 0 段。 |
| qh-neg-03 | 王政君 | 0 | Coverage | 語料作 元后 44（qh-alias-09），「王政君」0 段；且 元后 还混有尚書的天子义 |
| qh-neg-04 | 文景之治 | 0 | Coverage | 现代合成词，古籍不作四字连写（文帝 515 / 景帝 401 都有） |
| qh-neg-05 | 党锢之祸 | 0 | Coverage | 现代合成词；語料作 黨錮 21（qh-event-01） |
| qh-neg-06 | 黄巾之乱 | 0 | Coverage | 語料 黃巾 仅 1 段（qh-event-02），无「之乱」连写 |
| qh-neg-07 | 推恩令 | 0 | Coverage | 語料作 推恩 10（qh-event-03），无「令」字连写 |
| qh-neg-08 | 丝绸之路 | 0 | Coverage | 现代术语，古籍不作此名（語料有 西域 310 段） |
| qh-neg-09 | 河西之战 | 0 | Coverage | 现代合成词；地名 河西 有 127 段（qh-place-02） |
| qh-neg-10 | 刘邦 项羽 | 0 | Coverage | 两个检索词都按现代写法：劉邦 0 段、項羽 456 段，AND 下必然为 0。改用語料写法（高祖 项羽）共段 8（qh-multi-06） |
| sz-section-02 | 魏志卷三十 | 0 | - | **纯篇名命中**：hit_total=0（正文里没有「魏志卷三十」这个串），两块都是 match_type=section 的卷端（陈寿撰/裴松之注署名行）。这条专门证明「命中段数=0 不等于没结果」——所以 recall.py 对 recall:section 的条目看 match_type 而不是 hit_total。也因为没有正文命中，这条不设 top_needle |
| sz-multi-04 | 諸葛亮 先主 | 0 | Coverage | 负例：语料里**没有一段**同时写着这两词（蜀書里的君臣同段不在库内）。与 sz-multi-01/02/03 对照：AND 检索在共现为 0 时如实返回空。旁边有 sz-alias-07（先主 29 段）与 sz-person-01（諸葛亮 59 段）证明两个词各自都有命中 —— 空是**共现**的空，不是词的空 |
| sz-multi-05 | 關羽 荊州 | 0 | Coverage | 负例：繁体写法的「荊州」只有 5 段（全在尚書/史記的九州名），与關羽无共现。**注意这条只对 U+834A 成立**：换成 sz-script-14 的 U+8346「荆州」，语料里有关羽+荆州同段 2 段，而引擎同样返回 0 —— 那是转换造成的漏召回，不是语料没有 |
| sz-multi-06 | 諸葛亮 北伐 | 0 | Coverage | 负例：语料里没有一段同时含「諸葛亮」与「北伐」—— 现代合成词「北伐」是史书里没有的说法（史书作「北征」「伐魏」）。与 sz-person-01 对照 |
| sz-multi-07 | 司馬懿 公孫淵 | 0 | Coverage | **第六点三阶段第二批扩容后本条收窄到晉書**：原是全库负例（9 部时代共段 0），扩容后宋書天文志 有 3 段同段共现（「司馬懿與相持」「司馬懿圍公孫淵於襄平」），全库不再是 0。晉書 内仍 0：司马懿平公孙渊是帝紀第一的大事，行文写「帝」而不直书「司馬懿」（晉書「司馬懿」仅 1 段、「公孫淵」0 段），这类空最容易误判成引擎坏了 —— 它其实是**称谓差异**（sz-person-05/alias-04 同源现象）。 |
| sz-neg-01 | 安祿山 | 0 | Coverage | 负例标杆：安祿山只存在于舊唐書/新唐書（尚未收录）。第二批要加的南北朝 10 部与隋書（止于 618 年）都写不到他，所以这个名字能一直用作「语料确实没有」的探针 —— 它同时是 tests/test_phase4_questions.py 的探针词（那里也有一条断言钉住它没入库） |
| sz-neg-02 | 三顧茅廬 | 0 | Coverage | 负例：**成语**而非史书原文 —— 三顾茅庐的故事出自蜀書諸葛亮傳（「凡三往乃見」），该传不在库内，而成语本身史书不用。与 sz-person-01 对照：诸#葛亮本人有 59 段命中 |
| sz-neg-03 | 桃園結義 | 0 | Coverage | 负例：**演义/民间叙事**的说法，正史里沒有桃园结义这一幕。与 sz-person-21（劉備 169 段）、sz-script-05（張飛 13 段）指向同一个「书里有这些人、但没这个说法」的分辨 |
| sz-neg-04 | 魏志卷三十一 | 0 | Coverage | 负例：**截断边界的探针** —— 三國志目錄里卷三十一是蜀書第一（劉焉/劉璋），但转录止于魏書卷三十，所以库里没有。这条用例随上游补齐会**自动变红**，正好提醒改（不是删） |
| sz-neg-05 | 赤壁之戰 | 0 | Coverage | 负例：现代合成词「赤壁之战」；史书写「赤壁」而不用「之戰」。与 sz-war-02（赤壁 10 段）精确对照 —— 同一件事，加了「之戰」就查不到 |
| sz-neg-06 | 姜維 陳壽 | 0 | Coverage | 负例：两个词各自都在库里（姜維 42 段、陳壽 5 段），但**没有一段同时写着两者** —— 空的归因是 Coverage（共现为 0），不是引擎坏了。这类负例证明 AND 检索不会因为两词都热门就乱返回 |
| sz-neg-07 | 臥龍 | 0 | Coverage | **第六点三阶段第二批扩容后本条收窄到晉書**：原是全库负例（U+81E5「臥龍」9 部时代 0 段），扩容后梁書 有 1 段（「豈無臥龍之臣乎」），全库不再是 0；晉書 内 0 段。但同一本书里另有**另一种字形**：晉書 的「卧龍」（U+5367）有 1 段 —— 语料用字不统一（不同转录批次的异体字取向不同），而繁简转换器把「卧」「臥」都归到 U+81E5，于是语料里写 U+5367 的那 3 段（梁書1/晉書1/南史1）**两种写法都查不到**（实测：查 臥龍=1 段、查 卧龍 也是那同一段），写法差一个码位就丢。与 sz-script-14（荆州/荊州）同源，属本阶段「C 向字形收窄」已知缺口。 |

## 繁简一致性

简体输入与繁体直输必须给出相同的命中数，否则说明转换环节有问题。

| 简体 | 繁体 | 简体命中 | 繁体命中 | 一致 |
|---|---|---|---|---|
| 侯景之乱 | 侯景之亂 | 117 | 117 | 是 |
| 陈庆之 | 陳慶之 | 31 | 31 | 是 |
| 齐桓公 | 齊桓公 | 159 | 159 | 是 |
| 苏秦 | 蘇秦 | 235 | 235 | 是 |
| 张仪 | 張儀 | 357 | 357 | 是 |
| 郑庄公 | 鄭莊公 | 21 | 21 | 是 |
| 晋文公 | 晉文公 | 112 | 112 | 是 |
| 卫青 | 衛青 | 54 | 54 | 是 |
| 张骞 | 張騫 | 55 | 55 | 是 |
| 苏武 | 蘇武 | 45 | 45 | 是 |
| 吕后 | 呂后 | 186 | 186 | 是 |
| 刘秀 | 劉秀 | 47 | 47 | 是 |
| 党锢 | 黨錮 | 25 | 25 | 是 |
| 诸葛亮 | 諸葛亮 | 92 | 92 | 是 |
| 司马懿 | 司馬懿 | 22 | 22 | 是 |
| 张飞 | 張飛 | 15 | 15 | 是 |
| 华佗 | 華佗 | 32 | 32 | 是 |
| 陆逊 | 陸遜 | 13 | 13 | 是 |
| 谢安 | 謝安 | 56 | 56 | 是 |
| 荆州 | 荊州 | 17 | 17 | 是 |

## UNKNOWN 清单（0 条）

无。分类器对所有 0 命中都给出了归因。

## 基线漂移（0 条）

`search_cases/` 里的 `baseline_hits` / `baseline_blocks` 是 2026-09-12 在 7 部语料上的实测值（第六点二阶段扩容后重测）。漂移本身不是失败——语料扩充后必然变化——但每条都要能解释。

无漂移，与建立基线时完全一致。
