/* 自然语言问题的分析层（浏览器版）—— search/question.py 的忠实移植。
 *
 * 职责**只有分析**，不做检索、不生成答案（§2.3：不能让 AI 代替检索器）。
 * 输入一句中文问题，输出结构化的：
 *
 *     原始问题 → 繁简统一 → 实体识别 → 意图识别 → 古代表达扩展
 *
 * 词典独立存放（任务书 §7）：意图词表、时间词、场所词都集中在本文件顶部，
 * 扩展成检索词由 query_expansion.js 负责，两者分开便于审查与替换。
 * 所有词表都用**本语料实测频次**校准（2026-09-11 在本库正文 175 万字体量里
 * 数的），注释里的频次一并搬过来 —— 改词表的人要知道每个词的分量。
 *
 * 与原模块的唯一差别：各函数不再收 db_path，改为收一个 Entities 实例。
 */
"use strict";
(function (NS) {
  // ------------------------------------------------------------------ 意图词表
  //
  // 每个意图给出：问句里的触发词（用户怎么问），以及语料里**真实存在**的表达
  // （史料怎么写）。触发词用于判意图，真实表达用于检索，两者不混。
  const INTENT_VOCAB = {
    // 某人什么时候死的 / 怎么死的
    death: {
      ask: ["卒", "死", "去世", "逝世", "过世", "亡", "崩", "薨", "殁", "没",
            "怎么死", "如何死", "何时死", "哪年死", "死于", "被杀", "被弑",
            "结局", "下场", "最后怎么样", "怎么没的"],
      // 强词：这些字**基本只**用于「死」。问某人怎么死的，命中它们就八九不离十。
      // 歿/殁 是任务书 §31 点名的扩展词，语料里确实有（歿 7 段、殁 2 段，全是
      // 「死」的意思），按「说不清出处的词不写进结果」的标准够格，收进强词。
      corpus: ["卒", "薨", "崩", "沒", "歿", "殁", "弑", "卒於", "卒于",
               "死於", "死于", "卒之歲", "卒之岁"],
      // 弱词：确实能表示死，但**大量**用于别的意思，单靠它召回的全是噪声。
      // 实测（与各实体共现段数 / 全库段数）：
      //   卒  齊桓公9 管仲6 晉文公8   → 强
      //   終  管仲1                  → 弱（終=终于/最终，全库660）
      //   亡  齊桓公1 管仲1 晉文公1   → 弱（亡=灭亡/逃亡，全库1680）
      //   殺  管仲3                  → 弱（殺=杀别人，全库2352）
      // 弱词仍参与标注与排序，但**不能**单独把段落召回来，否则召回池被灌爆。
      corpus_weak: ["死", "終", "亡", "殺"],
      // 「亡」还要排除用法（全库 1680 次里占多数的是「追亡人」23、「必亡」17、
      // 「存亡之」12 这类**灭亡/逃亡**）。不加排除的话「流亡了多少年」会被判成
      // death 意图 —— 见 triggered 与这张 exclude 表。
      exclude: {
        "亡": ["流亡", "出亡", "亡人", "亡去", "追亡", "存亡",
               "亡國", "亡国", "亡奔", "逃亡", "亡命", "亡歸",
               "亡归", "亡走", "亡之", "亡也", "不亡", "未亡"],
        "死": ["不死", "死罪", "死士", "致死", "死之", "死於"],
      },
    },
    // 出亡 / 流亡（「重耳流亡了多少年」这类）
    flight: {
      ask: ["流亡", "出亡", "逃亡", "出奔", "奔亡", "避难", "避難",
            "在国外", "在外", "客居", "流落"],
      corpus: ["出亡", "奔", "出奔", "亡奔", "出居", "居"],
      // 語料实测：重耳 198 段里有 出亡4 / 奔9 / 居多，且「重耳居狄凡十二年而去」
      // 直接写着年数。注意「奔」「居」本身很泛（奔=投奔/奔走，居=居住），
      // 但它们只在**问句已表明在问流亡**时才进意图组，不影响别的问句。
    },
    // 多少年 / 多久
    duration: {
      ask: ["多少年", "几年", "多久", "多长时间", "多少时候", "几年间",
            "十九年", "多少载", "几年时间"],
      corpus: [],                            // 无固定词：靠年数正则提年数
    },
    // 某人何时出生 / 出身
    birth: {
      ask: ["出生", "生于", "何时生", "哪年生", "出身", "来历", "身世",
            "是谁的儿子", "谁之子", "家族"],
      corpus: ["生", "生於", "生于", "立", "少", "初", "為公子", "为公子"],
      // 频次: 生 常见但多义（生死/生出），作检索词要配合实体
    },
    // 某句史料说的是谁 / 某人是哪国人
    identity: {
      ask: ["是谁", "什么人", "哪个国家", "哪国人", "何人", "何许人",
            "什么意思", "指谁", "说的谁"],
      // 全部是单字且极泛（曰/名/字/氏/姓 全库各上万段），**不能**用来召回：
      // 实测问「董卓是什么人」时它们把兜底池灌到 8757 条，等于用无关史料
      // 假装回答了「董卓不在语料里」这个问题。只留作排序时的弱信号。
      corpus: [],
      corpus_weak: ["曰", "謂", "谓", "名", "字", "諡", "谥", "氏", "姓"],
    },
    // 两个人什么关系
    relation: {
      ask: ["关系", "什么关系", "与", "和", "之间", "父子", "君臣",
            "谁的儿子", "朋友", "敌对", "辅佐", "推荐", "师父"],
      // 只用多字词召回。单字（子/父/臣/君/兄/弟/友/相/佐/事）会被「母弟」
      // 「公子」「晉君」这样的词内部命中，等于给所有段落加同样的分，
      // 结果把「秦穆公以夫人入公子夷吾為晉君」顶到「秦穆公+百里奚同段」前面，
      // 而后者才是回答问题的段落。降为弱词，只参与排序。
      corpus: ["父子", "君臣", "兄弟", "師事", "相之", "輔佐", "薦", "事之"],
      corpus_weak: ["子", "父", "臣", "君", "兄", "弟", "友", "師", "师",
                    "相", "佐", "事", "從", "从"],
    },
    // 某件事的经过
    event: {
      // 触发词**不含**「怎么」「如何」：它们是疑问副词，任何问句里都可能出现，
      // 拿它们判「事件」意图会让「管仲是怎么死的」同时命中 death 与 event，
      // 于是事件强词（伐/戰于）也跟着参与打分 —— 实测「管夷吾奉公子糾奔魯」
      // 那一段因此压过了「管仲卒受下卿之禮」。判事件意图要用**实义**问法。
      ask: ["发生了什么", "什么事", "经过", "怎么回事",
            "哪件事", "做了", "事件", "变法", "變法", "改革", "新政"],
      // 只用**多字**词做召回。单字（立/殺/取/入）看着像事件词，实际全库到处
      // 都有，召回的全是噪声，而且命中理由无法向用户交代。
      corpus: ["伐", "戰", "盟", "會", "出奔", "圍", "敗", "變法",
               "伐之", "戰于", "會于", "盟于", "至于", "法", "令", "政"],
      corpus_weak: ["立", "殺", "取", "入"],
      // 「法」「令」「政」是变法类问句的正词：实测与商鞅同段的「法」6 次、
      // 「令」4 次（例「至夫秦用商鞅之法，¶」「衛鞅聞是令下，¶」）。它们放强档
      // 的前提是**事件意图不被疑问副词误触发**（见上面的 ask 表）。
    },
    // 为什么
    reason: {
      // 「怎么回事」不在这里：它问的是「这是件什么事」，属于 event；
      // 「怎么会」才是问原因。两个词只差一个字，意图完全不同，不能混。
      ask: ["为什么", "為何", "为何", "原因", "怎么会", "凭什么", "何以",
            "所以然"],
      corpus: ["故", "是以", "由是", "於是", "于是", "以此",
               "所以", "為之", "为之", "故曰"],
      // 注：史书里原因常以「故」「是以」引出，但这两个词极泛，
      // 单靠它们检索噪声很大，实际使用时会与实体联合收紧。
    },
    // 什么时候
    time: {
      ask: ["什么时候", "何时", "哪一年", "几年", "那年", "哪一年",
            "同时", "先后"],
      corpus: ["元年", "二年", "三年", "四年", "五年", "六年", "七年",
               "八年", "九年", "十年", "是歲", "是岁", "其年", "明年"],
    },
    // 在哪里
    place: {
      ask: ["哪里", "在哪", "何地", "什么地方", "哪国", "都城", "到哪"],
      corpus: ["于", "於", "在", "至", "及", "之", "邑", "城", "都"],
    },
  };

  // 意图判定优先级：一句话里同时出现多个触发词时，按此顺序取第一个命中的。
  // 死亡/出生这类「事实性」意图比「事件」「关系」更具体，优先匹配能减少误判。
  // flight 排在 death 前面：问「重耳流亡多少年」时问句里既有「亡」也有年数，
  // 但用户问的是流亡这件事，不是死。让它先命中，death 的「亡」还有 exclude 兜底。
  const INTENT_PRIORITY = ["flight", "duration", "death", "birth", "relation",
                           "reason", "time", "place", "identity", "event"];

  function cplen(s) { return [...s].length; }

  /** Python 的 float.__str__：整数值的浮点要写成「1.0」，JS 的模板串会给「1」。
   *  实体权重 1.0 / 0.7 / 0.3 会进 notes，而 notes 是直接显示给用户的，
   *  也是 harness 的比对字段 —— 两边必须逐字一致。 */
  function pyFloat(v) { return Number.isInteger(v) ? v.toFixed(1) : String(v); }

  /** 问句里是否真的命中了这个词：命中位置若落在排除用法内，不算。
   *
   *  「流亡了多少年」里的「亡」不算死亡意图——排除表把它剔掉。
   *  判定看命中处左右各一字能否凑成排除词，而不是简单 `in`。
   *
   *  按**码位**遍历：Python 的 str 是码位序列，text.find 与切片都以码位计。
   *  这里不能直接用 indexOf + slice —— UTF-16 单元下标在有星形字时会与码位
   *  下标错开，窗口就取偏了。问句很短，逐码位比对的开销可以忽略。 */
  function triggered(text, term, excludes) {
    const cs = [...text], ts = [...term];
    let start = 0;
    for (;;) {
      let i = -1;
      for (let k = start; k + ts.length <= cs.length; k++) {
        let ok = true;
        for (let j = 0; j < ts.length; j++) {
          if (cs[k + j] !== ts[j]) { ok = false; break; }
        }
        if (ok) { i = k; break; }
      }
      if (i < 0) return false;
      // 命中处能否和邻近字凑成排除词？窗口取「命中前 2 字 + 词本身」，这样
      // 排除词无论从命中处往前伸（流亡 的 流 在 亡 前面）还是往后伸都能盖住。
      const window = cs.slice(Math.max(0, i - ts.length), i + ts.length * 2).join("");
      if (!excludes.some((x) => window.indexOf(x) >= 0)) return true;
      start = i + 1;
    }
  }

  // 关系类问句里「A 和 B」的分隔写法。
  // 与 Python 原版一致：定义了但全文件没有使用处（留着是为了与原型逐行对得上）。
  const RE_RELATION_SPLIT = /[与與和及]|之间|之間|的关系|的關係/;

  // 年份表达：隐公元年 / 僖公三十年 / 周赧王元年…
  const RE_REIGN_YEAR = /([一-鿿]{1,4}(?:公|侯|伯|王))([元一二三四五六七八九十百]+年)/g;
  // 公元纪年：公元前 651 年 / 651 BC（春秋战国问题里用户常用公历）。
  // 两处都不能照抄 JS 的 \s 与 \d：Python 的 \s 多 U+001C-1F/U+0085、少 U+FEFF，
  // \d 还认全角数字（６５１）。这里按 Python 的语义写死（PY_WS + \p{Nd}）。
  const PY_WS = NS.PY_WS;
  const RE_BC = new RegExp(
    "(?:公元前|西元前|BC|bc)[" + PY_WS + "]*([\\p{Nd}]{1,4})" +
    "|([\\p{Nd}]{1,4})[" + PY_WS + "]*(?:BC|bc|年[" + PY_WS + "]*前)", "gu");

  /** 一句问题分析完之后的样子。 */
  class Question {
    constructor(raw, text) {
      this.raw = raw;
      this.text = text;
      this.intents = [];
      this.entities = [];
      this.years = [];
      this.asks_duration = false;
      this.notes = [];
    }

    get main_intent() { return this.intents.length ? this.intents[0] : null; }

    /** 权重最高的实体（排序层会用）。没有实体就只能靠意图词检索。
     *  Python 的 max(key=…) 取的是**第一个**最大值，这里也用 > 而非 >=。 */
    get main_entity() {
      let best = null;
      for (const e of this.entities) {
        if (best === null || e.weight > best.weight) best = e;
      }
      return best ? best.entity : null;
    }

    asDict() {
      return {
        raw: this.raw,
        text: this.text,
        intents: this.intents,
        main_intent: this.main_intent,
        entities: this.entities,
        years: this.years,
        notes: this.notes,
      };
    }
  }

  /** 判意图，返回 [命中的意图序列, 说明]。按 INTENT_PRIORITY 排序，不是出现顺序。 */
  function detectIntents(text) {
    const hits = [], notes = [];
    for (const name of INTENT_PRIORITY) {
      const spec = INTENT_VOCAB[name];
      const excludes = spec.exclude || {};
      for (const kw of spec.ask) {
        if (triggered(text, kw, excludes[kw] || [])) {
          hits.push(name);
          notes.push(`意图「${name}」由问句里的「${kw}」触发`);
          break;
        }
      }
    }
    return [hits, notes];
  }

  /** 找出问题里的实体，带上语料给的确定度权重。
   *
   *  权重是**可解释的**，来自实体层而不是拍脑袋：
   *    · 全称（齊桓公/秦繆公）    → 1.0，语料里就是这么写的
   *    · 短称且上下文能裁决       → 0.7，语料分布支持
   *    · 短称但上下文裁决不了     → 0.3，只是候选，排序时不能压过确定命中 */
  function findEntities(text, ents) {
    // 同一个实体的多种写法（齊桓公 与 桓公 同时出现在问句里）合并成一条，
    // 保留权重最高的那次作为主命中，其余写法记在 also 里。
    const best = new Map();
    for (const [ent, word, start, end] of ents.findInText(text)) {
      let weight, why;
      if (ent === word) {                       // 全称直接命中
        weight = 1.0; why = "全称精确命中";
      } else {
        const res = ents.resolveAlias(word, text);
        why = res[1];
        // 語料裁决成功 → 0.7；没裁决成功（实体层返回 null 或指到别人）→ 0.3，
        // 当候选处理，排序时压不过确定命中。
        //
        // 实测（2026-09-11，§18 只读核查，**未改 Python**）：0.3 这一档在
        // Python 原版里**取不到**。理由在实体层：find_in_text 命中别名落地失败时
        // 报的是 `ent if ent else word`，即把 word 自己当实体报出来 —— 于是
        // `ent !== word` 只可能发生在「resolve_alias 裁决成功」的情形，而这里再
        // 问一次 resolve_alias（同参数、无随机、无外部状态）必然得到同一个答案，
        // 永远走 0.7 分支。全语料 203,308 段、2,937 个命中对（其中 484 个是
        // 别名裁决）遍历核验，违反「ent !== word ⟹ resolve_alias(word) === ent」
        // 的样本为 **0 个**。
        //
        // 照抄不改：改掉就是改第四阶段的行为（§18），而这档留着也无害。
        weight = res[0] === ent ? 0.7 : 0.3;
      }
      const cur = best.get(ent);
      if (cur === undefined) {
        best.set(ent, { entity: ent, matched: word, span: [start, end],
                        weight, why, also: [] });
      } else if (weight > cur.weight) {
        cur.also.push(cur.matched);
        cur.matched = word; cur.span = [start, end];
        cur.weight = weight; cur.why = why;
      } else if (word !== cur.matched) {
        cur.also.push(word);
      }
    }
    // key 的先后沿用插入序（Map 保证），按权重降序稳定排序 —— 与 Python 的
    // sorted(key=-weight)（稳定）一致，同权重时保持命中先后。
    return [...best.values()].sort((a, b) => b.weight - a.weight);
  }

  /** 抽取纪年表达。只做识别与保留，不做公历换算（换算是史实推断，见 §八.4）。 */
  function findYears(text) {
    const out = [];
    RE_REIGN_YEAR.lastIndex = 0;
    let m;
    while ((m = RE_REIGN_YEAR.exec(text)) !== null) out.push(m[0]);
    RE_BC.lastIndex = 0;
    while ((m = RE_BC.exec(text)) !== null) out.push(pyStrip(m[0]));
    return out;
  }

  /** 把一句自然语言问题分析成结构化结论。
   *
   *  这是本模块唯一的入口。确定性（deterministic）——同样的问题永远得到同样的
   *  结论，没有随机、没有外部调用、没有 LLM（§2.3）。 */
  function analyze(question, ents) {
    const raw = pyStrip(question || "");
    if (!raw) return withNote(new Question(raw, ""), "空问题");

    // 1. 繁简统一：用户多半用简体问，语料是繁体。
    //    转换不可靠时恒等回退（保留原字），由 zh.to_traditional 负责，§12 的要求。
    const text = NS.zh.toTraditional(raw);
    const q = new Question(raw, text);
    if (text !== raw) {
      q.notes.push(`繁简统一：「${raw}」→「${text}」`);
    } else {
      q.notes.push("繁简统一：未发生转换（原文已可用）");
    }

    // 2. 实体识别
    q.entities = findEntities(text, ents);
    for (const e of q.entities) {
      q.notes.push(
        `实体 ${e.entity}（问句里写作「${e.matched}」，权重 ${pyFloat(e.weight)}）：${e.why}`);
    }

    // 3. 意图识别
    const [intents, notes] = detectIntents(text);
    q.intents = intents;
    q.notes.push(...notes);

    // 4. 纪年 + 时长问法
    q.years = findYears(text);
    if (q.years.length) q.notes.push("识别到纪年：" + q.years.join("、"));
    if (q.intents.indexOf("duration") >= 0) {
      q.asks_duration = true;
      q.notes.push("问的是时长：排序时优先含年数表达的史料");
    }

    // 5. 关系类问句：两个实体都在时，明确记下配对，供事件聚合区分 A→B / B→A
    if (q.intents.indexOf("relation") >= 0 && q.entities.length >= 2) {
      q.notes.push("关系类问句，涉及 " +
                   q.entities.slice(0, 2).map((e) => e.entity).join(" 与 "));
    }

    if (!q.intents.length && !q.entities.length) {
      q.notes.push("未能识别出实体或意图，将退化为普通全文检索");
    }
    return q;
  }

  function withNote(q, note) { q.notes.push(note); return q; }

  /** Python 的 str.strip()（空白集合与 JS 的 trim 不同，见 corpus.js）。 */
  function pyStrip(s) { return NS.pyStrip(s); }

  /** 问题 → 检索词表（古代表达扩展）。排序：实体 > 意图词 > 年份。
   *
   *  只做**词表扩展**，不做同义改写：每个词都能在语料里查到实际出现，
   *  这是 §2.3「不能凭空让 AI 生成史料」在检索侧的最低要求。 */
  function expandTerms(q, ents) {
    const terms = [];
    for (const e of q.entities) {
      if (terms.indexOf(e.matched) < 0) terms.push(e.matched);
      if (terms.indexOf(e.entity) < 0) terms.push(e.entity);
      // 别名也进检索：问「商鞅」时「衛鞅」「公孫鞅」的史料同样该召回
      for (const al of NS.entities.aliasesOf(e.entity)) {
        if (terms.indexOf(al) < 0) terms.push(al);
      }
    }
    for (const name of q.intents) {
      for (const w of INTENT_VOCAB[name].corpus) {
        if (terms.indexOf(w) < 0) terms.push(w);
      }
    }
    for (const y of q.years) {
      if (terms.indexOf(y) < 0) terms.push(y);
    }
    return terms;
  }

  NS.question = {
    INTENT_VOCAB, INTENT_PRIORITY, Question,
    analyze, detectIntents, findEntities, findYears, expandTerms,
    triggered, RE_REIGN_YEAR, RE_BC, RE_RELATION_SPLIT, pyFloat,
  };
  NS.pyFloat = pyFloat;          // 后续模块（排序/聚合）也要按 Python 的浮点写法输出
})(window.YindeEngine = window.YindeEngine || {});
