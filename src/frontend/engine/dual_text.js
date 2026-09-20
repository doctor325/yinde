/* 繁简双轨显示（浏览器版）—— search/dual_text.py 的逐行转写。
 *
 * 三条硬约束与 Python 侧完全相同（原文见 dual_text.py 文件头）：
 *   1. text_orig 永不被覆盖，简体放在另一个字段里；
 *   2. 确定性，不调 LLM；
 *   3. **长度一变就整段回退原文** —— 宁可显示繁体，也不显示一份被悄悄改过的史料。
 *
 * 长度按**码位**数：Python 的 len() 数码位，JS 的 .length 数 UTF-16 单元。
 * CJK 扩展 B 汉字在 JS 里占 2 个单元、Python 里算 1 个码位，用 .length 判长度
 * 会得出与 Python 相反的结论。故一律 [...s].length。
 *
 * 回退判据除了长度，还有「有没有孤立代理项」（与 Python 侧 _has_lone_surrogate
 * 一致）：那类字符是非法 Unicode，长度可能恰好不变，发出去只能显示成「�」。
 * 修掉 zh.js 的截断后这条不该再触发，留着是为了让两侧的契约逐字相同。
 */
"use strict";
(function (NS) {
  const MODES = ["orig", "simplified", "both"];
  const DEFAULT_MODE = "orig";

  function hasLoneSurrogate(s) {
    for (let i = 0; i < s.length; i++) {
      const cu = s.charCodeAt(i);
      if (cu >= 0xd800 && cu <= 0xdbff) {
        if (i + 1 >= s.length) return true;
        const lo = s.charCodeAt(i + 1);
        if (lo < 0xdc00 || lo > 0xdfff) return true;
        i++;                                  // 完整代理对，跳过低位
      } else if (cu >= 0xdc00 && cu <= 0xdfff) {
        return true;                          // 没有高位领着的低位代理
      }
    }
    return false;
  }

  function simplify(text) {
    const src = text || "";
    if (!src) return { text: src, ok: true, changed: 0 };
    const out = NS.zh.toSimplified(src);
    if ([...out].length !== [...src].length || hasLoneSurrogate(out)) {
      // 长度变了 = 逐字对齐不成立；含孤立代理项 = 非法 Unicode。
      // 两条都算「这次转换不可靠」→ 整段回退，显示原文。
      return { text: src, ok: false, changed: 0 };
    }
    // changed 是逐字比对出来的改动字数（Python 用 zip，同样按码位）。
    const a = [...src], b = [...out];
    let changed = 0;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) changed++;
    return { text: out, ok: true, changed };
  }

  function attach(block, mode, srcKey) {
    mode = mode || DEFAULT_MODE;
    srcKey = srcKey || "text";
    if (MODES.indexOf(mode) < 0) {
      throw new Error("未知的繁简显示方式：" + mode + "（可用：" + MODES.join("、") + "）");
    }
    const res = simplify(block[srcKey] || "");
    const out = Object.assign({}, block);      // 就地改副本，不动 srcKey 指向的原文
    out.text_mode = mode;
    out.text_simplified = res.text;
    out.simplified_ok = res.ok;
    out.simplified_chars = res.changed;
    return out;
  }

  NS.dualText = { MODES, DEFAULT_MODE, simplify, attach };
})(window.YindeEngine = window.YindeEngine || {});
