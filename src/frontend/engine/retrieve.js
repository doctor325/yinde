/* 第四阶段：面向问题的召回与事件聚合 —— search/retrieve.py 的 JS 移植。
 *
 * 召回策略（这是第四阶段最关键的一处设计）
 * ------------------------------------------
 * **实体命中的段落必须被单独捞出来，不能和意图词命中的段落混在一个池子里排序。**
 *
 * 原因是实测出来的：`終`/`亡`/`殺` 这类词全库各处都是，如果和实体平起平坐地
 * OR 在一起，召回池（取前 N 条）会被纯意图词命中的噪声灌满，真正写着
 * 「齊桓公卒。」的段落根本进不了池子。实测就是这样：问「齊桓公何时死」，
 * 排在前面的是「終取之必以亡」这种段落，实体一次都没出现。
 *
 * 所以召回分两池：
 *
 *   · **实体池**：任一实体词命中 → 必收。问谁就找谁，这是用户真正的约束。
 *   · **兜底池**：没有任何实体命中时（问句里没识别出人物，或者语料里根本没这个人），
 *     才用意图强词去兜。
 *
 * 两池分开还有一个好处：能如实回答「语料里没有这个人」—— §18.3 的董卓测试
 * 要求系统不能凭空造答案，那就必须允许「实体池为空」这个结论存在。
 *
 * 事件聚合（§10 / §11）
 * ---------------------
 * 按**事件**把片段聚起来，而不是按关键词。聚合键来自史料本身：
 * 同一文件、`# src:` 段号相同、或行号相邻（沿用 Phase 3 result_block 的边界判断）。
 *
 * 不同史书**保持独立**：齊桓公之死在《左傳》和《史記》里是两条记录，绝不合并成
 * 一条 —— 合并等于替用户裁决史料的异同（§八.4「不要猜」）。
 *
 * ## 与 Python 侧的两处差别（都是数据来源，不是逻辑）
 *
 *   1. 取数从 SQL 换成内存语料：`_like_any` 的 `normalized_text LIKE '%词%'`
 *      等价于对 normalized 文本做子串匹配（LIKE 只对 ASCII 折大小写，JS 侧的
 *      mtext 与 likeScan 已是同一口径，见 corpus.js）；
 *   2. Entities 实例由调用方传进来（Python 侧 entities 是模块级函数 + 现读库，
 *      JS 侧是构造时全语料扫一遍的实例），所以几个原本不需要它的调用要多传一个
 *      参数 —— 逻辑一字未改。
 */
(function (NS) {
  "use strict";

  // 召回上限：够覆盖一个问题的全部相关史料，又不至于把库全扫一遍
  const POOL_LIMIT = 2000;

  /** 召回结果 + 过程中的可复核信息。 */
  class Retrieved {
    constructor(question, expanded) {
      this.question = question;                 // question.Question
      this.expanded = expanded;                 // query_expansion.Expanded
      this.entity_pool = [];                    // ranking.Candidate
      this.fallback_pool = [];
      this.ranked = [];
      this.notes = [];
    }
  }

  /** 行下标 → Candidate。字段与 Python `_cand(r)` 一一对应：
   *  seq 缺省补 0，text_orig / title / layer 缺省补空（或 main）。 */
  function candOf(corpus, i) {
    const r = corpus.rows[i];
    const c = corpus.col;
    const b = corpus.bookOfRow[i];
    return new NS.ranking.Candidate({
      passage_id: r[c.passage_id],
      file_id: corpus.cell(i, "file_id"),
      row_no: r[c.row_no],
      seq: r[c.seq] || 0,
      text_orig: r[c.text_orig] || "",
      layer: corpus.cell(i, "layer") || "main",
      book_id: b ? b.book_id : "",
      book_title: (b && b.title) || "",
    });
  }

  /** 一次扫库取出命中**任一**词的段落，按 passage_id 升序取前 limit 条。
   *
   *  两处必须照 Python 来：
   *
   *  1. **合并成一次 OR 扫描。** LIKE '%x%' 用不上索引，一个词就是一次全表
   *     扫描（实测 20 万行约 76ms）。问一句「齊桓公什么时候死的」要查三个词
   *     （齊桓公/桓公/小白），三次扫描就是 230ms —— 合并成一次，结果完全相同
   *     （都是「命中任一词」的并集，调用方本来就去重），耗时降到三分之一。
   *  2. **顺序是 passage_id 升序。** Python 侧的 SQL 没有 ORDER BY，靠 SQLite
   *     扫 passages 给出 rowid 序；而 `LIMIT ?` 截的正是这个顺序。JS 的 likeScan
   *     走的是**打包序**（按 book_id/file_no/row_no 分组），不重排的话池子被截断
   *     时两边装的是**不同的段落**。 */
  function likeAny(corpus, terms, limit) {
    const ts = terms.filter((t) => t);
    if (!ts.length) return [];
    const rows = corpus.likeScan(ts, { any: true }).hits;   // OR，不是 AND
    const cPid = corpus.col.passage_id;
    rows.sort((a, b) => corpus.rows[a][cPid] - corpus.rows[b][cPid]);
    return rows.slice(0, limit).map((i) => candOf(corpus, i));
  }

  /** 一句问题 → 排序好的候选史料。全程只读，不写语料、不改原文。 */
  function retrieve(corpus, qtext, ents) {
    const q = NS.question.analyze(qtext, ents);
    const exp = NS.queryExpansion.expand(q, ents);
    const out = new Retrieved(q, exp);

    // 实体词按扩展层分好的档位取用（档位依据见 query_expansion.termRoles）
    const termsOf = (role) => exp.groups.filter((g) => g.role === role)
      .flatMap((g) => g.terms);
    const entityTerms = termsOf("entity");
    const aliasTerms = termsOf("alias");
    const weakTerms = termsOf("weak");
    const intentTerms = termsOf("intent");
    const weakIntent = termsOf("intent_weak");

    const seen = new Set();
    const entityQuery = entityTerms.concat(aliasTerms, weakTerms);
    for (const c of likeAny(corpus, entityQuery, POOL_LIMIT)) {
      if (!seen.has(c.passage_id)) {
        seen.add(c.passage_id);
        out.entity_pool.push(c);
      }
    }
    out.notes.push(`实体池：${out.entity_pool.length} 条` +
                   `（实体词 ${NS.ranking.pyReprList(entityQuery)}）`);

    const topicTerms = termsOf("topic");
    if (!out.entity_pool.length) {
      // 没有人物实体时，「城濮之战」这类**事件名**就是用户真正的约束，
      // 所以主题词要和意图词一起兜底 —— 只兜意图词会让这类问句空手而归
      // （实测：「城濮之战谁赢了」曾经返回 0 条，而语料里「城濮」有大把段落）。
      const seen2 = new Set();
      const fallbackQuery = topicTerms.concat(intentTerms);
      for (const c of likeAny(corpus, fallbackQuery, POOL_LIMIT)) {
        if (!seen2.has(c.passage_id)) {
          seen2.add(c.passage_id);
          out.fallback_pool.push(c);
        }
      }
      out.notes.push(
        `实体池为空，兜底池：${out.fallback_pool.length} 条` +
        `（主题词 ${NS.ranking.pyReprList(topicTerms)} + ` +
        `意图强词 ${NS.ranking.pyReprList(intentTerms)}）`);
      if (!out.fallback_pool.length) {
        out.notes.push("语料中没有该实体，也没有可用的意图词 —— 如实返回空，不编造");
        return out;
      }
    }

    const pool = out.entity_pool.length ? out.entity_pool : out.fallback_pool;
    // 用户在问句里**实际写下的**称谓（「重耳」而不是规范名「晉文公」）——
    // 命中它比命中规范名更该算数，交给排序层当精确命中处理。
    const askedForms = q.entities.filter((e) => e.matched).map((e) => e.matched);
    const forms = entityForms(q, ents);
    out.ranked = NS.ranking.rank(
      pool, entityTerms, aliasTerms, weakTerms, intentTerms,
      exp.asks_duration || false, weakIntent, topicTerms, askedForms, forms, ents);

    // 问「A 和 B 是什么关系」时，若**没有任何一段**同时写着这两个人，必须如实
    // 说清楚：下面的结果只是分别提到其中一方。不说的话，用户会把「穆公相之」
    // 当成两者的关系记载 —— 那就是系统在替史料下结论（§八.4「不要猜」）。
    //
    // 判定必须用**全部写法**，不能只看规范名：语料里写的是「秦繆公」，只查
    // 「秦穆公」会得出「没有一段同时写着两人」这个**假的**结论 —— 实测語料里
    // 就有 2 段同时写着 繆公 与 百里奚（「繆公」正是秦穆公的已核实别名）。
    // 系统可以少说，不能说错。
    if (q.entities.length >= 2) {
      const groups = forms;
      const both = out.entity_pool.filter((c) =>
        groups.every((g) => NS.ranking.hasForm(c.text_orig || "", g, ents)));
      const names = q.entities.map((e) => e.entity).join(" 与 ");
      if (!both.length) {
        if (out.entity_pool.length >= POOL_LIMIT) {
          // 池子被截断时不敢断言「语料里没有」，只说清检索范围
          out.notes.push(
            `在前 ${POOL_LIMIT} 条候选里没有任何一段同时写着 ${names} —— ` +
            `以下结果只是分别提到其中一方，系统不据此推断两者的关系`);
        } else {
          out.notes.push(
            `语料中没有任何一段同时写着 ${names} —— 以下结果只是分别提到` +
            `其中一方，系统不据此推断两者的关系`);
        }
      } else {
        out.notes.push(`语料中有 ${both.length} 段同时写着 ${names}，已优先排在前面`);
      }
    }
    return out;
  }

  /** 每个实体在语料里的全部写法 [[档位, 词], …]，用于判定「同段」。
   *
   *  实体词按可核实的档位取用（档位依据见 query_expansion.termRoles）：
   *  规范名用字面比对，别名/短称要过 bare_hit —— 否则「鄭穆公」里的「穆公」
   *  会被当成秦穆公（判据见 entities.bareHit）。 */
  function entityForms(q, ents) {
    return q.entities.map((e) =>
      NS.queryExpansion.termRoles(e.entity, e.matched, ents).filter(([, t]) => t));
  }

  /** 给 API 层用的可序列化结构。文本一律原样来自 text_orig，不做任何加工。
   *
   *  `withEvents`（Python 侧的 cur 参数）为真时才会做事件聚合（聚合要组装片段，
   *  是额外的一次取数）。 */
  function asDict(res, top, opts) {
    opts = opts || {};
    const topN = top === undefined || top === null ? 20 : top;
    const out = baseDict(res, topN);
    if (opts.withEvents !== false) {
      out.aggregation = NS.aggregate.aggregate(
        opts.corpus, res.ranked, opts.mode === undefined || opts.mode === null
          ? NS.resultBlock.DEFAULT_MODE : opts.mode,
        undefined, opts.textMode === undefined || opts.textMode === null
          ? NS.dualText.DEFAULT_MODE : opts.textMode);
    }
    return out;
  }

  function baseDict(res, top) {
    return {
      question: res.question.asDict(),
      expanded: res.expanded.asDict(),
      counts: {
        entity_pool: res.entity_pool.length,
        fallback_pool: res.fallback_pool.length,
      },
      notes: res.notes,
      results: res.ranked.slice(0, top).map((c) => ({
        passage_id: c.passage_id,
        file_id: c.file_id,
        row_no: c.row_no,
        book_id: c.book_id,
        book_title: c.book_title,
        layer: c.layer,
        text_orig: c.text_orig,
        score: c.score,
        hits: c.hits,
        detail: c.detail,
      })),
    };
  }

  NS.retrieve = { POOL_LIMIT, Retrieved, candOf, likeAny, retrieve,
                  entityForms, asDict, baseDict };
})(window.YindeEngine = window.YindeEngine || {});
