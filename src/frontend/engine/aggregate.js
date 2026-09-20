/* 第四阶段：事件级聚合与不同史书的对照 —— search/aggregate.py 的 JS 移植。
 *
 * ## 这一层不重新发明「事件边界」
 *
 * 任务书要求「片段 → 事件级聚合 → 不同史书对照」。事件边界**不由本模块决定**，
 * 而是复用第三阶段已经定好的 Result Block（result_block.js）：
 * 一个 block 就是一段可连续阅读的史料，它的边界由语料自身的证据收口 ——
 * 同一文件、同一结构键（section/subsection/ab）、同一个 `# src:` 段号、
 * 同一 layer。本模块只做两件事：
 *
 *   1. 把第四阶段的**相关性得分与命中理由**贴回每个 block（Phase 3 只有 bm25
 *      或 None，看不出「为什么这段和问题有关」）；
 *   2. 把 block 按**史书**分开摆好，供前端做对照展示。
 *
 * 之所以不另写一套事件切分：result_block._expand 的停止条件就是「同一件事的
 * 范围」的判据，重写一遍必然出现两种口径，用户会看到同一段史料在两个页面上
 * 边界不同。文本也一律沿用 block 的 text（由 text_orig 直接相接），
 * **本模块不截取、不拼接、不改写一个字**（§2.2）。
 *
 * ## 不同史书绝不合并
 *
 * 齊桓公之死在《春秋左傳》和《史記》里是两条各自独立的记载。把它们合成一条
 * 「事件」等于替用户裁决两部史书的异同 —— 那是史学研究，不是检索系统该做的
 * （§八.4「不要猜」）。所以本模块只做**并列**（sources），不做合并：
 * 每部史书的记载各自成条，各自带自己的书名、篇卷、原文。
 *
 * ## 为什么事件里可能只有一个 block
 *
 * 因为没有证据说更多。aggregate 只在**同文件 + 结构键完全相同**时才把多个
 * block 并成一个事件（这种情形出现在命中相距很远、Phase 3 的区间合并不上、
 * 但结构键证明它们同属一段的时候）。拿不出这种证据时，一段就是一件事 ——
 * 宁可让用户多点开几条，也不把两件事说成一件事（§十：宁可短，不可臆造）。
 *
 * ## 与 Python 侧的唯一差别
 *
 * 数据来源：Python 从 SQLite 取行窗口，这里从内存语料取（result_block.js 的
 * FileCache 已经是内存版）。组装规则、排序键、文案一字未改。
 */
(function (NS) {
  "use strict";

  const RB = NS.resultBlock;

  // 送进 block 组装的命中条数。40 条足够覆盖一个问题的相关史料，又不会让
  // 组装过程（每文件都要取行窗口）变慢 —— 实测 40 条在 100ms 量级。
  const TOP_FOR_BLOCKS = 40;

  /** block 的结构键：完全相同才算同一段。字段缺失时返回 null（不猜）。 */
  function structKey(block) {
    const parts = [block.juan, block.section, block.subsection,
                   block.division, block.ab];
    if (!parts.some((p) => p)) return null;
    return parts;
  }

  // 分簇间距：同一文件里的两条命中相隔超过这个行数就分两批组装。
  // 取 Phase 3 行窗口上限的一半 —— 窗口宽 600 行、两侧还各留 20 行余量，
  // 半窗一组能保证每组的命中都落在自己那次的窗口内。
  const CLUSTER_GAP = Math.floor(RB.WINDOW_HARD_CAP / 2);

  /** 把命中按「同文件 + 行号相近」分批。
   *
   *  为什么必须分批：Phase 3 的 FileCache.window 一次最多取 600 行（任务书
   *  §二十一禁止整文件入内存）。若把同一文件里相距几千行的命中塞进同一次调用，
   *  窗口只会盖住靠前的那几条，其余的在 `pos.get(pid) is None` 处被静默跳过 ——
   *  实测「管仲是怎么死的」40 条命中只装配出 17 个片段，《左傳》里真正的答案
   *  「管仲卒受下卿之禮」因为行号离别的命中太远，一个片段都没生成。
   *  分批之后每批各自的窗口都盖得住，组装结果再按下文去重。
   *
   *  分批的判据是「与本批**第一条**的距离」，不是与上一条 —— 照抄 Python。 */
  function cluster(hits) {
    const groups = new Map();
    for (const h of hits) {
      if (!groups.has(h[1])) groups.set(h[1], []);   // 按 file_id 分组
      groups.get(h[1]).push(h);
    }
    const out = [];
    for (const hs of groups.values()) {
      hs.sort((x, y) => (x[2] - y[2]) || (x[3] - y[3]));   // (row_no, seq)
      let curBatch = [hs[0]];
      for (let k = 1; k < hs.length; k++) {
        const h = hs[k];
        if (h[2] - curBatch[0][2] > CLUSTER_GAP) {
          out.push(curBatch);
          curBatch = [h];
        } else {
          curBatch.push(h);
        }
      }
      out.push(curBatch);
    }
    return out;
  }

  /** 分批组装会产生重叠片段：区间被更大片段包住的丢掉（保宽的）。
   *
   *  只做包含判断，不做并集 —— 并集需要两侧行号都在同一个窗口里，跨批做不到；
   *  硬并会拼出中间缺行的正文，那是伪造原文（§2.2）。 */
  function dedupe(blocks) {
    const kept = [];
    const sorted = blocks.slice().sort((a, b) =>
      (a.file_id - b.file_id) || (a.row_first - b.row_first) ||
      (b.n_chars - a.n_chars));
    for (const b of sorted) {
      let hit = false;
      for (const k of kept) {
        if (k.file_id === b.file_id &&
            k.row_first <= b.row_first && k.row_last >= b.row_last) {
          k.match_count += b.match_count;
          hit = true;
          break;
        }
      }
      if (!hit) kept.push(b);
    }
    return kept;
  }

  /** 排序好的候选 → 事件（含跨史书对照）。
   *
   *  `ranked` 是 ranking.rank() 的输出（Candidate 列表，已按相关度降序）。
   *  返回的每个事件都带 `text`（原文，来自 text_orig）、`relevance`（第四阶段的
   *  相关度）与 `why`（逐项得分，可复核）。 */
  function aggregate(corpus, ranked, mode, top, textMode) {
    mode = mode === undefined ? RB.DEFAULT_MODE : mode;
    top = top === undefined ? TOP_FOR_BLOCKS : top;
    textMode = textMode === undefined ? NS.dualText.DEFAULT_MODE : textMode;

    const picked = ranked.slice(0, top).filter((c) => c.seq !== null && c.seq !== undefined);
    const out = { events: [], sources: [], limits: Object.assign({}, RB.MODE_LIMITS[mode]),
                  notes: [], n_blocks: 0 };
    if (!picked.length) {
      out.notes.push("没有候选片段，未组装事件");
      return out;
    }

    const hits = picked.map((c) => [c.passage_id, c.file_id, c.row_no, c.seq, c.score]);
    // 篇名区间表要走同一条路：问答路径不传的话，同一个查询在搜索页与问答页会
    // 得到**不同的块边界**（史記/國語的正文行没有行级 section，只有区间表能判
    // 篇界）。与 Python 侧 aggregate 同形。
    const secIdx = RB.sectionIndex(corpus);
    const blocksRaw = [];
    for (const batch of cluster(hits)) {
      for (const b of RB.buildResultBlocks(corpus, batch, mode, secIdx).blocks) blocksRaw.push(b);
    }

    // 命中 → 打分结果。一个 block 里可能有多个命中（Phase 3 已把区间合并），
    // 取其中**相关度最高**的那条作为这个 block 的「为什么相关」，其余记进
    // also_hits —— 不丢弃，用户可以复核。
    const byPid = new Map(picked.map((c) => [c.passage_id, c]));
    // 每条候选在排序里的名次：事件排序的第二关键字（见下文 _key）。
    // 这样「排在最前面的事件」就是「排序第一的那条史料所在的段落」，
    // 用户看到的第一个片段必然包含排名最高的命中。
    const rankOf = new Map(picked.map((c, i) => [c.passage_id, i]));
    const blocks = [];
    for (const raw of dedupe(blocksRaw)) {
      let inside = raw.passage_ids.filter((p) => byPid.has(p)).map((p) => byPid.get(p));
      if (!inside.length) {
        // 结构行（# src: / <pb:>）也被圈进 block 时可能出现：这些行不是命中，
        // 但它们的 text_orig 已被 Phase 3 排除在正文之外，这里照常保留 block。
        inside = byPid.has(raw.hit_passage_id) ? [byPid.get(raw.hit_passage_id)] : [];
      }
      if (!inside.length) continue;
      // Python 的 max(key=...)：同分取**先出现**的那条，故用严格大于。
      let best = inside[0];
      for (const c of inside) if (c.score > best.score) best = c;

      const b = Object.assign({}, raw);
      b.relevance = best.score;
      b.why = { hits: best.hits.slice(), detail: Object.assign({}, best.detail),
                passage_id: best.passage_id };
      b.also_hits = inside.filter((c) => c !== best)
        .map((c) => ({ passage_id: c.passage_id, score: c.score, hits: c.hits.slice() }));
      b.book_id = best.book_id;
      b.book_title = best.book_title;
      b.best_rank = rankOf.get(best.passage_id);
      // Phase 3 的 score 槽位在第四阶段装的是**第四阶段相关度**（上面 hits 的第
      // 5 个元素传的是 c.score），不是 bm25；而且 Phase 3 的合并取的是块内最小值，
      // 与这里的 relevance（取最大）口径不同，所以改名保留，免得被当成 bm25 误读。
      b.hit_score_min = b.score === undefined ? null : b.score;
      delete b.score;
      // 繁简双轨：原文留在 text 里不动，简体另存一个字段（见 dual_text）。
      blocks.push(NS.dualText.attach(b, textMode));
    }

    // 同文件 + 结构键完全相同 → 并成一个事件（证据不足则各自成事件）。
    const merged = [];
    for (const b of blocks) {
      const key = structKey(b);
      let hit = false;
      for (const m of merged) {
        if (key !== null && sameTuple(m._key, key) && m.file_id === b.file_id) {
          m.blocks.push(b);
          m.match_count += b.match_count;
          m.relevance = Math.max(m.relevance, b.relevance);
          m.best_rank = Math.min(m.best_rank, b.best_rank);
          hit = true;
          break;
        }
      }
      if (!hit) {
        merged.push({ _key: key, file_id: b.file_id, book_id: b.book_id,
                      book_title: b.book_title, blocks: [b],
                      match_count: b.match_count, relevance: b.relevance,
                      best_rank: b.best_rank });
      }
    }

    // 排序：相关度 → 最好那条命中的名次 → 命中条数 → 书/文件/行号。
    // 第二级用「名次」而不是「命中条数」是有原因的：命中多的块往往是**大块**
    // （史記世家一次收十几条），把「齐桓公卒」那句话埋在中间，反而把真正写着他
    // 死亡年月的那一小段挤到后面。按名次排，「排在最前面的事件」就是排序第一的
    // 那条史料所在的段落，用户点开第一件事看到的必然是排名最高的命中。
    // 末三级是数据库里的稳定字段，保证同分时两次请求结果一致。
    //
    // JS 的 Array#sort 自 ES2019 起是稳定排序，与 Python 的 Timsort 都是稳定排序，
    // 而上面这个键在 (book_id, file_id, row_first) 上还未必全序，稳定性就是契约。
    merged.sort((a, b) =>
      (b.relevance - a.relevance) ||
      (a.best_rank - b.best_rank) ||
      (b.match_count - a.match_count) ||
      cmpStr(a.book_id || "", b.book_id || "") ||
      (a.file_id - b.file_id) ||
      (Math.min.apply(null, a.blocks.map((x) => x.row_first)) -
       Math.min.apply(null, b.blocks.map((x) => x.row_first))));

    const events = [];
    for (let i = 0; i < merged.length; i++) {
      const m = merged[i];
      const first = m.blocks[0];
      events.push({
        event_id: "E" + (i + 1),
        book_id: m.book_id,
        book_title: m.book_title,
        file_id: m.file_id,
        // 篇卷标签：只如实带出数据库里有的，缺的写 None，不编「第几篇」
        juan: orNull(first.juan),
        section: orNull(first.section),
        subsection: orNull(first.subsection),
        division: orNull(first.division),
        relevance: NS.ranking.pyRound2(m.relevance),
        best_rank: m.best_rank,
        match_count: m.match_count,
        n_blocks: m.blocks.length,
        blocks: m.blocks,
      });
    }

    // 跨史书对照：按史书分开摆，**不合并**。顺序按各书最好的一条的相关度排，
    // 让记载更切题的史书排在前面。
    const sources = new Map();
    for (const ev of events) {
      if (!sources.has(ev.book_id)) {
        sources.set(ev.book_id, { book_id: ev.book_id, book_title: ev.book_title,
                                  n_events: 0, best_relevance: ev.relevance,
                                  event_ids: [] });
      }
      const s = sources.get(ev.book_id);
      s.n_events += 1;
      s.best_relevance = Math.max(s.best_relevance, ev.relevance);
      s.event_ids.push(ev.event_id);
    }
    const srcList = [...sources.values()].sort((a, b) =>
      (b.best_relevance - a.best_relevance) ||
      cmpStr(a.book_id || "", b.book_id || ""));

    out.events = events;
    out.sources = srcList;
    out.n_blocks = blocks.length;
    out.notes.push(
      `事件聚合：${hits.length} 条命中 → ${blocks.length} 个史料片段 → ` +
      `${events.length} 件事，分属 ${srcList.length} 部史书（不同史书各自成条，不合并）`);
    if (picked.length < ranked.length) {
      out.notes.push(
        `只把相关度最高的 ${picked.length} 条命中送去组装片段，` +
        `其余 ${ranked.length - picked.length} 条未展开（避免组装耗时失控）`);
    }
    return out;
  }

  /** Python 的 `first["juan"]`：字典里缺键读到的是 None。block 是 JS 对象，
   *  缺键读到 undefined，序列化后 undefined 会**整个键消失**（JSON 里没有），
   *  与 Python 的 null 不同 —— 统一归一成 null。 */
  function orNull(v) { return v === undefined ? null : v; }

  /** 结构键相等判定。任一侧是 null（= 结构字段全缺，判据不足）即**不相等**，
   *  除非两侧都是 null —— 但调用点已经先要求 `key !== null`，所以这里只为
   *  复刻 Python `None == (…,)` → False 这个语义：读到对方是 None 时不能当成
   *  「空元组」去逐项比（JS 里 null.length 会直接抛）。 */
  function sameTuple(a, b) {
    if (a === null || b === null) return a === b;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  /** 书号是 ASCII（KR2a0001 之类），逐码位比与 UTF-16 单元比同序；
   *  与 engine.js / result_block.js 里的同名函数同口径，故不复用 engine 的
   *  （那个没导出，而这里只需要这一处）。 */
  function cmpStr(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  NS.aggregate = { TOP_FOR_BLOCKS, CLUSTER_GAP, structKey, cluster, dedupe, aggregate };
})(window.YindeEngine = window.YindeEngine || {});
