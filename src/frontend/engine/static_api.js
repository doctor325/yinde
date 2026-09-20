/* 静态检索分发器 —— api/main.py + api/db.py 的浏览器端等价物。
 *
 * 公开站（GitHub Pages）上没有 Python 进程，`/api/*` 全部 404。本模块把同一个
 * URL 空间在浏览器里重新实现一遍：**路由、参数解析、返回结构、错误消息都照
 * api/main.py 来**，数据来自 export_site.py 导出的静态 JSON。
 *
 * 为什么要逐字段照抄而不是「简化一下」
 * ----------------------------------
 * 前端 app.js 一个字节都不改（除了 api() 那一处接缝），它读的字段、
 * 判的空值、显示的错误消息，全都是照 Python 侧的形状写的。静态版少一个键、
 * 多一层包装、或者把空数组写成 404，页面上就会是另一种东西 —— 而且是在公开
 * 站上、用户可见的地方。所以这里的形状是**契约**，不是实现细节。
 *
 * 三处必须知道的口径差（都在各自位置有注释）
 * -----------------------------------------
 *   1. `normalized_text` 不在打包语料里，由 text_orig 现算（导出时逐行断言过）；
 *   2. `origin_path` 是相对 library 的路径（不是本机绝对路径，导出脚本会拦）；
 *   3. 本机绝对路径（`stats.library`）在这里**再剥一次**：数据层已经剥过，
 *      这里剥是为了保证即使将来导出脚本回退，绝对路径也进不了页面。
 *
 * 取数由调用方注入（`opts.raw`），所以浏览器与 Node 自检跑的是同一份代码：
 * 浏览器用 fetch，自检用 fs —— 一致性检查因此能直接把静态分发器的响应与
 * 真 API 的响应逐字段对拍（scripts/site/check_engine.py 的 static-api 检查）。
 */
(function (NS) {
  "use strict";

  const PARA_CHAR = "¶";

  // passages 表的列序（database/schema.sql）。响应里的键序与它一致 ——
  // /api/passages/<id> 与 /api/files/<id>/passages 返回的是**库行原样**，
  // 不是 engine._shape_row 那种整形行（后者见 shapeRow，键完全不同）。
  const DB_COLUMNS = [
    "passage_id", "book_id", "file_id", "row_no", "seq", "kind", "layer",
    "status", "juan", "section", "subsection", "division", "ab",
    "text_orig", "normalized_text", "char_start", "char_end",
    "pb_raw", "pb_block", "pb_page", "pb_side", "pb_edition",
    "special_chars_json", "source_ref_json", "notes_json",
  ];

  /** 路径 → 路由。与 api/main.py 的 ROUTE_RE 一一对应（含锚定）。 */
  const ROUTES = [
    ["stats", /^\/api\/stats$/],
    ["books", /^\/api\/books$/],
    ["book_files", /^\/api\/books\/([^/]+)\/files$/],
    ["file", /^\/api\/files\/(\d+)$/],
    ["passages", /^\/api\/files\/(\d+)\/passages$/],
    ["raw", /^\/api\/files\/(\d+)\/raw$/],
    ["passage", /^\/api\/passages\/(\d+)$/],
    ["context", /^\/api\/passages\/(\d+)\/context$/],
    ["block_expand", /^\/api\/blocks\/(\d+)$/],
    ["search", /^\/api\/search$/],
  ];

  /** URL 查询串 → 参数表。照 api/main.py 的 do_GET：
   *  只认**含 `=`** 的 kv（`?flag` 整体忽略），解码用 unquote（**不是** unquote_plus，
   *  所以 `+` 保持字面量，不会被当成空格）。 */
  function parseQuery(qs) {
    const out = Object.create(null);
    for (const kv of String(qs || "").split("&")) {
      const i = kv.indexOf("=");
      if (i < 0) continue;
      out[decode(kv.slice(0, i))] = decode(kv.slice(i + 1));
    }
    return out;
  }

  function decode(s) {
    try {
      return decodeURIComponent(s);
    } catch (e) {
      // Python 的 unquote 对残缺的 % 序列是**宽容**的（原样保留），
      // decodeURIComponent 会抛。此处退化成不解码，宁可少解也不整条失败。
      return s;
    }
  }

  /** Python int()：容忍首尾空白与正负号，其余一律失败。
   *  （不实现 Python 允许的下划线分隔与全角数字 —— 前端不会传这两种，
   *  真传了也只是回落到默认值，与 Python 的「转换失败回落默认值」同结果。） */
  function pyInt(v) {
    const t = NS.pyStrip(v);
    return /^[+-]?[0-9]+$/.test(t) ? parseInt(t, 10) : null;
  }

  /** json.loads 的安全版：空/失败 → null。 */
  function parseJson(s) {
    if (!s) return null;
    try {
      return JSON.parse(s);
    } catch (e) {
      return null;
    }
  }

  /** SQLite LIKE 的等价物（`%` 任意串、`_` 单字符，仅 ASCII 折大小写）。
   *  api/db.py 的 list_passages 用的是**未转义**的 `text_orig LIKE '%q%'`，
   *  所以 q 里的 `%`/`_` 在 Python 侧真的是通配符 —— 这里照做，不"修正"。 */
  function likeRe(pat) {
    let out = "^";
    for (const ch of pat) {
      if (ch === "%") out += "[\\s\\S]*";
      else if (ch === "_") out += "[\\s\\S]";
      else out += ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
    try {
      // 不带 `i` 旗标：调用方已把两侧都做过 asciiFold。JS 的 i 会额外折叠
      // Unicode（如 K 与开尔文记号），那超出 SQLite LIKE 的范围。
      return new RegExp(out + "$");
    } catch (e) {
      return null;
    }
  }

  /** LIKE 是否命中，模式是 `%词%`（api/db.py 的 list_passages 就是这么拼的）。
   *  两侧都先 asciiFold —— LIKE 只折 ASCII，JS 的 indexOf 一个都不折。 */
  function likeHit(hay, needle) {
    const h = NS.asciiFold(hay);
    const n = NS.asciiFold(needle);
    // 无通配符时走 indexOf：这是绝大多数调用，别为它编译正则。
    if (n.indexOf("%") < 0 && n.indexOf("_") < 0) return h.indexOf(n) >= 0;
    const re = likeRe("%" + n + "%");
    return re ? re.test(h) : false;
  }

  class Site {
    /**
     * @param {object} data {corpus, books, files, bookFiles, stats, raw}
     *   corpus   NS.Corpus 实例（已建索引）
     *   books    books.json 的内容
     *   files    files.json 的内容（file_id 字符串 → /api/files/<id> 响应）
     *   bookFiles book_files.json 的内容（book_id → /api/books/<id>/files 响应）
     *   stats    stats.json 的内容
     *   raw      async (fileId) => 原始行对象 | null（懒加载，缺数据返回 null）
     */
    constructor(data) {
      this.corpus = data.corpus;
      this.books = data.books || [];
      this.files = data.files || {};
      this.bookFiles = data.bookFiles || {};
      this.stats = data.stats || null;
      this.raw = data.raw || (async () => null);
      this._rawCache = new Map();
      this._ents = null;
    }

    /** 提问链路要的 Entities：全语料扫一遍，构造一次后复用。 */
    entities() {
      if (!this._ents) this._ents = new NS.entities.Entities(this.corpus);
      return this._ents;
    }

    fileMeta(fileId) { return this.files[String(fileId)] || null; }

    /** 与 app.js 的 api() 同契约：出错**抛** Error（消息即响应里的 error 值），
     *  成功返回解析好的对象。 */
    async get(urlPath) {
      const i = String(urlPath).indexOf("?");
      const path = i < 0 ? String(urlPath) : String(urlPath).slice(0, i);
      const params = parseQuery(i < 0 ? "" : String(urlPath).slice(i + 1));

      for (const [name, rx] of ROUTES) {
        const m = rx.exec(path);
        if (m) return this["_h_" + name](m[1], params);
      }
      throw new Error("unknown api path");
    }

    // ------------------------------------------------------------ 参数
    /** api/main.py 的 _q：缺参数 → default；转换失败 → default（**不报错**）。
     *  注意空串对 cast=str 返回 ""（**不会**回落默认值），对 int 转换失败回落。 */
    _q(params, key, def, cast) {
      const v = params[key];
      if (v === undefined) return def === undefined ? null : def;
      if (cast === Number) {
        const n = pyInt(v);
        return n === null ? (def === undefined ? null : def) : n;
      }
      return v;
    }

    // ------------------------------------------------------------ handlers

    _h_stats() {
      const st = this.stats;
      if (!st) throw new Error("静态数据缺少 stats.json");
      // 本机语料库绝对路径（api/main.py 的 d["library"]）**永不**下发到前端。
      // 导出脚本已剥过一层，这里再剥一层：这是纪律，不是重复劳动。
      const out = Object.assign({}, st);
      delete out.library;
      if (out.run && typeof out.run === "object") {
        out.run = Object.assign({}, out.run);
        delete out.run.library;
      }
      return out;
    }

    _h_books() { return this.books; }

    /** 未知 book_id → `[]`（不是 404），与 api/main.py 一致。 */
    _h_book_files(bookId) { return this.bookFiles[bookId] || []; }

    _h_file(fileId) {
      const f = this.fileMeta(fileId);
      if (!f) throw new Error("no such file");
      return f;
    }

    _h_passage(pid) {
      const i = this.corpus.indexOfPassage(Number(pid));
      if (i < 0) throw new Error("no such passage");
      return this.dbRow(i);
    }

    _h_passages(fileId, params) {
      const fid = Number(fileId);
      // 与 _h_passages 同样：limit 只封顶不封底（负值 = SQLite 的 LIMIT -1 = 不限），
      // offset 封底为 0。
      const limit = Math.min(this._q(params, "limit", 100, Number), 500);
      const offset = Math.max(this._q(params, "offset", 0, Number), 0);
      const kind = this._q(params, "kind");
      const layer = this._q(params, "layer");
      const status = this._q(params, "status");
      const q = this._q(params, "q");

      const span = this.corpus.spanOfFile(fid);
      const hits = [];
      if (span) {
        for (let i = span[0]; i < span[1]; i++) {
          // kind/layer/status 的判定是**真值**判定：空串等于不过滤。
          if (kind && this.corpus.cell(i, "kind") !== kind) continue;
          if (layer && this.corpus.cell(i, "layer") !== layer) continue;
          if (status && this.corpus.cell(i, "status") !== status) continue;
          if (q && !likeHit(this.corpus.rows[i][this.corpus.col.text_orig], q)) continue;
          hits.push(i);
        }
      }
      // 文件内的打包顺序就是 ORDER BY row_no, seq（导出脚本的排序键），
      // 所以这里不必再排一次。
      const rows = (limit < 0 ? hits.slice(offset) : hits.slice(offset, offset + limit))
        .map((i) => this.dbRow(i));
      return { total: hits.length, offset, limit, rows };
    }

    async _h_raw(fileId, params) {
      const fid = Number(fileId);
      const doc = await this._raw(fileId);
      if (!doc) throw new Error("no such file");
      const lines = doc.lines || [];
      const total = lines.length;

      let start = Math.max(this._q(params, "start", 1, Number), 1);
      let end = Math.max(this._q(params, "end", start + 99, Number), start);
      end = Math.min(end, total);
      start = Math.min(start, total);
      if (end < start) end = start;

      const ann = this._coverage(fid, start, end);
      const out = [];
      for (let no = start; no <= end; no++) {
        const ln = lines[no - 1];
        // if/elif 互斥：命中前面就不再追加后面（api/main.py 的 _h_raw）。
        let a;
        if (no <= doc.header_lines) a = ["文件头"];
        else if (!NS.pyStrip(ln.text) && ln.text.indexOf(PARA_CHAR) < 0) a = ["空行(不入库)"];
        else a = (ann.get(no) || []).slice();
        out.push({ no, text: ln.text, annotation: a });
      }
      return { file_id: fid, header_lines: doc.header_lines, total_lines: total,
               start, end, lines: out };
    }

    _h_context(pid, params) {
      const i = this.corpus.indexOfPassage(Number(pid));
      if (i < 0) throw new Error("no such passage");
      // search/context.py：before/after 钳在 [0, 10]，负值连查询都不发。
      const before = clamp10(this._q(params, "before", 3, Number));
      const after = clamp10(this._q(params, "after", 3, Number));
      const c = this.corpus;
      const fid = c.fileOfRow[i];
      const span = c.spanOfFile(fid);
      const row = c.rows[i][c.col.row_no];
      const seq = c.rows[i][c.col.seq];

      // 邻居 = 同文件、kind='passage'，按 (row_no, seq) 升序；文件边界自然截短。
      const pass = [];
      for (let k = span[0]; k < span[1]; k++) {
        if (c.cell(k, "kind") === "passage") pass.push(k);
      }
      const cur = pass.filter((k) => isBefore(c, k, row, seq));
      const next = pass.filter((k) => isAfter(c, k, row, seq));
      const bIdx = cur.slice(Math.max(0, cur.length - before));
      const aIdx = next.slice(0, after);
      return {
        current: this.shapeRow(i),
        before: bIdx.map((k) => this.shapeRow(k)),
        after: aIdx.map((k) => this.shapeRow(k)),
      };
    }

    _h_block_expand(pid, params) {
      const direction = this._q(params, "direction") || "both";
      const count = this._q(params, "count", 20, Number);
      try {
        return NS.resultBlock.expandBlock(this.corpus, Number(pid), {
          direction,
          count,
          beforePassageId: this._q(params, "before_passage_id", null, Number),
          afterPassageId: this._q(params, "after_passage_id", null, Number),
        });
      } catch (e) {
        // Python 侧 KeyError → 404「no such passage」（result_block.js 用 "KEY:<id>"
        // 这个哨兵表达 KeyError，见其 expandBlock）；ValueError → 400，消息原样。
        const msg = String((e && e.message) || e);
        if (msg.startsWith("KEY:")) throw new Error("no such passage");
        throw e;
      }
    }

    _h_search(_pid, params) {
      const page = this._q(params, "page", 1, Number);
      const pageSize = this._q(params, "page_size", NS.engine.PAGE_SIZE_DEFAULT, Number);
      // 第三阶段：默认返回 Result Block；level=passage 保留第二阶段契约。
      const level0 = (this._q(params, "level") || "block").toLowerCase();
      let mode = this._q(params, "mode") || NS.resultBlock.DEFAULT_MODE;
      const textMode = (this._q(params, "text") || NS.dualText.DEFAULT_MODE).toLowerCase();
      let level = level0;
      // mode 在第三阶段是「显示长度」，第四阶段把它当提问入口。一个参数名不能
      // 有两种含义 —— 收到 mode=question 就切到提问模式，显示长度回默认值。
      if (mode.toLowerCase() === "question") {
        level = "question";
        mode = NS.resultBlock.DEFAULT_MODE;
      }
      const qtext = this._q(params, "q") || "";
      if (level === "passage") {
        return NS.engine.runSearch(this.corpus, qtext, {
          book: this._q(params, "book"), edition: this._q(params, "edition"),
          page, pageSize,
        });
      }
      if (level === "block") {
        return NS.resultBlock.searchResultBlocks(this.corpus, qtext, {
          book: this._q(params, "book"), edition: this._q(params, "edition"),
          page, pageSize, mode, textMode,
        });
      }
      if (level === "question") return this._question(params, mode, textMode);
      throw new Error("level 只能是 block、passage 或 question");
    }

    /** api/main.py 的 _question：检索 + 聚合 + 三个前端要用的附加字段。
     *  **只检索，不生成答案**（§2.3）—— 这里同样一句都不生成。 */
    _question(params, mode, textMode) {
      const qtext = (this._q(params, "q") || "").trim();
      if (!qtext) throw new Error("请提供问题");
      const limits = NS.resultBlock.MODE_LIMITS;
      if (!Object.prototype.hasOwnProperty.call(limits, mode)) {
        throw new Error(`未知的显示长度：${mode}（可用：${Object.keys(limits).join("、")}）`);
      }
      if (NS.dualText.MODES.indexOf(textMode) < 0) {
        throw new Error(`未知的繁简方式：${textMode}（可用：${NS.dualText.MODES.join("、")}）`);
      }
      const res = NS.retrieve.retrieve(this.corpus, qtext, this.entities());
      const out = NS.retrieve.asDict(res, 20, {
        corpus: this.corpus, mode, textMode,
      });
      // 检索词的简体形态：前端在「只看简体/繁简对照」里要用它高亮。
      // 用的是和正文**同一个**转换函数，两边不会出现两套简体。
      const seen = new Set();
      for (const t of out.expanded.all_terms) seen.add(NS.dualText.simplify(t).text);
      out.terms_simplified = Array.from(seen).sort();
      out.q = qtext;
      out.level = "question";
      out.mode = mode;
      out.text_mode = textMode;
      out.disclaimer = "以下均为语料原文片段，系统只做检索与聚合，"
                     + "不生成、不改写、不摘要（第四阶段不含 AI 作答）。";
      return out;
    }

    // ------------------------------------------------------------ 行整形

    /** 库行原样（25 列，键序 = 建表序）。normalized_text 由 text_orig 现算。 */
    dbRow(i) {
      const c = this.corpus;
      const out = {};
      for (const name of DB_COLUMNS) {
        out[name] = name === "normalized_text" ? c.norm[i] : c.cell(i, name);
      }
      return out;
    }

    /** search/engine._shape_row 的等价物：**不是**库行，键集完全不同
     *  （没有 seq / file_id / char_* / notes_json，多出出处字段与解析过的
     *  source_ref / special_chars）。上下文视图要的是这个形状。 */
    shapeRow(i) {
      const c = this.corpus;
      const f = this.fileById(c.fileOfRow[i]);
      const b = c.bookOfRow[i] || null;
      const page = (c.cell(i, "pb_page") || "") + (c.cell(i, "pb_side") || "");
      const layer = c.cell(i, "layer");
      return {
        book_title: b ? (b.title === undefined ? null : b.title) : null,
        book_id: b ? b.book_id : null,
        juan: c.cell(i, "juan"),
        section: c.cell(i, "section"),
        subsection: c.cell(i, "subsection"),
        division: c.cell(i, "division"),
        ab: c.cell(i, "ab"),
        edition: b ? b.edition : null,
        family: b ? b.family : null,
        file_name: f ? f.file_name : null,
        file_sha256: f ? f.sha256 : null,
        origin_path: f ? f.origin_path : null,
        page: page || null,
        pb_block: c.cell(i, "pb_block"),
        pb_raw: c.cell(i, "pb_raw"),
        source_ref: parseJson(c.cell(i, "source_ref_json")),
        passage_id: c.rows[i][c.col.passage_id],
        row_no: c.rows[i][c.col.row_no],
        kind: c.cell(i, "kind"),
        layer: layer,
        status: c.cell(i, "status"),
        text_orig: c.rows[i][c.col.text_orig],
        normalized_text: c.norm[i],
        special_chars: parseJson(c.cell(i, "special_chars_json")),
        commentary_candidate: layer === "commentary_candidate",
      };
    }

    fileById(fid) {
      const f = this.corpus.fileById.get(fid);
      return f || null;
    }

    /** 原文对照的行标注（api/main.py 的 _h_raw）：行号 → ["kind/layer", …]。
     *
     *  一条记录按其 text_orig 里的换行数占**多行**，只有首行标 `kind/layer`，
     *  续行标 `·(并入上块)`；首行且 status 不是 ok 时再缀上 status（续行不缀）。
     *  往前多看 100 行：跨行块的开头可能落在窗口之外，不看就认不出续行。 */
    _coverage(fid, start, end) {
      const out = new Map();
      const span = this.corpus.spanOfFile(fid);
      if (!span) return out;
      const c = this.corpus;
      const fromLo = Math.max(1, start - 100);
      for (let i = span[0]; i < span[1]; i++) {
        const r = c.rows[i];
        const rowNo = r[c.col.row_no];
        if (rowNo < fromLo || rowNo > end) continue;
        const text = r[c.col.text_orig] || "";
        const n = text.split("\n").length;
        const kind = c.cell(i, "kind"), layer = c.cell(i, "layer");
        const status = c.cell(i, "status");
        for (let k = 0; k < n; k++) {
          const no = rowNo + k;
          let tag;
          if (k === 0) {
            tag = kind + "/" + layer;
            if (status !== "ok") tag += "/" + status;
          } else {
            tag = "·(并入上块)";
          }
          if (!out.has(no)) out.set(no, []);
          out.get(no).push(tag);
        }
      }
      return out;
    }

    async _raw(fileId) {
      const key = String(fileId);
      if (!this._rawCache.has(key)) this._rawCache.set(key, await this.raw(fileId));
      return this._rawCache.get(key);
    }
  }

  function clamp10(v) { return Math.min(Math.max(v, 0), 10); }

  /** 与检索无关、纯粹是「同文件内 (row_no, seq) 序」的比较。
   *  seq 可为 NULL，Python 侧 `seq < ?` 对 NULL 恒为假 —— 这里同样：
   *  任一侧 seq 为 null 时只按 row_no 比。 */
  function isBefore(c, k, row, seq) {
    const r2 = c.rows[k][c.col.row_no];
    if (r2 !== row) return r2 < row;
    const s2 = c.rows[k][c.col.seq];
    if (s2 === null || s2 === undefined || seq === null || seq === undefined) return false;
    return s2 < seq;
  }

  function isAfter(c, k, row, seq) {
    const r2 = c.rows[k][c.col.row_no];
    if (r2 !== row) return r2 > row;
    const s2 = c.rows[k][c.col.seq];
    if (s2 === null || s2 === undefined || seq === null || seq === undefined) return false;
    return s2 > seq;
  }

  NS.staticApi = { Site, DB_COLUMNS, ROUTES, parseQuery, pyInt, likeHit };
})(window.YindeEngine = window.YindeEngine || {});
