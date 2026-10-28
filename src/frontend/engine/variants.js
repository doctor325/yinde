/* Phase 7-E：检索词的字形变体扩展 —— `search/variants.py` 的 JS 镜像。
 *
 * 为什么需要这一层
 * ----------------
 * 检索前会把查询 toTraditional 一次。但**语料里简繁/异体是并存的**，实测
 * （19 部 / 670,473 段）：302 个字存在「转换后即漏段」，这些字的原形共 86,692 段，
 * 其中 86,198 段（99.4%）在用户输入原形时被漏掉。
 * 例：「内」10,820 段 vs 「內」742 段，**两形零重叠**。
 *
 * **toTraditional 本身没有错** —— 它做的正是简→繁，且被 check_engine.py 第 2 项
 * 以**逐字节相等**的强度对拍保护着。错的层次是**调用方式**：把转换结果当
 * 「替换」（replacement），而语料并存时正确语义是「扩展」（expansion）。
 *
 * 本模块只做一件事：给一个检索词，返回它自己 + 它的另一种字形（去重、保序）。
 *
 * 与 Python 同源（Phase 7 审查 F1）
 * ---------------------------------
 * `queryVariants` 是**检索式字形变体的唯一来源**，block 轨与 question 轨共用。
 * 整串变体（whole-string）已移除：它对「语料里真有一模一样的一整句」才有意义，
 * 属未经验证的额外行为；单 token 查询不受影响。
 */
(function (NS) {
  "use strict";

  /** 一个词的等价字形（含原词，去重、保序）。
   *
   *  两个方向都试：`toTraditional` 覆盖「用户打简体」，`toSimplified` 覆盖
   *  「用户打繁体而语料里也有简体形」。与 Python 的 `term_variants` 逐行对应。
   */
  function termVariants(term) {
    const t = term || "";
    if (!t) return [];
    const out = [t];
    const conv = NS.zh.toTraditional(t);
    if (conv && conv !== t && out.indexOf(conv) < 0) out.push(conv);
    const simp = NS.dualText.simplify(t).text;
    if (simp && simp !== t && out.indexOf(simp) < 0) out.push(simp);
    return out;
  }

  /** 一组词的变体并集（去重、保序）。顺序 = 输入顺序，每个词后跟它的变体。 */
  function termVariantsMany(terms) {
    const out = [];
    for (const t of terms) {
      for (const v of termVariants(t)) {
        if (out.indexOf(v) < 0) out.push(v);
      }
    }
    return out;
  }

  /** 把用户原话切成检索用的 token（按空白）。 */
  function queryTokens(raw) {
    // Python str.split whitespace; FEFF is deliberately not a separator.
    return (raw || "").split(/[\t\n\v\f\r \u001c-\u001f\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/)
      .filter((w) => w.length > 0);
  }

  function keywordGroups(raw) { return queryTokens(raw).map(termVariants); }

  function scanGroups(corpus, groups, filter) {
    let acc = null;
    for (const group of groups) {
      const hits = corpus.likeScan(group, Object.assign({}, filter, { any: true })).hits;
      const next = new Set(hits);
      acc = acc === null ? next : new Set([...acc].filter((i) => next.has(i)));
    }
    // Preserve corpus order for LIKE pagination and tie-breaking.
    return acc === null ? [] : corpus.passageIdx.filter((i) => acc.has(i));
  }

  /** 检索式字形变体的**唯一来源** —— 与 `variants.query_variants` 对应。 */
  function queryVariants(raw) {
    return termVariantsMany(queryTokens(raw));
  }

  NS.variants = { termVariants, termVariantsMany, queryTokens, queryVariants,
    keywordGroups, scanGroups };
})(window.YindeEngine = window.YindeEngine || {});
