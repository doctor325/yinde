/* 简体 ↔ 繁体（浏览器版）—— search/zh.py 的并行实现。
 *
 * Python 那边调 Windows 的 kernel32.LCMapStringEx 做整串转换，浏览器没有这个 API，
 * 因此改用预生成的字符表（zh_table.js）。这不是「退而求其次」：字符表对 dual_text
 * 真正会采纳的范围（长度不变）与整串转换**完全等价**，脚本 gen_zh_table.py 每次
 * 生成表时都重跑穷举断言（语料全部单字 / 二字组 / 三字组，见该文件头注释）。
 *
 * 「只处理前 n 个 UTF-16 单元」那段历史（第六点二阶段修掉）
 * --------------------------------------------------------
 * 本文件原先刻意复现过 Python 侧的一个 bug：`n = len(text)` 传的是**码位**数，
 * 而 LCMapStringEx 的 cchSrc 数的是 **UTF-16 单元**，于是含星形汉字（扩展 B 的
 * 𫝊/𤣥）的串会被截断——尾巴少掉若干个单元，单字时甚至只剩一个孤立代理项。
 * 当时的读法是「Python 既有行为，JS 照抄才一致」，并加了一条实测：203,308 条
 * 正文里 1,176 条含星形字，1,172 条因长度变了而回退、**4 条不回退**。
 *
 * 那 4 条就是问题：它们采纳了一个含孤立代理项（非法 Unicode）的「简体」。
 * 第六点二阶段修了根因（search/zh.py 改传 UTF-16 单元数），JS 侧随之删掉截断，
 * 星形字一律原样放行。现在两侧都不再有残码位，长度判据也不再被这件事左右。
 * 查不到就原样返回该字（= 该字繁简同形），这条没变。
 */
"use strict";
(function (NS) {
  if (!NS.zhTable) throw new Error("zh.js 需要先加载 engine/zh_table.js");
  // 转成无原型的字典：查表时不必担心 "constructor" 之类的键名撞上 Object.prototype。
  const S2T = Object.assign(Object.create(null), NS.zhTable.s2t);
  const T2S = Object.assign(Object.create(null), NS.zhTable.t2s);

  const isHigh = (u) => u >= 0xd800 && u <= 0xdbff;
  const isLow = (u) => u >= 0xdc00 && u <= 0xdfff;

  function convertWith(table, s) {
    if (!s) return s;
    // 整串处理，不再按「前 n 个 UTF-16 单元」截断（见文件头那段历史）。
    let out = "";
    for (let i = 0; i < s.length; ) {
      const cu = s.charCodeAt(i);
      if (isHigh(cu) && i + 1 < s.length && isLow(s.charCodeAt(i + 1))) {
        out += s.substr(i, 2);       // 完整代理对 = 一个星形汉字：系统原样放行
        i += 2;
      } else {
        const ch = s[i];             // 孤立代理项也走这里：表里没有它 → 原样放行
        const v = table[ch];
        out += v === undefined ? ch : v;
        i += 1;
      }
    }
    return out;
  }

  NS.zh = {
    // 与 search/zh.py 同名同义；失败时 Python 恒等回退，这里查不到即原字，一致。
    toTraditional: (s) => convertWith(S2T, s),
    toSimplified: (s) => convertWith(T2S, s),
    // 供自检使用：表里有多少条
    size: () => ({ s2t: Object.keys(S2T).length, t2s: Object.keys(T2S).length }),
  };
})(window.YindeEngine = window.YindeEngine || {});
