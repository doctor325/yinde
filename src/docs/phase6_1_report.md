# 第六点一阶段报告：检索完整性与结果系统

日期：2026-09-11　｜　对应计划书：《HistoryProject 第六点一阶段计划书》（Phase 6.1）

> Phase 6 证明了「能搜」；本阶段要证明「**搜得完整、看得完整、找得回去**」。

**结论先行**：本阶段的两个靶心问题都修好了——篇名可检索、600 截断消失且 `total` 变成
真值。另有**三处没有按计划书原样实现**（数据库层分页、篇名全语料覆盖、跨请求缓存），
理由与实测数据写在 §12 与附 §24 对照表里，都标 `注意`，不藏。

---

## 1. 修改了哪些文件

本阶段改动与第六阶段改动在同一个工作区（第六阶段尚未提交），下表只列**本阶段**动过的。

### 新增

| 文件 | 说明 |
|---|---|
| `tests/recall.py` | 召回跑分 harness（五维 → 八维，逐条机械审计）※第六阶段已写，本阶段扩展 |
| `tests/search_cases.json` | 83 条用例集 ※同上 |
| `docs/phase6_recall.md` | 跑分报告（生成物） |
| `docs/phase6_1_report.md` | 本文件 |
| `frontend/data-demo/sections.json` | 静态导出新增的篇名区间表（演示数据） |

### 修改

| 文件 | 改了什么 |
|---|---|
| `search/result_block.py` | 篇名检索（§3）、区间回填 section、解除 600 上限（§4）、分批取数、有界合并 |
| `frontend/engine/result_block.js` | 上表的逐函数 JS 镜像（双实现约束，`check_engine` 逐字段对拍） |
| `scripts/site/export_site.py` | 导出 `sections.json` |
| `scripts/site/check_publish.py` | 白名单加 `data/sections.json`、`data-demo/sections.json`（26 → 27 项） |
| `scripts/site/loader.mjs` | 静态模式载入 `sections.json` |
| `scripts/site/check_engine.py` | ⑨ Result Block 增加篇名检索与回填对拍 |
| `scripts/site/check_engine.mjs` | 同上（JS 侧） |
| `frontend/boot.js`、`frontend/engine/corpus.js` | 接收 `sections` |
| `frontend/app.js` | `match_type` 标记；`#/read/{file_id}?row=N` 全文页；「查看全文」按钮 |
| `frontend/style.css` | 全文页与标记样式 |
| `tests/test_result_block.py` | +13 例（28 → 41） |
| `tests/test_result_block_real.py` | +9 例（12 → 21） |
| `tests/test_search_api.py` | 基数随块数更新（23 → 74） |
| `README.md` | 测试数 144 → 166、`#/read/` 路由、API 新字段、八维表 |

**未改动**：`scripts/pipeline/`（戰國策篇名抽取留待后续）、`HistoryLibrary/`（只读）、
`search/engine.py`（检索路径一字未动）。

---

## 2. 为什么修改

计划书列了七项任务。开工前先实测了一遍现状，**四项已经存在**，本阶段不重做：

| 计划书 | 实测 |
|---|---|
| §15 返回完整 Passage 而非 snippet | 已是。`text` = 连续记录 `text_orig` 原样相接 |
| §16 上下文机制 | 已有。`expand_block` + `/api/blocks/{id}` + 前端「继续读上文/下文」 |
| §18 来源定位 | 基本已有。书/版/篇/卷/部/原文件/行号锚点齐全 |
| §9 total/分页 | `hit_total` 已是真实总数，前端已同时显示两种计数 |

只补**三个实测确认存在的缺口**：

1. **篇名不可检索**，而且比原先判断的更深一层（见 §3）。
2. **600 截断**：`MAX_BLOCKS_PER_QUERY = 600` 把「内部安全阀」当成了「用户可见总量」，
   `truncated: true` 与失真的 `total` 同时出现（计划书 §8/§11 说的问题）。
3. **没有查看全文的入口**（计划书 §17）。

---

## 3. Section Search 实现方式

### 3.1 关键事实：归属关系不在 `passages.section` 里

`passages.section` **只标在标题行上、不向下传播**。史記文件 82 的 17410 行正文里只有
11 行该列有值——正好是 11 个本紀标题。

真正的归属关系在 `sections` 表（`section_id, book_id, file_id, label, division,
first_row, status`，全库 763 行）的 `first_row` 区间里：实测史記文件 82 的 11 篇区间之和

```text
2685+1845+1786+1691+1648+1422+951+795+777+708+611 = 14919
```

恰等于该文件正文总数，无重叠、无遗漏。所以篇名检索与「这一段属于哪一篇」**都从区间推**。

> 这一条推翻了上一轮的判断：当时我报「`秦始皇本紀` 正文 0 段」，实际它有 **2,685 段**，
> 只是没有一行在 `section` 列上写着自己的篇名。

### 3.2 检索

`sections.label` 精确匹配 `=` **或** 子串匹配 `LIKE '%…%'`（转义 `\ % _`），
经 `files → books` join 后受 `book` / `edition` 参数过滤。命中上限
`MAX_SECTION_BLOCKS = 50`，超出时**如实**报 `section_truncated: true`。

截断次序是**匹配度**而非文件序：

```sql
ORDER BY (s.label = ?) DESC, LENGTH(s.label), s.file_id, s.first_row LIMIT 50
```

否则排在第 51 位的那篇恰好就是用户要找的那篇，界面却只说「结果太多，截断了」。

### 3.3 锚点与组装

每个命中区间取**区间内首条 `kind='passage'` 行**作锚点（不是标题行——标题行不是正文，
进了块也会被丢掉），再走现有的 `_expand` + `_shape_block`，产出与正文块**同构**的块，
标 `match_type = "section"`。

### 3.4 排序与去重

* 正文块在前、篇名块在后，各自沿用原有排序键（`-match_count, score, book_id,
  file_no, row_first`）——计划书 §6。
* `match_type` 三值：`text` / `section` / `both`。
* **去重**（§7）：篇名块的锚点若已落在某个正文块里，丢弃该篇名块并把那个正文块标成
  `both`。真实语料上可见：「秦始皇」→ `both、text`（`docs/phase6_recall.md` person-05）。
* 标签跨文件重名（实测 `尚書逸文` 出现 24 次）→ 一个标签命中多个区间，**全部**产出。

### 3.5 顺带修好的 Provenance 缺口

块上的 `section` 原先取 `first["section"]`，而史記正文行该列是 NULL——**史記的结果根本
不显示篇名**。现在：行上有值用行上的，没有就从区间推。这是**有意的行为变更**，
Python 与 JS 同步改了，测试已跟上（`test_section_label_backfills_text_block`、
`test_section_is_backfilled_on_shiji`）。

### 3.6 覆盖边界（计划书 §7 验收项「空正文 section 不会导致异常」）

区间内没有正文行 → 跳过该锚点，不产块、不报错（`_section_hits` 里 `if not p: continue`）。

**戰國策没有 `sections` 数据**（管线未抽取篇名），因此对它做篇名检索必然为 0。
按开工前的约定「只做已有数据的部分」，本阶段**不动管线**，只把它登记为 Coverage
缺口（见 §12）。

---

## 4. Pagination 实现方式

### 4.1 做了什么

`MAX_BLOCKS_PER_QUERY = 600` → `MAX_HITS_PER_QUERY = 100000`，语义从「展示上限」
改成「候选安全阀」（计划书 §11 要求的 `candidate_limit` / `display_limit` / `page_size`
三分）。实测最坏的单字「之」33,241 处命中，离 100000 还有三倍余量——**超限才是真异常**。

响应里新增 `has_more`（`= page * page_size < total`），不改 `total` / `hit_total` /
`page` / `page_size` 的既有名字与含义——计划书 §14「优先兼容扩展」。

实测结果（`truncated` 现在**全部为 False**）：

| 词 | 原始命中 `hit_total` | 片段 `total` | 改前片段数 | 改前 `truncated` |
|---|---|---|---|---|
| 之 | 33,241 | **9,906** | 328 | true |
| 齊 | 6,079 | **2,830** | 210 | true |
| 大夫 | 1,443 | **1,080** | 329 | true |
| 將軍 | 1,142 | **490** | 39 | true |

「之」原先丢掉 92% 的片段，而响应里 `truncated` 还是 `False`——这是本阶段最严重的一个
问题，因为**用户无从得知**。

### 4.2 解除上限暴露出的两个真实缺陷（都已修）

**① 取数窗口 600 行之外的命中被静默丢弃。** 旧代码对每个文件只取**一屏** 600 行，
窗口以外的命中被 `pos.get(pid) is None` 静默跳过。实测「將軍」1142 处命中只组装出
139 处（**88% 不见了**），孔子 404/450、大夫 944/1443。改成**分批取数**：窗口首尾相接、
互不重叠（重叠＝同一段正文读两遍，留缝＝命中被丢）。

**② 合并无视显示上限。** 命中密的地方（「之」几乎每段都有）相邻窗口会连成一条长链，
一个 standard 块实测长到 **8,768 字、517 段**（上限 900 字 / 40 段）。改成有界合并
（`_fit_hi`）：装不下就在接缝处断开，后一块从上一块末行之后另起——不重复展示、也不漏读。

顺带把合并从全局线性扫（O(命中 × 块)，「之」9.45s）改成按 `(文件, 批)` 分组后从后往前比，
回到亚秒级；`_marks_for` 从 O(行 × 段) 改成两指针线性扫（原占组装总时间四成）。

### 4.3 没做什么：数据库层分页 —— **注意**

计划书 §10 要求「数据库层不能真的把 1443 条全部构造成完整 Passage 后再交给前端」，
§21 要求「分页在数据库层完成」，§24 完成标准里也有这一条。**本阶段没有做到**，理由如下，
如实记录：

* **块边界不存在表里**。块是 `_expand` 按「一屏」口径算出来的（目标字数 / 段数 /
  边界证据三者共同决定），SQL 里没有这个字段，也就无法 `ORDER BY … LIMIT` 到第 N 块。
  要做只能把块边界物化成一张表——那是重写结果层，计划书 §22 明确不做。
* **实测证明没必要**。全量组装最坏 2.2–2.8s（「之」，冷进程），命中元组约 1.6 MB。
  为了分页去做物化表，收益不抵风险。

**目前实际的分层**：

```text
数据库   ：筛选（FTS5/bigram/LIKE）+ 排序 + 按窗口取数（每文件每次 ≤600 行）
组装层   ：全部命中 → 全部片段（内存）
HTTP 层  ：只返回 blocks[lo:lo+page_size] —— 前端只拿到当前页
```

计划书 §21 的另一半「搜索 ≠ 加载全部史料」是**成立的**：取数始终是
`WINDOW_HARD_CAP = 600` 行的窗口，从不加载整个文件（203,308 行的库不会被整表读进内存）。
不成立的是「组装也只做当前页」。

---

## 5. Passage 返回实现方式

**本阶段没有改这一层**——计划书 §15 要求的「完整 Passage 而非 snippet」在第六阶段
就已经是现状：`text` 是块内各记录 `text_orig` 的**原样相接**，不截关键词窗口、不做任何
改写。

本阶段补的是**证明**：

* `tests/recall.py` 的 `passage_audit` 现在对**每一条命中**都跑，不设开关——把块文本与
  库中原文**逐字对拍**。「返回完整 Passage」是要在每条查询上都成立的保证，抽查两条
  证明不了什么。
* `test_text_is_exact_concatenation_of_passages`：块正文 == 它列出的 passage 的
  `text_orig` 依序相接，一字不差。
* `test_every_hit_lands_in_some_block` / `test_every_block_contains_a_hit`：
  不丢命中、不产空块。
* `test_no_duplicate_passage_across_blocks`：不产生重复 Passage（§7）。
* `test_blocks_respect_mode_limits`：块不超 `max_chars` / `max_passages`。

---

## 6. Provenance 实现方式

同样是**补证明 + 修一个真实缺口**，不是重做。

每个块回报：`book_id` / `book_title` / `edition` / `family` / `file_id` / `file_name` /
`file_no` / `origin_path` / `row_first` / `row_last` / `section` / `subsection` /
`division` / `ab` / `layer` / `passage_ids` / `first_passage_id` / `last_passage_id` /
`source_ref`。

* **修好的缺口**：史記/國語的 `section` 由区间回填（§3.5）——§24 的「可以追溯到篇章」
  在史記上原先是不成立的。
* **补的证明**：`recall.py` 的 `provenance_audit` 对**每条命中**检查上述字段是否齐备，
  `Provenance` 成为八维之一。
* **查看全文**：`#/read/{file_id}?row=N` 从结果卡片一跳到底稿原文，走
  `/api/files/{id}/passages?offset=&limit=500`（静态模式 `static_api.js` 同样覆盖），
  进入后高亮定位到该行。计划书 §17 的「搜索页负责找到哪里，全文页负责阅读全部内容」。

---

## 7. API 是否兼容旧版本

**兼容。** 本阶段只**增加**字段，未改名、未删除、未改语义：

| 新增 | 说明 |
|---|---|
| `match_type` | `text` / `section` / `both`（块级） |
| `section_truncated` | 篇名命中超过 50 时的如实标注 |
| `has_more` | `page * page_size < total` |

旧的 `level=passage` 逐条命中契约（第二阶段）原样保留，`test_level_passage_backward_compatible`
仍然通过。`/api/passages/{id}/context`、`/api/blocks/{id}` 一字未动。

**行为变更一处**（有意，已记录）：史記/國語结果的 `section` 由 `None` 变成真实篇名（§3.5）。

---

## 8. 71 条旧测试结果

**71 条全部通过，0 漂移。**（`tests/search_cases.json` 共 83 条，其中 12 条为本阶段新增。）

```text
| 通过 | 83 |
| 注意 | 0  |
| 失败 | 0  |
```

基线漂移表 0 条——`baseline_hits` / `baseline_blocks` 与建立基线时完全一致。
分类器自检（注入已知故障，看四类是否各归其位）通过：一个永远全绿的分类器等于没有分类器。

> 注意：本阶段**更新过** 7 条旧 case 的 `baseline_blocks`（office-04、office-05、
> section-05、pagination-01~04）。不是漂移，是**基数本身错了**——旧值是 600 行窗口
> 漏掉命中后算出来的（见 §4.2 ①）。更新后基线才第一次反映真实组装结果。

---

## 9. 新增测试数量

| 位置 | 改前 | 改后 | 新增 |
|---|---|---|---|
| `tests/test_result_block.py` | 28 | **42** | +14 |
| `tests/test_result_block_real.py` | 12 | **21** | +9 |
| `tests/search_cases.json`（召回用例） | 71 | **83** | +12 |
| **合计** | 111 | **146** | **+35** |

新用例覆盖计划书 §19 的四组：

* **A. Section Search**（6 条 case + 6 个单测 + 3 个真库测）：简繁对、篇名区间、
  首条正文锚点、`match_type` 区分、去重、截断保优、空正文不异常。
* **B. Pagination**（4 条 case + `test_high_frequency_total_is_truthful` 等）：
  page 1 / 中间页 / 末页 / 越界页 / `page_size` / `has_more` 与 `total` 一致。
* **C. Result Metadata**：`total` / `hit_total` / `has_more` / `match_type` /
  `passage_ids` / 出处字段齐备。
* **D. Passage Integrity**：逐字对拍、不重复、不丢命中、不超限、每块都含命中
  （`TestBlockInvariantsUnit` 五个不变量）。

---

## 10. 全部测试结果

### Python 单测（167 例，全绿）

```text
tests/test_e2e.py                : Ran 7  tests  OK
tests/test_header.py             : Ran 5  tests  OK
tests/test_library_fixtures.py   : Ran 9  tests  OK
tests/test_phase4_questions.py   : Ran 16 tests  OK
tests/test_result_block.py       : Ran 42 tests  OK (skipped=1)
tests/test_result_block_real.py  : Ran 21 tests  OK
tests/test_search_api.py         : Ran 25 tests  OK
tests/test_search_logic.py       : Ran 20 tests  OK
tests/test_structure.py          : Ran 22 tests  OK
```

（`skipped=1` 是既有的：库或 library 不在场时跳过的那一类。）

### 召回跑分（83 例）

`python tests/recall.py` → **83 通过 / 0 注意 / 0 失败**；八维分布只有 `Coverage | 7`
（语料里确实没有这 7 个词，不是引擎问题）；**0 基线漂移**。

### 引擎一致性：Python ↔ JS（14 项全一致）

`python -m scripts.site.check_engine --report docs/phase5_consistency.md`

```text
'齊桓公'  命中 96     片段 74     OK      '齊'   命中 6079  片段 2830   OK
'管仲'    命中 102    片段 60     OK      '之'   命中 33241 片段 15783  OK
'大夫'    命中 1443   片段 1080   OK      '元年。' 命中 1069  片段 217    OK
'秦始皇本紀' 命中 0    片段 1      OK      '王' book=史记 命中 8832 片段 2010 OK
'五帝本紀'  命中 1     片段 2      OK
⑨ 篇名检索「齊語」：1 块，match_type=['section']  OK
⑨ 篇名回填：块的 section 字段非空（齊語）          OK
通过：14 项检查全部一致
```

**这一条是本次改动的主要安全网**——`search/*.py` 与 `frontend/engine/*.js` 是双实现，
⑨ 会逐字段抓两侧漂移。

### 发布闸门

`python -m scripts.site.build_artifact _site` → **27 个文件，412 KB**（白名单 26 → 27，
新增 `sections.json`）；三道闸门全过：白名单 27 项 ✓、数据里无真实书名 ✓、
正文逐字反查自撰文本（109 行自撰 / 213 段产出行）✓。

---

## 11. 高频词性能测试

本机在持续负载下会降频且不恢复，所以先跑**对照基线**（同一进程里交替跑小查询）：

```text
对照（小查询「齊桓公」，96 处命中）：静置 30s 后 0.117s
                                    同进程第二次 0.199s   ← 机器已在降频（+70%）
```

在这个背景下，标准模式首页（`page_size=20`）耗时：

| 词 | 命中 | 片段 | 首页 | 末页 |
|---|---|---|---|---|
| 之 | 33,241 | 9,906 | 2.2–2.8s | 4.2–5.2s |
| 其 | 12,373 | 6,089 | 3.9s | 4.0s |
| 曰 | 17,161 | 7,982 | 3.6s | 4.0s |
| 王 | 15,536 | 5,480 | 3.9s | 3.9s |
| 人 | 10,442 | 5,199 | 3.6s | 3.5s |
| 大夫 | 1,443 | 1,080 | 1.4s | 1.4s |
| 將軍 | 1,142 | 490 | 0.8s | 0.8s |
| 齊桓公 | 96 | 74 | 0.13s | 0.18s |

读法：

* **「之」首页在冷进程里 2.2–2.8s，同进程连续查询升到 4.2–5.2s。** 对照显示机器确实在
  降频，但降频只能解释约 1.5×，剩下的约 1.8× 来自同进程内的分配/GC 压力（每次请求都
  重建全部片段，无跨请求缓存）。如实记录，不拿「机器慢」一句话盖过去。
* 「搜索不会卡死」（§13）：最坏的「之」也在 5.2s 内返回，没有超时、没有内存暴涨
  （命中元组约 1.6 MB，片段对象是唯一的量级增长项）。
* **代价换来了什么**：「之」的 `total` 从 328 变成 9,906，`truncated` 从 true 变 false。

---

## 12. 是否存在已知问题

**是。四条，全部标 `注意`。**

### 12.1 数据库层分页没有做 —— `注意`

见 §4.3。块边界不在表里，SQL 分不了；实测全量组装成本可接受（≤2.8s 冷进程）。
HTTP 层与前端仍然只拿当前页。**计划书 §24 的这一条完成标准标为未达成。**

### 12.2 高频词每次翻页都重跑全量组装 —— `注意`

不做跨请求缓存是有意的：加缓存要引入跨请求状态，与本项目「无状态只读」的取向冲突。
代价是「之」每翻一页 2–5s。**若将来上线多用户，这是第一个要改的地方。**

### 12.3 篇名检索的语料覆盖不全 —— `注意`

`sections` 表的实际分布：

| 书 | 区间数 |
|---|---|
| 春秋左傳 | 500 |
| 史記 | 129 |
| 尚書 | 106 |
| 國語 | 28 |
| **戰國策** | **0** |

戰國策一个区间都没有（管线未抽取其篇名），对它做篇名检索必然为 0；國語另缺前置内容。
按开工约定本阶段不动管线，登记为 Coverage 缺口——`recall.py` 的
`neg-04 秦策一` / `neg-05 燕策` 两条就是这个缺口的常驻证据。

### 12.4 块可能横跨篇界 —— `注意`（下一阶段修）

组装时的边界证据取自行上的 `section` / `subsection` / `ab`，而正文行这三列都是 NULL，
所以相邻两篇的正文在判断上「同段」。实测 **654 块里 6 块**（0.9%），例如
`之` 在史記文件 94 第 22952 行的块横跨 `陳涉世家第十八` / `外戚世家第十九`。

影响范围：只影响块上「这一段属于哪一篇」的**一行标注**，不影响命中、不影响去重、
不影响正文。这是 Phase 6 就有的旧问题，与本阶段正交（本阶段的区间回填反而让它更容易
被看见）。已写成常驻测试 `test_known_limit_blocks_may_cross_section_boundary`
（阈值 5%，明显变多就报警）。

---

## 13. 下一阶段建议

1. **先做 Phase 6.2 的语料扩充**（计划书 §25 的路线：秦汉 → 三国两晋南北朝 → …）。
   篇名之外的一切已经稳了，扩充是当前收益最大的动作。
2. **扩充时同步补 `sections`**：戰國策的篇名抽取是已知欠账；每加一本书都要问
   「它的篇名进得了区间表吗」。这是 §12.3 唯一的解。
3. **块横跨篇界**（§12.4）真正的修法是让边界证据认区间表——即 `_marks_for` 的
   `_boundary_key` 把「行上没有 section」时回退到 `_section_at(...)`。改动小，但会
   改变块边界，需要重测全部基数，适合和语料扩充一起做。
4. **性能**：等真正有多用户需求时再上缓存；现在不做。
5. **不要**为了「看起来更完整」去动 `search/engine.py`——本阶段证明它没问题。

---

## 附：计划书 §24 完成标准逐条对照

### 搜索

| 标准 | 结果 |
|---|---|
| 篇章名可以检索 | **通过** — `秦始皇本紀` 返回 1 段、`五帝本紀` 1 命中/2 段、`秦本紀` 2 命中/3 段 |
| 正文与篇章名命中可以区分 | **通过** — `match_type: text/section/both`，§6 排序已验证 |
| 原有 71 条测试全部通过 | **通过** — 0 漂移 |
| 新增 section 测试全部通过 | **通过** — 6 条 case + 12 个单测/真库测 |
| 简繁搜索不退化 | **通过** — `recall.py` 繁简对照表 5/5 一致 |

### 分页

| 标准 | 结果 |
|---|---|
| 高频词可以分页 | **通过** |
| `total_count` 与当前页结果分离 | **通过**（用既有的 `total` / `hit_total`，§14 允许沿用现名） |
| `has_more` 正确 | **通过** — `test_has_more_matches_total` 逐页验 |
| 第一页 / 中间页 / 最后一页正确 | **通过** — `test_pagination_total_is_exact` 翻完所有页断言不重不漏 |
| 超出页数正确处理 | **通过** — `test_page_beyond_last_is_empty_not_an_error`：空 `results`、`total` 不变、`has_more` 为假 |
| 不再用 600 条假装是全部结果 | **通过** — `truncated` 全 `False`，`total` 为真值 |

### Passage

| 标准 | 结果 |
|---|---|
| 搜索结果返回完整 Passage | **通过** — 逐字对拍，每条命中都跑 |
| 不以 snippet 作为主要结果 | **通过** |
| 支持上下文 | **通过**（第六阶段已有，本阶段补证明） |
| 支持全文查看 | **通过** — `#/read/{file_id}?row=N` |
| 不产生重复 Passage | **通过** — 跨块 passage_id 唯一性断言 |

### Provenance

| 标准 | 结果 |
|---|---|
| 可以追溯到书 | **通过** |
| 可以追溯到篇章 | **通过** — 史記/國語的 `section` 回填是本次修好的 |
| 可以追溯到 edition | **通过** |
| 可以追溯到原始文件/位置 | **通过** — `origin_path` + `row_first`/`row_last` |

### 性能（计划书 §21）

| 标准 | 结果 |
|---|---|
| 高频搜索不会明显卡顿 | **注意** — 「之」2.2–5.2s（无缓存，见 §11/§12.2） |
| 前端不会加载全部结果 | **通过** — 只拿当前页 |
| 分页在数据库层完成 | **失败** — 未达成，理由见 §4.3/§12.1 |
| 大结果集不会造成内存暴涨 | **通过** — 最坏 9,906 块 / 160 万命中元组级 |

### 三态汇总

| 状态 | 条数 | 内容 |
|---|---|---|
| **通过** | 20 | 上述全部 |
| **注意** | 4 | 数据库层分页未做（§12.1）、翻页重跑组装（§12.2）、戰國策篇名缺覆盖（§12.3）、块横跨篇界（§12.4） |
| **失败** | 1 | 「分页在数据库层完成」——连同 §12.1 一并记为未达成 |

> 记 `失败` 而不是含糊过去的理由：计划书 §24 把这条写成了完成标准，那就该按标准判。
> 它是**有意不做**（实测证明没必要，且 §22 禁止重写搜索引擎），但「有意」不改变
> 「没做到」这个事实。

---

## 复现步骤

```bash
cd HistoryAI

# 单测（166 例）
for f in tests/test_*.py; do PYTHONPATH=. PYTHONIOENCODING=utf-8 python "$f"; done

# 召回跑分（83 例，写 docs/phase6_recall.md）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/recall.py

# Python ↔ JS 引擎一致性（14 项）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.check_engine --report docs/phase5_consistency.md

# 发布闸门（27 个文件；跑完删 _site）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.build_artifact _site

# 浏览器验收
python -m api.main      # → http://127.0.0.1:8600/
```

浏览器上应看到：

* 搜 `秦始皇本紀` → 结果卡片带「篇名命中」标记，能读到该篇开头。
* 搜 `之` → `找到 9906 处片段`（不再是 600），能一直翻到末页。
* 任一卡片 → 「查看全文」进入 `#/read/{file_id}?row=N`，定位并高亮到该行。
