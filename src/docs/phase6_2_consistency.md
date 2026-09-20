# 第五阶段 · 引擎一致性验证报告

> 本文件由 `python -m scripts.site.check_engine --report <本文件>` **自动生成**，
> 内容就是该次运行的真实输出。重跑即整体覆盖，不存在手工维护的第二份。

- 运行时间：2026-09-13 00:29:16
- 运行环境：Windows-11-10.0.26200-SP0 / AMD64
- Python 3.14.0　Node v24.18.0
- 参照语料：`frontend/data/corpus.json`（60.0 MB；**不发**，仅本机验证用）
- 结论：**一致** —— 14 项检查中 14 项通过

## 这是什么

第五阶段把 `search/` 的检索逻辑移植成了浏览器能跑的 JS（`frontend/engine/`），好让公开站在没有 Python 进程的情况下也能检索。
**Python 原版一行未改**（§18）。因此需要一个证据，说明两份实现在同一份
语料、同一组输入下给出同一个答案 —— 这就是本报告。

对拍方式：两侧各自读**同一份**导出语料，同一组输入各算一遍，逐字段比。
比摘要而不是比全量文本（两侧 JSON 转义规则不同，摘要按码位字节规范化）。

## 判定分级

- **第一级（必须完全一致）**：命中集合、passage_ids、text、n_passages、match_count、total、hit_total、exec_mode、层级与出处字段。这是「同一个答案」的定义，任何一处不同都算失败。
- **第二级（须一致，否则逐条列明）**：score 与排序（bm25 复现的验收项）。bm25 涉及浮点与索引建法，是移植里最可能走偏的一环；它不影响命中集合，只影响同 `match_count` 下的次级排序与输出里的 score 值。

## 检查项

| # | 检查 | 结果 | 内容 |
|---|---|---|---|
| 1 | `dual-text` | 通过 | ① dual_text.simplify：全部 passage 正文（Python vs JS） |
| 2 | `zh` | 通过 | ② zh.js 接线（语料全部单字 + 固定多字样本） |
| 3 | `norm` | 通过 | ③ normalized_text 重算：JS vs 数据库真值（全表） |
| 4 | `dl` | 通过 | ④ dl 推导：JS 现算 vs 数据库影子表（全表） |
| 5 | `search` | 通过 | ⑤ engine.run_search：固定查询集（Python 对 SQLite vs JS 对内存语料） |
| 6 | `entities` | 通过 | ⑥ 实体层：全语料扫描 + 固定样本裁决（Python vs JS） |
| 7 | `question` | 通过 | ⑦ 问题分析 + 检索式扩展：固定问题集（Python vs JS） |
| 8 | `ranking` | 通过 | ⑧ 排序层：固定候选池上的打分与排序（Python vs JS） |
| 9 | `result-block` | 通过 | ⑨ Result Block：组装 + 展开（Python vs JS） |
| 10 | `aggregate` | 通过 | ⑩ 事件聚合：block → 事件 → 跨史书对照（Python vs JS） |
| 11 | `retrieve` | 通过 | ⑪ 提问链路：召回 → 排序 → 聚合（question + query_expansion + ranking + aggregate 端到端） |
| 12 | `static-api` | 通过 | ⑫ 静态分发器：10 条路由 vs 真 API（Python http vs JS 内存语料） |
| 13 | `demo-site` | 通过 | ⑬ 演示数据集：公开站的三种模式能否真的跑通（frontend/data-demo） |
| 14 | `boot` | 通过 | ⑭ boot.js 冒烟：演示模式的横幅与页脚 |

## 逐项输出

### 1. `dual-text`

```text
① dual_text.simplify：全部 passage 正文（Python vs JS）
   rows       py=380,446      js=380,446      OK
   ok         py=380,446      js=380,446      OK
   fallback   py=0            js=0            OK
   changed    py=862,704      js=862,704      OK
   摘要 一致
```

### 2. `zh`

```text
② zh.js 接线（语料全部单字 + 固定多字样本）
   样本 9,508 个（单字 9,485，其中星形 97）
   简→繁（toTraditional）不符 0 / 9,508
   繁→简（toSimplified） 不符 0 / 9,508
```

### 3. `norm`

```text
③ normalized_text 重算：JS vs 数据库真值（全表）
   rows        py=421,178      js=421,178      OK
   nonNull     py=380,197      js=380,197      OK
   files       py=345          js=345          OK
   books       py=7            js=7            OK
   passageIdx  py=380,446      js=380,446      OK
   contiguous  py=1            js=1            OK
   byIdSize    py=421,178      js=421,178      OK
   摘要 一致
```

### 4. `dl`

```text
④ dl 推导：JS 现算 vs 数据库影子表（全表）
   rows      py=421,178      js=421,178      OK
   triZero   py=56,716       js=56,716       OK
   bgZero    py=48,389       js=48,389       OK
   triTotal  py=3,465,681    js=3,465,681    OK
   bgTotal   py=3,793,274    js=3,793,274    OK
   摘要 一致
```

### 5. `search`

```text
⑤ engine.run_search：固定查询集（Python 对 SQLite vs JS 对内存语料）
   '齊桓公' book=None edition=None p=1/20                  mode=fts     total=151     前4=[376124, 376155, 376182, 223565] OK
   '管仲' book=None edition=None p=1/20                   mode=bigram  total=189     前4=[262857, 263083, 263165, 374636] OK
   '齊' book=None edition=None p=1/20                    mode=like    total=8204    前4=[212359, 212464, 214445, 215228] OK
   '之' book=None edition=None p=2/10                    mode=like    total=75766   前4=[212814, 212821, 212840, 212846] OK
   '齊桓公 管仲' book=None edition=None p=1/20               mode=fts     total=10      前4=[261583, 406220, 223548, 77550] OK
   '齊桓公 管' book=None edition=None p=1/20                mode=fts     total=14      前4=[301775, 261583, 406220, 223548] OK
   '秦穆公 百里奚' book=None edition=None p=1/20              mode=fts     total=0       前4=[] OK
   '秦穆公' book=史记 edition=None p=1/20                    mode=fts     total=19      前4=[223637, 263302, 265498, 266113] OK
   '秦穆公' book=史記 edition=None p=1/20                    mode=fts     total=19      前4=[223637, 263302, 265498, 266113] OK
   '秦穆公' book=KR2a0001 edition=None p=1/20              mode=fts     total=19      前4=[223637, 263302, 265498, 266113] OK
   '秦穆公' book=《史記》 edition=None p=1/20                  mode=fts     total=19      前4=[223637, 263302, 265498, 266113] OK
   '秦穆公' book=None edition=sbck p=1/20                  mode=fts     total=5       前4=[7493, 8075, 355049, 1025] OK
   '秦穆公' book=None edition=tls p=1/20                   mode=fts     total=20      前4=[223637, 263302, 265498, 266113] OK
   '城濮之战' book=None edition=None p=1/20                 mode=fts     total=1       前4=[378593] OK
   '城濮' book=None edition=None p=1/20                   mode=bigram  total=35      前4=[380985, 384813, 397334, 404105] OK
   '晉文公重耳' book=None edition=None p=1/20                mode=fts     total=6       前4=[269737, 266371, 267344, 44562] OK
   '齐桓公' book=None edition=None p=1/20                  mode=fts     total=151     前4=[376124, 376155, 376182, 223565] OK
   '國語' book=None edition=None p=1/20                   mode=bigram  total=95      前4=[2, 30268, 341438, 136403] OK
   '董卓' book=None edition=None p=1/20                   mode=bigram  total=157     前4=[16650, 83698, 84559, 85006] OK
   '齊桓公' book=None edition=None p=3/7                   mode=fts     total=151     前4=[223512, 224490, 224569, 224650] OK
   '齊桓公' book=None edition=None p=1/100                 mode=fts     total=151     前4=[376124, 376155, 376182, 223565] OK
   '。管' book=None edition=None p=1/20                   mode=bigram  total=95      前4=[25637, 5406, 264222, 264417] OK
   '管。' book=None edition=None p=1/20                   mode=bigram  total=95      前4=[25637, 5406, 264222, 264417] OK
   '亡\x01' book=None edition=None p=1/20                mode=bigram  total=511     前4=[392010, 115087, 119204, 173611] OK
   '。，' book=None edition=None p=1/20                   mode=bigram  total=0       前4=[] OK
   '(( ' book=None edition=None p=1/20                  mode=bigram  total=0       前4=[] OK
   'KR' book=None edition=None p=1/20                   mode=bigram  total=715     前4=[159849, 130949, 131344, 199278] OK
   '1管' book=None edition=None p=1/20                   mode=bigram  total=0       前4=[] OK
   路径分布：{'fts': 15, 'bigram': 11, 'like': 2}
```

### 6. `entities`

```text
⑥ 实体层：全语料扫描 + 固定样本裁决（Python vs JS）
   样本：正文 24 条、词 20 个、裁决 20 例
   全称 913、短称 451、裸计数 420、已认实体 273
   实体层 一致
```

### 7. `question`

```text
⑦ 问题分析 + 检索式扩展：固定问题集（Python vs JS）
   问题 69 条、检索词组 509 个 —— 一致
```

### 8. `ranking`

```text
⑧ 排序层：固定候选池上的打分与排序（Python vs JS）
   问题 8 条，候选池大小 [366, 299, 740, 599, 740, 35, 751, 925]
   '管仲是怎么死的'                    池   366 首条 score=37.0 detail={'实体:管仲': 14.0, '意图:卒': 8.0, '实体+意图同段': 15.0}
   '秦穆公和百里奚是什么关系'               池   299 首条 score=58.5 detail={'实体:百里奚': 14.0, '2个实体同段': 20.0, '意图:薦': 8.0, "弱意图词['臣']": 1.5, '实体+意图同段': 15.0}
   '重耳流亡了多少年'                   池   740 首条 score=57.0 detail={'问句原词:重耳': 14.0, '意图:居': 8.0, '实体+意图同段': 15.0, '含时长:凡十二年': 20.0}
   '桓公'                         池   599 首条 score=17.0 detail={'实体:桓公': 14.0, '仅实体': 3.0}
   '重耳出亡'                       池   740 首条 score=41.0 detail={'问句原词:重耳': 14.0, '意图:奔': 8.0, '意图词+2': 4.0, '实体+意图同段': 15.0}
   '城濮之战谁赢了'                    池    35 首条 score=5.0 detail={'主题:城濮': 5.0}
   '公曰'                         池   751 首条 score=5.0 detail={'主题:公曰': 5.0}
   '齊桓公与管仲是什么关系'                池   925 首条 score=46.5 detail={'实体:齊桓公': 14.0, '实体:管仲': 9.5, '2个实体同段': 20.0, '仅实体': 3.0}
   排序层 一致
```

### 9. `result-block`

```text
⑨ Result Block：组装 + 展开（Python vs JS）
   '齊桓公' book=None ed=None p=1/5 standard/orig                命中 151    片段 126   fts     OK
   '齊桓公' book=None ed=None p=1/5 short/orig                   命中 151    片段 138   fts     OK
   '齊桓公' book=None ed=None p=1/5 long/orig                    命中 151    片段 115   fts     OK
   '齊桓公' book=None ed=None p=2/5 standard/orig                命中 151    片段 126   fts     OK
   '齊桓公' book=None ed=None p=1/20 standard/simplified         命中 151    片段 126   fts     OK
   '齊桓公' book=None ed=None p=1/20 standard/both               命中 151    片段 126   fts     OK
   '管仲' book=None ed=None p=1/10 standard/orig                命中 189    片段 141   bigram  OK
   '管仲是怎么死的' book=None ed=None p=1/5 standard/orig            命中 0      片段 0     fts     OK
   '秦穆公 百里奚' book=None ed=None p=1/10 standard/orig           命中 0      片段 0     fts     OK
   '齊' book=None ed=None p=1/3 standard/orig                  命中 8204   片段 5131  like    OK
   '之' book=None ed=None p=1/2 short/orig                     命中 75766  片段 48701 like    OK
   '之' book=None ed=None p=7/100 short/orig                   命中 75766  片段 48701 like    OK
   '元年。' book=None ed=None p=1/5 standard/orig                命中 1069   片段 217   fts     OK
   '元年。' book=None ed=None p=3/5 standard/orig                命中 1069   片段 217   fts     OK
   '大夫' book=None ed=None p=1/5 standard/orig                 命中 3531   片段 2994  bigram  OK
   '王' book=史记 ed=None p=1/5 standard/orig                    命中 8832   片段 1987  like    OK
   '秦始皇本紀' book=None ed=None p=1/5 standard/orig              命中 2      片段 3     fts     OK
   '五帝本紀' book=None ed=None p=1/5 standard/orig               命中 2      片段 3     fts     OK
   '秦本紀' book=None ed=None p=1/5 standard/orig                命中 8      片段 9     fts     OK
   '公' book=None ed=None p=1/3 standard/orig                  命中 17377  片段 9876  like    OK
   '城濮之战' book=None ed=None p=1/5 standard/orig               命中 1      片段 1     fts     OK
   '秦穆公' book=史记 ed=None p=1/5 standard/orig                  命中 19     片段 18    fts     OK
   '秦穆公' book=None ed=sbck p=1/5 standard/orig                命中 5      片段 5     fts     OK
   '董卓' book=None ed=None p=1/5 standard/orig                 命中 157    片段 132   bigram  OK
   '' book=None ed=None p=1/5 standard/orig                   报错 一致
   '齊桓公' book=None ed=None p=0/5 standard/orig                报错 一致
   '齊桓公' book=None ed=None p=1/5 bogus/orig                   报错 一致
   上下文展开 5 例
   pid=2 before/20                                            新增 0    到头部=True 到尾部=None OK
   pid=3 after/20                                             新增 20   到头部=None 到尾部=False OK
   pid=4 both/20                                              新增 22   到头部=True 到尾部=False OK
   pid=5 before/3                                             新增 3    到头部=True 到尾部=None OK
   pid=6 after/100                                            新增 30   到头部=None 到尾部=True OK
   Result Block 一致
```

### 10. `aggregate`

```text
⑩ 事件聚合：block → 事件 → 跨史书对照（Python vs JS）
   '齊桓公' standard/orig                候选  45  事件 18  片段  30  史书 2  首事件 春秋左傳 OK
   '重耳' standard/orig                 候选  45  事件 16  片段  24  史书 2  首事件 春秋左傳 OK
   '管仲' standard/orig                 候选  45  事件 15  片段  24  史书 2  首事件 春秋左傳 OK
   '管仲是怎么死的' standard/orig            候选  45  事件 26  片段  33  史书 6  首事件 春秋左傳 OK
   '秦穆公和百里奚是什么关系' standard/orig       候选  45  事件 32  片段  39  史书 6  首事件 前漢書 OK
   '城濮之战' standard/orig               候选  35  事件 24  片段  34  史书 6  首事件 春秋左傳 OK
   '董卓' standard/orig                 候选  45  事件 10  片段  28  史书 2  首事件 前漢書 OK
   '齊桓公' short/orig                   候选  45  事件 18  片段  36  史书 2  首事件 春秋左傳 OK
   '齊桓公' long/both                    候选  45  事件 18  片段  25  史书 2  首事件 春秋左傳 OK
   事件聚合 一致
```

### 11. `retrieve`

```text
⑪ 提问链路：召回 → 排序 → 聚合（question + query_expansion + ranking + aggregate 端到端）
   '齐桓公是怎么死的？' standard/orig      候选  622/0    事件  29  史书 5  note 1  OK
   '管仲是怎么死的？' standard/orig       候选  366/0    事件  26  史书 6  note 1  OK
   '重耳流亡' standard/orig           候选  740/0    事件  23  史书 5  note 1  OK
   '商鞅变法' standard/orig           候选   82/0    事件  31  史书 4  note 1  OK
   '秦穆公和百里奚' standard/orig        候选  299/0    事件  32  史书 6  note 2  OK
   '管仲和鲍叔牙' standard/orig         候选  393/0    事件  29  史书 6  note 2  OK
   '城濮之战' standard/orig           候选    0/35   事件  24  史书 6  note 2  OK
   '董卓' standard/orig             候选    0/157  事件  10  史书 2  note 2  OK
   '齊桓公' long/both                候选  622/0    事件  18  史书 2  note 1  OK
   '管仲是怎么死的？' short/simplified    候选  366/0    事件  27  史书 6  note 1  OK
   '' standard/orig               候选    0/0    事件   0  史书 0  note 3  OK
   扫描 ['之']                      limit=2000  命中  2000  首/末 6/6805 OK
   扫描 ['不']                      limit=1500  命中  1500  首/末 11/10370 OK
   扫描 ['王', '侯']                 limit=2000  命中  2000  首/末 4/23469 OK
   扫描 ['齊', '楚']                 limit=800   命中   800  首/末 34/35729 OK
   扫描 ['a']                      limit=2000  命中  1861  首/末 214934/419752 OK
   扫描 ['卒', '薨', '崩', '沒']       limit=300   命中   300  首/末 209/22219 OK
   提问链路 一致
```

### 12. `static-api`

```text
⑫ 静态分发器：10 条路由 vs 真 API（Python http vs JS 内存语料）
   /api/stats                                                                     334 B OK
   /api/books                                                                     2552 B OK
   /api/books/KR1b0001/files                                                      34208 B OK
   /api/books/NOSUCHBOOK/files                                                    2 B OK
   /api/files/1                                                                   700 B OK
   /api/files/99999999                                                            错误体一致 OK
   /api/files/322/passages                                                        55877 B OK
   /api/files/322/passages?limit=5&offset=3                                       2930 B OK
   /api/files/322/passages?limit=9999                                             283199 B OK
   /api/files/1/passages?limit=-1&offset=2                                        25042 B OK
   /api/files/322/passages?limit=0                                                53 B OK
   /api/files/322/passages?offset=100000                                          60 B OK
   /api/files/322/passages?kind=passage&layer=main&status=ok                      56042 B OK
   /api/files/322/passages?kind=                                                  55877 B OK
   /api/files/322/passages?q=%E7%8E%8B                                            59740 B OK
   /api/files/322/passages?q=%25                                                  55877 B OK
   /api/files/322/passages?q=_                                                    55877 B OK
   /api/files/322/passages?q=a%2525b                                              51 B OK
   /api/files/99999999/passages                                                   51 B OK
   /api/files/1/raw                                                               6609 B OK
   /api/files/1/raw?start=10&end=20                                               1648 B OK
   /api/files/1/raw?start=999999                                                  176 B OK
   /api/files/1/raw?start=5&end=3                                                 178 B OK
   /api/files/1/raw?start=0&end=2                                                 257 B OK
   /api/files/1/raw?start=abc                                                     6609 B OK
   /api/files/322/raw?start=2&end=4                                               344 B OK
   /api/files/99999999/raw                                                        错误体一致 OK
   /api/passages/2                                                                531 B OK
   /api/passages/212287                                                           755 B OK
   /api/passages/1                                                                508 B OK
   /api/passages/99999999                                                         错误体一致 OK
   /api/passages/2/context                                                        2972 B OK
   /api/passages/2/context?before=0&after=0                                       694 B OK
   /api/passages/2/context?before=10&after=10                                     8492 B OK
   /api/passages/2/context?before=99&after=-5                                     694 B OK
   /api/passages/212287/context                                                   4900 B OK
   /api/passages/1/context                                                        2817 B OK
   /api/passages/99999999/context                                                 错误体一致 OK
   /api/blocks/2                                                                  3381 B OK
   /api/blocks/2?direction=after&count=3                                          699 B OK
   /api/blocks/2?direction=both&count=0                                           386 B OK
   /api/blocks/2?direction=sideways                                               错误体一致 OK
   /api/blocks/212287                                                             错误体一致 OK
   /api/blocks/1                                                                  错误体一致 OK
   /api/blocks/99999999                                                           错误体一致 OK
   /api/search                                                                    错误体一致 OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC                                      74856 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&level=passage                        14146 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&level=passage&page=2&page_size=5     3597 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&level=passage&page=0                 错误体一致 OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&mode=short&text=simplified           32445 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&mode=long&text=both&page_size=3      29765 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&book=KR1b0001                        449 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&edition=tls                          74731 B OK
   /api/search?q=%E5%A4%A7%E5%A4%AB&level=passage                                 13836 B OK
   /api/search?q=%E5%85%83%E5%B9%B4%E3%80%82&level=passage&page_size=3            2254 B OK
   /api/search?q=%E7%8E%8B&level=passage&book=KR1b0001                            14103 B OK
   /api/search?q=%E8%91%A3%E5%8D%93                                               58020 B OK
   /api/search?q=&level=passage                                                   错误体一致 OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&level=bogus                          错误体一致 OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&page_size=abc                        74856 B OK
   /api/search?q=a+b                                                              408 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC%E6%98%AF%E6%80%8E%E4%B9%88%E6%AD%BB% 121611 B OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC%E6%98%AF%E6%80%8E%E4%B9%88%E6%AD%BB% 216059 B OK
   /api/search?q=%E7%AE%A1%E4%BB%B2%E6%98%AF%E6%80%8E%E4%B9%88%E6%AD%BB%E7%9A%84% 78836 B OK
   /api/search?q=%E5%9F%8E%E6%BF%AE%E4%B9%8B%E6%88%98&level=question              103102 B OK
   /api/search?q=%E8%91%A3%E5%8D%93&level=question                                80794 B OK
   /api/search?q=&level=question                                                  错误体一致 OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&level=question&mode=bogus            错误体一致 OK
   /api/search?q=%E9%BD%8A%E6%A1%93%E5%85%AC&level=question&text=bogus            错误体一致 OK
   /api/nope                                                                      错误体一致 OK
   /api/files/1/nope                                                              错误体一致 OK
   72 条路径，72 条一致
```

### 13. `demo-site`

```text
⑬ 演示数据集：公开站的三种模式能否真的跑通（frontend/data-demo）
   ① 10 条路由应答无错（26 条路径） OK
      错误契约：/api/nope → unknown api path OK
   ② stats 计数（2 书 / 4 文件 / 84 记录，实得 2/4/84） OK
      泄漏闸门：/api/stats 不含本机语料库路径 OK
   ③ 检索 text=orig：3 命中 / 1 块 OK
   ③ 检索 text=simplified：3 命中 / 1 块 OK
   ③ 检索 text=both：3 命中 / 1 块 OK
      检索路径 齊（1 字）→ like（实得 like） OK
      检索路径 重耳（2 字）→ bigram（实得 bigram） OK
      检索路径 齊桓公（3 字）→ fts（实得 fts） OK
   ④ Result Block 字段齐备（缺 []；n_passages=5） OK
   ⑤ 提问：实体 ['管仲'] / 意图 ['death'] OK
      提问：聚合出 3 个事件 / 14 条命中 OK
      别名扩展：[['亡', '卒', '卒之岁', '卒之歲', '卒于', '卒於', '夷吾', '崩', '弑', '死', '死于', '死於', '歿', '殁', '殺', '沒', '管仲', '管夷吾', '管子', '終', '薨']] OK
   ⑥ 原文对照：51 行，标注含「·(并入上块)」 OK
      原文对照：标注含「文件头」与 page 层 OK
   ⑦ 待确认注释：演示樣例·乙 pending=14，文件 3 的 kind_layer_counts 含 pending_commentary=True OK
   ⑨ 篇名检索「齊語」：1 块，match_type=['section'] OK
      篇名回填：块的 section 字段非空（齊語） OK
   ⑩ 泄漏闸门：出现的书名 ['演示樣例·乙', '演示樣例·甲'] 全部是演示书 OK
   26 条路径；全部通过
```

### 14. `boot`

```text
⑭ boot.js 冒烟：演示模式的横幅与页脚
   模式判定 static/demo OK
   横幅已显示 OK
   横幅说明本站不含真实史料 OK
   横幅点破误判（搜不到真实人名是正常的） OK
   横幅给出本地部署入口 OK
   不含字面 markdown 星号 OK
   页脚已换成演示声明 OK
```
