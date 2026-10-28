/* 检索核心（浏览器版）—— search/engine.py 的忠实移植。
 *
 * Python 原版一行未改（§18）。这里只做转写，并把三条 SQL 路径换成对内存语料的
 * 等价扫描。对拍方式见 scripts/site/check_engine.py。
 *
 * 三条路径的语义
 * --------------
 * run_search 依词长选 fts(trigram) / bigram / like。三条路径的**命中集合**在
 * 正常情况下都等价于「normalized_text 含全部词项」（AND），差别在排序：
 *
 *   fts / bigram → ORDER BY bm25, book_id, file_no, row_no
 *   like         → 打包顺序（= book_id, file_no, row_no, seq），见 corpus.js
 *
 * bigram 的例外（已实测，不是推测）
 * ---------------------------------
 * bigram 表建在「相邻两字」拼成的 bg 列上，unicode61 会把**非字母数字字符**
 * （Unicode 类别 Cc/Cf/Cs/Pd/Pe/Pf/Pi/Po/Ps/Sc/Sk/Sm/So/Zl/Zp/Zs）当分隔符。
 * 于是 2 字词项进 MATCH 时会先被切一遍：
 *
 *   「管仲」（都是字母数字）→ 短语 「管仲」 → 等价于子串命中
 *   「。管」「管。」          → 短语 「管」   → 命中的是「管」单独成词的行
 *   「。。」「((」           → 空短语        → FTS5 丢掉该词项
 *
 * 实测（scripts/site 的探针，13 个词项全部逐行吻合）：`。管` 真库命中 42 行，
 * 而 `LIKE '%。管%'` 只有 0 行 —— 两者并不等价。所以这里照抄 token 语义，
 * 不图省事写成子串。全部词项都被丢掉时 FTS5 返回空集（不是全集），照抄。
 *
 * 大小写
 * ------
 * SQLite 的 LIKE 与两个分词器都对 ASCII 字母大小写不敏感（实测 'aaa' 能被
 * 'AAA' 命中）。匹配一律走 corpus.mtext（已折叠）与折叠后的词项。
 * 非 ASCII 的大小写折叠与 unicode61 的 remove_diacritics 未复现：本语料里
 * 一个这样的字符都没有，`\p{L}` 之外的差异无从检验，写进报告而不是假装支持。
 *
 * 排序的最后一级
 * --------------
 * SQL 的 ORDER BY 到 p.row_no 为止，同键行的先后由 sorter 的输入序（FTS 迭代
 * 出来的 rowid 序）决定。这里补一级 passage_id 兜底 —— 打包序里有 2 处
 * passage_id 逆序，两种口径只在那两行上可能不同。
 */
"use strict";
(function (NS) {
  const PAGE_SIZE_MAX = 100;
  const PAGE_SIZE_DEFAULT = 20;

  // Python 的 \s（不是 JS 的 \s：两边差 U+001C-1F、U+0085、U+FEFF 等字符）
  const WS_RE = new RegExp("[" + NS.PY_WS + "]+", "g");
  const WS_TRIM_RE = new RegExp("^[" + NS.PY_WS + "]+|[" + NS.PY_WS + "]+$", "g");

  // 常用书名的用户友好写法（简/繁），与 engine.py 的 _SHORT_NAMES 逐条对应。
  // 书目集合本身仍由 books.json 自动发现，这里只是把口语名映射到书号。
  const SHORT_NAMES = {
    "尚书": "KR1b0001", "尚書": "KR1b0001", "书经": "KR1b0001",
    "左传": "KR1e0001", "左傳": "KR1e0001", "春秋左传": "KR1e0001",
    "春秋左傳": "KR1e0001", "春秋": "KR1e0001",
    "史记": "KR2a0001", "史記": "KR2a0001",
    "国语": "KR2e0001", "國語": "KR2e0001",
    "战国策": "KR2e0003", "戰國策": "KR2e0003",
  };

  // unicode61 的分隔符类别（SQLite 文档口径）。已在语料全部 6,922 个字符上
  // 用 fts5vocab 逐字实测过：分隔符 74 个，恰好是这 16 个类别。
  // 注意 Co（私用区）**不是**分隔符，语料里的  等就靠它才能建出 token。
  const SEP_RE = /[\p{Cc}\p{Cf}\p{Cs}\p{Pd}\p{Pe}\p{Pf}\p{Pi}\p{Po}\p{Ps}\p{Sc}\p{Sk}\p{Sm}\p{So}\p{Zl}\p{Zp}\p{Zs}]/u;

  // ---------------------------------------------------------------- 小工具

  function cplen(s) { return [...s].length; }

  function pyStrip(s) { return s.replace(WS_TRIM_RE, ""); }

  function cmpStr(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  /** 重叠计数，对应 Python 的 str.count（indexOf 走一步而不是跳一个词长）。 */
  function countOcc(hay, needle) {
    if (!needle) return 0;
    let n = 0, at = 0;
    for (;;) {
      const k = hay.indexOf(needle, at);
      if (k < 0) return n;
      n++;
      at = k + 1;
    }
  }

  // ---------------------------------------------------------------- 词项

  /** plan_query：去空白后按 Python 的 \s 切词，分 (>=3 字, <3 字)。 */
  function planQuery(q) {
    const words = q.split(WS_RE).filter((w) => w.length > 0);
    const long = [], short = [];
    for (const w of words) (cplen(w) >= 3 ? long : short).push(w);
    return [long, short];
  }

  /** fts_match_text：短语串（引号内双写转义，空格 = 隐式 AND）。仅用于自检/调试。 */
  function ftsMatchText(terms) {
    return terms.map((t) => '"' + t.replace(/"/g, '""') + '"').join(" ");
  }

  // ------------------------------------------------------------ unicode61

  function isAlnumChar(c) { return !SEP_RE.test(c); }

  /** 词项的字母数字游程（unicode61 的切法）。 */
  function runsOf(term) {
    const out = [];
    let cur = "";
    for (const c of term) {
      if (isAlnumChar(c)) cur += c;
      else if (cur) { out.push(cur); cur = ""; }
    }
    if (cur) out.push(cur);
    return out;
  }

  /** 2 字词项在 unicode61 下的短语；空短语（全分隔符）返回 null。 */
  function phraseToken(term) {
    const runs = runsOf(term);
    return runs.length === 1 ? NS.asciiFold(runs[0]) : null;
  }

  const SURROGATE_RE = /[\uD800-\uDFFF]/;
  const PY_WS_ONE = new RegExp("[" + NS.PY_WS + "]");

  /** 一行的空白分段，再逐段取相邻两字（= 管线写进 bg 列的那串）。
   *  切的是**码位**，不是 UTF-16 单元：星形字与前后字构成的两字组属于同一类。 */
  function bgPairs(text) {
    const out = [];
    for (const run of text.split(WS_RE)) {
      if (SURROGATE_RE.test(run)) {
        const cs = [...run];
        for (let i = 0; i + 1 < cs.length; i++) out.push(cs[i] + cs[i + 1]);
      } else {
        for (let i = 0; i + 1 < run.length; i++) out.push(run.substr(i, 2));
      }
    }
    return out;
  }

  /** 一行的 bg 列经 unicode61 得到的 token 表（dl 就是它的长度）。
   *
   *  必须按**码位**判类：星形字占两个 UTF-16 单元，p[0]/p[1] 拿到的各是一个
   *  代理项，而代理项属于 Unicode 类别 Cs —— 会被当成分隔符，整对丢掉。
   *  （这个坑实际踩过：`(𥙷曰管/見前䇿)` 会少算两个 token，dl 从 8 掉到 6，
   *  bm25 随之错位，`。管` 的排序就对不上了。） */
  function bgTokens(text) {
    const out = [];
    for (const p of bgPairs(text)) {
      if (!SURROGATE_RE.test(p)) {          // 常态：两个 BMP 字符
        const a = isAlnumChar(p[0]), b = isAlnumChar(p[1]);
        if (a && b) out.push(NS.asciiFold(p));
        else if (a) out.push(NS.asciiFold(p[0]));
        else if (b) out.push(NS.asciiFold(p[1]));
        continue;
      }
      const cs = [...p];
      const a = isAlnumChar(cs[0]), b = isAlnumChar(cs[1]);
      if (a && b) out.push(NS.asciiFold(p));
      else if (a) out.push(NS.asciiFold(cs[0]));
      else if (b) out.push(NS.asciiFold(cs[1]));
    }
    return out;
  }

  // ---------------------------------------------------------------- bm25

  /** FTS5 的 idf；SQLite 的 bm25() 返回负值，这里一路按正值算最后取负。 */
  function idf(nRow, df) { return Math.log((nRow - df + 0.5) / (df + 0.5)); }

  function bm25(st, df, dl, f) {
    if (f <= 0) return 0;
    return idf(st.n_row, df) * (f * (st.k1 + 1)) /
           (f + st.k1 * (1 - st.b) + st.k1 * st.b * dl / st.avgdl);
  }

  /** trigram 的行 token 数：码位数 - 2（全 224,822 行与影子表逐行吻合）。 */
  function dlTrigram(text) { return Math.max(0, cplen(text) - 2); }

  // ---------------------------------------------------------------- 书/版本

  function resolveBook(corpus, param) {
    if (!param) return null;
    const key = pyStrip(param.replace(WS_RE, "")).replace(/^[《》]+|[《》]+$/g, "");
    if (!key) return null;
    const aliases = new Map();
    for (const k of Object.keys(SHORT_NAMES)) aliases.set(k, SHORT_NAMES[k]);
    for (const b of corpus.books) {
      for (const c of [b.book_id, b.book_dir, b.title,
                       NS.zh.toSimplified(b.title || "")]) {
        if (!c) continue;
        const k = c.replace(WS_RE, "");
        if (!aliases.has(k)) aliases.set(k, b.book_id);
      }
    }
    // setdefault 语义：先建的别名（_SHORT_NAMES）优先；此处按插入序查找
    let bid = aliases.has(key) ? aliases.get(key) : undefined;
    if (bid === undefined) {
      const t = NS.zh.toTraditional(key);
      bid = aliases.has(t) ? aliases.get(t) : undefined;
    }
    if (bid === undefined) {
      const known = [...new Set(aliases.values())].sort().join("、") || "—";
      throw new Error(`未识别的史书：${param}（可用：${known}）`);
    }
    return bid;
  }

  function resolveEdition(param) {
    if (!param) return null;
    const p = pyStrip(param.replace(WS_RE, "")).toLowerCase();
    return (p === "tls" || p === "sbck") ? p : null;
  }

  // ---------------------------------------------------------------- 记录整形

  /** _shape_row 的转写。i 是行下标（corpus.rows 的下标）。 */
  function shapeRow(corpus, i) {
    const j = (v) => {
      if (v === null || v === undefined || v === "") return null;
      try { return JSON.parse(v); } catch (e) { return null; }
    };
    const cell = (n) => corpus.cell(i, n);
    const b = corpus.bookOfRow[i];
    const f = corpus.fileById.get(corpus.fileOfRow[i]);
    const page = (cell("pb_page") || "") + (cell("pb_side") || "");
    const layer = cell("layer");
    return {
      // ---- 出处（前端优先展示；无则 null → UI 显示「暂无」）----
      book_title: b ? b.title : null,
      book_id: b ? b.book_id : null,
      juan: cell("juan"), section: cell("section"),
      subsection: cell("subsection"), division: cell("division"),
      ab: cell("ab"),
      edition: b ? b.edition : null,
      family: b ? b.family : null,
      file_name: f ? f.file_name : null,
      file_sha256: f ? f.sha256 : null,
      origin_path: f ? f.origin_path : null,
      page: page || null,
      pb_block: cell("pb_block"),
      pb_raw: cell("pb_raw"),
      source_ref: j(cell("source_ref_json")),
      // ---- 记录本身 ----
      passage_id: cell("passage_id"), row_no: cell("row_no"),
      kind: cell("kind"), layer: layer, status: cell("status"),
      text_orig: cell("text_orig"),
      normalized_text: corpus.norm[i],
      special_chars: j(cell("special_chars_json")),
      commentary_candidate: layer === "commentary_candidate",
    };
  }

  // ---------------------------------------------------------------- 检索

  /** 命中行的排序键：bm25 升序（SQLite 的负分越小越靠前），再 book/file/row。 */
  function sortKey(corpus, i, scoreNeg) {
    const b = corpus.bookOfRow[i];
    const f = corpus.fileById.get(corpus.fileOfRow[i]);
    return [scoreNeg, b ? b.book_id : "", f ? f.file_no : 0,
            corpus.rows[i][corpus.col.row_no], corpus.rows[i][corpus.col.passage_id]];
  }

  function compareKeys(x, y) {
    if (x[0] !== y[0]) return x[0] < y[0] ? -1 : 1;
    let c = cmpStr(x[1], y[1]);
    if (c) return c;
    if (x[2] !== y[2]) return x[2] < y[2] ? -1 : 1;
    if (x[3] !== y[3]) return x[3] < y[3] ? -1 : 1;
    return x[4] - y[4];
  }

  /** 全语料里含该子串的 kind='passage' 行数（= FTS 的 df；不带书/版本过滤）。 */
  function docFreq(corpus, needle) {
    const idx = corpus.passageIdx;
    let n = 0;
    for (let k = 0; k < idx.length; k++) {
      const t = corpus.mtext[idx[k]];
      if (t !== null && t !== undefined && t.indexOf(needle) >= 0) n++;
    }
    return n;
  }

  /** 一行里 1 字 token 的出现次数。
   *
   *  单字成词的条件：它与**同一个空白分段内**的某个非字母数字字符相邻
   *  （「。管」→ 两字组「。管」→ unicode61 取出的 token 是「管」）。
   *  跨空白分段的邻居不构成两字组，故空白要单独排除 —— 注意空白本身
   *  也属于分隔符类别（Zs），不排掉就会把「分 段」算成一词。 */
  function countSingleToken(text, ch) {
    const cs = SURROGATE_RE.test(text) ? [...text] : text.split("");
    let n = 0;
    for (let j = 0; j < cs.length; j++) {
      if (cs[j] !== ch) continue;
      const prev = j > 0 ? cs[j - 1] : null;
      const next = j + 1 < cs.length ? cs[j + 1] : null;
      if ((prev !== null && !PY_WS_ONE.test(prev) && !isAlnumChar(prev)) ||
          (next !== null && !PY_WS_ONE.test(next) && !isAlnumChar(next))) n++;
    }
    return n;
  }

  /**
   * run_search 的转写。
   *
   * @param {NS.Corpus} corpus
   * @param {string} q
   * @param {{book?:string, edition?:string, page?:number, pageSize?:number}} opts
   * @returns 与 /api/search 同形的结果对象；参数非法抛 Error。
   */
  function runSearch(corpus, q, opts) {
    opts = opts || {};
    const page = opts.page === undefined ? 1 : opts.page;
    let pageSize = opts.pageSize === undefined ? PAGE_SIZE_DEFAULT : opts.pageSize;
    if (page < 1) throw new Error("页码从 1 开始");
    pageSize = Math.min(Math.max(pageSize, 1), PAGE_SIZE_MAX);

    const qRaw = pyStrip(q || "");
    const qTrad = NS.zh.toTraditional(qRaw);
    if (!qTrad) throw new Error("请提供搜索关键词");
    const bid = resolveBook(corpus, opts.book);
    const edition = resolveEdition(opts.edition);
    if (opts.variants === true) {
      const groups = NS.variants.keywordGroups(qRaw);
      if (groups.some((g) => g.length > 1)) {
        const hits = NS.variants.scanGroups(corpus, groups, { bookId: bid, family: edition });
        const lo = (page - 1) * pageSize;
        return { q: qRaw, q_traditional: qTrad, mode: "like",
          book: bid || "全部", edition: edition || "全部",
          total: hits.length, page, page_size: pageSize,
          items: hits.slice(lo, lo + pageSize).map((i) => shapeRow(corpus, i)) };
      }
    }
    const [longTerms, shortTerms] = planQuery(qTrad);

    const st = corpus.bm25 || {};
    const useFts = !!st.trigram && longTerms.length > 0;
    const pureTwo = shortTerms.length > 0 && shortTerms.every((t) => cplen(t) === 2);
    const useBg = !useFts && pureTwo && !!st.bigram;

    const filter = { bookId: bid, family: edition };
    // Phase 7-E：字形变体组（组内 OR、组间 AND）。
    //
    // **变体必须进「扫描词表」，不能只做事后过滤**（Phase 7 修复）：
    // 若扫描仍用基词 `["內"]`、只用变体组过滤结果，那么「內」只能命中 742 段，
    // 而「内」的 10820 段根本没被扫到 —— 过滤是收窄，不可能补回没扫到的。
    // Python 侧把变体拼进 WHERE 的 `(LIKE %內% OR LIKE %内%)`，命中 11562，
    // 所以这里也必须把变体并进扫描词表，两边才是同一种检索语义。
    //
    // 变体**不参与路径判定**（useFts/useBg 仍按基词算），与 Python 一致。
    const variants = opts.variants === true;
    const groupsOf = (terms) => {
      if (!variants) return [];
      const out = [];
      for (const t of terms) {
        const vs = NS.variants.termVariants(t);
        if (vs.length > 1) out.push(vs);
      }
      return out;
    };
    const gLong = groupsOf(longTerms);
    const gShort = groupsOf(shortTerms);
    // 变体组的候选集：**组内 OR**（一次 any 扫描）、**组间 AND**（求交）。
    // 这就是 Python `WHERE (LIKE a OR LIKE b) AND (LIKE c OR LIKE d)` 的等价物。
    const unionHits = (groups) => {
      let acc = null;
      for (const g of groups) {
        const s = new Set(corpus.likeScan(g, Object.assign({}, filter, { any: true })).hits);
        if (acc === null) { acc = s; continue; }
        const next = new Set();
        for (const i of acc) if (s.has(i)) next.add(i);
        acc = next;
      }
      return acc;                    // null = 没有变体组
    };
    // FTS 路径要保留「必须含原短语」这一条（Python 的 MATCH 用基词长词）。
    // JS 用基词长词再扫一次当守门集合，只保留其中的行。
    const longGate = (variants && gLong.length)
      ? new Set(corpus.likeScan(longTerms, filter).hits)
      : null;
    let mode, hits, scoreNeg = null;

    if (useFts) {
      // 长词走 FTS（Python 的 `MATCH("齊桓公")` 短语），短词作 LIKE 附加约束。
      // 两者是**两件事**，不能合并成一次 `likeScan` 的 AND 词表：
      //   · 长词：必须含**原短语** —— 用基词扫描 + longGate；
      //   · 短词：命中任一写法即可（组内 OR）—— 用 unionHits(gShort)。
      // 合并会把「齊桓公 AND 内」压成同一层 AND、丢掉短词的变体语义
      // （实测 `齊桓公 内` py=4 而 js=1）。
      const uLong = unionHits(gLong);            // 长词变体的或集
      const uShort = unionHits(gShort);          // 短词变体的或集
      // 短词**没有**变体组时，仍要按基词并进同一次扫描（否则会丢掉它的 AND 约束，
      // 实测让 `齊桓公 管仲` 从 11 条涨到 20 条 —— 因为「管仲」不见了）。
      // 短词**有**变体组时改由 uShort 负责（命中任一写法即可）。
      const baseTerms = uShort === null ? longTerms.concat(shortTerms) : longTerms;
      const base = corpus.likeScan(baseTerms, filter).hits;
      hits = base.filter((i) => (uLong === null || uLong.has(i))
                            && (uShort === null || uShort.has(i))
                            && (longGate === null || longGate.has(i)));
      const folded = longTerms.map(NS.asciiFold);
      const df = folded.map((t) => docFreq(corpus, t));
      scoreNeg = new Float64Array(hits.length);
      for (let h = 0; h < hits.length; h++) {
        const i = hits[h];
        const text = corpus.mtext[i];
        const dl = dlTrigram(text);
        let s = 0;
        for (let k = 0; k < folded.length; k++) {
          s += bm25(st.trigram, df[k], dl, countOcc(text, folded[k]));
        }
        scoreNeg[h] = -s;
      }
      mode = "fts";
    } else if (useBg) {
      // 2 字词项各自切出一个 token；空短语被 FTS5 丢掉，全丢 = 空集
      const toks = shortTerms.map(phraseToken);
      const kept = [];
      for (let k = 0; k < toks.length; k++) if (toks[k] !== null) kept.push(toks[k]);
      if (!kept.length) {
        hits = [];
      } else if (kept.every((t) => cplen(t) === 2)) {
        // 常态：token 就是两字本身，等价于子串命中（走快路）。
        // 有变体组时改用变体组的或集（与 Python 的 OR 约束等价）。
        const u = unionHits(gShort);
        hits = u === null ? corpus.likeScan(kept, filter).hits
                          : [...u].filter((i) => kept.every(
                              (k) => corpus.mtext[i].indexOf(k) >= 0));
      } else {
        const u = unionHits(gShort);
        const base = scanBgTokens(corpus, kept, filter);
        hits = u === null ? base : base.filter((i) => u.has(i));
      }
      scoreNeg = new Float64Array(hits.length);
      for (let h = 0; h < hits.length; h++) {
        const i = hits[h];
        const text = corpus.mtext[i];
        const own = bgTokens(text);
        const dl = own.length;
        let s = 0;
        for (let k = 0; k < kept.length; k++) {
          let f = 0;
          for (let j = 0; j < own.length; j++) if (own[j] === kept[k]) f++;
          s += bm25(st.bigram, bigramDf(corpus, kept[k]), dl, f);
        }
        scoreNeg[h] = -s;
      }
      mode = "bigram";
    } else {
      // 无索引可走：LIKE 全扫。用「长词或集 × 短词或集」求交 ——
      // 等价于 Python 该分支的 `(LIKE a OR LIKE b) AND (LIKE c OR LIKE d)`。
      const uLong = unionHits(gLong);
      const uShort = unionHits(gShort);
      if (uLong === null && uShort === null) {
        hits = corpus.likeScan(longTerms.concat(shortTerms), filter).hits;
      } else {
        const src = uLong !== null ? [...uLong]
                                   : corpus.likeScan(longTerms, filter).hits;
        hits = src.filter((i) => (uLong === null || uLong.has(i))
                              && (uShort === null || uShort.has(i)));
      }
      mode = "like";
    }

    // 排序：fts/bigram 按 bm25，like 保持打包顺序
    if (scoreNeg !== null && hits.length) {
      const keys = hits.map((i, h) => sortKey(corpus, i, scoreNeg[h]));
      const order = hits.map((i, h) => h);
      order.sort((a, b) => compareKeys(keys[a], keys[b]));
      hits = order.map((h) => hits[h]);
    }

    const total = hits.length;
    const pageHits = hits.slice((page - 1) * pageSize, page * pageSize);
    return {
      q: qRaw, q_traditional: qTrad,
      mode: mode,
      book: bid || "全部", edition: edition || "全部",
      total: total, page: page, page_size: pageSize,
      items: pageHits.map((i) => shapeRow(corpus, i)),
    };
  }

  /** 含 1 字 token 的 bigram 查询：逐行走 token 表（慢路，平时不走）。 */
  function scanBgTokens(corpus, toks, filter) {
    const hits = [];
    const idx = corpus.passageIdx;
    for (let k = 0; k < idx.length; k++) {
      const i = idx[k];
      if (filter.bookId || filter.family) {
        const b = corpus.bookOfRow[i];
        if (!b) continue;
        if (filter.bookId && b.book_id !== filter.bookId) continue;
        if (filter.family &&
            String(b.family || "").toLowerCase() !== filter.family) continue;
      }
      const text = corpus.mtext[i];
      if (text === null || text === undefined) continue;
      const own = bgTokens(text);
      let ok = true;
      for (let t = 0; t < toks.length && ok; t++) {
        let found = false;
        for (let j = 0; j < own.length; j++) if (own[j] === toks[t]) { found = true; break; }
        ok = found;
      }
      if (ok) hits.push(i);
    }
    return hits;
  }

  /** bigram 的 df：含该 token 的行数。2 字 token 就是子串计数，1 字的要走 token。 */
  function bigramDf(corpus, tok) {
    if (cplen(tok) === 2) return docFreq(corpus, tok);
    let n = 0;
    const idx = corpus.passageIdx;
    for (let k = 0; k < idx.length; k++) {
      const t = corpus.mtext[idx[k]];
      if (t === null || t === undefined) continue;
      if (countSingleToken(t, tok) > 0) n++;
    }
    return n;
  }

  NS.engine = {
    PAGE_SIZE_MAX, PAGE_SIZE_DEFAULT,
    planQuery, ftsMatchText, resolveBook, resolveEdition, shapeRow, runSearch,
    // 供自检与演示数据生成复用
    bgPairs, bgTokens, phraseToken, isAlnumChar, dlTrigram, countOcc, docFreq,
    SEP_RE,
    // result_block.js 要用：它得取**全部**命中（不分页）并自带 bm25 分数，
    // 而 runSearch 的返回值形状已被一致性检查钉住（多一个键就会红），
    // 所以那三条路径的零件单独导出，由 result_block 自己拼一遍。
    bm25, bigramDf, scanBgTokens,
  };
})(window.YindeEngine = window.YindeEngine || {});
