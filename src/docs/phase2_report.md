# 第二阶段交付报告（先秦史料全文检索 + 中文查询台）

> 交付日期：2026-09-09　｜　仓库：`HistoryProject/HistoryAI`　｜　状态：浏览器验收期

## 1. 实际文件改动

**新增**
| 文件 | 内容 |
|---|---|
| `search/__init__.py` | 模块说明（原则：索引只吃 normalized_text；不猜文件名章节） |
| `search/zh.py` | 简↔繁转换（kernel32 LCMapStringEx，zh-CN；失败恒等回退，零依赖） |
| `search/engine.py` | 检索调度：FTS5 trigram / bigram / LIKE 三路 + 书/版别名 + 出处形状 |
| `search/context.py` | 上下文端点核心：同文件真实邻居（前 3/后 3，上限 10） |
| `frontend/index.html` `style.css` `app.js` | 中文查询台整站（无依赖 hash SPA，首页/全文检索/详情/数据检查/待确认注释/项目说明） |
| `tests/test_search_logic.py` | 检索纯逻辑 20 例（分词/转义/别名/简繁） |
| `tests/test_search_api.py` | 真实库检索/上下文 + API 契约 23 例 |
| `tests/perf_phase2.py` | 性能基准（逐操作 min/median 表） |

**修改**
`scripts/pipeline/sqlite_store.py`（FTS 索引动态构建 + 新增 bigram 辅助表）、`database/schema.sql`（镜像注释）、`api/main.py`（search/context 路由、stats 单遍聚合、**修复 raw 越界 bug**）、`api/db.py`（books/files 计数改单趟 GROUP BY FILTER，0.9s→0.17s）、`README.md`（第二阶段全文）。原始史料、`data/raw/pre_qin/` 零改动。

## 2. 数据库改动

`passages` 新增索引 `idx_pas_file_row(file_id, row_no, seq)`（上下文/回溯用）；原表结构与数据未变。两张 FTS 派生表由 `rebuild()` 末尾动态建、随重建整体重生：
- `passages_fts`（external-content，trigram）——≥3 字词子串检索；
- `passages_bg`（相邻两字 token，unicode61）——2 字词快路，与 `LIKE '%词%'` **集合等价**（用命中数回归锁定等价性）。

## 3. 搜索索引

| 项 | 值 |
|---|---|
| 类型 | SQLite FTS5（trigram + unicode61-bigram 辅助） |
| 索引来源 | 仅 `normalized_text`（kind='passage'）；`text_orig` 不进索引、永不改 |
| docs | fts 203,308；bg 200,358 |
| 构建时长 | trigram ~2s + bigram ~1.7s（`run_all` 全量约 12s） |
| 校验 | 齐桓公 fts 96 = like 96；管仲 bigram 102 = like 102 |

## 4. API 清单（全只读、全分页）

新增：`GET /api/search?q&book&edition&page&page_size`（默认 20、上限 100 封顶，绝不无限；参数非法 → 400 带中文提示）、`GET /api/passages/{id}/context?before&after`（真实 DB 邻居，非 AI；未知 id → 404）。保留第一阶段全部：`/api/stats`（新增 fts.bigram 字段）、`/api/books`、`/api/books/{id}/files`、`/api/files/{id}`、`/api/files/{id}/passages`、`/api/files/{id}/raw`、`/api/passages/{id}`。

## 5. 前端

中文界面，无第三方依赖：首页「先秦史料查询」搜索框 + 史书卡（示例词全部经过真实 DB 命中核验）；`#/search` 结果史料卡片（书名/卷/篇/页码/版本如实展示、缺失「暂无」）；`#/p/{id}` 阅读式详情（宋体大字号、上下文前 3/后 3 可点跳、数据详情默认折叠）；`#/books → #/book → #/file` 数据检查台（解析记录 + 原文对照两页签）；`#/pending` 逐条过 SBCK 括号候选；`#/about`。括号注候选仅角标「原刊括号内容 · 待确认」，状态与注者归属（韦昭/高诱）一律不动。tls 按原样显示、SBCK 显示「四部丛刊（SBCK）」。

## 6. 性能实测（本地 loopback，min/median）

首页数据 90/92ms · 书列表 153/167ms · 文件列表 24ms · 记录分页 2.1ms · 原文对照 3–7ms · FTS 检索 4–6ms · **bigram 管仲 4.9ms（改造前 LIKE 362ms）** · 限定书+版 15ms · 深分页 8.8ms · 详情 4.5ms · 上下文 5.3ms —— 全部 ≤170ms（目标 ≤300ms）。卡顿根因（默认 100 行 DOM + 重复 stats + 子查询计数）已消除。

## 7. 测试

86/86 通过（10.2s）：`test_search_logic` 20 + `test_search_api` 23 + 既有 43。新增覆盖：命中基数回归、bigram↔LIKE 一致性、简繁转换、书名/版本别名、400/404 契约、分页边界、上下文邻接与排序、未知 id。

## 8. 阶段一回归

43/43 通过（test_header 5 + test_structure 22 + test_library_fixtures 9 + test_e2e 7）；`run_all` 校验 **118/118**：sha256、字符守恒、头部元数据、pb_bad=0、kr_bad=0。

## 9. HistoryLibrary 零写入确认

全程（解析、重建、检索、基准、测试）只读 library；API/测试连接均 `mode=ro`；`data/raw/pre_qin/` 冗余副本未删未盖，仅标注。FTS 表为纯派生，删 data/ 后 `run_all` 即复原。

## 10. 已知问题

①「崤之战」搜不到——语料原文作「殽」（殽之戰 66 处），零结果提示引导用短词；② 1 字查询无索引可走，仍 LIKE 全扫 ~350ms；③ 重建后 file_id/passage_id 从 1 起（API 按当前库查询，不受影响）；④ `stats.fts.docs` 为正文文档数口径（external-content FTS 的 count(*) 会读 content 表，已在注释记录）。

## 11. 启动方式

```bash
PYTHONIOENCODING=utf-8 python -m api.main      # http://127.0.0.1:8600/
```

## 12. 浏览器验收路径

首页搜「齐桓公」(96) /「管仲」(102) → 点结果卡「查看原文 · 上下文」（前 3/后 3 真实相邻句）→「待确认注释」选國語逐条过括号 → 任意文件「原文对照」页签对账 → 检索限定「國語 + 四部丛刊（SBCK）」验证全出該版本 → 复测 `python tests/perf_phase2.py`。

---

**两件收尾小事**：① VS Code 修复时留下的一个空目录（在仓库根下、名为 `Microsoft VS Code`）还被某个开着 VS Code 的进程占作工作目录——关掉 VS Code 后即可手动删除；② 本仓库尚无任何 commit（整个工作区未跟踪），如需把这一阶段落盘可以随时 `git add` 提交。

> 注：原文此处写了本机绝对路径（工作区根 + 目录名），第四阶段发布前安全审计（§22）要求源码与文档里不出现本机绝对路径，故改为相对描述。
