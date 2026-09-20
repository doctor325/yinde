/* 语料派生的轻量实体层（浏览器版）—— search/entities.py 的忠实移植。
 *
 * 设计原则照抄原模块（§6 / §33），一字不改：
 *   1. 候选来自语料，不来自外部知识。这里不硬编码任何生卒、事迹、亲属关系。
 *   2. 别名分组必须带语料依据。已核实表里的每条别名都附取自语料的佐证。
 *   3. 短称不自动等于全称。「桓公」467 条分属齊/魯/鄭/秦桓公，一律低权重候选。
 *   4. 实体识别只读：全程不写、不改原文。
 *
 * 与原模块的唯一差别是**数据来源**：Python 侧现查 SQLite，这里扫内存语料。
 * `_corpus_scan` 的 lru_cache 换成实例内的一次性惰性计算（同一个语料只扫一遍）；
 * 各函数不再收 db_path，改为 Entities 实例的方法。取值与判定与 Python 一致。
 *
 * 扫描用的是 **text_orig**（不是 normalized_text）：实体形态是「原文里怎么写」，
 * 在 normalized_text 上扫会因为去掉了 <pb:…> 标记而与 Python 侧结果不同。
 */
"use strict";
(function (NS) {
  // 春秋战国主要国名。只用于**识别语料里的称谓形态**，不代表任何史实主张。
  const STATES = ["齊", "晉", "秦", "魯", "楚", "宋", "衛", "鄭", "燕", "陳", "曹", "蔡",
                  "吴", "吳", "越", "虞", "虢", "滕", "薛", "許", "杞", "莒", "邾",
                  "趙", "魏", "韓", "田", "中山"];
  // 战国以后的国名/名号也用得上（史記、戰國策）
  const STATES_EXTRA = ["周", "召", "單", "劉", "尹"];
  const TITLE = "公侯伯子男";

  // 国名+爵位+一字：齊桓公、晉文公、秦穆公、鄭莊公、魯隱公…
  // 不直接用「国名+爵位」：「國名+公」会被两类东西污染 ——
  //   (a) 句中巧合：「辰請如齊公使往」里「齊公」= 「如齊」+「公使」；
  //   (b) 亲属/宗族词：「晉公族」「魯公子翬」「秦公孫枝」。
  // 两者都靠「至少还有第三个字、且那个字不是 族子孫…」排除。
  const NON_FOLLOW = "(?![族子孫女弟主室家甸后妃母])";
  // 国名 + 亲属/封号字 + 爵位，如 楚公子/晉公子/楚太子/燕太子/魏公子/魯夫人。
  // 这类是「某国的公子/太子」，**不是某一个人**，不能当实体。
  const NON_KIN = new Set([..."公太世王長次幼少夫母女弟兄孫族室主后妃妾"]);

  const STATE_CLS = "[" + STATES.concat(STATES_EXTRA).join("") + "]";
  // 全称 → 实体。**字序是 国名 + 谥号 + 爵位**：齊桓公 = 齊 + 桓 + 公，
  // 中间的桓是谥号，末尾的公才是爵位。爵位必须落在**最后**一位，
  // 写成 [公侯伯子男] 打头会把「晉侯使」「公曰」这类散文当人名收进来。
  // 注意「一字」用的是**真的** U+4E00–U+9FFF 两个字符，不是转义序列。
  const PAT_FULL = new RegExp(
    STATE_CLS + "([一-鿿])([" + TITLE + "])" + NON_FOLLOW, "g");

  // 单字国名，用于判别短称前面是不是别国的国名。中山是双字国名，且「中」「山」
  // 单用太常见（中间/山戎），不放进这个集合 —— 宁可漏判，不可错杀。
  const STATE_CHARS = new Set(
    STATES.concat(STATES_EXTRA).filter((s) => [...s].length === 1));

  const ASCII_UPPER_RE = /[A-Z]/;

  // ------------------------------------------------------------ 已核实体表
  // 每一条别名的依据都来自**本语料自身**的原文，写在 evidence 里。
  // 加新条目时，evidence 必须是能 grep 到的原文；拿不出就说明这个别名不该加。
  const CURATED = {
    "齊桓公": {
      aliases: ["桓公", "小白"], state: "齊",
      evidence: {
        "桓公": "史記·齊太公世家等以「桓公」承指齊君（96 条「齊桓公」与 467 条「桓公」并存，需上下文裁决）",
        "小白": "國語「奉公子小白出奔莒」——齊桓公名小白",
      },
    },
    "晉文公": {
      aliases: ["文公", "重耳"], state: "晉",
      evidence: {
        "重耳": "史記·晉世家、國語載晉公子重耳出亡十九年而後立，是為晉文公",
      },
    },
    "秦穆公": {
      aliases: ["穆公", "繆公"], state: "秦",
      evidence: { "繆公": "史記·秦本紀「穆公」「繆公」互見（同一君主的两种写法）" },
    },
    "管仲": {
      aliases: ["管夷吾", "夷吾", "管子"], state: null,
      evidence: {
        "管夷吾": "國語注「管夷吾齊卿姬姓之後」；左傳「管夷吾」與「管仲」互見",
        "管子": "戰國策、國語中以「管子」称管仲",
      },
    },
    "商鞅": {
      aliases: ["衛鞅", "公孫鞅"], state: null,
      evidence: {
        "衛鞅": "史記「秦封衛鞅於商」——封於商故又称商鞅（語料自证得名之由）",
        "公孫鞅": "史記「衛公孫鞅為大良造」，公孫鞅即衛鞅",
      },
    },
    "鮑叔牙": {
      aliases: ["鮑叔"], state: null,
      evidence: { "鮑叔": "左傳、國語「鮑叔牙」與「鮑叔」互見" },
    },
    "晉惠公": {
      aliases: ["夷吾"], state: "晉",
      evidence: { "夷吾": "史記·晉世家晉公子夷吾是為晉惠公（注意与管夷吾同名，靠上下文区分）" },
    },
    // 姓名式称谓（非「国名+爵位」形态，语料派生规则抓不到），逐个按原文核实后登记。
    // 别名栏留空的，表示**语料里找不出可靠的互指证据** —— 宁可只认全称。
    "百里奚": {
      aliases: [], state: null,
      evidence: { "百里奚": "史記「虜虞公及其大夫井伯百里奚以媵秦穆姬」（語料 11 段）" },
    },
  };

  // 纯短称（不带国名）：必须靠上下文裁决，默认低权重。
  const AMBIGUOUS = Object.create(null);
  for (const canon of Object.keys(CURATED)) {
    for (const al of CURATED[canon].aliases) {
      // 国名开头的别名（齊桓公）本身就是全称，不算歧义
      if (STATES.indexOf(al[0]) >= 0) continue;
      (AMBIGUOUS[al] = AMBIGUOUS[al] || []).push(canon);
    }
  }

  // 短称要能参与别名裁决，至少得单独出现过几次，否则一律当句中巧合
  // （「侯使」「公曰」这类确实追不到任何全称）。
  const SHORT_MIN_BARE = 3;

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  /** 码位序字符串比较（Python 的 str 比较语义；JS 默认按 UTF-16 单元）。 */
  function cmpStr(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  // ------------------------------------------------------------ 实例

  class Entities {
    constructor(corpus) {
      this.corpus = corpus;
      this._cText = corpus.col.text_orig;
      this._scanCache = null;
      this._shortForms = null;
      this._names = null;
      this._occurCache = new Map();
    }

    /** _corpus_scan：扫一遍正文，返回 {full, short, bare}。
     *
     *  full  全称 → 条数
     *  short 短称 → {来源全称: 条数}
     *  bare  短称单独出现的次数 = 裸计数 − 被全称解释掉的部分
     *  仅此一次，实例内缓存（对应 Python 的 lru_cache）。
     *
     *  行序必须是**数据库的 rowid 序**（= passage_id 升序），不是打包序：
     *  full/short 的插入序会一路传到短称的候选顺序（resolve_short 的稳定排序
     *  在频次相同时保留插入序），行序不同，同频次候选的先后就不同。
     *  Python 侧扫的是 `SELECT … WHERE kind='passage'` 的 rowid 序，
     *  passage_id 是 INTEGER PRIMARY KEY AUTOINCREMENT，两者实测完全一致。 */
    _scan() {
      if (this._scanCache) return this._scanCache;
      const c = this.corpus;
      const rows = c.rows, cText = this._cText, cPid = c.col.passage_id;
      const idx = c.passageIdx.slice().sort((a, b) => rows[a][cPid] - rows[b][cPid]);
      const full = {}, raw = {};
      for (let k = 0; k < idx.length; k++) {
        const text = rows[idx[k]][cText];
        if (!text) continue;
        PAT_FULL.lastIndex = 0;
        let m;
        while ((m = PAT_FULL.exec(text)) !== null) {
          if (NON_KIN.has(m[1])) continue;   // 楚公子/燕太子：不是某一个人
          full[m[0]] = (full[m[0]] || 0) + 1;
        }
        // 两字形态的**重叠**裸计数。不用正则：finditer 非重叠，在「齊桓公」里
        // 只匹到「齊桓」而跳过 offset 1 的「桓公」（短称统计整批丢光，实测
        // bare['桓公'] 为 None）；改用先行断言又要求匹配后面还有一个字符，
        // 句末的「桓公」匹不上。两字窗格直接数最稳。
        //
        // 只数**以爵位字收尾**的窗格：短称 = 全称去掉首字，而全称的末字必是
        // 公侯伯子男，所以凡是 short 可能有的键都落在这个集合里，一个不漏；
        // 其余的窗格（侯使/公曰/桓𥙷…）本来就进不了 short，数了也要扔。
        // 这一条也顺带把「Python 按码位切、JS 按 UTF-16 单元切」的差异彻底
        // 抹平：星形字附近的假两字组末字是代理项，不在爵位字集里。
        const n = text.length;
        for (let i = 0; i + 1 < n; i++) {
          if (TITLE.indexOf(text[i + 1]) < 0) continue;
          const g = text.substr(i, 2);
          raw[g] = (raw[g] || 0) + 1;
        }
      }
      // 短称（全称去掉国名，于是齊桓公 → 桓公）及其来源分布
      const short = {};
      for (const name of Object.keys(full)) {
        const s = [...name].slice(1).join("");
        const d = short[s] || (short[s] = {});
        d[name] = (d[name] || 0) + full[name];
      }
      // 单独出现 = 裸计数 − 被全称解释掉的部分
      const bare = {};
      for (const s of Object.keys(short)) {
        let explained = 0;
        for (const v of Object.values(short[s])) explained += v;
        const left = (raw[s] || 0) - explained;
        if (left > 0) bare[s] = left;
      }
      this._scanCache = { full, short, bare };
      return this._scanCache;
    }

    /** 语料里「国名+爵位+名」**全称**及条数（只读副本，同 Python 的 dict() 拷贝）。 */
    corpusNames() {
      return Object.assign({}, this._scan().full);
    }

    /** 两字短称 → {对应全称: 次数}，只保留**至少被一个全称佐证过**的短称。
     *
     *  「佐证」= 语料里存在 国名+该短称 的全称形态（齊桓公 佐证 桓公）。
     *  拿不出佐证的两字组合（侯使/公曰/子伐）是散文里跨词边界的巧合，不是称谓，
     *  直接丢弃 —— 这是本模块唯一能干净区分「真爵称」与「巧合」的判据。
     *
     *  返回值的 value 就是语料给的**歧义分布**：齊桓公 93 / 魯桓公 12 / 鄭桓公 7，
     *  既是候选顺序也是权重，不掺任何外部知识。 */
    shortForms() {
      if (this._shortForms) return this._shortForms;
      const scan = this._scan();
      const out = {};
      for (const s of Object.keys(scan.short)) {
        if (!Object.keys(scan.short[s]).length) continue;   // 无全称佐证 → 巧合
        if ((scan.bare[s] || 0) < SHORT_MIN_BARE) continue;
        out[s] = Object.assign({}, scan.short[s]);
      }
      // 已核实别名**并入**语料分布（不是替换）：齊桓公的别名「桓公」要和语料里
      // 魯桓公/鄭桓公/秦桓公的真实频次放在一张表里比，才能被上下文正确裁决。
      const cd = this._curatedDistribution();
      for (const s of Object.keys(cd)) {
        const cur = out[s] || (out[s] = {});
        for (const canon of Object.keys(cd[s])) {
          cur[canon] = (cur[canon] || 0) + cd[s][canon];
        }
      }
      for (const s of Object.keys(out)) Object.freeze(out[s]);
      this._shortForms = Object.freeze(out);
      return this._shortForms;
    }

    /** 把已核实表里的别名折进语料分布，格式与语料派生的完全一致。 */
    _curatedDistribution() {
      const out = {};
      for (const canon of Object.keys(CURATED)) {
        for (const al of CURATED[canon].aliases) {
          // 用 has()，不能用 `in`：Set 的元素不是属性键，`x in set` 恒为 false
          // （实测 '衛' in new Set(['衛']) === false）。写成 `in` 会让「衛鞅」
          // 这类黏着国名的别名漏进来，多出一个 Python 侧没有的短称。
          if (STATE_CHARS.has(al[0])) {
            continue;                 // 黏着国名的别名本身就是全称，不算短称
          }
          const d = out[al] || (out[al] = {});
          d[canon] = (d[canon] || 0) + 1;
        }
      }
      return out;
    }

    /** 短称可能指向哪些全称，按语料频次降序；国名出现在上下文里的排前面。
     *
     *  频次就是**语料给的权重**：「桓公」93 次由齊桓公贡献、12 次由魯桓公贡献，
     *  所以不带国名的「桓公」优先按齊桓公解，但保留魯/鄭/秦桓公为候选。
     *
     *  context 里出现的国名只做**重排**，不做过滤：即使上下文写的是晉，
     *  齊桓公仍在候选里（那句话可能在拿齊桓公作对比）。
     *  排序是**稳定**的：频次与国名都相同时保持语料分布里的插入顺序
     *  （Python 的 sorted 同样是稳定排序，键里没有字母序）。 */
    resolveShort(short, context) {
      const dist = this.shortForms()[short] || {};
      const ctx = new Set([...(context || "")]);
      return Object.keys(dist).sort((a, b) => {
        const ka = ctx.has(a[0]) ? 0 : 1, kb = ctx.has(b[0]) ? 0 : 1;
        return ka !== kb ? ka - kb : dist[b] - dist[a];
      });
    }

    /** 判定别名指向哪个实体，返回 [实体, 依据说明]。落不了地返回 [null, 原因]。
     *
     *  全称（带国名）直接命中；短称必须能**唯一**落到某个人身上才升级为实体。
     *  两条裁决通路：
     *    · 已核实表（人工看过原文、带 evidence 的那批）
     *    · 语料派生（short_forms 的分布）——没人工核过，但语料自己给出的占比
     *      就是权重，仍是可复核的证据，不是猜的。 */
    resolveAlias(alias, context) {
      alias = (alias || "").trim();
      if (!alias) return [null, "空"];
      if (alias in CURATED) return [alias, "全称精确命中"];
      if (alias in this.corpusNames()) return [alias, "语料全称精确命中"];

      // 短称：只有**一份**分布（语料频次 + 已核实别名合并而成），只有一条裁决路径
      const dist = this.shortForms()[alias];
      if (dist === undefined) {
        return [null, "语料中无此称谓（或其全部出现都追不到全称）"];
      }
      // 同频次时的先后沿用分布里的插入顺序（Python 的 sorted(key=-n) 是稳定排序）
      const order = Object.keys(dist).sort((a, b) => dist[b] - dist[a])
        .map((f) => `${f}(${dist[f]})`).join("、");
      const keys = Object.keys(dist);
      if (keys.length === 1) {
        return [keys[0], `「${alias}」的语料分布唯一：${order}`];
      }
      const ranked = this.resolveShort(alias, context);
      if ((context || "").indexOf(ranked[0][0]) >= 0) {   // 上下文点了这个国名
        // 说明文字里报的是**国名那一个字**（ranked[0][0]），不是全称
        return [ranked[0], `上下文出现「${ranked[0][0]}」，语料分布为 ${order}`];
      }
      return [null, `「${alias}」在语料中分属 ${order}，上下文不足以裁决`];
    }

    /** 已知实体**全称** = 已核实表 ∪ 语料里出现次数达标的「国名+爵位+名」形态。
     *
     *  语料派生那部分提供**覆盖**（这五本书里的国君都能被认出来），
     *  已核实表提供**别名与裁决**（同一个人的多种叫法）。
     *  刻意**不**把「桓公」这类短称放进来：短称要靠 resolve_short 结合上下文才能
     *  落到具体的人身上，直接当实体会把 467 条「桓公」全按到一个国君头上（§6）。 */
    knownEntities() {
      if (this._names) return this._names;
      const out = new Set(Object.keys(CURATED));
      const names = this.corpusNames();
      for (const name of Object.keys(names)) {
        if (names[name] >= 3) out.add(name);   // 少于 3 次的形态多半是句中巧合，不收
      }
      this._names = Object.freeze([...out].sort(cmpStr));
      return this._names;
    }

    /** 这个词在正文里出现过几**段**（0 = 没出现过）。
     *
     *  用于判断一个**已核实别名**是否真的有语料支撑：重耳 这类姓名式别名从不写成
     *  「國名+爵位+名」形态，所以不在 corpus_names 里，但它实实在在出现 198 次。
     *
     *  两字词先查**裸计数表**（已有缓存，零成本）；表里没有的才现查一次并缓存。
     *  注意 Python 侧 `occurs_in_corpus` 里那句
     *      `full, _short, raw = _corpus_scan(...)`   ← 第三个返回值其实是 bare
     *  变量名叫 raw，拿到的却是 bare（_corpus_scan 只返回 (full, short, bare)）。
     *  行为是确定的、也被测试锁住了，所以这里**照样用 bare**，不"顺手修正"——
     *  修正就等于改动了第四阶段的行为（§18）。这一点记在 phase5 报告里。
     *
     *  SQLite 的 LIKE 对 ASCII 字母不分大小写，且 `%`/`_` 是通配符（本模块不转义），
     *  这里照抄：词里有通配符时按 LIKE 语义匹配，否则退化成子串判断（等价且更快）。 */
    occursInCorpus(word) {
      if (!word) return 0;
      const bare = this._scan().bare;
      if ([...word].length === 2 && word in bare) return bare[word];
      if (this._occurCache.has(word)) return this._occurCache.get(word);
      const n = this._likeRowCount(word);
      this._occurCache.set(word, n);
      return n;
    }

    _likeRowCount(word) {
      const c = this.corpus;
      const idx = c.passageIdx, cText = this._cText;
      const hasWild = word.indexOf("%") >= 0 || word.indexOf("_") >= 0;
      const needle = NS.asciiFold(word);
      const re = hasWild ? likeRegex(needle) : null;
      let n = 0;
      for (let k = 0; k < idx.length; k++) {
        let t = c.rows[idx[k]][cText];
        // 只跳过 NULL：SQL 里 LIKE 对 NULL 求值为 NULL（不计入），对空串则是普通
        // 字符串比较（模式全为通配符时**能**匹上空串）。两者不能一并当空跳过。
        if (t === null || t === undefined) continue;
        // LIKE 折叠的是**两侧**：词里没有大写字母、正文里有（「&KR0862;」）时，
        // 只折叠词项就会漏。折叠只动 A-Z、不动漢字，所以子串判断仍然成立。
        if (ASCII_UPPER_RE.test(t)) t = NS.asciiFold(t);
        if (hasWild ? re.test(t) : t.indexOf(needle) >= 0) n++;
      }
      return n;
    }

    /** 短称在文本里是否**作为独立的称谓**出现，而不是别人的全称的一部分。
     *
     *  語料里「鄭穆公之子」也含「穆公」，但它写的是鄭穆公，不是秦穆公。
     *  判据只看**紧邻的前一个字**：是国名字（鄭/召/晉/楚…）就是在组成别人的全称。
     *  之所以能这么判，是因为全称的构造固定为「国名+谥号+爵位」，国名永远紧挨在
     *  谥号前面。 */
    bareHit(text, short) {
      if (!short) return false;
      let i = text.indexOf(short);
      while (i >= 0) {
        if (i === 0 || !STATE_CHARS.has(text[i - 1])) return true;
        i = text.indexOf(short, i + 1);
      }
      return false;
    }

    /** 在文本里找出实体，返回 [[实体, 命中词, 起, 止], …]。
     *
     *  长的优先，所以「齊桓公」不会被「桓公」吃掉（单个 alternation 里 Python 也是
     *  对每个位置取最长分支，行为一致且更快；等长的两个不同词不可能在同一位置同时
     *  命中，故同长分支之间的先后不影响结果）。
     *
     *  命中的若是短称，走 resolveAlias 按上下文落地；落不了地的把原词当候选实体
     *  报出去，由调用方按低权重处理——**不在这里丢掉**，否则「桓公」这种词在问句里
     *  会整个消失。 */
    findInText(text) {
      if (!text) return [];
      // 全称与别名一起进模式；长词在前，保证最长匹配
      const vocab = new Set(this.knownEntities());
      for (const canon of Object.keys(CURATED)) {
        for (const al of CURATED[canon].aliases) vocab.add(al);
      }
      const words = [...vocab].sort((a, b) => {
        const d = [...b].length - [...a].length;    // 长词在前
        return d !== 0 ? d : cmpStr(a, b);          // 等长时定序，保证结果可重复
      });
      if (!words.length) return [];
      const re = new RegExp(words.map(escapeRe).join("|"), "g");
      const out = [];
      let m;
      while ((m = re.exec(text)) !== null) {
        const word = m[0];
        // 三种情形：
        //   · 别名裁决成功（重耳 → 晉文公）→ 实体是规范名
        //   · word 本身就是规范名（晉文公）→ 实体即 word，resolve_alias 原样返回
        //   · 裁决不了（短称无上下文）→ 仍把 word 当候选实体报出去
        // 注意：重耳 这种别名在语料里出现 198 次但从不写成「國名+爵位+名」形态，
        // 所以不在 corpus_names 里；它能被识别**完全靠** _CURATED 的别名表。
        const res = this.resolveAlias(word, text);
        out.push([res[0] ? res[0] : word, word, m.index, m.index + word.length]);
      }
      return out;
    }
  }

  /** LIKE（无 ESCAPE）转正则：`%` 任意串、`_` 任意单字符；字母大小写已在两侧折叠。 */
  function likeRegex(word) {
    let out = "";
    for (const c of word) {
      if (c === "%") out += "[\\s\\S]*";
      else if (c === "_") out += "[\\s\\S]";
      else out += escapeRe(c);
    }
    return new RegExp("^[\\s\\S]*" + out + "[\\s\\S]*$");
  }

  // ------------------------------------------------------------ 只读接口

  function curated() {
    const out = {};
    for (const k of Object.keys(CURATED)) {
      out[k] = Object.assign({}, CURATED[k], { aliases: [...CURATED[k].aliases] });
    }
    return out;
  }

  function aliasesOf(entity) {
    const info = CURATED[entity];
    return info ? [...info.aliases] : [];
  }

  function ambiguousHeads() {
    const out = {};
    for (const k of Object.keys(AMBIGUOUS)) out[k] = [...AMBIGUOUS[k]];
    return out;
  }

  function isAmbiguous(alias) { return alias in AMBIGUOUS; }

  NS.entities = {
    Entities, STATES, STATES_EXTRA, CURATED, SHORT_MIN_BARE,
    curated, aliasesOf, ambiguousHeads, isAmbiguous,
  };
})(window.YindeEngine = window.YindeEngine || {});
