/* 问题 → 检索式的扩展（浏览器版）—— search/query_expansion.py 的忠实移植。
 *
 * **这里不生成史料，只生成检索词**（§2.3）。每个词都能在语料里查到实际出现：
 * 实体词来自实体层，意图词来自 question.INTENT_VOCAB["corpus"]（频次实测过），
 * 年份词来自问句本身。任何扩不出来、说不清出处的词都不写进结果。
 *
 * 为什么要分组而不是拉平成一个词表
 * --------------------------------
 * engine.run_search 把多个词按 **AND** 处理（fts_match_text 用空格连接 = 隐式 AND）。
 * 所以：
 *
 *     「管仲」「卒」「薨」「死」「沒」 当成 AND
 *
 * 要求同一段同时含这四个词，命中的段落几乎为零 —— 第二版把这些词拉平是错的。
 *
 * 正确的语义是：
 *
 *     (管仲 OR 管夷吾 OR 管子)   ← 同一个实体的不同写法，取任一
 *     AND
 *     (卒 OR 薨 OR 死 OR 沒)     ← 死亡的各种古代说法，取任一
 *
 * 也就是「组内 OR、组间 AND」。本模块的职责就是把词表切成这样的组。
 *
 * 与原模块的唯一差别：expand 不再收 db_path，改为收一个 Entities 实例。
 * _split_han / _RE_YEAR_COUNT 在 Python 原版里也**没有调用处**，照抄保留，
 * 免得日后与原版逐行比对时多出「这边少了什么」的疑问。
 */
"use strict";
(function (NS) {
  /** 一组语义等价的检索词（组内 OR）。 */
  class TermGroup {
    constructor(role, terms, note) {
      this.role = role;              // entity / intent / year / topic
      this.terms = terms || [];
      this.note = note || "";        // 这一组怎么来的，写进 API 响应便于复核
    }
    get length() { return this.terms.length; }
  }

  /** 扩展结果：分组 + 去重后的全部词。 */
  class Expanded {
    constructor() {
      this.groups = [];
      // 用户在问时长（「多少年」）时置位。排序层据此奖励含年数表达的段落 ——
      // 「重耳居狄凡十二年而去」正是问题的答案所在。年数本身不进检索词。
      this.asks_duration = false;
    }

    get all_terms() {
      const out = [];
      for (const g of this.groups) {
        for (const t of g.terms) if (out.indexOf(t) < 0) out.push(t);
      }
      return out;
    }

    asDict() {
      return {
        // Python 侧是 `for g in self.groups if g`：空组（terms 为空）不出现
        groups: this.groups.filter((g) => g.terms.length)
          .map((g) => ({ role: g.role, terms: g.terms, note: g.note })),
        all_terms: this.all_terms,
      };
    }
  }

  /** 把分析好的问题扩展成检索词组。确定性——同样的问题永远同样的词表。 */
  function expand(q, ents) {
    const exp = new Expanded();

    // 组 1：实体（含别名）。这是权重最高的一组，也是召回的主力。
    // 分档见 termRoles：规范名 / 已核实别名 / 语料派生短称，排序层按档给分。
    for (const e of q.entities) {
      for (const [role, term] of termRoles(e.entity, e.matched, ents)) {
        exp.groups.push(new TermGroup(
          role, [term],
          `实体 ${e.entity}（${NS.pyFloat(e.weight)}）：${e.why}`));
      }
    }

    // 组 2：意图强词。古人的说法与今人不同，这一组负责把「死」扩成 卒/薨/崩/沒。
    // 这些词**基本只**用于这个意思，可以凭它们单独把段落召回来。
    for (const name of q.intents) {
      const spec = NS.question.INTENT_VOCAB[name];
      const terms = spec.corpus.filter((t) => t !== "于" && t !== "於");
      if (terms.length) {
        exp.groups.push(new TermGroup(
          "intent", terms,
          `意图「${name}」的古代表达（语料实测词表，可独立召回）`));
      }
      // 组 2b：意图弱词。也说这个意思，但更常用于别的意思，**只参与排序**，
      // 不单独召回 —— 否则「殺之」「終取之」这类段落会灌满召回池。
      const weak = (spec.corpus_weak || []).filter((t) => terms.indexOf(t) < 0);
      if (weak.length) {
        exp.groups.push(new TermGroup(
          "intent_weak", weak,
          `意图「${name}」的弱表达（只参与排序，不独立召回）`));
      }
    }

    // 组 3：年份。用户提了纪年就一定要用上。
    if (q.years.length) {
      exp.groups.push(new TermGroup("year", [...q.years], "问句中的纪年表达"));
    }

    // 组 4：没有实体时的兜底——把问句里的实词切出来当主题词。
    // 「城濮之战谁赢了」里 城濮 不是人物实体，但它是检索的关键。
    if (!q.entities.length) {
      const topics = topicTerms(q.text);
      if (topics.length) {
        exp.groups.push(new TermGroup(
          "topic", topics, "问句切分得到的主题词（无人物实体时使用）"));
      }
    }

    exp.asks_duration = q.asks_duration;
    return exp;
  }

  /** 短称分布的键数 > 1 就是歧义。
   *
   *  一个称谓若**有可能指的人不止一个**，就不算确定别名 —— 单独出现时分不清是谁。
   *  实测：`夷吾` 同时是管仲（管夷吾）与晉惠公的别名。问「管仲是怎么死的」时，
   *  若把「夷吾」当确定别名，語料里「屈羽卒，子夷吾立。夷吾卒」这种**吴国**
   *  世系就会被当成管仲的史料排到前面。共用的别名一律降为候选档（弱）。
   *
   *  判据直接取实体层那份**唯一的**分布（short_forms，已核实别名已折入其中）：
   *  候选超过一个就是歧义。只看已核实表是不够的 —— 「桓公」在已核实表里
   *  只挂在齊桓公名下，但语料分布是 齊桓公 93 / 魯桓公 12 / 鄭桓公 7 / 秦桓公，
   *  漏掉它，史記「考王封其弟于河南，是為桓公…桓公卒」这段**東周**桓公就会蹿到
   *  第 4 位（实测 rel=29.0），而正文里根本没有「齊」。 */
  function sharedAliases(ents) {
    const out = new Set();
    const sf = ents.shortForms();
    for (const al of Object.keys(sf)) {
      if (Object.keys(sf[al]).length > 1) out.add(al);
    }
    return out;
  }

  /** 把「实体 + 别名」按**可核实的强弱**分成三档，而不是简单取前 2 个。
   *
   *  为什么不能按下标取：重耳（198 段）是晉文公的已核实别名，但它从不写成
   *  「國名+爵位+名」形态，所以不在 corpus_names 里 —— 按位置分类会把它当成
   *  「弱候选」，只给 2 分，结果「重耳居狄凡十二年而去」这种真正的答案段落
   *  排到了「魯僖之二十五年」后面。
   *
   *  分档依据是实体层自己的证据结构：
   *    · 规范名（晉文公）           → entity  精确 14 分
   *    · 已核实别名且语料里确实有   → alias   6 分（重耳/小白/管夷吾…）
   *    · 别名但语料里查无此词       → 丢掉（写进表却没出现过，不参与检索）
   *    · 多家共用的别名（夷吾）     → weak    2 分，且拿不到共现加成
   *    · 语料派生短称（文公/穆公）  → weak    2 分 */
  function termRoles(entityName, matched, ents) {
    const curated = NS.entities.CURATED[entityName] || {};
    const curatedAliases = new Set(curated.aliases || []);
    const shared = sharedAliases(ents);

    const roles = [["entity", entityName]];
    // aliasesOf 是 entities 的**模块级**函数（与 Python 侧 entities.aliases_of 同形），
    // 不是 Entities 实例的方法 —— 它只读 CURATED 常量表，与语料无关。
    for (const al of NS.entities.aliasesOf(entityName)) {
      if (shared.has(al)) {
        // 两家共用的别名（夷吾）：语料里是它，但**不敢说是谁**，按候选档。
        roles.push(["weak", al]);
      } else if (curatedAliases.has(al)) {
        // 已核实的别名：只要**语料里真的有**就是 alias 档。
        // 不能要求它出现在 corpus_names（那是「國名+爵位+名」形态表）——
        // 重耳 出现 198 次却从不是那个形态，按 corpus_names 判会把它降成
        // 弱候选，"重耳居狄凡十二年而去" 这种答案段落就沉下去了。
        if (ents.occursInCorpus(al)) roles.push(["alias", al]);
      } else if (al in ents.corpusNames()) {
        roles.push(["weak", al]);         // 只有语料频次的短称（文公/穆公）
      }
      // 语料里查不到的别名：不参与检索，也不报给用户当检索词
    }
    if (matched && !roles.some(([, t]) => t === matched)) {
      roles.push([
        (curatedAliases.has(matched) && !shared.has(matched)) ? "alias" : "weak",
        matched]);
    }
    return roles;
  }

  // 问句里的功能词/疑问词，切主题词时去掉
  const STOPWORDS = new Set([..."的了是在有和与與及之其所以为何何誰谁什么什麼怎么怎麼如何"
    + "吗嗎呢吧啊请问請問是什么是不是多少哪哪个個这這那哪些"
    + "年月份日时候時候关系關係原因经过經過事件事情"
    + "贏赢输輸人"]);
  // 问句里成段的功能短语，先整体摘掉再切
  const STOP_PHRASES = ["是什么", "什么关系", "怎么回事", "什么时候", "为什么",
                        "怎么样", "怎么办", "哪一年", "发生了", "做了什么",
                        "有多少", "是谁", "谁赢", "誰贏"];

  /** 从问句里切出可检索的主题词（2–4 字、非功能词）。
   *
   *  这不是分词器，只是把疑问句里剩下的实词捞出来 —— 宁可少切，不可乱切：
   *  切错了会引入用户根本没问的词，那就是在替用户编问题。 */
  function topicTerms(text) {
    let s = text;
    for (const p of STOP_PHRASES) s = s.split(p).join("");
    const out = [];
    HAN_RUN.lastIndex = 0;
    let m;
    while ((m = HAN_RUN.exec(s)) !== null) {
      // 只削句尾的泛称/疑问字（「董卓人」→「董卓」），不削句首 ——
      // 句首的「人」可能是主体的一部分（人傑、人臣），削错了就是替用户改问题。
      const run = rstripChars(m[0], TAIL_NOISE);
      let i = 0;
      while (i < run.length) {
        let advanced = false;
        for (const size of [4, 3, 2]) {
          const seg = run.slice(i, i + size);
          if (seg.length === size && ![...seg].some((c) => STOPWORDS.has(c))) {
            if (out.indexOf(seg) < 0) out.push(seg);
            i += size;
            advanced = true;
            break;
          }
        }
        if (!advanced) i += 1;           // 对应 Python 的 for…else
      }
    }
    return out;
  }

  const HAN_RUN = /[一-鿿]+/g;
  // 年数表达：十二年 / 十九年 / 三年 / 三百餘年。「多少年」里没有年数，所以必须
  // 先有个汉字数词再跟「年」，不能只匹「年」。（Python 原版定义了但未使用）
  const RE_YEAR_COUNT = /[一二三四五六七八九十百千萬万]{1,6}餘?年|[兩两三四五六七八九十]年/;
  // 句尾的泛称/疑问字，切主题词时削掉
  const TAIL_NOISE = new Set([..."人誰谁贏赢輸输呢吗嗎吧啊了"]);

  /** Python 的 str.rstrip(chars)：从右侧削掉落在这组字符里的任意字符。 */
  function rstripChars(s, chars) {
    let end = s.length;
    while (end > 0 && chars.has(s[end - 1])) end -= 1;
    return s.slice(0, end);
  }

  /** 连续汉字串按 2–4 字滑窗切，保留较长的优先。（Python 原版里没有调用处） */
  function splitHan(text) {
    const out = [];
    HAN_RUN.lastIndex = 0;
    let m;
    while ((m = HAN_RUN.exec(text)) !== null) {
      const run = m[0];
      let i = 0;
      while (i < run.length) {
        let advanced = false;
        for (const size of [4, 3, 2]) {
          if (i + size <= run.length) {
            out.push(run.slice(i, i + size));
            i += size;
            advanced = true;
            break;
          }
        }
        if (!advanced) i += 1;
      }
    }
    return out;
  }

  NS.queryExpansion = {
    TermGroup, Expanded, expand, termRoles, sharedAliases, topicTerms,
    splitHan, STOPWORDS, STOP_PHRASES, TAIL_NOISE, RE_YEAR_COUNT,
  };
})(window.YindeEngine = window.YindeEngine || {});
