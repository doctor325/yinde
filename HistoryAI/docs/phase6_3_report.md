# 第六点三阶段报告：二十四史扩张与史料结构标准化

生成时间：2026-09-14
数据库：`data/database/history.db`（19 部 / 760,380 条记录 / 490 MB）
用例集：`tests/search_cases/`（4 个时代文件，360 条）

**一句话结论**：库内从 7 部扩到 **19 部**（正史 15 部），「加一本书」从改代码变成
改一条 JSON；`failed` 0、`warning` 1（國語，源文件如此）、召回 360 例 0 失败、
Python↔JS 14 项全对拍、发布闸门三条判据全过。两件事需要你拍板（见 §15）：
高频词「之」首次触发 10 万命中安全阀；**12 部新书里有 5 部上游（Kanripo）本身就残缺**。

---

## 0. 先说三件最要紧的事

1. **上游缺卷（本阶段最重要的发现）**：新入库的 12 部正史里，**5 部上游数字化本身不完整** ——
   三國志只到卷三十（通行本 65）、晉書只到卷三十三（130）、北齊書只到卷三十五（50）、
   隋書只到卷四十九（85）、北史只到卷二十二（100）。这不是没下载全，也不是解析漏了：
   `WYG` 分支与 `master` 文件集完全相同、`_data` 分支只有书影清单 —— 换个分支解决不了。
   逐书证据与核查方法见 **`docs/phase6_3_upstream_gaps.md`**。检索能力不受影响（已入库的
   正文都能搜到），但「19 部正史齐备」这个说法不成立，准确说法是「19 部可检索，
   其中 14 部完整、5 部为上游残缺版本」。
2. **高频词安全阀首次触发**：「之」命中 158,417 段 > `MAX_HITS_PER_QUERY = 100000`，
   块只由前 10 万条命中组装（40,566 块，`truncated=True`）。所以比 9 部时的 44,537 块
   **反而少** —— 不是检索退化。前端已有如实提示（「命中过多，本次只组装了前 N 段」）。
   是否提高上限，见 §15。
3. **两条旧负例被扩容填上、另有两条归因被修正**：4 条负例 + 1 个自检探针因新书入库
   而不再成立，处理方式是**收窄到仍有意义且可验证的范围并写清反转**（§6.3），不是删用例变绿。

---

## 1. 语料扩充结果

| 项 | 6.2（7 部） | 6.3 第一批（9 部） | 6.3 第二批（19 部） |
|---|---|---|---|
| 条记录（`rows`） | 421,178 | 465,507 | **760,380** |
| 可检索正文（`normalized_text` 非空） | — | — | **669,996** |
| 文件 | 345 | 410 | **1,026** |
| 卷（`juans`） | 267 | 336 | **1,192** |
| 篇/节（`sections`） | 1,010 | 1,078 | **1,944** |
| 库大小 | 639 MB | 280 MB※ | **490 MB** |
| `source_references` / `src_paragraphs` | 11,403 / 11,200 | 同 | 同（未变） |

※ 9 部时那次的库大小是 280 MB —— 那已经是一次重建后的值，与 7 部那次不是同一口径，
仅作参考；本阶段的可比口径是 **19 部 / 490 MB**。

新增 12 部（全部 WYG 文淵閣四庫全書家族）：

| 批次 | 书 | 文件 | 篇/节 | 条记录 | coverage |
|---|---|---|---|---|---|
| 一 | 三國志 | 31 | 33 | 18,751 | OK |
| 一 | 晉書 | 34 | 35 | 20,203 | OK |
| 二 | 宋書 | 102 | 107 | 46,693 | OK |
| 二 | 南齊書 | 61 | 60 | 17,427 | OK |
| 二 | 梁書 | 58 | 56 | 15,063 | OK |
| 二 | 陳書 | 37 | 36 | 8,426 | OK |
| 二 | 魏書 | 116 | 126 | 56,532 | OK |
| 二 | 北齊書 | 36 | 92 | 9,168 | OK |
| 二 | 周書 | 52 | 50 | 13,697 | OK |
| 二 | 隋書 | 50 | 136 | 34,283 | OK |
| 二 | 南史 | 81 | 79 | 35,287 | OK |
| 二 | 北史 | 23 | 124 | 14,510 | OK |

审计结论（`data/metadata/corpus_manifest.json`）：**已入库 19 部：verified 18、
warning 1（國語，源文件本身缺 003/007）、failed 0**。catalog 在册 28 部
（4 部先秦文獻 + 24 部正史），其中 9 部正史仍是 `planned`（未下载）。

---

## 2. 「加一本书」= 加数据，不改代码（6.3-A/C/D）

**规则集中到两处，一处代码一处数据：**

- **`scripts/pipeline/title_patterns.py`（代码）**：标题形态正则的**唯一注册处**。
  什么版式长什么样属于代码（正则依赖 `config.PARA_CHAR`、CJK 字形类等常量），
  原来内联在 `segmentation.py` 里的 `WYG_SECTION_RE` 等原样搬来，行为一字未变。
- **`scripts/pipeline/corpus_catalog.json`（数据）**：谁用哪个模式、家族默认值、
  逐文件层覆盖 —— 只写**模式名**，不写正则（写进 JSON 就会多出第二份真源）。
  加载器 `scripts/pipeline/catalog.py` 校验：schema、`book_id` 唯一、
  `kanripo_id` 与 `dir` 对账、`title_patterns` 引用的名字必须在 `PATTERNS` 里已定义。

四处单书硬编码（`segmentation.py` 的 `H2_ROLE`、`:313` 左傳、`:384` 戰國策、
`structure.py` 的 overrides）全部改成查表；`family_of()` 补上漏掉的 WYG
（6.2 遗留的陈旧平行实现）。**结果：新增一本同版式的书 = 只改 JSON 一条**，
本阶段 12 部新书里 11 部走的都是这条路。

### 三國志：计划书点名的那个风险，实测结论与预案不同

计划书预判三國志的 `武帝(操)` 会需要新加 `cjk_ming_paren` 模式。实测后**没用上**：
三國志正文里**根本没有篇题行**，卷首的卷目行把该卷傳名并列成一行
（`呂布　張邈(陳登)　臧洪(陳容)¶`，带不带缩进两种都有），傳正文直接从
`夏侯惇字元讓沛國譙人…` 起 —— 全书正文层没有一行能成 section。真实的篇名粒度
就是**卷**，所以改成 `juan_as_section: true`，用卷题当 section（33 个，全部
`title` @0.9）。这条判断的实测依据写进了 catalog 该书的 `note` 里，没有新增正则。

---

## 3. Section 溯源（6.3-C③）

`sections` 表新增 4 列：`detection_method` / `confidence` / `last_row` / `note`。
写入点唯一（`segmentation` 里各设 `self.section` 的分支），经 `records.Record` 流进
JSONL → `rebuild()` 的 INSERT。旧库不迁移：`connect()` 里 `PRAGMA table_info(sections)`
缺列就 `ALTER TABLE ADD COLUMN`（旧行 NULL＝未标注），重建＝`run_all` 全跑一次。

全库直方图（1,944 条）：

| detection_method | confidence | 条数 | 分布 |
|---|---|---|---|
| `title` | 0.9 | 1,851 | 12 部新书 **100%** 在这一档 |
| `header` | 0.8 | 68 | 尚書 58 + 史記 10 |
| `first-occurrence` | 0.5 | 25 | 尚書 24 + 春秋左傳 1 |

`last_row` NULL = **0**（回填完整）。低置信的 93 条**全部**集中在三部 tls 系老书，
12 部新书一条不在其中 —— 也就是说新书的篇名解析不是「靠猜的」。
这比一个覆盖率百分比更能说明问题，也是 6.3 想要的「知道这个 Section 是怎么来的」。

---

## 4. 搜不到诊断系统（6.3-H）：测试与 API 共用一份实现

`search/diagnose.py` 是**唯一**归因实现，`tests/recall.py` 与 `/api/diagnose` 都调它
（recall.py 删掉了本地 `_empty_class`，避免两套说法）。回答链是六层，每层给 `ok` 与证据：

```
语料在册 → 库内正文 → 文本命中 → 索引可达 → 检索返回 → 块组装
```

实测（本地服务）：

| 查询 | 结果 | 系统给出的答案 |
|---|---|---|
| `諸葛亮` | 92 段 / 82 块 | 正常返回，首条即三國志 |
| `安祿山` | 0 | `Coverage / not-in-text`：**「未收录的正史还有 9 部（隋唐 2 部、五代 2 部、宋 3 部、元 1 部、明 1 部；最早的缺口是《舊唐書》，一直到《明史》）」** |
| `高阿那肱` | 0 | 「已收录的正文里确实没有这个串」+ 列出 9 部未收录 —— **答不到点子上**，真相是北齊書上游只到卷三十五（见 §0.1 与 `docs/phase6_3_upstream_gaps.md`） |

第三行是本阶段暴露的**已知缺口**：诊断链目前只有「这本书有没有入库」这一层，
没有「这本书本身缺哪些卷」这一层。按既定方针（不擅自改口径）只留档、不擅自扩，
作为决策项上报（§15）。

---

## 5. 覆盖状态与前端（6.3-I）

- `/api/catalog` 输出 `counts`（五态）、`by_era_group`（按时代：在库/未收录/已验证）、
  `planned`（9 部正史骨架）、`on_disk_not_imported`、`uncatalogued_dirs`。
- `#/coverage` 顶部加按 `era_group` 分组的收录进度条（已收录／部分／未开始），
  数据来自 `/api/catalog`；实测十个时代分组齐全，`五代/宋/元/明` 显示为「未开始」。
- 顺手修掉写死的「五部史书／118 个原始文件」三处（`app.js:192,553,975`），
  改为由数据计算。
- 演示模式仍只显示演示书：`check_engine` 第 ⑬ 项（demo-site）与发布闸门都守着这条。

---

## 6. 召回扩大：160 → 360 例（6.3-G）

| 结果 | 条数 |
|---|---|
| 通过 | 358 |
| 注意 | 2 |
| 失败 | **0** |
| 合计 | 360 |

八维分布：`Coverage` 37（语料确实没有）、`Pagination` 2（触发组装上限）。
两条「注意」就是 `之` 的两条用例（`nb-single-01`、`pagination-01`），
原因都是 158,417 命中越过 10 万安全阀，**不是查不到**，末页第 406 页仍翻得到。

新增两个用例文件 `tests/search_cases/sanguozhi_jinshu.json`（三國志/晉書）与
`nanbeichao.json`（南北朝 10 部，隋書归此组），覆盖计划书 §10 的七类
（人名／别名／繁简／篇名／高频字／书名过滤／版片过滤）。**每部 verified 史书至少
1 条专属用例**（作为 `verified` 的前置条件，已写进 catalog 的对账）。

### 6.1 旧用例漂移：196 条逐条归类

语料翻倍必然让既有的 `baseline_hits` / `baseline_blocks` 过期。196 条漂移
**逐条**归了类，原始表与解释见 **`docs/phase6_3_baseline_drift_b2.txt`**：

| 类别 | 条数 | 说明 |
|---|---|---|
| A 新书含词 → 命中上升 | 194 | 纯语料效应：库里多了几部书提到该词 |
| B 安全阀 | 1 | `之`：命中越 10 万 → 块数**下降**（40,566 < 44,537） |
| C 新增篇名块 | 1 | `周本紀` 命中数不变、块 +1 = 北史多出的篇名块 |
| odd（未归类） | **0** | 196 = 194 + 1 + 1 |

并对两类量做了**方向性断言**：命中数无一下降；A 组块数无一下降。
`之` 是唯一一处块数下降，且已定位到安全阀 —— 这条也顺手更正了 6.2 留下的
`pagination-01` note（原来只说「末页翻得到」，没写上限）。

### 6.2 归因器看不见 book/edition 的缺陷（测试侧）

4 条被收窄的负例一开始仍 FAIL，查下来不是命中问题而是**归因**问题：测试侧
`classify()` 没把 `book` / `edition` 传给 `corpus_count` / `classify_empty`
（API 侧 `diagnose()` 本来就传）。修法是补齐下传 + 新增**自检 ③b**：
运行时当场数「全库必须 >0、该书必须 =0」，把这类缺口变成会红的回归网。

### 6.3 被扩容证伪的 4 条负例 + 1 个探针（收窄，不删）

| 用例 | 原断言 | 扩容后事实 | 处理 |
|---|---|---|---|
| `preqin neg-02`（坑儒） | 全库 0 | 宋書/陳書/隋書 各 1 段 | 收窄为 `book=史記`（史記仍 0；史記作「阬術士」1 段） |
| `qin_han qh-neg-02`（刘邦） | 全库 0 | 梁書 1 段「赤泉未賞劉邦尚曰漢王」 | 收窄为 `book=史記`（史記 0，作 高祖299/沛公243） |
| `sz-multi-07`（司馬懿×公孫淵） | 晉書 0 | 宋書天文志 3 段共现 | 收窄为 `book=晉書`（晉書仍 0） |
| `sz-neg-07`（臥龍） | 全库近 0 | 梁書新增 1 段 | 收窄为 `book=晉書` |
| 自检探针（坑儒） | 全库 0 | 被填上 | 换 `朱元璋`（宋史/明史未收录） |

**原则**：新书里有这个词不代表用例错了，而是「全库 0」这个断言不再成立；
收窄到**仍有意义且可验证**的范围（某部书仍为 0），并把反转写进 note。
另修正一条**口径失效**的用例：`nb-edition-03`（崔浩 × `edition=tls`）原来靠
「归因器看不见版片」蒙对，现在归因器能看见 → 明确写 `expect_class: Coverage`。

---

## 7. 字形：繁简收窄的完整清单（6.3-F）

`tests/test_unicode_corpus.py` 对**全库** `normalized_text` 断言孤立代理项 = 0，
并保留 `check_engine.py` 的 `ASTRAL_SAMPLES`（CJK 扩展 B，注释写明「不得因罕见而删除」）。

三向收窄（19 部语料重测，方法见本报告附录的脚本口径）：

| 向 | 条数 | 含义 | 例子 |
|---|---|---|---|
| A | 27 字 | 语料原形 ≥20 次，而**转换形**在库里不足原形 20% | 语料写「荆」1083 次，转换器转成「荊」（库 181 次） |
| B | 131 字 | 语料字形 `c`，而 `to_traditional(to_simplified(c)) != c` | 「熲」→「颎」转不回来（简体用户搜 高颎 = 0 段，语料实有 13 段） |
| C | 5 组 | 两形并存、转换器**两个方向都不映射**（需异体字表） | 髙/高、䕶/護、𫝊/傳、夲/本、寛/寬 |

本批新发现一个 B 向实例：**卧(U+5367) / 臥(U+81E5)** —— 转换器两个写法都归到
U+81E5，而语料里有 3 段写 U+5367（梁書/晉書/南史），**两种写法都到不了**。

---

## 8. Python ↔ JS 一致性（6.3-K）

`python -m scripts.site.check_engine --report docs/phase6_3_consistency.md`：
**通过，14 项检查全部一致** —— `dual-text`、`zh`、`norm`、`dl`、`search`、`entities`、
`question`、`ranking`、`result-block`、`aggregate`、`retrieve`、`static-api`、
`demo-site`、`boot`。含 sha256 摘要对拍与 boot.js 冒烟；「泄漏闸门」一项确认
演示站只出现演示书。

`frontend/data/corpus.json` 随之增长到 760,380 行 / 1,026 文件 / 182.73 MB
（本地导出物，不入库、不发布）。

---

## 9. 发布边界（6.3-L）：一处语义变化，必须写明

`check_publish.py` 的 `REAL_TITLES` **改为从 catalog 派生**（24 部正史 + 4 部先秦），
不再是手抄名单 —— 6.2 把 `REAL_TITLES` 定为「唯一真源、只对账不复制」，
本阶段一次加 12 本书，靠记性同步名单正是 6.2 补过的那类 bug，所以真源上移到 catalog。

**语义变化（不能悄悄改口径）**：

- `publish_gate.missing`：**结构性恒为空**。它原来表示「真源里有、闸门名单里没有」，
  现在同源，永远不会再有差异 —— 也就是说它不再是判据。
- `publish_gate.stale`：**仍需人看**。当前 9 条 = 在册但未入库（`planned`）的书。
  它是「预期之内」还是「漏了」只有人能判断，所以照旧报出来。

实测：`build_artifact _site` → 27 个文件 / 426 KB，三条判据全过
（① 白名单 27 项 ② 数据里无真实书名（28 个）③ 正文逐字反查 109 行自撰 / 213 段产出行）；
`data-demo/` 未被本阶段任何改动波及（git 里零改动）。

---

## 10. 全部测试结果

12 个测试文件（`for f in tests/test_*.py; do PYTHONPATH=. python "$f"; done`）：

| 文件 | 例数 | 结果 |
|---|---|---|
| test_catalog.py | 38 | OK |
| test_e2e.py | 7 | OK |
| test_header.py | 5 | OK |
| test_library_fixtures.py | 9 | OK |
| test_phase4_questions.py | 16 | OK |
| test_result_block.py | 43 | OK |
| test_result_block_real.py | 21 | OK（877s，见 §11.3） |
| test_search_api.py | 25 | OK |
| test_search_logic.py | 20 | OK |
| test_structure.py | 22 | OK |
| test_title_patterns.py | 13 | OK |
| test_unicode_corpus.py | 6 | OK |
| **合计** | **225** | **全绿**（6.2 是 168 例） |

本次扩容暴露并修好的**三处测试侧问题**（都是「测试写死了只在 7~9 部语料下成立的东西」）：

1. `test_catalog.py::test_every_book_has_recall_flag`：verified 名单写死 7 部。
   改成**双向点名**（verified = 19 部逐一点名；unverified = 恰好那 9 部 `planned` 正史），
   比原来数个数更强：哪天多出一部没背书的书、或哪部书漏进召回，都会在这里顶出来。
2. `test_result_block_real.py` 的 `BASELINE`：命中基数写死 9 部的数。
   按扩容重测并补第 ④ 条注释：齐桓公 154→**159**、管仲 201→**219**、黄帝 173→**206**
   （涨的全部是新书里的段落，不是已有 Passage 被改写）。
3. `test_search_api.py` 的 4 处**固定命中数**（文件里原本就写着「命中数是语料的函数，
   加书必然涨」的注释，惯例就是重测＋注明）：齐桓公 151→**159**、管仲 189→**219**、
   `level=passage` 151→**159**、`/api/search` 段数 126→**133**（`hit_total` 151→159）。
   同一测试里还有两条**没跑到**的断言也在同一轮漂了（城濮 35→**40**、长勺 7→**8**）——
   前一条断言先失败，后面两条就不会执行；**不实测就照报错改，会漏改这两条**。

另有一处**上游数据例外**被点名固化（不放松判据）：三國志/晉書 的 `_000.txt` 里有 3 处
`<pb:KR2a0012_WYG_1a>` 形式的页标记（规范形应带「文件号-」，如 `_000-1a`），
源文件如此。它们 `kind=page`/`layer=structure`，不进正文、字符守恒通过、前端
`rich()` 用宽容正则隐藏 `<pb:…>` 也不漏字。`test_e2e.py` 里的对账改成
**点名到「文件 + 字面值 + 出现次数」**，其余任何一处不合规的 pb 写法仍会让用例变红。

---

## 11. 性能（6.3-J，先测后议）

### 11.1 对照口径的更正

本机在持续负载下会降频且不恢复，所以每次跑都带一个**控制查询**（`齊桓公`，语料变化
对它影响极小）。6.3「扩容前（9 部）」那次是**降频状态**（控制 192 → 282 ms，+47%），
它的绝对值整体偏高 —— 主对照因此改用两次都干净的点：
**6.2 扩容后（7 部）↔ 6.3 扩容后（19 部）**。

### 11.2 四批对照（`ms_p50`，standard 模式）

| 查询 | 7 部（干净） | 9 部（降频） | 19 部（干净） | 命中数 7→19 部 |
|---|---|---|---|---|
| `齊桓公`（对照） | 163 | 182 | **176** | 151 → 159 |
| `管仲` | 179 | 204 | **232** | 189 → 219 |
| `將軍`（中频） | 1,103 | 3,016 | **3,275** | 3,975 → 17,625 |
| `大夫`（中频） | 1,686 | 4,034 | **3,347** | 3,531 → 6,512 |
| `曰`（高频） | 3,643 | 9,064 | **7,667** | 65,682 → 95,460 |
| `王`（高频） | 3,173 | 7,724 | **7,131** | 28,303 → 58,990 |
| `之`（单字） | 3,925 | 11,116 | **5,610** | 75,766 → 158,417 |
| `齊桓公 卒`（多词） | 13 | 36 | **18** | 10 → 10 |
| `秦始皇本紀`（篇名） | 6 | 15 | **8** | 2 → 2 |

峰值内存：`之` 526 → 625 MB，`曰` 519 → 877 MB，`王` 429 → 791 MB。

**读法**：命中数翻倍而耗时**没有同幅增长**（`之` 5,610 ms 比 7 部时的 3,925 ms 只多
43%，命中却多了 109%），因为命中越线后块只由前 10 万条组装 —— 耗时被安全阀削平了。
`齊桓公`（159 命中）与 `管仲`（219）这类人名查询仍是 0.2 秒级，篇名/多词 ~10 ms。
**真正需要你拍板的是上限本身**，不是这些数字好坏（§15）。

### 11.3 真实语料回归测试的耗时（既有问题被放大）

`test_result_block_real.py` 在 19 部下实测 **877 秒（约 15 分钟）/ 21 例**（7 部时是分钟级）。
原因不是新写的测试慢，而是**既有问题 §12「高频词整段组装每次翻页重跑」被放大**：
该文件的 `all_blocks()` 要翻完所有页做不变量断言，`大夫` 一类高频词
（4,901 块）按 `page_size=30` 要翻 164 页，每页都把整段组装重跑一遍（~3.3 s/次）。
测试本身没有错，是它的成本暴露了检索侧那条既有性能债。

---

## 12. 已知问题

| 级别 | 问题 | 状态 |
|---|---|---|
| **待决策** | 5 部新书上游缺卷（三國志/晉書/北齊書/隋書/北史） | 新，见 `docs/phase6_3_upstream_gaps.md` |
| **待决策** | `MAX_HITS_PER_QUERY = 100000` 首次触发（`之` 158,417） | 新，前端已如实提示 |
| 注意 | 诊断链答不出「这本书缺哪些卷」 | 新，与上游缺卷同源 |
| 注意 | 高频词翻页每次重跑整段组装（§11.3） | **既有**，19 部下把该测试拖到 877s |
| 注意 | 字形 B 向 131 字 / C 向 5 组可达性缺口 | 既有，本批新增 `卧/臥` 一例 |
| 注意 | 國語 coverage WARN（003/007 覆盖不到） | **既有**，源文件如此 |
| 注意 | 12 部新书篇名溯源 100% `title@0.9`，无需人工复核 | 新，是好消息 |
| 注意 | 三國志/晉書 的 3 处非规范 `<pb:>` 写法 | 新，上游数据，已点名固化 |

---

## 13. 顺手修（用户已批准的三项，逐条交代）

| 项 | 做法 | 证据 |
|---|---|---|
| `family_of()` 漏 WYG | 改成 `{"tls":"tls","SBCK":"sbck","WYG":"wyg"}.get(ed)` | 单测 `test_header.py` 5 例 OK |
| 0 字节空文件与死引用 | 删 5 个 0 字节脚本 + 2 个 0 字节路由文件（`api/routes/` 空目录随之消失）、`database/migrations/` 空目录；`export_site.py` 对不存在的 `lint_publish.py` 的死引用删掉 | 删除前逐个 `git show HEAD:<path> \| wc -c` 确认**全是 0**；`grep -rn lint_publish` 已无命中 |
| 前端写死的「五部史书／118 个原始文件」 | `app.js:192,553,975` 改为由数据计算 | 三处都已改；`check_engine` ⑭ boot 冒烟通过 |

全部删除只发生在 `D:\MyCode\HistoryProject` 内；`D:\MyCode` 下其他目录一概未碰，
工作区根目录未执行任何 git 写操作。

---

## 14. 复现步骤

```bash
cd D:/MyCode/HistoryProject/HistoryAI

# 1) 管线重建（inventory → process → db → validate）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.pipeline.run_all
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.pipeline.manifest     # failed 必须 0

# 2) 单测（tests/ 无 __init__.py，逐文件跑；HTTP 类需先起服务）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m api.main &
for f in tests/test_*.py; do PYTHONPATH=. PYTHONIOENCODING=utf-8 python "$f"; done

# 3) 召回（360 例，写 docs/phase6_recall.md）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/recall.py --report docs/phase6_recall.md

# 4) Python ↔ JS 一致性（14 项）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.check_engine --report docs/phase6_3_consistency.md

# 5) 性能（同一进程内含对照小查询）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/perf_corpus.py --label "6.3 第二批扩容后（19 部）" \
    --json docs/phase6_3_perf_after.json

# 6) 发布闸门（跑完删 _site）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.build_artifact _site

# 7) 上游完整度复核（只读，见 docs/phase6_3_upstream_gaps.md）
#    在 HistoryLibrary/kanripo 下比对每本书 Readme.org 目次末条卷目 vs 文件数 vs 库内末篇

# 8) 浏览器验收：http://127.0.0.1:8600/ → 全文检索 / 篇名覆盖 #/coverage
#    （改过代码后必须先重启 API：旧进程不加载新路由）
```

---

## 15. 待你决策（两条）+ 一项请确认

### ① `MAX_HITS_PER_QUERY` 是否提高？

- **现状**：10 万条命中上限。`之` 158,417 命中越线 → 块只由前 10 万条组装
  （40,566 块，`truncated=True`），前端提示「命中过多，本次只组装了前 40,566 段」。
  323 条正例里**只有这一条**越线（次高 `帝` 34,807）。
- **不动的理由**：单字查询要的是「能找到、能翻」，不是「一次看全 15 万条」；
  当前上限已经把峰值内存压在 625 MB 内。
- **提高的代价**：按当前比例，全量组装 15.8 万命中约 9–10 s / ~1 GB 峰值内存
  （本机 19 部实测 `之` 5.6 s / 625 MB）。也可以折中（如 15 万）或维持现状只保留提示。
- **我不会擅自改**：等你定。

### ② 5 部上游缺卷的正史怎么处理？

三个选项（详见 `docs/phase6_3_upstream_gaps.md` 末节）：

- **(a) 登记为已知结构问题**（推荐）：catalog 给这 5 部加缺口说明，`manifest` 置
  `warning`，`#/coverage` 显示「部分收录（35/50 卷）」。改动小、可回滚，
  但验收口径会变（verified 18 → 13、warning 1 → 6）。
- **(b) 换/补语料源**：另找一份完整文本补这 5 部。工作量大，超出本阶段范围。
- **(c) 只留档**：状态不动（**现状**），只在本报告与缺口文档里写明。

现状是 (c)：我按「不擅自改口径」处理，只做了留档。

### ③ 请确认（不是决策）：改测试基数的三处

扩容让三处**写死在测试里的语料相关数字**不再成立，我按各文件已有的惯例
（「命中数是语料的函数，加书必然涨」，重测＋注明）改了 6 个数：`test_catalog.py`
的 verified 名单（7 部 → 19 部逐一点名）、`test_result_block_real.py` 的 BASELINE
（159/219/206）、`test_search_api.py` 的 4 处固定命中数（159/219/159/133，另两条
没跑到的 40/8）。判据一条没放宽（详见 §10）。**这几处改动值得看一眼** ——
如果哪一条你认为应该保持原样（比如宁可让它红着提示「语料变了」），我改回去。

---

## 附：本阶段交付物

**新增**：`scripts/pipeline/title_patterns.py`、`scripts/pipeline/catalog.py`、
`scripts/pipeline/corpus_catalog.json`（24 部正史全册 + 规则表）、
`scripts/pipeline/audit_sections.py`（只读审计工具）、`search/diagnose.py`、
`tests/test_catalog.py`、`tests/test_title_patterns.py`、`tests/test_unicode_corpus.py`、
`tests/search_cases/{sanguozhi_jinshu,nanbeichao}.json`、
`docs/phase6_3_upstream_gaps.md`、`docs/phase6_3_baseline_drift_b2.txt`、
`docs/phase6_3_consistency.md`、`docs/phase6_3_perf_{before,after}.json`、本报告。

**修改**：`segmentation.py`、`structure.py`、`records.py`、`sqlite_store.py`、
`kanripo_header.py`、`manifest.py`、`inventory.py`、`database/schema.sql`、
`api/main.py`、`api/db.py`、`frontend/app.js`、`frontend/index.html`、
`frontend/style.css`、`scripts/site/{check_engine,check_publish,export_site}.py`、
`tests/recall.py`、`tests/{test_e2e,test_catalog,test_result_block_real,test_phase4_questions}.py`、
`tests/search_cases/{preqin,qin_han}.json`。

**删除**：5 个 0 字节脚本 + 2 个 0 字节路由文件 + 2 个空目录（见 §13）。

**语料**：`HistoryLibrary/kanripo/` 下新增 12 个书目录（gitignore 内，不入库、不发布）。

**边界**：原始语料只读（未改写任何 `kanripo/` 文件）；未改 `search/engine.py` 核心算法；
真实书名未进任何发布产物；一个提交，是否 push 由你决定。
