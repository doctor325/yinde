/* 引擎一致性验证 —— Node 侧。由 scripts/site/check_engine.py 调用。
 *
 * 每个子命令打印一行 JSON（摘要或明细），Python 侧算同样的东西再逐项比对。
 * 比摘要而不是比整体文本：两边的 JSON 转义规则不同（尤其孤立代理项），
 * 摘要按**码位字节**规范化，绕开这个差异。
 */
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import { loadEngine, loadCorpus, loadStaticData, loadSiteBoot, utf16beBytes }
  from "./loader.mjs";

const argv = process.argv.slice(2);
const cmd = argv[0];
const opt = {};
for (let i = 1; i < argv.length; i += 2) opt[argv[i].replace(/^--/, "")] = argv[i + 1];

const NS = loadEngine();

function digestRows(rows, pidOf, textOf) {
  const h = crypto.createHash("sha256");
  let n = 0, ok = 0, fallback = 0, changed = 0;
  for (const r of rows) {
    const res = NS.dualText.simplify(textOf(r));
    n++;
    if (res.ok) ok++; else fallback++;
    changed += res.changed;
    h.update(Buffer.from(`${pidOf(r)}|${res.ok ? 1 : 0}|${res.changed}|`, "utf8"));
    h.update(utf16beBytes(res.text));
    h.update(Buffer.from("\n", "utf8"));
  }
  return { rows: n, ok, fallback, changed, digest: h.digest("hex") };
}

const CHECKS = {
  /** dual_text.simplify 在全部 203,308 条 passage 正文上的汇总摘要。 */
  "dual-text-corpus"() {
    const c = loadCorpus(opt.corpus);
    // 只比 kind='passage'：只有这些行的 normalized/显示文本走 dual_text。
    const rows = c.rows.filter((r) => c.get(r, "kind") === "passage");
    return Object.assign(
      { check: "dual-text-corpus", corpus: opt.corpus, source: "text_orig" },
      digestRows(rows, (r) => c.get(r, "passage_id"), (r) => c.get(r, "text_orig")));
  },

  /** normalized_text 重算：JS 现算 vs 数据库真值，全 224,822 行。
   *  Python 侧直接读库里的 normalized_text，所以这条检查同时覆盖
   *  make_normalized 的实现、Python 的 strip 语义、以及「只有 passage 行有值」的规则。 */
  "norm-corpus"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const pids = [...c.byId.keys()].sort((a, b) => a - b);
    const h = crypto.createHash("sha256");
    let nonNull = 0;
    for (const pid of pids) {
      const t = c.norm[c.byId.get(pid)];
      if (t !== null) nonNull++;
      h.update(Buffer.from(`${pid}|${t === null ? -1 : 1}|`, "utf8"));
      if (t !== null) h.update(utf16beBytes(t));
      h.update(Buffer.from("\n", "utf8"));
    }
    // 顺带自检索引结构：这些不变式若被破坏，后面所有检索都会错。
    const spans = [...c.fileSpan.values()].sort((a, b) => a[0] - b[0]);
    let contiguous = true;
    for (let i = 1; i < spans.length; i++) {
      if (spans[i - 1][1] !== spans[i][0]) contiguous = false;
    }
    return {
      check: "norm-corpus",
      rows: pids.length, nonNull, passageIdx: c.passageIdx.length,
      files: c.fileSpan.size, books: c.bookById.size,
      contiguous, byIdSize: c.byId.size,
      digest: h.digest("hex"),
    };
  },

  /** 两条 dl 推导 vs 数据库影子表，全表。
   *
   *  dl 是 bm25 的输入之一：trigram 的 dl = max(0, 码位数-2)，bigram 的 dl =
   *  bg 列的 unicode61 token 数（含分隔符判定与星形字按码位切）。推导错一位，
   *  分数就错一片 —— 但只在排序上体现，靠逐条搜索很难覆盖全，所以这里全表对。
   *  不在 bigram 影子表里的行（没有任何相邻两字组）其 token 数必为 0。 */
  "dl-corpus"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const pids = [...c.byId.keys()].sort((a, b) => a - b);
    const h = crypto.createHash("sha256");
    let triZero = 0, bgZero = 0, triTotal = 0, bgTotal = 0;
    for (const pid of pids) {
      const t = c.norm[c.byId.get(pid)];
      const tri = t === null ? 0 : NS.engine.dlTrigram(t);
      const bg = t === null ? 0 : NS.engine.bgTokens(t).length;
      if (tri === 0) triZero++;
      if (bg === 0) bgZero++;
      triTotal += tri;
      bgTotal += bg;
      h.update(Buffer.from(`${pid}|${tri}|${bg}|`, "utf8"));
    }
    return {
      check: "dl-corpus", rows: pids.length,
      triZero, bgZero, triTotal, bgTotal, digest: h.digest("hex"),
    };
  },

  /** engine.runSearch 对固定查询集的结果。Python 侧跑 engine.run_search 比对。
   *  查询集由 Python 侧给出（单一事实来源），逐字段对拍，不做摘要。 */
  "search"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const queries = JSON.parse(fs.readFileSync(opt.queries, "utf8"));
    const results = queries.map((qy) => {
      try {
        return NS.engine.runSearch(c, qy.q, {
          book: qy.book, edition: qy.edition,
          page: qy.page, pageSize: qy.page_size,
        });
      } catch (e) {
        return { error: String(e && e.message) };
      }
    });
    return { check: "search", results };
  },

  /** 实体层：全语料扫描的产物 + 固定样本上的裁决结果。
   *
   *  full/short/bare 是**全语料**统计（不是样本），键数几百，直接对拍。
   *  样本集由 Python 侧给出（单一事实来源），与 search 同样做法。
   *
   *  这三张表的**键序**也是被测对象：full/short 的插入序一路传到短称候选顺序
   *  （resolve_short 在频次相同时按稳定排序保留插入序），所以 JS 侧必须按
   *  数据库 rowid 序（= passage_id 升序）扫，不能按打包序扫。 */
  "entities"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const ent = new NS.entities.Entities(c);
    const samples = JSON.parse(fs.readFileSync(opt.samples, "utf8"));
    const scan = ent._scan();
    return {
      check: "entities",
      states: NS.entities.STATES, statesExtra: NS.entities.STATES_EXTRA,
      shortMinBare: NS.entities.SHORT_MIN_BARE,
      curated: NS.entities.curated(), ambiguous: NS.entities.ambiguousHeads(),
      full: ent.corpusNames(), short: ent.shortForms(),
      bare: scan.bare, known: ent.knownEntities(),
      occurs: samples.words.map((w) => ent.occursInCorpus(w)),
      resolveAlias: samples.alias_cases.map((a) => ent.resolveAlias(a[0], a[1])),
      resolveShort: samples.short_cases.map((s) => ent.resolveShort(s[0], s[1])),
      bareHit: samples.bare_cases.map((b) => ent.bareHit(b[0], b[1])),
      findInText: samples.texts.map((t) => ent.findInText(t)),
    };
  },

  /** 问题分析 + 检索式扩展：固定问题集上的完整产物。
   *
   *  比的是**整棵结果树**（analyze 的 asDict + expand 的 asDict），不是摘要：
   *  这里没有大表，逐字段直接对拍最省事，也最容易看出哪一步走偏。
   *  问题集由 Python 侧给出（单一事实来源）。 */
  "question"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const ent = new NS.entities.Entities(c);
    const qs = JSON.parse(fs.readFileSync(opt.samples, "utf8")).questions;
    return {
      check: "question",
      analyses: qs.map((s) => NS.question.analyze(s, ent).asDict()),
      expansions: qs.map((s) =>
        NS.queryExpansion.expand(NS.question.analyze(s, ent), ent).asDict()),
    };
  },

  /** 排序层：对**同一批候选池**打分，与 Python 逐条对拍。
   *
   *  候选池由 Python 侧给出（它直接用 retrieve._like_any 的 SQL 取数），
   *  JS 侧只负责打分与排序 —— 这样这条检查压的是 ranking.js 本身，
   *  而不是又一次重测召回路径（召回已由 search 检查覆盖）。
   *  池子的输入顺序不影响结果：排序键 (score, book_id, file_id, row_no)
   *  在 passage 上是全序，不存在同分同位的情形。 */
  "ranking"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const ent = new NS.entities.Entities(c);
    const cases = JSON.parse(fs.readFileSync(opt.samples, "utf8")).cases;
    return {
      check: "ranking",
      results: cases.map((cs) => {
        const cands = cs.pool.map((r) => new NS.ranking.Candidate(r));
        const ranked = NS.ranking.rank(
          cands, cs.entity_terms, cs.alias_terms, cs.weak_terms,
          cs.intent_terms, cs.asks_duration, cs.intent_weak, cs.topic_terms,
          cs.asked_forms, cs.entity_forms, ent);
        return ranked.map((c) => ({
          passage_id: c.passage_id, score: c.score,
          hits: c.hits, detail: c.detail,
        }));
      }),
    };
  },

  /** Result Block 组装 + 上下文展开：整棵结果树对拍。
   *
   *  这是第三阶段的展示层核心（片段边界、合并去重、繁简双轨、可展开标记），
   *  查询集与展开用例都由 Python 侧给出（单一事实来源）。 */
  "result-block"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const spec = JSON.parse(fs.readFileSync(opt.samples, "utf8"));
    const results = spec.queries.map((s) => {
      try {
        return NS.resultBlock.searchResultBlocks(c, s.q, {
          book: s.book, edition: s.edition, page: s.page,
          pageSize: s.page_size, mode: s.mode, textMode: s.text_mode,
        });
      } catch (e) {
        return { error: String(e && e.message) };
      }
    });
    const expands = spec.expands.map((s) => {
      try {
        return NS.resultBlock.expandBlock(c, s.passage_id, {
          direction: s.direction, count: s.count,
          beforePassageId: s.before_passage_id,
          afterPassageId: s.after_passage_id,
        });
      } catch (e) {
        return { error: String(e && e.message) };
      }
    });
    return { check: "result-block", results, expands };
  },

  /** 事件聚合：对**同一批已排序候选**做组装，与 Python 逐字段对拍。
   *
   *  候选池由 Python 侧给出（它跑完整的 retrieve+rank 再序列化），JS 侧只负责
   *  聚合 —— 这样这条检查压的是 aggregate.js 本身，而不是又一次重测召回与排序
   *  （那两层各自已有检查覆盖）。
   *
   *  与 Python 的 aggregate 一样，每个用例单独 new 一次 Candidate（聚合只读
   *  score/hits/detail，不写回），所以用例之间不会互相污染。 */
  "aggregate"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const spec = JSON.parse(fs.readFileSync(opt.samples, "utf8"));
    const cases = spec.cases.map((cs) => {
      const ranked = cs.ranked.map((o) => new NS.ranking.Candidate(o));
      try {
        return NS.aggregate.aggregate(c, ranked, cs.mode, cs.top, cs.text_mode);
      } catch (e) {
        return { error: String(e && e.message) };
      }
    });
    return { check: "aggregate", cases };
  },

  /** 提问链路的对外结构：analyze → expand → 召回 → 排序 → 聚合，整棵树对拍。
   *
   *  这条是**端到端**检查（Python 侧只给问题原文，召回/排序都在 JS 侧跑），
   *  压的是 retrieve.js 的编排：两池召回的顺序与截断、同段判定的写法、
   *  以及 asDict 的字段形状。 */
  "retrieve"() {
    const dir = path.dirname(opt.corpus) + "/";
    const c = loadStaticData(dir, NS);
    const ent = new NS.entities.Entities(c);
    const spec = JSON.parse(fs.readFileSync(opt.samples, "utf8"));
    return {
      check: "retrieve",
      cases: spec.cases.map((cs) => {
        try {
          const res = NS.retrieve.retrieve(c, cs.q, ent);
          return NS.retrieve.asDict(res, cs.top, {
            corpus: c, mode: cs.mode, textMode: cs.text_mode,
          });
        } catch (e) {
          return { error: String(e && e.message) };
        }
      }),
      // 召回扫描本身：词表 → passage_id 序列（顺序 + LIMIT 截断都在里面）
      scans: spec.scans.map((s) =>
        NS.retrieve.likeAny(c, s.terms, s.limit).map((x) => x.passage_id)),
    };
  },

  /** 静态分发器（static_api.js）对**全部 10 条路由**的响应，与真 API 逐字段对拍。
   *
   *  公开站上没有 Python 进程，`/api/*` 全靠这个分发器在浏览器里重新实现；
   *  它一旦与 api/main.py 走偏，页面上的表现就是「本地能看、线上不对」——
   *  而且是在公开站上。所以这里把路由、参数解析、错误消息、返回结构整条链路
   *  都拉出来对一遍，路径清单由 Python 侧给出（单一事实来源）。
   *
   *  取数走 fs（raw 目录按需读），与浏览器用 fetch 取的是同一批导出文件。 */
  "static-api"() {
    const dir = opt.staticdir;
    const paths = JSON.parse(fs.readFileSync(opt.paths, "utf8"));
    const readJson = (name) => {
      try {
        return JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
      } catch (e) {
        return null;
      }
    };
    const site = new NS.staticApi.Site({
      corpus: loadStaticData(dir, NS),
      books: readJson("books.json"), files: readJson("files.json"),
      bookFiles: readJson("book_files.json"), stats: readJson("stats.json"),
      // 懒加载：只有真的请求 /raw 才读整份原始行
      raw: async (fid) => readJson("raw/" + fid + ".json"),
    });
    return Promise.all(paths.map(async (p) => {
      try {
        return await site.get(p);
      } catch (e) {
        // 与 api/main.py 的错误契约同形：{"error": msg}
        return { error: String((e && e.message) || e) };
      }
    })).then((results) => ({ check: "static-api", results }));
  },

  /** boot.js 冒烟：横幅与页脚在两种模式下各写了什么。
   *
   *  判据在 Python 侧 —— 这里只如实回传它写进 DOM 的字符串。 */
  "boot"() {
    return loadSiteBoot(opt.dir).then((r) => Object.assign({ check: "boot" }, r));
  },

  /** zh.js 与生僻/星形字符：逐项明细（样本小，直接对拍不必摘要）。 */
  "zh-table"() {
    const samples = JSON.parse(fs.readFileSync(opt.samples, "utf8"));
    return {
      check: "zh-table",
      size: NS.zh.size(),
      toTraditional: samples.map((s) => NS.zh.toTraditional(s)),
      toSimplified: samples.map((s) => NS.zh.toSimplified(s)),
    };
  },
};

if (!CHECKS[cmd]) {
  console.error(`未知子命令：${cmd}（可用：${Object.keys(CHECKS).join("、")}）`);
  process.exit(2);
}
// 有的检查是 async（static-api 要按需读原始行文件）。Python 侧读的是**最后一行**
// stdout，所以异步分支也要保证只吐一行 JSON。
const out = CHECKS[cmd]();
if (out && typeof out.then === "function") {
  out.then((v) => console.log(JSON.stringify(v)))
     .catch((e) => {
       console.error(String((e && e.stack) || e));
       process.exit(3);
     });
} else {
  console.log(JSON.stringify(out));
}
