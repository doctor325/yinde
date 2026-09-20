/* 候选史料的相关性排序（浏览器版）—— search/ranking.py 的忠实移植。
 *
 * 输入是召回阶段的候选片段，输出是按相关性降序的列表。
 * **纯打分排序，不做文本加工** —— 排序只读原文，不改写、不摘要、不生成（§2.3）。
 *
 * 打分项都是可解释的、逐项能说清「为什么加了这 5 分」的，便于在验收时复核
 * （数值与 WEIGHTS 一致，改权重时两处一起改）：
 *
 *     实体精确命中      +14   问「齊桓公」，段里就写着「齊桓公」
 *     第二个实体        +9.5  问「秦穆公和百里奚」，第二个人的规范名也在段里
 *     实体别名命中      +6    写的是「重耳」；只在没有规范名命中时才计
 *     实体候选（短称）  +2    语料裁决不了的短称，只是个候选
 *     意图强词命中      +8    问「怎么死的」，段里有「卒/薨/崩/沒」
 *     多意图词          +2/个 词命中的越多越相关，但收益递减（封顶 +14）
 *     意图弱词          +1.5/个 兼用字（殺/子/君），封顶 +4.5，且不触发共现加成
 *     同 passage 内共现 +15   实体与**强**意图词在同一段里 —— 这是最强的信号
 *     多实体同段        +20   问 A 与 B，一段里同时写着 A、B（按全部写法判）
 *     只有实体没有意图  +3    相关，但不是问的那件事
 *     时长年数          +20   问「多少年」时，写着「凡十二年」的段落就是答案
 *
 * 相邻段落/成段拼接属于**事件聚合**那一步（第三阶段 result_block 的职责），
 * 不在这里打分 —— 这里只给单段打分，避免同一份信息被两处重复计分。
 *
 * 与原模块的唯一差别：has_form 与 score_candidate 多收一个 Entities 实例，
 * 因为 bare_hit（短称要独立出现才算命中）要看语料统计，Python 侧读库、这里读内存。
 */
"use strict";
(function (NS) {
  // 打分权重。数值不是调参调出来的玄学，每一项都能对着一条真实命中说明白：
  // 见文件头注释。改动这里要同步更新 docs/phase4_report.md。
  const WEIGHTS = {
    entity_exact: 14.0,     // 命中第一个实体（全称）：最确定的信号
    entity_more: 9.5,       // 命中第二个及以后的实体（问「A 和 B」时 B 也要算）
    entity_alias: 6.0,      // 别名命中：确定，但隔了一层
    entity_weak: 2.0,       // 短称且上下文裁决不了：只是个候选
    intent_hit: 8.0,        // 第一个意图强词命中（卒/薨/崩/沒 这类专用词）
    intent_extra: 2.0,      // 每多命中一个意图词
    intent_cap: 14.0,       // 意图词加分上限，避免穷举词表刷分
    // 弱意图词（子/君/弟/殺 这类兼用字）：命中说明不了什么，只给很小的分。
    // 关键：弱词**不能**触发 co_occur 加成 —— 否则「公子」「晉君」里的
    // 「子」「君」会把「秦穆公以夫人入公子夷吾為晉君」顶到真正的
    // 「秦穆公与百里奚同段」之上（实测就是这么错的）。
    intent_weak_hit: 1.5,
    intent_weak_cap: 4.5,
    co_occur: 15.0,         // 实体与（强）意图词同段：最强的相关性证据
    // 候选实体（裁决不了的短称/共用别名）与意图词同段：段落确实在讲这件事，
    // 但「是不是这个人」没定论，所以只给一半多一点的加成，不能让它顶掉确定命中。
    co_occur_weak: 6.0,
    // 问「A 和 B」时，A、B 同时出现在一段里。20 分和 duration_answer 同一个道理：
    // 用户问的是两个人的关系，那么「两位都写着」就比「写着其中一位 + 意图词」
    // 更贴近所问（实测：「管仲和鲍叔牙」若不压过，左傳「有鮑叔牙、賓須無…」
    // 只写着一位却霸着第一，史記「鮑叔牙曰…君將治齊」那段正面记载反而沉下去）。
    multi_entity: 20.0,
    entity_only: 3.0,       // 只有实体、没有意图词
    // 没有人物实体时（「城濮之戰谁赢了」），问句里的实词就是用户唯一的约束。
    // 这一档只做**如实标注**用：这些片段能进候选池本就是因为命中了主题词，
    // 给分是为了让结果里的「命中理由」不是空的，不改变它们之间的次序。
    topic_hit: 5.0,
    // 问「多少年」时，含年数表达的段落优先：「重耳居狄凡十二年而去」直接就是答案。
    // 20 分压过 co_occur，因为这时候年数比「有没有写卒/奔」更贴近用户所问。
    duration_answer: 20.0,
  };

  // 年数表达（十二年 / 十九年 / 三年）—— 只用于判断段落里有没有年数，不改文本
  // 时长年数 vs 纪年：区分靠**前一个字**，不靠年数本身。
  //   「重耳居狄凡十二年而去」→ 凡十二年 = 时长，是答案
  //   「魯僖之二十五年」      → 之二十五年 = 纪年，只是时间定位
  // 前置字取表时长/累计的动词与副词（凡/居/立/行/後/積/共/前後/歷）才认。
  const RE_YEAR_COUNT = new RegExp(
    "[凡居立行後后積积共歷历]([一二三四五六七八九十百千萬万]{1,6}餘?年" +
    "|[兩两三四五六七八九十]年)");

  /** 文本里有没有这个实体的任何一种写法。
   *
   *  一个实体的写法分档（见 query_expansion.termRoles），比对方式也跟着分：
   *  规范名用字面比对；别名/短称要过 bare_hit，否则「鄭穆公」里的「穆公」会被
   *  当成秦穆公（判据见 entities.bare_hit）。
   *
   *  注意 Python 写法 `if t in text if role == "entity" else bare_hit(...)`
   *  的条件表达式结合方式是 `(t in text) if (role == "entity") else bare_hit(...)`，
   *  不是 `t in (text if ...)` —— 这里照它的语义写。 */
  function hasForm(text, forms, ents) {
    for (const [role, t] of forms) {
      if (!t) continue;
      if (role === "entity" ? text.indexOf(t) >= 0 : ents.bareHit(text, t)) {
        return true;
      }
    }
    return false;
  }

  /** 一条候选片段。字段与引擎返回的命中行对齐，额外记打分明细。 */
  class Candidate {
    constructor(o) {
      this.passage_id = o.passage_id;
      this.file_id = o.file_id;
      this.row_no = o.row_no;
      this.seq = o.seq || 0;              // 文件内记录序，组装片段要用
      this.book_id = o.book_id || "";
      this.book_title = o.book_title || "";
      this.text_orig = o.text_orig || "";
      this.score = o.score || 0;
      this.hits = o.hits || [];           // 实际命中的检索词
      this.detail = o.detail || {};       // 逐项得分，验收时可复核
      this.layer = o.layer || "main";     // 第一阶段结构层
    }
  }

  /** Python 的 repr(str)：列表进 detail 的键时要写成 ['卒', '死'] 这个样子。
   *
   *  CPython 选引号的规则是「除非串里有 ' 且没有 \"，否则用单引号」；
   *  汉字等可打印字符原样输出（不转义）。这里的词表只有汉字，
   *  转义分支实际用不到，但既然要复刻 repr 就把关键几条写全。 */
  function pyReprStr(s) {
    const quote = (s.indexOf("'") >= 0 && s.indexOf('"') < 0) ? '"' : "'";
    let out = "";
    for (const c of s) {
      if (c === "\\") out += "\\\\";
      else if (c === quote) out += "\\" + c;
      else if (c === "\n") out += "\\n";
      else if (c === "\r") out += "\\r";
      else if (c === "\t") out += "\\t";
      else out += c;
    }
    return quote + out + quote;
  }

  function pyReprList(xs) { return "[" + xs.map(pyReprStr).join(", ") + "]"; }

  /** Python 的 round(x, 2)。参与求和的权重都是一位小数（.0 / .5 / .5），
   *  部分和在二进制里精确，不存在 round() 的银行家舍入边界，取两位即可。 */
  function pyRound2(v) { return Number(v.toFixed(2)); }

  /** 给一条片段打分。就地写 cand.score / hits / detail 并返回它。
   *
   *  参数顺序与 Python 侧一一对应，末尾多一个 Entities 实例（bare_hit 要用）。 */
  function scoreCandidate(cand, entityTerms, aliasTerms, weakTerms, intentTerms,
                          asksDuration, intentWeak, topicTerms, askedForms,
                          entityForms, ents) {
    const text = cand.text_orig || "";
    const detail = {};
    const hits = [];

    // --- 实体侧 ---------------------------------------------------------
    // 问句里每个实体都单独计分：问「秦穆公和百里奚是什么关系」时，同时写着
    // 这两个人的段落才是回答问题的段落，只写着秦穆公的只是话题相关。
    let entScore = 0.0;
    let entN = 0;
    let entTier = "";                                 // exact / alias / weak
    for (const t of entityTerms) {
      if (!t || text.indexOf(t) < 0) continue;
      const w = entScore === 0.0 ? WEIGHTS.entity_exact : WEIGHTS.entity_more;
      entScore += w;
      entN += 1;
      entTier = "exact";
      hits.push(t);
      detail["实体:" + t] = w;
    }
    // 问「A 和 B 是什么关系」时，一段里同时写着 A 和 B 才是回答问题的段落 ——
    // 只写着其中一位的段落，哪怕把意图词命中了，也只是回答了半个问题。
    //
    // 同段要按**全部写法**判，不能只看规范名：语料里写「昔繆公求士…東得百里奚
    // 於宛」（諫逐客書），只认「秦穆公」就会漏掉这段**正面记载两人关系**的史料。
    // 短称（穆公/繆公）仍旧要过 bare_hit，不至于把「鄭穆公」算成秦穆公。
    let present = entN;
    if (entityForms && entityForms.length) {
      present = entityForms.filter((forms) => hasForm(text, forms, ents)).length;
    }
    if (present >= 2) {
      entScore += WEIGHTS.multi_entity;
      detail[`${present}个实体同段`] = WEIGHTS.multi_entity;
    }
    if (entScore === 0.0) {
      for (const t of aliasTerms) {
        // 别名同样要过 bare_hit：已核实的别名里既有姓名式（重耳/小白/
        // 管夷吾，前面不可能跟国名，守卫对它们是空操作），也有谥号式
        // （穆公/繆公），后者正是「鄭穆公」会冒充的那个。
        if (t && ents.bareHit(text, t)) {
          // 用户**自己写的那个词**命中，比规范名命中更该算数：问「重耳流亡」
          // 时，写着「重耳出奔」的段落是正面回答，只写「晉文公」的段落可能
          // 讲的是别人的事（实测 國語「敗於城濮懼出奔楚…晉文公討不伏」讲的是
          // 衛成公出奔，却凭「晉文公+奔」压过了「重耳出奔」那一句）。
          // 这不是新增同义词，只是承认用户的原词就是最精确的检索词。
          const asked = (askedForms || []).indexOf(t) >= 0;
          const w = asked ? WEIGHTS.entity_exact : WEIGHTS.entity_alias;
          entScore += w;
          entTier = asked ? "exact" : "alias";
          hits.push(t);
          detail[(asked ? "问句原词:" : "实体别名:") + t] = w;
          break;
        }
      }
    }
    if (entScore === 0.0) {
      for (const t of weakTerms) {
        // 短称必须**独立**出现才算命中：段里写「鄭穆公之子」时，其中的
        // 「穆公」是鄭穆公，不能拿去顶秦穆公（判据见 entities.bare_hit）。
        if (t && ents.bareHit(text, t)) {
          entScore += WEIGHTS.entity_weak;
          entTier = "weak";
          hits.push(t);
          detail["实体候选:" + t] = WEIGHTS.entity_weak;
          break;
        }
      }
    }

    // --- 意图侧 ---------------------------------------------------------
    // 强词与弱词分开算：强词（卒/薨/崩）命中说明段落真的在讲这件事；
    // 弱词（子/君/殺）命中只说明这个字出现过。两者对「实体+意图同段」这个
    // 最强信号的意义完全不同，所以弱词的分数独立记、且**不**触发组合加成。
    const intentHits = intentTerms.filter((t) => t && text.indexOf(t) >= 0);
    const weakHits = (intentWeak || []).filter((t) => t && text.indexOf(t) >= 0);
    let intentScore = 0.0;
    if (intentHits.length) {
      intentScore = WEIGHTS.intent_hit;
      detail["意图:" + intentHits[0]] = WEIGHTS.intent_hit;
      const extra = Math.min((intentHits.length - 1) * WEIGHTS.intent_extra,
                             WEIGHTS.intent_cap - WEIGHTS.intent_hit);
      if (extra > 0) {
        intentScore += extra;
        detail["意图词+" + (intentHits.length - 1)] = extra;
      }
      hits.push(...intentHits);
    }
    if (weakHits.length) {
      const weakScore = Math.min(weakHits.length * WEIGHTS.intent_weak_hit,
                                 WEIGHTS.intent_weak_cap);
      intentScore += weakScore;
      detail["弱意图词" + pyReprList(weakHits.slice(0, 3))] = weakScore;
      hits.push(...weakHits);
    }

    // --- 主题词（无人物实体时的兜底约束）---------------------------------
    let topicScore = 0.0;
    const topicHits = (topicTerms || []).filter((t) => t && text.indexOf(t) >= 0);
    if (topicHits.length) {
      topicScore = WEIGHTS.topic_hit;
      hits.push(topicHits[0]);
      detail["主题:" + topicHits[0]] = WEIGHTS.topic_hit;
    }

    // --- 组合项 ---------------------------------------------------------
    // co_occur 只认**强**意图词：弱词遍地都是，拿它当「同段」证据等于没证据。
    let combo = 0.0;
    if (entScore > 0 && intentHits.length) {
      // 只有**确定**的实体命中（全称/已核实别名）才配拿满额共现加成。
      // 候选档（裁决不了的短称、多家共用的别名）与意图词同段只说明「这段在讲
      // 这件事」，不说明「讲的是这个人」—— 给它满分会让「屈羽卒，子夷吾立」
      // 这种别的国家的世系排到真正的史料前面。
      const w = (entTier === "exact" || entTier === "alias")
        ? WEIGHTS.co_occur : WEIGHTS.co_occur_weak;
      combo += w;
      detail[w === WEIGHTS.co_occur ? "实体+意图同段" : "候选实体+意图同段"] = w;
    } else if (entScore > 0 && !intentHits.length) {
      combo += WEIGHTS.entity_only;
      detail["仅实体"] = WEIGHTS.entity_only;
    }

    // 问「多少年」时，写了**时长**年数的段落更接近答案（「居狄凡十二年而去」）。
    // 纪年（「魯僖之二十五年」）不算 —— 它只是时间定位，不是用户问的时长。
    if (asksDuration) {
      const m = RE_YEAR_COUNT.exec(text);
      if (m) {
        combo += WEIGHTS.duration_answer;
        detail["含时长:" + m[0]] = WEIGHTS.duration_answer;
      }
    }

    cand.score = pyRound2(entScore + intentScore + topicScore + combo);
    cand.hits = hits;
    cand.detail = detail;
    return cand;
  }

  /** 打分并降序排。
   *
   *  同分时的次序是**确定的**（不依赖输入顺序，避免两次请求结果不一样）：
   *  先按书、再按文件、最后按行号 —— 都是数据库里的稳定字段。
   *  （JS 的 Array.prototype.sort 自 ES2019 起保证稳定，与 Python 的稳定排序一致。）*/
  function rank(cands, entityTerms, aliasTerms, weakTerms, intentTerms,
                asksDuration = false, intentWeak = null, topicTerms = null,
                askedForms = null, entityForms = null, ents = null) {
    for (const c of cands) {
      scoreCandidate(c, entityTerms, aliasTerms, weakTerms, intentTerms,
                     asksDuration, intentWeak, topicTerms, askedForms,
                     entityForms, ents);
    }
    return cands.slice().sort((a, b) => {
      if (a.score !== b.score) return b.score - a.score;
      const ab = a.book_id || "", bb = b.book_id || "";
      if (ab !== bb) return ab < bb ? -1 : 1;
      if (a.file_id !== b.file_id) return a.file_id - b.file_id;
      return a.row_no - b.row_no;
    });
  }

  NS.ranking = {
    WEIGHTS, Candidate, hasForm, scoreCandidate, rank,
    pyReprStr, pyReprList, pyRound2, RE_YEAR_COUNT,
  };
})(window.YindeEngine = window.YindeEngine || {});
