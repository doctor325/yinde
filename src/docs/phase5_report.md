# 第五阶段报告：零成本公开网站化 + GitHub Pages 部署

公开站：**https://doctor325.github.io/HistoryProject/**

交付状态：**PARTIAL**（§36 措辞见第 9 节）

---

## 1. 本阶段目标

前四阶段只能在 `127.0.0.1:8600` 上访问（`python -m api.main` 起一个 stdlib
http.server）。本阶段要把它变成**互联网上可公开访问、零服务器成本**的静态站点。

约束是硬的（§0、§5、§6、§7、§12）：不买服务器、不买域名、不接任何 AI API
（付费的、免费的都不接）、不接本地模型、不实现 LLM Provider、不做模型训练、
不做 RAG；**Kanripo 语料一律不发布**。

这两组约束合起来限制出一个很窄的解空间：

- 零成本 → 只能用 GitHub Pages 这类免费静态托管，**没有 Python 进程**
- 不发语料 → 公开站上不能有真实正文

而检索逻辑（三路检索 / Result Block / 提问模式）全在 Python 里，靠 SQLite。
把这两件事同时满足，有且只有一条路：**把检索逻辑移植成 JS，在浏览器里跑**。

§12 已经把这条路的底线写清楚了：

> 如果目前无法找到适合公开分发的史料数据：**宁可公开一个功能完整的前端 Demo，
> 也不要违规上传 corpus。**

所以本阶段的交付是：**一个功能完整的前端 Demo** —— 检索、Result Block、提问模式
在公开站上真的能跑通，跑的是我们自撰的演示数据；真实语料不随本站发布。

---

## 2. 架构：一个前端，两种模式，零构建

```
HistoryAI/frontend/          ← 本地 API 的 docroot 与 Pages 的发布产物**是同一个目录**
├── index.html               +1 行横幅容器，+14 个 <script>
├── style.css                +横幅样式
├── app.js                   只改 api() 一处（唯一的取数接缝）
├── boot.js                  新增：判定模式、装载数据
├── engine/                  新增：search/ 的 JS 移植（13 个经典脚本）
├── data-demo/               新增：自撰演示数据（MIT，入库）
└── data/                    真实语料导出（**.gitignore，永不入库**）
```

没有站点生成器、没有打包器、没有构建产物需要同步。**线上和本地是同一份文件**，
「本地能跑线上不行」这类问题在结构上就不存在。

`app.js` 全文只有一个 `fetch()`，在 `api()`（`app.js:14-19`），约 28 个调用点
都传根绝对路径。路由是 hash 的，`index.html` 资源全是相对路径 —— 子路径托管
（`/HistoryProject/`）天然安全。所以改造面就一处：

```js
async function api(path) {
  const boot = await window.HistoryAIBoot;
  if (boot.mode === "api") { /* 原样：fetch(path) */ }
  if (!boot.site) throw new Error(boot.error || "静态模式：数据不可用");
  return boot.site.get(path);          // 浏览器里重放的同一组接口
}
```

**模式判定只做一次**（探测 `/api/stats`），不做「先试后降级」的逐请求回退 ——
那会让每个请求都先撞一次 404，而且把「真 API 报错」和「没有 API」混为一谈：
本地 API 对非法参数返回 400，那是真错误，不该降级。

数据目录**先 `data/` 后 `data-demo/`**，靠目录是否存在决定，没有配置开关。
将来数据来源问题解决了，导出一次放进 `data/`，公开版就自动具备完整检索，**无需改代码**。

---

## 3. 实际新增/修改文件

| 文件 | 说明 |
|---|---|
| `frontend/engine/*.js`（13 个） | `search/` 的 JS 移植，经典脚本挂 `window.HistoryAIEngine` |
| `frontend/engine/static_api.js` | 在浏览器里重放 10 条 `/api/*` 路由 |
| `frontend/boot.js` | 模式判定 + 数据装载 |
| `scripts/site/gen_zh_table.py` | 繁简字符表生成 + 穷举回归自检 |
| `scripts/site/export_site.py` | SQLite → 静态 JSON（列式打包 + 字典编码 + bm25 统计） |
| `scripts/site/make_demo_data.py` | 自制演示数据集（走真管线，不手搓 JSON） |
| `scripts/site/check_engine.py` / `.mjs` | 一致性验证 harness（13 项） |
| `scripts/site/loader.mjs` | 在 Node 里按浏览器的方式加载引擎 |
| `scripts/site/check_publish.py` | 发布闸门（3 条判据） |
| `scripts/site/build_artifact.py` | 装配发布产物 + 过闸门 |
| `.github/workflows/pages.yml` | Pages 部署 |
| `frontend/app.js` / `index.html` / `style.css` | 接缝 + 横幅（改动面严格受限） |

**`search/` 与 `api/` 下面的 Python 一行未改**（§18 保护名单）。

---

## 4. 关键技术判断（均已实测）

### 4.1 三条检索路径语义等价，差别只在排序

`engine.run_search` 依 tokenizer 与词长选 `fts`(trigram) / `bigram` / `like`。
读码确认：三条路径语义上等价于「`normalized_text` 含**全部**检索词子串（AND）」
（`result_block.py:433` 注释明写 bigram「与 LIKE 集合语义等价」）。唯一差别是排序：
fts/bigram 走 `bm25`，like 走 `book_id, file_no, row_no, seq`。

演示数据实测三条路径都走得到：单字 `齊`→`like`、双字 `重耳`→`bigram`、
三字 `齊桓公`→`fts`。

### 4.2 bm25 可以精确复现

FTS5 公式固定（k1=1.2, b=0.75），只需导出每个 doc 的 token 数与全局 df。
`n_row` / `n_token` 编码的是**索引的建法**（trigram 表被 rebuild + 重插，
总量翻倍），从行里推不出来，故随包下发。

### 4.3 繁简字符表充分性：**穷举证明**，不是推测

`search/zh.py` 用 `kernel32.LCMapStringEx`（Windows 专属），浏览器不可用。
`dual_text.simplify()` 的契约是**长度不变，否则整段回退原文**。

已用真实 Windows API 对语料穷举实测：

| 范围 | 整串≠逐字 | 其中长度变化 | **长度不变却不同** |
|---|---|---|---|
| 单字 6,932 | 0 | — | **0** |
| 二字组 272,455 | 182 | 182（全部含扩展 B 汉字） | **0** |
| 三字组 696,378 | 870 | 870（全部含扩展 B 汉字） | **0** |

**结论**：在 `dual_text` 接受的（长度不变的）范围内，字符级映射与 Windows API
完全等价，**无需词组例外表**。差异全部源自 `LCMapStringEx` 在 CJK 扩展 B 汉字
（代理对）相邻时返回残破码位 —— 这是 Python 侧**既有**行为，不是本次引入的。

---

## 5. 自制演示数据集

`frontend/data-demo/`，**MIT 发布**，2 部演示书 / 4 文件 / 84 记录 / 53 条正文。
每张结果卡片都显示演示书名，页面上常驻横幅，不会与真实史书混淆。

**走真管线造，不手搓 JSON**：源文本 → `scripts/pipeline`（与真实语料同一条）
→ 独立 DB（临时目录，用完即删）→ `export_site.py`。手写一份「差不多的」JSON
只能骗过前端一次；走真管线则导出格式天然一致，且顺带验证了管线在另一份输入上
也能跑通。

覆盖的形态（实测分布）：

| 维度 | 覆盖 |
|---|---|
| kind | `passage` 53 / `heading` 18 / `comment` 7 / `page` 6 |
| layer | `main` 39 / `commentary_candidate` 31 / `structure` 14 |
| status | `ok` 70 / `pending_commentary` 14 |
| 家族 | tls（`**` 分卷、`*** n.n《…》` 分篇、`# src:` 跨行注释块）、SBCK（行内括注） |
| 人物称谓 | 齊桓公/桓公/小白、管仲/管子/管夷吾/夷吾、晉文公/文公/重耳、秦穆公/繆公、百里奚 |

**故意不覆盖**：`kind='part'`（全库 224,822 行里只有 2 行，是 SBCK 首文件里正文
段落的 `#+` 块，属极边缘形态 —— 演示数据不该是某段代码唯一被跑到的地方）；
`preface`/`backmatter`/`toc` 层（真实语料里它们来自针对具体书目的手工确认表，
演示书没有对应的书目证据，造出来就是编，违反「不猜」）。

**与真实语料的隔离**：源文件与中间库写在系统临时目录（用完即删），产物写到
`data-demo/`。导出后有一道检查（`make_demo_data.check_isolated`）：产物里出现的
任何正文片段都必须是本文件自撰文本的**逐字子串**。判据是子串而不是整行相等，
因为管线会把 SBCK 的行内括注拆成两条记录（`公將伐虢(虢國名也/公晉獻公)¶`
→ 正文 + 注释两半）。

---

## 6. 一致性验证（§18）

报告：**`docs/phase5_consistency.md`**（由 harness 自动生成，重跑即覆盖）

```
python -m scripts.site.check_engine --report docs/phase5_consistency.md
```

**13 项检查，全部通过。** 对拍方式：两侧各自读**同一份**导出语料，同一组输入
各算一遍，逐字段比。判定分两级：

- **第一级（必须完全一致）**：命中集合、`passage_ids`、`text`、`n_passages`、
  `match_count`、`total`、`hit_total`、`exec_mode`、层级与出处字段 → **100%**
- **第二级（须一致，否则逐条列明）**：`score` 与排序 → **100%**

| # | 检查 | 覆盖 |
|---|---|---|
| 1 | `dual-text` | 全部 203,308 条 passage 正文的繁简转换 |
| 2 | `zh` | 语料全部单字 + 固定多字样本 |
| 3 | `norm` | `normalized_text` 重算，全表 |
| 4 | `dl` | dl 推导 vs 数据库影子表，全表 |
| 5 | `search` | `engine.run_search` 固定查询集（对 SQLite vs 对内存语料） |
| 6 | `entities` | 全语料扫描 + 固定样本裁决 |
| 7 | `question` | 问题分析 + 检索式扩展 |
| 8 | `ranking` | 固定候选池上的打分与排序（**bm25 复现的验收项**） |
| 9 | `result-block` | 组装 + 展开 |
| 10 | `aggregate` | block → 事件 → 跨史书对照 |
| 11 | `retrieve` | 提问链路端到端 |
| 12 | `static-api` | **72 条 URL 路径**，10 条路由的返回结构与错误消息逐字段对拍 |
| 13 | `demo-site` | 演示数据集能否撑起一个功能完整的公开站 |

第 12 项值得单说：公开站上没有 Python 进程，`/api/*` 全由
`frontend/engine/static_api.js` 在浏览器里重新实现。它一旦与 `api/main.py` 走偏，
表现是**「本地能看、线上不对」，而且是在公开站上**。所以这一项把整条链路
（路由匹配、参数解析、错误消息、返回键集）拉出来对一遍，**真 API 的响应是唯一
事实来源**。覆盖了分页边界（`limit=9999` / `-1` / `0`）、未转义的 LIKE 通配符
（`%` 与 `_`）、原文窗口（`start=999999`、`start=5&end=3`）、before/after 夹取
（`99`/`-5`）、三档 `text_mode`、三种 `level`、提问流程、`+` 保持字面量、未知道路。

**一处刻意的不一致**（不是差异，是纪律）：`/api/stats` 会带出本机语料库绝对路径
（`api/main.py` 的 `d["library"]` 及 `import_runs` 整行里的同名列）。静态版**有意
剥掉**这两个键 —— 公开产物里永远不该有这个路径。对拍前先在 Python 侧剥掉，
并注明这是泄漏闸门而非分歧。

---

## 7. 发布闸门

```
python -m scripts.site.check_publish            # 直接查 frontend/
python -m scripts.site.build_artifact _site     # 装配产物（排除 data/）再查
```

三条判据，都**不需要真实语料在场**（CI 里没有 `HistoryLibrary`，它被 .gitignore
排除）：

1. **文件白名单**（26 项，精确到文件名，不写 glob）—— 语料想进来，得先变成一个
   不在名单上的路径
2. **数据里无真实书名** —— 扫所有 JSON 的字段值找 尚書/春秋左傳/史記/國語/戰國策
3. **正文逐字反查自撰演示文本**（主力判据）—— 产物里出现的每一段正文，都必须是
   `make_demo_data.py` 里那 109 行自撰文本的子串。覆盖两类载体：打包语料
   （`corpus.json` 的 `text_orig` 列）与原文对照（`raw/*.json` 的 `lines[].text`）

**为什么不是「与真实语料比 sha256」**：最直觉的闸门是拿产物去和 `HistoryLibrary`
比 sha256，但 **CI 里没有 `HistoryLibrary`**。一个在 CI 里跑不了、或者因为找不到
参照物而「永远通过」的闸门，比没有闸门更危险：它给的是虚假的安心。

**双向验证过**（一个只会在某个方向上通过的闸门等于没有）：

| 场景 | 结果 |
|---|---|
| 真产物（26 文件，无 `data/`） | 通过，退出码 0 |
| 本地 `frontend/`（含真实 `data/`，133 个文件） | **拒绝**，退出码 1 |
| 注入一句真实语料正文 | **拒绝**：`语料里有非演示正文：夏四月，鄭伯克段于鄢。` |
| 多出一个未登记文件 | **拒绝**：`不在白名单的文件：data-demo/extra.json` |
| 书名改成 `史記` | **拒绝**：`真实书名出现在数据里：books.json → 史記` |
| 原文对照页混入真实语料 | **拒绝**：`1.json 第 11 行非演示正文：元年春王正月。` |

`.gitignore` 的写法同样是双向验证过的 —— 这条是「误发语料」与「演示数据没进库」
二选一的地方，裸写 `data/` 一旦写歪就是事故：

```
$ git check-ignore -v HistoryAI/frontend/data/corpus.json
HistoryAI/.gitignore:9:frontend/data/    ← 被忽略（正确）
$ git check-ignore -v HistoryAI/frontend/data-demo/corpus.json
（无输出 → 不被忽略，正确）
```

---

## 8. 三种模式的实际验证

没有用无头浏览器（不引入任何依赖），而是**把发布产物装出来，用 Node 按浏览器的
方式加载 `boot.js` 与引擎**（`scripts/site/loader.mjs` 就是干这个的），
`fetch` 指向本地静态服务器 —— 跑的是浏览器将要跑的那份代码，不是另写一份。

| 模式 | 触发条件 | 结果 |
|---|---|---|
| **API 模式** | 真 API 在 8600 | `mode=api`，**不显示横幅**，与第一到第四阶段完全一致 |
| **本地静态模式** | 有 `frontend/data/` | `mode=static` `kind=local`，本地横幅；启动 **504 ms**（32 MB JSON 解析 + 解码）；检索 20 块 / 96 命中；提问 19 个事件 |
| **公开演示模式** | 只有 `data-demo/`（= Pages 的实际情形） | `mode=static` `kind=demo`，演示横幅；检索 / Result Block / 提问三种模式全部可用；对照页 51 行 |

三种模式的横幅文案与显隐都逐字核对过。API 模式下横幅 `hidden=true`，
这是「本地完整版不退化」在 UI 上的直接证据。

---

## 9. §36 交付状态声明

> **PARTIAL**
>
> 公开 Demo 前端已经完成，本地完整版搜索保持正常，完整在线史料搜索需要未来
> 重新确定可公开的数据来源或部署方案。

换句话说：

- ✅ 公开站可用，检索 / Result Block / 提问模式在演示数据上真的能跑通
- ✅ 本地完整版（`python -m api.main`）一行未改，144 个测试全绿
- ✅ 真实语料的完整检索能力，在本地与「本地静态模式」下都完好
- ❌ **公开站上检索不到真实史料** —— 因为真实语料不可发布（§5）

最后一条不是能力缺陷，是**数据来源问题**：`HistoryLibrary/kanripo/` 的整理本
没有随附 README/LICENSE/版权声明，无法据此确认可否再分发，按 §23「如果无法确认：
不要上传原始 corpus」。将来若确定了一个可公开分发的史料数据源，导出一次放进
`frontend/data/`，公开版**无需改任何代码**即具备完整检索。

---

## 10. 测试与回归

| 项 | 结果 |
|---|---|
| Python 测试（9 个文件，144 例） | **全部通过** |
| 引擎一致性（13 项检查） | **全部通过**（含两级判定 100%） |
| 发布闸门（3 条判据 × 6 个场景） | **全部符合预期** |
| `.gitignore` 双向验证 | **通过** |
| `HistoryLibrary` 零修改 | 见下节 |

一致性自检本身也加了一道防线：`check_script_order()` 比对 `index.html` 的
`<script>` 顺序与 `loader.mjs` 的 `ENGINE_FILES`。自检在 Node 里按 `ENGINE_FILES`
跑，浏览器按 `index.html` 跑，两者一旦不一致就是**「自检通过而线上是坏的」**——
证据说没事、用户看到白屏，这是最坏的一类失败，所以直接比字符串，不等出错再查。

---

## 11. 原始语料零修改验证

- `HistoryLibrary/` 未做任何写入；本阶段所有脚本对它**只读**
- 演示数据的造数过程用 `HISTORY_LIBRARY` / `HISTORY_DATA` 环境变量指向临时目录，
  不触碰真实语料的任何路径
- `git ls-files` 中 0 个语料文件、0 个数据库、0 个 `frontend/data/`
- 发布产物中 0 处真实正文（第 7 节的闸门，已双向验证）

---

## 12. 已知问题与取舍

### 12.1 `question._find_entities`：权重 0.3 的分支不可达，短称被误标「全称精确命中」

`question.py:255` 写的是 `weight = 0.7 if resolved == ent else 0.3`，但 `ent` 来自
`entities.find_in_text`，而后者（`entities.py:419`）用**完全相同的参数**调用过
`resolve_alias`。同一进程、同一参数、同一函数 → 同一次结果，所以 `resolved == ent`
恒成立，**0.3 分支是死代码**。

后果不止于此。`find_in_text` 在裁决不了时把 `word` 本身当候选实体报出去
（`entities.py:429`），于是 `_find_entities` 走了 `ent == word` 这条「全称」分支，
给出 `weight=1.0` 与 `why='全称精确命中'`。实测：

```
'桓公'  → [('桓公', '桓公', 1.0, '全称精确命中')]      ← 实际有 16 个候选，无法裁决
'文公'  → [('文公', '文公', 1.0, '全称精确命中')]      ← 实际有 18 个候选
'重耳'  → [('晉文公', '重耳', 0.7, '「重耳」的语料分布唯一：晉文公(1)')]   ← 正常
```

而 `resolve_alias` 算出的那句很有用的解释 ——
「「桓公」在语料中分属 齊桓公(94)、魯桓公(12)、鄭桓公(7)…上下文不足以裁决」——
**被算出来了又被丢掉**，用户看到的是不成立的「全称精确命中」。

**影响**：`weight` 目前只用于实体之间的排序与展示（`why` 会显示在提问模式的
说明里），不影响召回集合。属**文案与权重标注错误**，不是检索错误。

**为什么不改**：`question.py` / `entities.py` 在 §18 保护名单里，本阶段不得改动
第四阶段的检索逻辑。JS 移植**如实复现了这个行为**（实测两侧输出逐字相同）——
移植的职责是等价，不是顺手修 bug。

**建议**：留到后续阶段在 Python 侧修（`find_in_text` 应把「未裁决」与「全称命中」
区分开，例如返回 `ent=None` 让调用方按 0.3 处理；`_find_entities` 也应把
`resolve_alias` 的 `why` 保留下来）。

### 12.2 SQLite 隐式扫描顺序是一份没写下来的契约

`_fetch_hits` / `_like_any` 的 SQL **没有 ORDER BY**，它们依赖 SQLite `passages`
按 rowid 顺序扫描（= `passage_id` 升序）。这个顺序是有语义的：`LIMIT` 截断时
先来后到由它决定。

JS 侧 `corpus.passageIdx` 是导出时的打包顺序（= `book_id, file_no, row_no, seq`），
与 rowid 顺序**不是一回事**。所以凡是会 `LIMIT` 截断的消费方，都必须在 JS 侧
按 `passage_id` 重排一次。

这是一行注释都没写过的隐式依赖，是本阶段最容易埋雷的地方。第 6 节的
`static-api` 与 `search` 两项检查覆盖了它（含 `limit=0` / `-1` / `9999` 等边界）。

### 12.3 其它

- `entities.occurs_in_corpus` 里 `full, _short, raw = _corpus_scan(...)` 的局部变量
  名叫 `raw`，拿到的却是 `bare`（`_corpus_scan` 返回 `(full, short, bare)`）。
  纯命名问题，行为正确（`entities.py:339`）。
- `query_expansion` 的 `POOL_LIMIT = 2000` 在 `_CURATED` 实体命中时到不了 ——
  实体池远小于它，这个上限只在无实体、走 fallback 池时有意义。
- 演示数据不覆盖 `kind='part'` 与 preface/backmatter/toc 层（理由见第 5 节）。
  这两类由真实语料的一致性检查覆盖，公开站上的演示数据只覆盖常见形态。

---

## 13. 复现步骤

```bash
# 一致性验证（需要真实语料 + DB；只有在 Windows 上有意义，见 preflight）
cd HistoryAI
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.check_engine \
    --report docs/phase5_consistency.md

# 发布闸门
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.check_publish

# 装配发布产物（本地预览 Pages 上的样子）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.build_artifact _site
python -m http.server 8800 --directory _site     # 打开 127.0.0.1:8800

# 重建演示数据（走真管线；耗时约 1 分钟）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.make_demo_data

# 重建真实语料的静态导出（本地完整静态模式用；产物永不入库）
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m scripts.site.export_site frontend/data
```

---

## 附：§35 验收清单逐条对照

| # | 要求 | 结果 |
|---|---|---|
| 1 | 公开站可访问，无 404、无控制台报错 | 见第 8 节（产物装配 + 三种模式实测）；线上访问需仓库设置里启用 Pages（Actions 源），见第 14 节 |
| 2 | 不发布 Kanripo 语料 | ✅ 闸门 3 条判据 + 6 个场景双向验证 |
| 3 | 不发布 `history.db` / `*.db` / `*.sqlite*` | ✅ 白名单判据 + workflow 独立复查 |
| 4 | 不把语料塞进 Git | ✅ `git ls-files` 0 语料 / 0 db / 0 `data/` |
| 5 | 不接任何 AI API、不接本地模型、不做 RAG | ✅ 全站零外部依赖、零网络请求（除静态资源） |
| 6 | 不改动 Phase 1–4 检索逻辑（§18） | ✅ `search/` + `api/` 一行未改，144 测试全绿 |
| 7 | 本地完整版不退化（§37） | ✅ API 模式实测：横幅不显示，行为与第四阶段一致 |
| 8 | 一致性验证报告入库 | ✅ `docs/phase5_consistency.md`（自动生成，13 项全通过） |
| 9 | 不买域名、代码不绑定自定义域名 | ✅ 只用 `doctor325.github.io` 默认域 |
| 10 | 不 force push、不删历史（§34） | ✅ 未执行任何破坏性 git 操作 |
| 11 | 不改 `HistoryLibrary`（§32） | ✅ 见第 11 节 |
| 12 | Git 根为 `D:\MyCode\HistoryProject`（§33） | ✅ `git rev-parse --show-toplevel` 确认；未执行 `git init D:\MyCode`、未执行 `git add D:\MyCode\*` |
| 13 | 演示数据明确标注、不与真实史料混淆 | ✅ 横幅 + 书名「演示樣例·甲/乙」+ 每份数据文件头部 LICENSE/说明注释 |
| 14 | 做不到完整在线检索时标 PARTIAL 并给出规定措辞 | ✅ 第 9 节，措辞逐字照用 |

---

## 14. 部署状态与下一步

**代码侧已就绪**：`.github/workflows/pages.yml`（build → 闸门 → 复查 → deploy）、
`build_artifact.py`、`check_publish.py` 都已落盘并本地验证。

**需要人工完成的一步**：GitHub 仓库的 Pages 源要设成 **GitHub Actions**
（Settings → Pages → Build and deployment → Source）。当前 `has_pages: false`，
这一步需要在网页上点。设好之后，push 到 `main` 会自动发布。

**发布后要做的事**：访问 https://doctor325.github.io/HistoryProject/ ，逐页点检
首页 / 全文检索 / 提问 / 数据检查 / 待确认注释 / 项目说明，确认横幅文案正确、
三种模式都出结果、无 404、控制台无报错。这一步**不能由本地验证替代** ——
本地验证的是「产物正确」，线上验证的是「托管行为符合预期」（子路径、MIME、
缓存策略、Pages 的 404 行为）。
