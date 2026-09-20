/* Result Block —— 把「命中句」组装成「可连续阅读的史料片段」（浏览器版）。
 * search/result_block.py 的忠实移植。
 *
 * ## 为什么需要
 * tls 系（尚書/左傳/史記）¶ 一句一行，一条 passage 常常就是一个 6 字句子
 * （`齊桓公卒。¶`）。命中是「句」，用户要读的是「一件事」。本模块只做展示层
 * 聚合：**不改索引、不改原文、不写任何东西**。
 *
 * ## 组装规则（机会主义：有什么证据用什么）
 * 一个 block = 命中记录 + 同一原始文件内沿 (row_no, seq) 向两侧扩展的相邻记录，
 * 遇到下列任一**停止条件**即停：
 *
 * 1. 文件边界 —— 绝不跨 file_id（硬边界，永不越过）。
 * 2. layer 变化 —— 正文与 commentary_candidate 不混（任务书 §八.4）。
 * 3. 结构键变化（按书自适应，全部取数据库真实列）：
 *    - `section`    左傳(僖公十七年傳) / 尚書(篇名) / 史記表(三代世表)
 *    - `subsection` 左傳条目号
 *    - `ab`         左傳 A=經 / B=傳
 *    - `# src:` 段落号  史記 纪/傳 的段号（srcParagraph 截前两级）
 *    **注意史記内部极不均匀**：f82(本紀) 有 1894 条 src、f95(傳) 2611 条，
 *    而 f94(世家) 仅 78 条、f83/f84(表) 为 0。没有证据时不猜测段落，
 *    改由硬上限兜底（任务书 §十：宁可短，不可臆造）。
 * 4. 硬上限 —— 两种情形都生效：max_passages / max_chars。
 *    对國語 / 戰國策 / 史記世家等无结构字段的书，这是唯一的收束手段。
 *
 * page/pb 换页**不**作为停止条件：一段史料本就可能跨页（任务书 §八.5），
 * 只在结果里如实带出 pb_first / pb_last。
 *
 * ## 合并去重
 * 同一文件内相邻的多个命中若落入同一个区间，只产出一个 block，match_count
 * 汇总（任务书 §十二）。区间重叠时取并集，不丢结果。
 *
 * ## 不改原文
 * `text` 由各记录 `text_orig` 直接相接，不改写、不补标点、不做任何润色
 * （任务书 §十 / §二十四）。含 <pb:…>、¶ 等原样保留。
 *
 * ## 与原模块的唯一差别：数据来源
 * Python 从 SQLite 现取行窗口；这里从内存语料取。**窗口语义照抄**——
 * 同样有 FALLBACK_MARGIN 余量、同样受 WINDOW_HARD_CAP 截断、同样复用
 * 已覆盖请求的缓存窗口。若图省事直接返回全文件，扩展就会读到 Python 读不到
 * 的行，片段长度和 more_before/more_after 都会跟着变。
 */
"use strict";
(function (NS) {
  // 三种展示长度。target_chars 是「尽量达到」，max_* 是「绝不超过」。
  const MODE_LIMITS = {
    short:    { target_chars: 120,  max_passages: 12,  max_chars: 260 },
    standard: { target_chars: 420,  max_passages: 40,  max_chars: 900 },
    long:     { target_chars: 1200, max_passages: 120, max_chars: 2600 },
  };
  const DEFAULT_MODE = "standard";

  // 单次搜索最多组装多少条**命中**。安全阀，不是展示上限——超限才置 truncated。
  // 与 Python 的 MAX_HITS_PER_QUERY 同值同名（原为 MAX_BLOCKS_PER_QUERY = 600，
  // 实测 '之' 33241 命中被截到 600 只出 328 块，全量是 4058 块且只要 0.97s）。
  const MAX_HITS_PER_QUERY = 100000;

  // 篇名命中最多产出多少个片段（匹配到的 section 数上限）。篇名走**部分匹配**，
  // 一个字能匹配到一串篇名（「公」→ 隱公/桓公/…），不设上限会被短查询灌满。
  const MAX_SECTION_BLOCKS = 50;

  // 行窗口：命中行两侧至少取 FALLBACK_MARGIN 条（无结构证据时的兜底观察范围），
  // 窗口总跨度不超过 WINDOW_HARD_CAP（防 src 边界远在千里时拖进半个文件）。
  const FALLBACK_MARGIN = 40;
  const WINDOW_HARD_CAP = 600;
  const WINDOW_PAD = 20;          // 二次取数时在块区间外额外留的行数

  // 段落号形态：'004.41.2' / '17.5.6' / '28.70.3' —— 点分数字，至少两级。
  // 必须用 ^ 锚定 section_ref 开头，否则会误吃 `# dating: 6220卿有札書…` 这类
  // 注解或引文里的数字（实测踩过）；只认开头一个，也不许到处抓。
  const SRC_CODE_RE = /^([A-Za-z]*)\s*(\d+(?:\.\d+)+)/;

  // 组装时要读的列（对应 Python 的 _ROW_COLS；这里直接用打包语料的列名）。
  const ROW_COLS = ["passage_id", "file_id", "row_no", "seq", "kind", "layer",
                    "status", "juan", "section", "subsection", "division", "ab",
                    "text_orig", "pb_block", "pb_page", "pb_side"];

  function cplen(s) { return [...s].length; }

  // Python 的 str.split()（不带参数）按 Python 的空白集合切，见上面 terms_simplified。
  const PY_WS_SPLIT_RE = new RegExp("[" + NS.PY_WS + "]+");

  // ----------------------------------------------------------------- 边界证据

  /** 从 `# src:` 的 section_ref 里取「段落号」前两级，作史記等书的段边界。
   *
   *  数据库里的 section_ref 形态不统一，实测有：
   *    '004.41.2, ed. Zhōnghuáshūjú …'  → 004.41   （史記本紀，段号在最前）
   *    'SHIJI 28.70.3 1393/94; …'       → 28.70    （书名前缀 + 空格 + 段号）
   *    'ZUO 17.5.6 (643 B.C.); …'       → 17.5     （左傳，前缀 + 空格）
   *    '5.28.3 (…) …'                   → 5.28
   *  取开头「≥2 级点分数字」的第一二级。**只基于第一阶段已入库的值**，
   *  不重新解析原文、不猜测章节归属。开头不是号的一律视为无证据——例如
   *  'ZUO Xi 17.5.6'（前缀里夹了非书名词）或 '# dating: 6220…' 这类注解。 */
  function srcParagraph(sourceRefJson) {
    if (!sourceRefJson) return null;
    let ref = "";
    try {
      ref = ((JSON.parse(sourceRefJson) || {}).section_ref) || "";
    } catch (e) {
      return null;
    }
    const m = SRC_CODE_RE.exec(ref.replace(/^\s+/, ""));
    if (!m) return null;
    // 至少两级才当段号：'5' 这种孤立数字说不清是卷次还是页码，宁可不切
    // （任务书 §十：宁可短，不可臆造）。
    return m[2].split(".").slice(0, 2).join(".");
  }

  /** 结构键：键不同 = 不同段；null = 该行无段落级结构证据。 */
  function boundaryKey(section, subsection, ab) {
    if (section) return ["sec", section, ab];
    if (subsection) return ["sub", subsection, ab];
    if (ab) return ["ab", ab];
    return null;
  }

  /** 单行的边界键：行上有结构字段就用行上的，没有就回退到篇名区间表。
   *
   *  为什么需要回退：`passages.section` **只标在标题行上，不向下传播**（见下方
   *  「篇名检索」一节的说明）。史記/國語的正文行 section 全是 NULL，于是每一行
   *  的 boundaryKey 都是 null —— 严格口径下「null ↔ null」算同键，任何两行都能
   *  互并，块于是横跨篇界（实测 9905 块里 130 块）。
   *
   *  回退键必须复用 `["sec", label, ab]` 的形状，否则与标题行的键永远不相等，
   *  正文反而认不出自己的标题行（键长不等 → canTake 判不同段）。
   *
   *  secIdx 为 null（合成小库、老调用方）时**逐字保持今日行为**：键就是 null。
   *  与 Python 侧 _row_key 同形。 */
  function rowKey(row, secIdx) {
    const key = boundaryKey(row.section, row.subsection, row.ab);
    if (key === null && secIdx) {
      const label = sectionAt(secIdx, row.file_id, row.row_no);
      if (label) return ["sec", label, null];
    }
    return key;
  }

  /** rows[i] 能否并入以 refI 为参照的扩展区间（判定统一的唯一出口）。
   *
   *  非 passage 行（`# src:` / `<pb:>` / 标题）在正文里透明穿过：既不进片段
   *  正文，也不打断扩展。但**参照行不能跟着它们走**——`# src:` 这一行本身属于
   *  **新**段号，若拿它当参照，下一个 passage 就在跟新段号比较，于是从 004.42
   *  往回读能一路读穿到文件开头（实测踩过）。参照行永远停在「最近一条真正并入
   *  的 passage」上。
   *
   *  `keyK != keyRef` 把「有键 ↔ 无键」也判成变化（严格口径）：一侧有 section
   *  小节标题、另一侧无 section，就是两段史料，拼起来会读串。代价是个别片段
   *  短一点——按任务书 §十，宁可短，不可臆造。块之间的界限另由**锚点位置**保证：
   *  展开时不允许读回锚点另一侧。 */
  function canTake(row, marks, i, refI) {
    if (row.kind !== "passage") return true;
    const [keyRef, segRef, layerRef] = marks[refI];
    const [keyK, segK, layerK] = marks[i];
    if (layerK !== layerRef) return false;         // layer 边界：正文/注释不混
    // 键必须一样：**「有键 ↔ 无键」也算变化**。一侧是篇/章标题行，另一侧不属于
    // 任何篇，硬并起来就把两段史料接成一句。实测这条取严格口径与宽松口径在真实
    // 语料上产出的片段数、长度完全相同（说明宽松口径多读到的只是拼接缝上的
    // 零头），那就取不会读串的那个（任务书 §十）。
    const same = keyK === keyRef ||
      (keyK !== null && keyRef !== null && keyK.length === keyRef.length &&
       keyK.every((v, j) => v === keyRef[j]));
    if (!same) return false;
    // `# src:` 段号只在**本行没有结构字段**时才作边界证据（结构字段更可靠，
    // 且左傳/尚書的 src 行比 section 更细，用它会切碎阅读单元）。
    if (keyRef === null && segRef !== null && segK !== null && segK !== segRef) {
      return false;
    }
    return true;
  }

  /** 行号落在哪个段落号区间；不在任何区间内（如首条 `# src:` 之前的引子）返回 -1。
   *
   *  不做「就近归属」：段落号只往后管辖，把区间外的行算给最近的一段会让两段
   *  史料的正文被拼到一起。无证据就是无证据。 */
  function segOf(rowNo, segments) {
    for (let i = 0; i < segments.length; i++) {
      if (segments[i][0] <= rowNo && rowNo < segments[i][1]) return i;
    }
    return -1;
  }

  /** 预计算每行的边界证据 (结构键, 段序号, layer)。
   *
   *  rows 与 segments 都按行号有序，所以段序号一路往前走就行（线性），不必每行
   *  都从头扫一遍 segOf。与 Python 侧 marks_for 同形。
   *
   *  secIdx 只喂给 rowKey 的回退分支（纯内存二分，不碰 SQL）。 */
  function marksFor(rows, segments, secIdx = null) {
    const out = [];
    let k = 0;                              // 第一个 hi 还大于本行号的区间
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i], rn = r.row_no;
      while (k < segments.length && segments[k][1] <= rn) k++;
      const seg = (k < segments.length && segments[k][0] <= rn) ? k : null;
      out.push([rowKey(r, secIdx), seg, r.layer]);
    }
    return out;
  }

  // --------------------------------------------------------------- 有限行加载

  /** 单次请求内、按文件惰性加载**有限行窗口**（Python 的 _FileCache）。
   *
   *  任务书 §二十一禁止「一次加载整个文件」——这里照抄窗口语义：只取命中附近
   *  的行，窗口按需生长，最多长到 WINDOW_HARD_CAP。缓存随请求结束丢弃。 */
  class FileCache {
    constructor(corpus) {
      this.corpus = corpus;
      this._cols = ROW_COLS.map((c) => corpus.col[c]);
      this._segCache = new Map();
      this._winCache = new Map();       // file_id -> {rows, lo, hi}
    }

    /** 按 passage_id 物化一条记录；不存在返回 null（对应 Python 的 fetchone()）。 */
    recOfPassage(pid) {
      const i = this.corpus.indexOfPassage(pid);
      return i < 0 ? null : this.rec(i);
    }

    /** 把一行打包数据物化成对象，字段名与 Python 的 sqlite Row 键一致。 */
    rec(i) {
      const r = this.corpus.rows[i];
      const o = {};
      for (let k = 0; k < ROW_COLS.length; k++) {
        const name = ROW_COLS[k];
        const v = r[this._cols[k]];
        const d = this.corpus.dicts[name];
        o[name] = d ? (v >= 0 ? d[v] : null) : v;
      }
      return o;
    }

    // ---- 段落号索引（只看 `# src:` 行两列，代价与文件大小无关）----
    /** 该文件段落号管辖区间 [[lo_row, hi_row), …]；无 `# src:` 则返回 []。
     *
     *  区间在**段号变化的那个 src 行**处收口（不是在下一条 src 行）：那个 src
     *  行本身已经属于新段号，旧段不能把它圈进来。这样同一个段号在文件里出现
     *  多次（换页处重复标号）也会被切成多段，不会跨过中间别的段号把它们并起来。
     *
     *  建库时算好的是窄派生表 `src_paragraphs`；这里没有那张表，就地对 `# src:`
     *  行重算 —— 用的是**同一个** srcParagraph（建库侧也是调它），两边不会出现
     *  两种口径。 */
    segments(fileId) {
      if (this._segCache.has(fileId)) return this._segCache.get(fileId);
      const spans = [];
      const sp = this.corpus.spanOfFile(fileId);
      if (sp) {
        const cKind = this.corpus.col.kind, cRow = this.corpus.col.row_no;
        const cRef = this.corpus.col.source_ref_json;
        const kinds = this.corpus.dicts.kind;
        const rows = this.corpus.rows;
        for (let i = sp[0]; i < sp[1]; i++) {
          const r = rows[i];
          if (kinds[r[cKind]] !== "comment") continue;   // 段号只挂在 `# src:` 行上
          const refJson = r[cRef];
          if (!refJson) continue;
          const code = srcParagraph(refJson);
          if (code === null) continue;
          if (!spans.length || spans[spans.length - 1][0] !== code) {
            spans.push([code, r[cRow]]);
          }
        }
      }
      const out = spans.map((s, i) => [
        s[1], i + 1 < spans.length ? spans[i + 1][1] : 1e9]);
      this._segCache.set(fileId, out);
      return out;
    }

    // ---- 行窗口：按需生长 ----
    /** 取覆盖 [rowLo, rowHi] 的窗口，返回 rows（按 row_no, seq）。
     *
     *  带 WINDOW_PAD 余量；单次跨度不超过 WINDOW_HARD_CAP。同文件内若已缓存
     *  的窗口能满足请求（有膨胀余量），直接复用。
     *
     *  fresh=true 表示「这一屏必须是全新的」——分批取数时用，缓存里那份是上一批
     *  的，复用它会与上一批的区间重叠。 */
    window(fileId, rowLo, rowHi, fresh = false) {
      const lo = Math.max(1, rowLo - WINDOW_PAD);
      const hi = Math.min(rowHi + WINDOW_PAD, lo + WINDOW_HARD_CAP - 1);
      const cached = this._winCache.get(fileId);
      if (!fresh && cached && cached.lo <= lo && cached.hi >= hi) return cached.rows;

      const rows = [];
      const sp = this.corpus.spanOfFile(fileId);
      if (sp) {
        const rowsRaw = this.corpus.rows, cRow = this.corpus.col.row_no;
        // 文件内按 (row_no, seq) 有序，所以 row_no 落在 [lo, hi] 的行必是一段连续区间
        let i = this.corpus.lowerBoundInFile(fileId, lo);
        for (; i >= 0 && i < sp[1]; i++) {
          if (rowsRaw[i][cRow] > hi) break;
          rows.push(this.rec(i));
        }
      }
      this._winCache.set(fileId, { rows, lo, hi });
      return rows;
    }
  }

  // ----------------------------------------------------------------- 区间扩展

  /** 从 rows[i] 向两侧扩展，返回 [lo, hi, 尾部被截断, 头部被截断]。
   *
   *  marks[j] = (结构键, 段序号或 null, layer)。停止：结构键变 / 段号变 /
   *  layer 变 / 越过硬上限。page 换页不停止（任务书 §八.5）。
   *
   *  后两个布尔值区分「读到本段尽头」与「被字数/条数上限截断」——只有真被
   *  截断才提示可以继续展开（任务书 §十：读不到就说读到哪，不假装完整）。 */
  function expand(rows, i, limits, marks) {
    const maxP = limits.max_passages, maxC = limits.max_chars;
    const target = limits.target_chars;
    const n = rows.length;
    // **必须用 cplen 而不是 .length**：Python 侧是 len()，数字符（码位）；JS 的
    // .length 数 UTF-16 码元。四庫本正文里有扩展区字形（𠡠 U+20860、𫝊 U+2B74A…），
    // 一个字形在 JS 里算 2，于是同一个片段在两侧的「字数」能差出好几个，
    // 卡在上限边缘时块边界就会分叉（实测「之」/short 在後漢書 142 号文件上，
    // 累计 259 码位 = 261 码元，一边装得下、一边装不下，块数差 1）。本文件
    // 上方早有 cplen 就是为这个；n_chars 也一直用它，只有这几处累加漏了。
    const tlen = (j) => cplen(rows[j].text_orig || "");

    let lo = i, hi = i;
    let size = tlen(i), count = 1;
    let refLo = i, refHi = i;           // 参照行：最近一条真正并入的 passage
    let fwd = true, bwd = true;
    let cutFwd = false, cutBwd = false; // 该侧是被上限截断（而非读到尽头）
    while ((fwd || bwd) && count < maxP && size < maxC) {
      if (fwd) {
        if (hi >= n - 1) {
          fwd = false;
        } else {
          const j = hi + 1;
          if (!canTake(rows[j], marks, j, refHi)) {
            fwd = false;
          } else if (count + 1 > maxP || size + tlen(j) > maxC) {
            cutFwd = true;              // 还可能往下读，只是这一屏放不下
            fwd = false;
          } else {
            hi = j; count += 1; size += tlen(j);
            if (rows[j].kind === "passage") refHi = j;
          }
        }
      }
      if (bwd && size < target && count < maxP) {
        if (lo <= 0) {
          bwd = false;
        } else {
          const j = lo - 1;
          if (!canTake(rows[j], marks, j, refLo)) {
            bwd = false;
          } else if (count + 1 > maxP || size + tlen(j) > maxC) {
            cutBwd = true;
            bwd = false;
          } else {
            lo = j; count += 1; size += tlen(j);
            if (rows[j].kind === "passage") refLo = j;
          }
        }
      }
      if (size >= target) break;
    }
    return [lo, hi, cutFwd, cutBwd];
  }

  /** 块区间 [lo,hi] 之外、仍属**同一段史料**的行能延伸多远（任务书 §二十）。
   *
   *  用 expand 的同一套 canTake 规则从区间两端继续走。返回可达的 [lo', hi']；
   *  与 [lo,hi] 相同即表示两侧都读到本段尽头了。这一步只判断边界、不取正文，
   *  也不做完整走查：累计额外走过 maxExtraChars 就收手——结论一样（「外面还有
   *  得读」），但不会为一个 2 万字的文件白走到底。 */
  function reachEdges(rows, lo, hi, marks, maxExtraChars = 4000) {
    const n = rows.length;
    let a = lo, b = hi;
    let refA = lo, refB = lo;           // 区间端行的结构键就是参照口径
    let extra = 0;
    while (b + 1 < n && extra < maxExtraChars &&
           canTake(rows[b + 1], marks, b + 1, refB)) {
      b += 1;
      extra += cplen(rows[b].text_orig || "");      // 码位，见 expand 里的说明
      if (rows[b].kind === "passage") refB = b;
    }
    while (a - 1 >= 0 && extra < maxExtraChars &&
           canTake(rows[a - 1], marks, a - 1, refA)) {
      a -= 1;
      extra += cplen(rows[a].text_orig || "");      // 码位
      if (rows[a].kind === "passage") refA = a;
    }
    return [a, b];
  }

  /** [lo,hi] 中从 lo 起还装得下的最大下标（计数口径与 expand 完全一致）。
   *
   *  [lo,lo] 本身一定装得下，所以返回值 ≥ lo。与 Python 侧 _fit_hi 同形。
   *
   *  **不只是长度**。合并两块时区间会被撑大，而撑大的那一段从没经过 canTake：
   *  只按字数/条数收，就能把邻篇的正文并进来——实测这才是块横跨篇界的大头
   *  （9905 块里 130 块，只修 marksFor 只消除 18%）。所以从 okHi + 1 起补做
   *  同一套结构判定：键变了就停在这里，不再往下装。
   *
   *  okHi（默认 = lo）是「已经被 expand 验证过同段」的那一段的上界，重复判它
   *  没有意义，也不该因为参照行不同而改判。marks 为 null 时不做结构检查，
   *  等于今日行为。 */
  function fitHi(rows, lo, hi, limits, marks = null, okHi = null) {
    const maxP = limits.max_passages, maxC = limits.max_chars;
    if (okHi === null) okHi = lo;
    let n = 0, s = 0, last = lo;
    let ref = null;                       // 最近一条真正并入的 passage 的下标
    const end = Math.min(hi, rows.length - 1);
    for (let j = lo; j <= end; j++) {
      if (marks !== null && j > okHi && ref !== null) {
        if (!canTake(rows[j], marks, j, ref)) return j - 1;
      }
      n += 1;
      s += cplen(rows[j].text_orig || "");     // 码位：计数口径必须与 expand 一致
      if (n > maxP || s > maxC) return j - 1;
      if (rows[j].kind === "passage") ref = j;
      last = j;
    }
    return last;
  }

  // ----------------------------------------------------------------- 篇名检索
  //
  // 篇名的归属关系**不在** passages.section 里：那一列只标在标题行上，不向下传播
  // （史記文件 82 的 17410 行正文里只有 11 行有值，正好是 11 个本紀标题）。真正的
  // 关系在 sections.first_row 的区间里——实测 11 篇区间之和 14919 恰等于该文件正文
  // 总数，无重叠无遗漏。所以这里不看 corpus 的 section 列，只看 sections 区间表，
  // 与 Python `_section_index` 同一口径。

  /** corpus.sections（[{file_id, label, first_row}, …]）→
   *  Map<file_id, [[first_row, label], …]>，first_row 升序。 */
  function sectionIndex(corpus) {
    const idx = new Map();
    for (const s of (corpus.sections || [])) {
      if (s.file_id === null || s.first_row === null || s.label === null) continue;
      if (!idx.has(s.file_id)) idx.set(s.file_id, []);
      idx.get(s.file_id).push([s.first_row, s.label]);
    }
    for (const spans of idx.values()) spans.sort((a, b) => a[0] - b[0]);
    return idx;
  }

  /** 该行所属篇名 = 区间内最后一个 first_row <= row_no 的 label；无区间则 null。 */
  function sectionAt(idx, fileId, rowNo) {
    const spans = idx.get(fileId);
    if (!spans || !spans.length) return null;
    let lo = 0, hi = spans.length - 1, best = null;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (spans[mid][0] <= rowNo) { best = spans[mid][1]; lo = mid + 1; }
      else hi = mid - 1;
    }
    return best;
  }

  /** 区间上界（不含）= 同文件下一个 first_row；已是最后一个则 null（到文件末）。 */
  function sectionEnd(idx, fileId, firstRow) {
    const spans = idx.get(fileId) || [];
    for (let i = 0; i < spans.length; i++) {
      if (spans[i][0] === firstRow) return i + 1 < spans.length ? spans[i + 1][0] : null;
    }
    return null;
  }

  /** 篇名命中 → { hits: [[passage_id, file_id, row_no, seq, 0.0], …], capped }。
   *
   *  锚点取区间内**首条 kind='passage' 行**——返回这一篇的开头，而不是标题行
   *  （标题行不是正文，进了块也会被 shapeBlock 丢掉）。区间内无正文则跳过。
   *  匹配顺序与 Python 的 ORDER BY 一致：精确匹配最前，再篇名短的优先，
   *  最后按文件与行序稳定。这个次序决定**谁进得了 MAX_SECTION_BLOCKS**；
   *  展示顺序不跟它走——篇名块与正文块一起按 cmpRank 排（行序在键里），
   *  一篇一篇顺着读下去比按匹配度跳着读更合直觉。 */
  function sectionHits(corpus, qTrad, bid, edition, idx) {
    const matches = (corpus.sections || []).filter((s) => {
      if (s.file_id === null || s.first_row === null || s.label === null) return false;
      if (s.label !== qTrad && s.label.indexOf(qTrad) < 0) return false;
      if (bid && s.book_id !== bid) return false;
      if (edition && String(s.family || "").toLowerCase() !== edition) return false;
      return true;
    });
    matches.sort((x, y) =>
      ((y.label === qTrad ? 1 : 0) - (x.label === qTrad ? 1 : 0)) ||
      (cplen(x.label) - cplen(y.label)) ||
      (x.file_id - y.file_id) || (x.first_row - y.first_row));
    const capped = matches.length >= MAX_SECTION_BLOCKS;

    const out = [], seen = new Set();
    for (const s of matches.slice(0, MAX_SECTION_BLOCKS)) {
      const end = sectionEnd(idx, s.file_id, s.first_row);
      const p = firstPassageIn(corpus, s.file_id, s.first_row, end);
      if (!p || seen.has(p.passage_id)) continue;
      seen.add(p.passage_id);
      out.push([p.passage_id, p.file_id, p.row_no, p.seq, 0.0]);
    }
    return { hits: out, capped: capped };
  }

  /** 区间内首条正文行（按 row_no, seq）。只在**该文件的行区间**里扫：corpus 是
   *  按文件打包的、同文件行连续且按 row_no 有序（FileCache.window 的二分也靠这个
   *  不变式），所以一撞到 row_no 越界就能停，不必扫全表。 */
  function firstPassageIn(corpus, fileId, fromRow, toRow) {
    const span = corpus.spanOfFile(fileId);
    if (!span) return null;
    const cRow = corpus.col.row_no, cSeq = corpus.col.seq, cKind = corpus.col.kind;
    const kinds = corpus.dicts.kind;
    const rows = corpus.rows;
    for (let i = span[0]; i < span[1]; i++) {
      const r = rows[i];
      if (r[cKind] >= 0 && kinds[r[cKind]] !== "passage") continue;
      const rn = r[cRow];
      if (rn < fromRow) continue;
      if (toRow !== null && rn >= toRow) break;
      return { passage_id: corpus.cell(i, "passage_id"), file_id: fileId,
               row_no: rn, seq: r[cSeq] };
    }
    return null;
  }

  /** 片段排序键：命中多的在前（任务书 §十四），同数按相关度，再按书/文件/行序稳定。
   *
   *  正文块与篇名块**各自**用这个键排（篇名块的 match_count 恒为 1、score 恒为 0，
   *  排出来自然靠后），再由调用方把两组接起来——不混排，免得伪命中插进正文中间。 */
  function rankKey(b) {
    return [-b.match_count, b.score === null ? 0.0 : b.score,
            b.book_id || "", b.file_no || 0, b.row_first];
  }
  function cmpRank(x, y) {
    const a = rankKey(x), b = rankKey(y);
    if (a[0] !== b[0]) return a[0] - b[0];
    if (a[1] !== b[1]) return a[1] - b[1];
    if (a[2] !== b[2]) return cmpStr(a[2], b[2]);
    if (a[3] !== b[3]) return a[3] - b[3];
    return a[4] - b[4];
  }

  // ----------------------------------------------------------------- 组装入口

  /** hits = [[passage_id, file_id, row_no, seq, score], …] → 全部 block（未排序）。
   *
   *  同一文件内反复取数走缓存；区间重叠的块合并而不是重复展示。
   *  secIdx 是 sectionIndex 的产物，只用于给块回填篇名（可为 null）。 */
  function buildResultBlocks(corpus, hits, mode = DEFAULT_MODE, secIdx = null) {
    const limits = MODE_LIMITS[mode];
    const cache = new FileCache(corpus);

    // 1) 逐命中扩展（按文件分组，保证窗口查询命中缓存）
    const raw = [];
    const byFile = new Map();
    for (const h of hits) {
      if (!byFile.has(h[1])) byFile.set(h[1], []);
      byFile.get(h[1]).push(h);
    }

    let batchId = 0;
    for (const [fid, hs] of byFile) {
      const segments = cache.segments(fid);
      // 窗口要足够宽，直到结构边界或硬上限先到（否则扩展会被窗口截断，
      // 片段看起来「短」其实是取数不够）。普通记录很短，按条数给足余量。
      const margin = Math.min(Math.floor(WINDOW_HARD_CAP / 2),
                              FALLBACK_MARGIN + limits.max_passages * 2);
      const sorted = hs.slice().sort((x, y) => (x[2] - y[2]) || (x[3] - y[3]));
      // 一个文件的命中行跨度可能远超一次取数的行数上限（「之」在史記文件 95
      // 横跨 59206 行）。**必须分批取数**：只取一个窗口时，窗口以外的命中会
      // 被下面的 pos.get() === undefined 静默丢掉——实测「將軍」1142 处命中
      // 只组装出 139 处（88% 不见了），而响应里 truncated 还是 false。
      //
      // 分批必须首尾相接、互不重叠：窗口重叠 = 同一段正文读两遍，窗口留缝 =
      // 命中被丢。下一批的取数起点定在上一批窗口末行之后（+WINDOW_PAD 抵消
      // window() 自己减掉的那段）。
      let nextLo = null, i = 0;
      while (i < sorted.length) {
        const lo = nextLo === null ? sorted[i][2] - margin
                                   : Math.max(nextLo, sorted[i][2] - margin);
        const winLo = Math.max(1, lo - WINDOW_PAD);
        const winHi = winLo + WINDOW_HARD_CAP - 1;
        const batch = [];
        while (i < sorted.length && sorted[i][2] <= winHi) batch.push(sorted[i++]);
        if (!batch.length) batch.push(sorted[i++]);   // 兜底：绝不空转
        batchId += 1;
        const rows = cache.window(fid, lo, batch[batch.length - 1][2] + margin, true);
        if (!rows.length) continue;
        nextLo = rows[rows.length - 1].row_no + 1 + WINDOW_PAD;
        const marks = marksFor(rows, segments, secIdx);
        const pos = new Map(rows.map((r, k) => [r.passage_id, k]));
        for (const h of batch) {
          const pid = h[0];
          const j = pos.get(pid);
          if (j === undefined) continue;  // 该行落在取数窗口之外（文件头/尾越界）
          const [a, b, cutF, cutB] = expand(rows, j, limits, marks);
          raw.push({ file_id: fid, batch: batchId, lo: a, hi: b, rows, marks,
                     match_count: 1, score: h[4], hit_passage_id: pid, hit_i: j,
                     more_before: cutB, more_after: cutF });
        }
      }
    }

    // 2) 区间合并（同文件同批、重叠或相邻 → 并集）。**合并也受显示上限约束**
    //    ——块是「一屏」，不是「这一段的全文」（任务书 §十二）。命中密的地方
    //    相邻窗口会连成一条长链，不设限时一个 standard 块实测长到 8768 字、
    //    517 段（上限 900 字 / 40 段）。装不下就在接缝处断开。
    //
    //    按 (文件, 批) 分组、组内从后往前比：下标只在同一批内可比，且 raw 已按
    //    行序、块区间长度有上限，可能与本块重叠的只有组尾那几条。全局线性扫是
    //    O(命中 × 块)，「之」实测 9.45s——分组后回到亚秒级。
    const groups = new Map();
    const merged = [];
    for (const blk of raw) {
      const key = blk.file_id + ":" + blk.batch;
      if (!groups.has(key)) groups.set(key, []);
      const group = groups.get(key);
      let pending = Object.assign({}, blk);
      for (let k = group.length - 1; k >= 0; k--) {
        const m = group[k];
        if (m.hi < pending.lo - 1) break;   // 组内区间按行序递增，再往前只会更远
        // okHi = m.hi：这一段是 expand 已经验证过同段的，从它之后再补结构检查
        // ——撑大的那一截必须和块的尾部同段，否则并进来的就是邻篇。
        m.hi = fitHi(m.rows, m.lo, Math.max(m.hi, pending.hi), limits, m.marks, m.hi);
        if (pending.hit_i <= m.hi) {
          // 命中点落在前一块里了：这一次命中归它（§七：不重复），本块作废。
          // 作废而不是「保留后半截」——片段是用来**看见命中**的，后半截没有
          // 命中，留下就是一个让人看不出为什么出现的结果。那半截正文读者仍能
          // 从前一块展开读到，没有丢。
          m.match_count += 1;
          if (pending.score < m.score) {
            m.score = pending.score;
            m.hit_passage_id = pending.hit_passage_id;
          }
          pending = null;
          break;
        }
        pending.lo = m.hi + 1;              // 接缝之后另起，接着往下读
      }
      if (pending !== null) {
        group.push(pending);
        merged.push(pending);
      }
    }

    // 3) 成文。合并会把区间撑大，单次扩展记的截断标记不再作数——按最终区间
    //    重算「两侧还能不能继续读」，否则合并块永远显示不出可展开（实测）。
    const shaped = [];
    for (const m of merged) {
      const [rlo, rhi] = reachEdges(m.rows, m.lo, m.hi, m.marks);
      m.more_before = rlo < m.lo;
      m.more_after = rhi > m.hi;
      const seg = m.rows.slice(m.lo, m.hi + 1);
      const pids = seg.filter((r) => r.kind === "passage")
        .map((r) => r.passage_id);
      if (!pids.length) continue;
      shaped.push(shapeBlock(seg, pids, m, secIdx));
    }
    return { blocks: shaped, limits };
  }

  /** 组装对外结构。text 由 text_orig 直接相接，绝不改写、不补标点。
   *
   *  只渲染 kind='passage' 的记录（真实史料正文）；`# src:`/`<pb:>`/标题等
   *  解析元数据行不进入片段正文——它们只在数据检查台出现（任务书 §十七）。
   *  页码信息另由 pb_first/pb_last 如实给出。
   *
   *  section 优先取行上的值；行上没有（史記/國語的正文行该列是 NULL，篇名只标在
   *  标题行）就用区间推——不推的话史記的结果根本不显示篇名（任务书 §十八）。 */
  function shapeBlock(seg, pids, m, secIdx) {
    const sectionOf = (r) =>
      (r.section || (secIdx ? sectionAt(secIdx, r.file_id, r.row_no) : null));
    const body = seg.filter((r) => r.kind === "passage");
    const text = body.map((r) => r.text_orig || "").join("");
    const pbs = body.filter((r) => r.pb_block || r.pb_page);
    const first = body.length ? body[0] : null;

    const side = (r) => ((r.pb_page || "") + (r.pb_side || "")) || null;

    return {
      block_id: first ? `${m.file_id}:${body[0].row_no}:${body[0].seq}` : null,
      hit_passage_id: m.hit_passage_id,
      file_id: m.file_id,
      row_first: first ? body[0].row_no : null,
      row_last: first ? body[body.length - 1].row_no : null,
      passage_ids: pids,
      match_count: m.match_count,
      score: m.score,
      text: text,
      juan: first ? first.juan : null,
      section: first ? sectionOf(first) : null,
      subsection: first ? first.subsection : null,
      division: first ? first.division : null,
      ab: first ? first.ab : null,
      layer: first ? first.layer : null,
      pb_first: pbs.length ? side(pbs[0]) : null,
      pb_last: pbs.length ? side(pbs[pbs.length - 1]) : null,
      n_passages: pids.length,
      n_chars: cplen(text),
      // 该侧是否还能继续读（被展示长度上限截断，而非读到本段尽头）。
      // 前端据此决定要不要给「展开更多上下文」按钮——读到尽头就不给，
      // 免得点开发现什么都没有（任务书 §十：不制造假象）。
      more_before: m.more_before,
      more_after: m.more_after,
      // 展开时的锚点：向外走要从片段首/末记录续，不能从命中点续（会重叠）。
      first_passage_id: pids[0],
      last_passage_id: pids[pids.length - 1],
    };
  }

  // ----------------------------------------------------------------- 对外入口

  /** 取全部命中的 [passage_id, file_id, row_no, seq, score]。
   *
   *  路径选择与 engine.runSearch 保持一致（trigram / bigram / LIKE），但**不分页**
   *  —— Result Block 必须先看全命中才能正确合并与计数。LIKE 路径无 bm25，score 记 null。 */
  function fetchHits(corpus, qTrad, bid, edition) {
    const E = NS.engine;
    const [longTerms, shortTerms] = E.planQuery(qTrad);
    const st = corpus.bm25 || {};
    const useFts = !!st.trigram && longTerms.length > 0;
    const pureTwo = shortTerms.length > 0 && shortTerms.every((t) => cplen(t) === 2);
    const useBg = !useFts && pureTwo && !!st.bigram;
    const filter = { bookId: bid, family: edition };

    const rowsOf = (idxs, scores) => idxs.map((i, k) => {
      const r = corpus.rows[i];
      const c = corpus.col;
      return [r[c.passage_id], corpus.cell(i, "file_id"),
              r[c.row_no], r[c.seq], scores === null ? null : scores[k]];
    });

    let hits, scoreNeg = null;
    if (useFts) {
      // 长词走 FTS，同查询的 <3 字词作 AND 附加约束参与命中（不计分）
      hits = corpus.likeScan(longTerms.concat(shortTerms), filter).hits;
      const folded = longTerms.map(NS.asciiFold);
      const df = folded.map((t) => E.docFreq(corpus, t));
      // 取负：SQLite 的 bm25() 返回负分（越小越相关），engine.bm25 算的是**正**
      // 分值，两侧的调用点都要翻一次号（engine.js 的 runSearch 也是 `-s`）。
      // 这里的分数直接进 block 的 score 字段，也是排序的次级键 —— 不翻号，
      // 强相关片段会被排到最后（实测：'齊桓公' 首位变成 戰國策 的一条）。
      scoreNeg = hits.map((i) => {
        const text = corpus.mtext[i];
        const dl = E.dlTrigram(text);
        let s = 0;
        for (let k = 0; k < folded.length; k++) {
          s += E.bm25(st.trigram, df[k], dl, E.countOcc(text, folded[k]));
        }
        return -s;
      });
    } else if (useBg) {
      const toks = shortTerms.map(E.phraseToken);
      const kept = toks.filter((t) => t !== null);
      if (!kept.length) {
        hits = [];
      } else if (kept.every((t) => cplen(t) === 2)) {
        hits = corpus.likeScan(kept, filter).hits;
      } else {
        hits = E.scanBgTokens(corpus, kept, filter);
      }
      scoreNeg = hits.map((i) => {
        const text = corpus.mtext[i];
        const own = E.bgTokens(text);
        const dl = own.length;
        let s = 0;
        for (let k = 0; k < kept.length; k++) {
          let f = 0;
          for (let j = 0; j < own.length; j++) if (own[j] === kept[k]) f++;
          s += E.bm25(st.bigram, E.bigramDf(corpus, kept[k]), dl, f);
        }
        return -s;   // 同上：SQLite 的负分约定
      });
    } else {
      hits = corpus.likeScan(longTerms.concat(shortTerms), filter).hits;
    }

    // 命中顺序按 passage_id 升序 —— 对齐 Python `_fetch_hits` 的**隐式**顺序：
    // 三条路径的 SQL 都没有 ORDER BY，实测靠 SQLite 全表扫 passages 给出 rowid
    // 序（= passage_id 升序，'齊'/'之'/'大夫'/'諸侯'/'諸侯之' 等逐一验过）。
    //
    // 为什么必须显式排：JS 的 likeScan 走的是**打包序**（按 book_id/file_no/
    // row_no 分组，窗口取数要求同文件的行连续），与 passage_id 序不同 ——
    // 而这个顺序是**可观测**的：区间合并是顺序敏感的（谁先入 `merged` 决定谁
    // 吸收谁、以及块的 hit_passage_id 取哪条），且 `hit_rows[:MAX_HITS_PER_QUERY]`
    // 截的也是它。不排的话两边会组装出不同的片段集
    // （实测 '齊' 6079 命中：match_count_sum 291 vs 162）。
    const pid = corpus.col.passage_id;
    const order = hits.map((_, k) => k)
      .sort((a, b) => corpus.rows[hits[a]][pid] - corpus.rows[hits[b]][pid]);
    return rowsOf(order.map((k) => hits[k]), scoreNeg === null ? null : order.map((k) => scoreNeg[k]));
  }

  /** 实际检索路径（fts/bigram/like），与 engine 的文案一致。 */
  function execMode(corpus, qTrad) {
    const E = NS.engine;
    const [longTerms, shortTerms] = E.planQuery(qTrad);
    const st = corpus.bm25 || {};
    if (st.trigram && longTerms.length) return "fts";
    if (shortTerms.length && shortTerms.every((t) => cplen(t) === 2) && st.bigram) {
      return "bigram";
    }
    return "like";
  }

  function fileMeta(corpus, fileIds) {
    const out = {};
    for (const fid of fileIds) {
      const f = corpus.fileById.get(fid);
      if (!f) continue;
      const b = corpus.bookById.get(f.book_id);
      out[fid] = {
        book_title: b ? b.title : null,
        book_id: f.book_id,
        edition: b ? b.edition : null,
        family: b ? b.family : null,
        file_name: f.file_name,
        file_no: f.file_no,
        origin_path: f.origin_path,
      };
    }
    return out;
  }

  /** 检索 → 组装 Result Block → 排序分页。参数非法抛 Error（API 转 400）。
   *
   *  `total` 是**合并后的片段数**（精确），`hit_total` 是原始命中 passage 数；
   *  两者不等正是第三阶段要解决的问题的可观测证据（任务书 §十二）。
   *
   *  `text_mode` 是繁简双轨（orig/simplified/both）：只**增加** `text_simplified`
   *  等字段，`text` 仍是原样的 text_orig，一字不改。 */
  function searchResultBlocks(corpus, q, opts) {
    opts = opts || {};
    const E = NS.engine;
    let page = opts.page === undefined ? 1 : opts.page;
    let pageSize = opts.pageSize === undefined ? 20 : opts.pageSize;
    const mode = opts.mode === undefined ? DEFAULT_MODE : opts.mode;
    const textMode = opts.textMode === undefined ? "orig" : opts.textMode;

    if (page < 1) throw new Error("页码从 1 开始");
    pageSize = Math.min(Math.max(pageSize, 1), E.PAGE_SIZE_MAX);
    if (!Object.prototype.hasOwnProperty.call(MODE_LIMITS, mode)) {
      throw new Error(`未知的显示长度：${mode}（可用：${Object.keys(MODE_LIMITS).join("、")}）`);
    }
    const qTrad = NS.zh.toTraditional(NS.pyStrip(q || ""));
    if (!qTrad) throw new Error("请提供搜索关键词");
    const bid = E.resolveBook(corpus, opts.book);
    const edition = E.resolveEdition(opts.edition);

    const hitRows = fetchHits(corpus, qTrad, bid, edition);
    const hitTotal = hitRows.length;
    // 检索词的简体形态：前端在「只看简体/繁简对照」里要用它高亮（繁体词高亮不到
    // 简体正文上）。用的是和正文同一个转换函数，两边不会出现两套简体。
    // 切词用 Python 的空白集合（PY_WS），不能用 /\s+/：两边差 U+001C-1F/U+0085
    // 与 U+FEFF，差一个字符就会多切/少切出一个词项，terms_simplified 跟着错。
    const simpleTerms = qTrad.split(PY_WS_SPLIT_RE).filter((t) => t)
      .map((t) => NS.dualText.simplify(t).text);
    const base = {
      q: NS.pyStrip(q || ""), q_traditional: qTrad, mode: mode,
      text_mode: textMode, terms_simplified: simpleTerms,
      exec_mode: execMode(corpus, qTrad),
      book: bid || "全部", edition: edition || "全部",
      hit_total: hitTotal, page: page, page_size: pageSize,
    };
    const secIdx = sectionIndex(corpus);
    // 篇名命中：单独组装（不混进正文命中，否则伪命中会把 match_count 灌高、
    // 打乱排序），组装后整体排在正文命中之后（§6：正文命中优先于篇名命中）。
    const sec = sectionHits(corpus, qTrad, bid, edition, secIdx);
    // 「正文没有」不等于「什么都没有」：搜「秦始皇本紀」正文命中是 0，
    // 篇名命中却有——空结果的早退必须把两边一起看。
    if (!hitRows.length && !sec.hits.length) {
      Object.assign(base, {
        total: 0, match_count_sum: 0, truncated: false, has_more: false,
        section_truncated: false,
        limits: MODE_LIMITS[mode], results: [],
      });
      return base;
    }

    const overflow = hitTotal > MAX_HITS_PER_QUERY;
    const hits = hitRows.slice(0, MAX_HITS_PER_QUERY)
      .map((r) => [r[0], r[1], r[2], r[3], r[4] === null ? 0.0 : r[4]]);

    const out = buildResultBlocks(corpus, hits, mode, secIdx);
    const out2 = buildResultBlocks(corpus, sec.hits, mode, secIdx);

    const fileIds = new Set(out.blocks.map((b) => b.file_id));
    for (const b of out2.blocks) fileIds.add(b.file_id);
    const meta = fileMeta(corpus, fileIds);
    for (const b of out.blocks) {
      Object.assign(b, meta[b.file_id] || {});
      b.match_type = "text";
    }
    for (const b of out2.blocks) {
      Object.assign(b, meta[b.file_id] || {});
      b.match_type = "section";
    }

    const textBlocks = out.blocks.slice().sort(cmpRank);
    const secBlocks = out2.blocks.slice().sort(cmpRank);

    // 去重（§7「不产生重复 Passage」）：篇名块的锚点若已落在某个正文块里，
    // 就不再单列——把它改标 both，读者从此知道这一篇既是篇名命中也是正文命中。
    const owner = new Map();
    for (const b of textBlocks) for (const pid of b.passage_ids) owner.set(pid, b);
    const kept = [];
    for (const b of secBlocks) {
      const hit = owner.get(b.hit_passage_id);
      if (hit !== undefined) hit.match_type = "both";
      else kept.push(b);
    }
    const blocks = textBlocks.concat(kept);

    const lo = (page - 1) * pageSize;
    const pageBlocks = blocks.slice(lo, lo + pageSize)
      .map((b) => NS.dualText.attach(publicBlock(b), textMode));
    Object.assign(base, {
      total: blocks.length,                   // 片段数（合并后，精确）
      match_count_sum: blocks.reduce((s, b) => s + b.match_count, 0),
      truncated: overflow,
      has_more: page * pageSize < blocks.length,
      // 篇名匹配被 MAX_SECTION_BLOCKS 截断（短查询会匹配到一串篇名）。
      section_truncated: sec.capped,
      limits: Object.assign({}, MODE_LIMITS[mode]),
      results: pageBlocks,
    });
    return base;
  }

  function cmpStr(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  // 只给前端用的字段（不含内部结构：score 是 bm25 相关度、rows/marks 是组装
  // 过程的中间态）。
  const INTERNAL_KEYS = ["score", "rows", "marks"];

  function publicBlock(b) {
    const out = {};
    for (const k of Object.keys(b)) {
      if (INTERNAL_KEYS.indexOf(k) < 0) out[k] = b[k];
    }
    return out;
  }

  // ------------------------------------------------------- 按需展开更多上下文

  /** 从片段**边界**继续向前/向后读更多**真实**邻居（任务书 §二十）。
   *
   *  以「当前片段的首/末记录」为界向外取数——不是从命中点取，否则取到的行
   *  会与已有片段重叠。行进中遇到与 block 相同的停止条件（layer 变 / 段落号变）
   *  即止，并如实告知是否读到了头（reaches_* 为 true 表示这一段到此为止，
   *  不是因为字数上限被截断）。
   *
   *  篇名区间表**就地自建**（corpus.sections 已在内存里，量级千行）。展开与组装
   *  必须用同一个口径：片段在篇界停住、展开却读过去，就会出现「读到的正文不
   *  属于片段自称的那一篇」。自建而不是要求调用方传，是为了让所有老调用点自动
   *  一致（与 Python 侧 expand_block 同形）。 */
  function expandBlock(corpus, passageId, opts) {
    opts = opts || {};
    let count = opts.count === undefined ? 20 : opts.count;
    const direction = opts.direction === undefined ? "both" : opts.direction;
    const beforePid = opts.beforePassageId === undefined ? null : opts.beforePassageId;
    const afterPid = opts.afterPassageId === undefined ? null : opts.afterPassageId;

    count = Math.min(Math.max(Math.trunc(count), 1), 100);
    if (["before", "after", "both"].indexOf(direction) < 0) {
      throw new Error("direction 只能是 before / after / both");
    }

    const cache = new FileCache(corpus);
    const hit = cache.recOfPassage(passageId);
    if (!hit) throw new Error("KEY:" + passageId);
    if (hit.kind !== "passage") {
      // 非正文记录（`# src:` / `<pb:>` 等）本就不在史料片段正文里，无从展开
      throw new Error("该记录不是史料正文，无法展开上下文");
    }
    const fid = hit.file_id;

    const anchor = (pid) => {
      if (pid === null || pid === undefined) return null;
      const r = cache.recOfPassage(pid);
      if (!r || r.file_id !== fid) return null;
      return r;
    };

    let secIdx = opts.secIdx === undefined ? null : opts.secIdx;
    if (secIdx === null) secIdx = sectionIndex(corpus);
    const segs = cache.segments(fid);
    const baseSeg = segOf(hit.row_no, segs);

    const markOf = (row) => [
      rowKey(row, secIdx),
      segs.length ? segOf(row.row_no, segs) : null,
      row.layer];

    /** 向一个方向走，返回 [新增行, 是否在本段内走到尽头]。
     *
     *  停止条件复用 canTake —— 与组装片段时**同一套判定**，不另写一份：
     *  两处规则一旦分家，就会出现「片段到 004.42 就停了，展开却读过去」这种
     *  前后不一致（实测踩过）。参照行同样是「最近一条真正并入的 passage」。
     *
     *  参照证据取**显式传来的那一侧端点**（前端传的片段首/末记录），且在整段
     *  行走中**不再变化**。若改成跟着刚读到的候选行走，参照会漂到下一条记录
     *  的键上，规则就失去意义（实测：从末行往回读会一路吞掉上一篇）。
     *
     *  另有一条硬界：**不许读到锚点的另一侧去**，即候选行必须严格在锚点之外。
     *  未传锚点时以记录自身为界，等于没有外层可读。 */
    const fetch = (a, explicit, order, limit) => {
      const cmpOp = order === "DESC" ? "<" : ">";
      // Python 侧是 SQL 的 (row_no <> a) OR (row_no = a AND seq <> a.seq)，
      // 这里在文件的行区间上顺序扫描，取严格在锚点之外的前 limit+1 条正文行。
      const sp = corpus.spanOfFile(fid);
      const cand = [];
      if (sp) {
        const cRow = corpus.col.row_no, cKind = corpus.col.kind, cSeq = corpus.col.seq;
        const kinds = corpus.dicts.kind, rowsRaw = corpus.rows;
        const cmpSeq = order === "DESC" ? (x, y) => x < y : (x, y) => x > y;
        const cmpRow = order === "DESC" ? (x, y) => x < y : (x, y) => x > y;
        const walked = [];
        for (let i = sp[0]; i < sp[1]; i++) {
          const r = rowsRaw[i];
          if (kinds[r[cKind]] !== "passage") continue;
          if (cmpRow(r[cRow], a.row_no) ||
              (r[cRow] === a.row_no && cmpSeq(r[cSeq], a.seq))) {
            walked.push(i);
          }
        }
        // SQL 的 ORDER BY row_no/seq 就等价于按 (row_no, seq) 升序/降序取
        walked.sort((x, y) => {
          const dx = rowsRaw[x][cRow] - rowsRaw[y][cRow];
          if (dx) return order === "DESC" ? -dx : dx;
          const ds = rowsRaw[x][cSeq] - rowsRaw[y][cSeq];
          return order === "DESC" ? -ds : ds;
        });
        for (const i of walked.slice(0, limit + 1)) cand.push(cache.rec(i));
      }

      const out = [];
      let ended = true;                 // 默认到头；下面只要能多取一条就翻案
      const refMark = explicit !== null && explicit !== undefined
        ? markOf(explicit) : markOf(a);
      const edge = explicit !== null && explicit !== undefined ? explicit : a;
      for (const r of cand) {
        if (out.length >= limit) {
          ended = false;                // 本次是条数收的，外面还有
          break;
        }
        if (order === "DESC") {
          if (r.row_no > edge.row_no ||
              (r.row_no === edge.row_no && r.seq >= edge.seq)) {
            break;                      // 锚点另一侧，不读
          }
        } else if (r.row_no < edge.row_no ||
                   (r.row_no === edge.row_no && r.seq <= edge.seq)) {
          break;
        }
        const mk = markOf(r);
        if (!canTake(r, [refMark, mk], 1, 0)) {
          break;                        // 走出这段史料了：不是被截断，是到头
        }
        out.push({ passage_id: r.passage_id, row_no: r.row_no,
                   seq: r.seq, text_orig: r.text_orig });
      }
      if (order === "DESC") out.reverse();
      return [out, ended];
    };

    let added = [];
    let reachHead = null, reachTail = null;
    let nextBefore = null, nextAfter = null;
    const aBefore = anchor(beforePid), aAfter = anchor(afterPid);
    if (direction === "before" || direction === "both") {
      const [got, rh] = fetch(aBefore || hit, aBefore, "DESC", count);
      reachHead = rh;
      added = got.concat(added);
      if (got.length) nextBefore = got[0].passage_id;
    }
    if (direction === "after" || direction === "both") {
      const [got, rt] = fetch(aAfter || hit, aAfter, "ASC", count);
      reachTail = rt;
      added = added.concat(got);
      if (got.length) nextAfter = got[got.length - 1].passage_id;
    }

    return {
      passage_id: passageId, file_id: fid, row_no: hit.row_no,
      direction: direction, count: count, layer: hit.layer,
      segment: baseSeg >= 0 ? baseSeg : null,
      reaches_head: reachHead, reaches_tail: reachTail,
      next_before_passage_id: nextBefore,
      next_after_passage_id: nextAfter,
      added: added.length, rows: added,
    };
  }

  NS.resultBlock = {
    MODE_LIMITS, DEFAULT_MODE, MAX_HITS_PER_QUERY, MAX_SECTION_BLOCKS,
    FALLBACK_MARGIN, WINDOW_HARD_CAP, WINDOW_PAD, ROW_COLS,
    srcParagraph, boundaryKey, canTake, segOf, marksFor,
    FileCache, expand, reachEdges, buildResultBlocks, shapeBlock,
    fetchHits, execMode, fileMeta, searchResultBlocks, expandBlock,
    publicBlock, sectionIndex, sectionAt, sectionEnd, sectionHits, rankKey,
  };
})(window.YindeEngine = window.YindeEngine || {});
