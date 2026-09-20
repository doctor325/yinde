/* 语料载入与内存索引（浏览器版）—— 替代 Python 侧的 sqlite 游标。
 *
 * 本地完整版与公开演示版走的是**同一份代码**：数据都是 scripts/site 导出的
 * 「列式打包 JSON」（格式 historyai-corpus/1），区别只在数据来源。
 *
 * 为什么是列式打包而不是一行一个对象
 * ----------------------------------
 * 22 万行 × 24 列。实测行对象写法 67.8 MB、列式 21.9 MB（同一批数据），
 * 字段名本身就占了 45 MB。低基数文本列（kind/layer/juan/section…）建字典，
 * 行里只存下标；正文列（text_orig）原样存。
 *
 * normalized_text 不随包下发，这里重算
 * ------------------------------------
 * 它由 text_orig 精确派生（scripts/pipeline/structure.make_normalized，5 行），
 * 导出时已对全部 224,822 行逐条断言「重算 == 库中值」。JS 侧重算的规则同样简单：
 *
 *     normalized_text 非空  ⟺  kind == 'passage' 且 make_normalized 非空
 *
 * （管线在 _base() 里对所有行都调 make_normalized，随后 _emit_comment_block /
 *  _emit_part_block 把 comment、part 行显式置 None，见 segmentation.py。）
 *
 * 行序即 LIKE 路径的顺序
 * ----------------------
 * 打包时按 (book_id, file_no, row_no, seq) 排列，与 engine.run_search 的 like 分支
 * 的 ORDER BY 完全一致。因此**数组顺序可以直接当 LIKE 顺序用**，不必再排一次；
 * fts/bigram 路径的 bm25 排序见 engine.js 的说明。
 */
"use strict";
(function (NS) {
  const PB_RE = /<pb:[^>]*>/g;
  const PARA_CHAR = "¶";

  // Python str.strip() 的空白集合。不能直接用 JS 的 .trim()，两边差两个字符：
  //   * Python 会去掉 U+001C..U+001F（文件/组分隔符）与 U+0085（NEL），JS 的 \s 不含；
  //   * JS 的 \s 含 U+FEFF（BOM），Python 不去。
  // 差一个字符，normalized_text 就会差一点，检索命中集合就会差一片。
  const PY_WS = "\\t\\n\\v\\f\\r \\u001c-\\u001f\\u0085\\u00a0\\u1680" +
                "\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  const PY_STRIP_RE = new RegExp("^[" + PY_WS + "]+|[" + PY_WS + "]+$", "g");

  function pyStrip(s) { return s.replace(PY_STRIP_RE, ""); }

  // SQLite 的三个匹配面都对 ASCII 字母大小写不敏感：LIKE 默认折叠 A-Z，
  // unicode61 与 trigram 分词器同样先折叠再建/查索引（三者在
  // 「'aaa' 能否被 'AAA' 命中」上实测行为一致）。JS 的 indexOf 区分大小写，
  // 故检索文本另存一份折叠版：折叠是逐字符 1:1 的，长度与偏移都不变，
  // 因此命中位置对原始 normalized_text 仍然有效。
  const ASCII_UPPER_RE = /[A-Z]/;
  function asciiFold(s) {
    return s.replace(/[A-Z]/g, (c) =>
      String.fromCharCode(c.charCodeAt(0) + 32));
  }

  /** structure.make_normalized 的转写。kind 不在 (passage, comment) 内返回 null。 */
  function makeNormalized(textOrig, kind) {
    if (kind !== "passage" && kind !== "comment") return null;
    let t = (textOrig || "").replace(PB_RE, "");
    t = t.split(PARA_CHAR).join("");
    t = pyStrip(t);
    t = t.replace(/^[　 ]+/, "");
    return t || null;
  }

  class Corpus {
    /**
     * @param {object} packed  corpus.json 的内容
     * @param {Array}  books   books.json 的内容（/api/books 的响应）
     * @param {object} files   files.json 的内容（file_id -> /api/files/<id> 的响应）
     * @param {Array}  sections  sections.json 的内容（篇名区间表，可缺省）
     */
    constructor(packed, books, files, sections) {
      this.format = packed.format;
      this.source = packed.source;
      this.bm25 = packed.bm25 || null;   // 两条 FTS 路径计分要用的全局总量
      this.columns = packed.columns;
      this.rows = packed.rows;
      this.n = this.rows.length;

      this.col = Object.create(null);
      this.columns.forEach((c, i) => { this.col[c] = i; });
      this.dicts = Object.create(null);
      for (const c of packed.dict_columns) this.dicts[c] = packed.dicts[c];

      // 篇名区间表（sections.json）。**不在** corpus.json 里：那一列 section 只标在
      // 标题行上、不向下传播，光有它推不出归属关系，区间表得单独下发。
      // 缺省成空数组：旧数据目录没有这个文件，退化成「没有篇名数据」而不是崩。
      this.sections = sections || [];

      this.books = books || [];
      this.bookById = new Map(this.books.map((b) => [b.book_id, b]));
      this.files = files || {};
      this.fileById = new Map();
      for (const k of Object.keys(this.files)) {
        this.fileById.set(Number(k), this.files[k]);
      }

      this._buildIndices();
      this._buildNormalized();
    }

    /** 取单元格原值：字典列换成原值，且 -1 还原成 null（打包格式里 -1 = 空）。
     *  非字典列原样返回（那些列的取值可能本来就是 -1，不能一并当空）。
     *  热路径上不要用，先查好列下标。 */
    cell(i, name) {
      const v = this.rows[i][this.col[name]];
      const d = this.dicts[name];
      if (!d) return v;
      return v >= 0 ? d[v] : null;
    }

    _buildIndices() {
      const cFile = this.col.file_id, cPid = this.col.passage_id;
      const cKind = this.col.kind;
      const kinds = this.dicts.kind;
      // file_id 是字典列：行里存的是字典下标，不是 file_id 本身。
      // 不还原就会让 fileSpan/bookOfRow 整体错位（file_id 从 1 起，下标从 0 起）。
      const fileDict = this.dicts.file_id;
      const rows = this.rows, n = this.n;

      this.byId = new Map();          // passage_id -> 行下标
      this.passageIdx = [];           // kind='passage' 的行下标，按打包顺序（= LIKE 顺序）
      this.fileSpan = new Map();      // file_id -> [起, 止)  同文件的行是连续的
      this.fileOfRow = new Array(n);
      this.bookOfRow = new Array(n);

      let lastFile = null;
      for (let i = 0; i < n; i++) {
        const r = rows[i];
        this.byId.set(r[cPid], i);
        if (kinds[r[cKind]] === "passage") this.passageIdx.push(i);
        const fid = fileDict ? fileDict[r[cFile]] : r[cFile];
        this.fileOfRow[i] = fid;
        if (fid !== lastFile) {
          const prev = this.fileSpan.get(lastFile);
          if (prev) prev[1] = i;
          this.fileSpan.set(fid, [i, n]);
          lastFile = fid;
        }
      }
      // bookOfRow：一次映射，避免扫描时反复查 Map
      for (const [fid, [lo, hi]] of this.fileSpan) {
        const f = this.fileById.get(fid);
        const b = f ? this.bookById.get(f.book_id) : null;
        for (let i = lo; i < hi; i++) this.bookOfRow[i] = b;
      }
    }

    _buildNormalized() {
      const cText = this.col.text_orig, cKind = this.col.kind;
      const kinds = this.dicts.kind;
      this.norm = new Array(this.n);
      this.mtext = new Array(this.n);   // 匹配用文本：无大写字母时就是 norm[i] 本身
      for (let i = 0; i < this.n; i++) {
        const r = this.rows[i];
        const kind = kinds[r[cKind]];
        // 只有 passage 行有 normalized_text（见文件头注释）
        const t = kind === "passage" ? makeNormalized(r[cText], kind) : null;
        this.norm[i] = t;
        this.mtext[i] = (t !== null && ASCII_UPPER_RE.test(t)) ? asciiFold(t) : t;
      }
    }

    // ------------------------------------------------------------ 查询原语

    /** 行下标 -> 记录对象（对应 search/engine._shape_row 的输入）。 */
    rowOf(i) { return this.rows[i]; }
    indexOfPassage(pid) { const i = this.byId.get(pid); return i === undefined ? -1 : i; }

    /** 某文件的行下标区间 [起, 止)；文件不存在返回 null。 */
    spanOfFile(fileId) { return this.fileSpan.get(fileId) || null; }

    /** 取相邻若干行（context.js 用）。返回 [起, 止) 夹在文件范围内。 */
    windowOf(fileId, from, to) {
      const sp = this.fileSpan.get(fileId);
      if (!sp) return null;
      const lo = Math.max(sp[0], from), hi = Math.min(sp[1], to);
      return hi > lo ? [lo, hi] : null;
    }

    /**
     * LIKE 路径：全部词项都作为子串命中（AND），只取 kind='passage' 的行。
     * 返回 {total, hits: 行下标数组}，顺序即打包顺序（= Python 的 like 分支顺序）。
     *
     * Python 侧对应 engine._query + _like_cond（SQLite 的 LIKE 对 ASCII 字母
     * 大小写不敏感，故这里用 mtext 并对词项做同样的折叠）。
     * trigram / bigram 两条 FTS 路径在语义上也等价于「含全部词项」，差别只在
     * 排序与少数分词细节，见 engine.js。
     */
    /** 子串扫描。默认**全部命中**（AND）—— runSearch 的三条路径与
     *  result_block 的取命中都是这个语义（SQL 里是一串 AND 条件）。
     *
     *  `opts.any` 换成**命中任一**（OR），对应 retrieve._like_any 的那条
     *  「一次 OR 扫描」（SQL 里是一串 OR 条件）—— 那是**召回池**，与检索
     *  语义无关：命中任一个词就进池子，之后由排序层决定次序。
     *  两者不能混：把 OR 当成 AND 会让召回池只剩同时含全部词的段落
     *  （实测「齊桓公是怎么死的？」实体池从 449 条掉到 1 条）。 */
    likeScan(terms, opts) {
      opts = opts || {};
      const wantBook = opts.bookId || null;
      const wantFamily = opts.family || null;
      const any = !!opts.any;
      const needles = terms.map(asciiFold);
      const hits = [];
      const idx = this.passageIdx;
      for (let k = 0; k < idx.length; k++) {
        const i = idx[k];
        if (wantBook || wantFamily) {
          const b = this.bookOfRow[i];
          if (!b) continue;
          if (wantBook && b.book_id !== wantBook) continue;
          if (wantFamily && String(b.family || "").toLowerCase() !== wantFamily) continue;
        }
        const t = this.mtext[i];
        if (t === null || t === undefined) continue;
        let ok = !any;
        for (let j = 0; j < needles.length; j++) {
          const has = t.indexOf(needles[j]) >= 0;
          if (any ? has : !has) { ok = has; break; }
        }
        if (ok) hits.push(i);
      }
      return { total: hits.length, hits };
    }

    /** 在指定文件内按 row_no 找第一个 >= 目标的行下标（bisect）。 */
    lowerBoundInFile(fileId, rowNo) {
      const sp = this.fileSpan.get(fileId);
      if (!sp) return -1;
      const cRow = this.col.row_no;
      let lo = sp[0], hi = sp[1];
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (this.rows[mid][cRow] < rowNo) lo = mid + 1; else hi = mid;
      }
      return lo;
    }
  }

  NS.Corpus = Corpus;
  NS.makeNormalized = makeNormalized;
  NS.pyStrip = pyStrip;          // 供自检
  NS.asciiFold = asciiFold;
  NS.PY_WS = PY_WS;              // engine.js 用它按 Python 的 \s 切词
  NS.CORPUS_FORMAT = "historyai-corpus/1";
})(window.YindeEngine = window.YindeEngine || {});
