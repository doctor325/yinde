"""生成繁简字符表（frontend/engine/zh_table.js）。

## 为什么需要
`search/zh.py` 靠 Windows 的 `kernel32.LCMapStringEx` 做繁简转换，浏览器里没有这个
API。静态站点要在 JS 里做同样的事，就得把「字符 → 字符」的对应关系预先算出来。

## 为什么是全字符集而不是只扫语料
用户**输入**是简体、不可预知，语料**正文**是繁体。只扫语料用字会让用户打进来的
生僻简体字没有对应关系，检索静默少命中。因此这里枚举整个 CJK 区段逐字问系统，
只保留「转换结果与输入不同」的条目——真正有繁简差异的字只有几千个，表很小。

## 逐字查表 vs 整串转换：已穷举证明等价
`dual_text.simplify()` 的契约是**长度必须不变**，变了就整段回退原文
（`search/dual_text.py`）。因此只有「长度不变」的那部分转换结果是会被用到的。
对语料全部单字 6,932 / 二字组 272,455 / 三字组 696,378 实测：

    整串转换 != 逐字转换 的实例全部满足「长度发生了变化」，
    「长度不变却与逐字不同」的实例恒为 0。

也就是说，在 dual_text 真正会采纳的范围内，逐字查表与 Windows API **完全等价**。
本脚本每次运行都重跑这个断言（见 verify_over_corpus），不成立就报错退出。

## 长度按「码位」数而不是 UTF-16 单元
Python 的 `len()` 数码位，JS 的 `.length` 数 UTF-16 单元。CJK 扩展 B 汉字在 JS 里
占 2 个单元、Python 里算 1 个码位。若 JS 侧按 `.length` 判长度，遇到扩展 B 就会
得出与 Python 相反的「长度变了」结论。所以 `dual_text.js` 判长度时数码位
（`[...s].length`）。

## 星形汉字：曾经的「残缺代理码位」已随根因修复而消失
第六点二阶段之前，`search/zh.py` 调用 LCMapStringEx 时传的是 Python 的 `len()`
（码位数），而该 API 数的是 **UTF-16 单元**，于是含扩展 B 汉字（𫝊/𤣥）的串会被
截断：尾巴少掉若干单元，单字时只剩一个**孤立代理项**（非法 Unicode）。当时本脚本
把这当作「Python 的既有行为」照抄进表里。根因修好后（改传 UTF-16 单元数），
系统对扩展 B 汉字原样放行，本表的「语料用字(区段外)」一组因此收不到任何条目
（旧表里那 51 个星形字条目连同残缺代理码位一并消失）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pipeline import config  # noqa: E402
from search import zh  # noqa: E402

OUT_PATH = config.HISTORY_AI_DIR / "frontend" / "engine" / "zh_table.js"

# 逐字枚举的区段：只收 BMP 内的汉字。
#
# 为什么不整段枚举星形平面（扩展 B 及以上）：整段收下来等于往表里塞 7 万条条目，
# 表从 150KB 涨到 3.7MB——比演示数据还大，而用户根本不会在搜索框里输入扩展 B
# 汉字。星形字符改由「语料用字」那一遍补：语料里真实出现的星形字符只有 51 个
# （扩展 B 46 + 扩展 E 2 + 私有使用区 3）。修好根因后这些字「转换结果 == 输入」
# （系统原样放行），于是连一条都不会进表——查不到就原样返回，正是期望的行为。
#
# 这个收法必须**同时**满足两个条件才成立，缺一不可：
#   ① BMP 区段收全 —— 覆盖用户输入的生僻简体字；
#   ② 语料用字一个不漏 —— 覆盖正文里的一切，包括不在任何标准区段的私有使用区。
# 实测漏掉扩展 E 区时，语料里有 126 个样本对不上；脚本的自检会当场抓住。
CJK_RANGES = (
    (0x3400, 0x4DBF, "扩展A"),
    (0x4E00, 0x9FFF, "基本区"),
    (0xF900, 0xFAFF, "兼容汉字"),
)


def js_string(s: str) -> str:
    """把 Python str 写成 JS 字符串字面量（全部转义，含代理对与残码位）。

    一律转义而不用字面量：扩展 B 与系统返回的残缺代理码位不能原样落进源文件，
    否则文件可能不是合法 UTF-8/JS。
    """
    out = []
    for ch in s:
        cp = ord(ch)
        if cp > 0xFFFF:                     # 星形平面 → 代理对
            cp -= 0x10000
            out.append("\\u%04X\\u%04X" % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)))
        else:
            out.append("\\u%04X" % cp)
    return '"' + "".join(out) + '"'


def build_table(texts: list[str]) -> tuple[dict[str, str], dict[str, str],
                                           list[tuple[str, int, int]]]:
    """枚举候选字符逐字转换，返回 (简→繁, 繁→简, 每段统计)。

    候选 = 标准 CJK 区段 ∪ 语料实际用字。后者不能省：语料里有私有使用区等
    不在任何标准区段内的字符，只有从语料里才发现得了（实测漏掉就会有 126 个
    样本对不上）。区段保证覆盖用户输入，语料用字保证覆盖正文。
    """
    groups: list[tuple[str, list[int]]] = [
        (label, list(range(lo, hi + 1))) for lo, hi, label in CJK_RANGES]
    covered = {cp for _, cps in groups for cp in cps}
    extra = sorted({ord(c) for t in texts for c in t} - covered)
    groups.append(("语料用字(区段外)", extra))

    s2t: dict[str, str] = {}
    t2s: dict[str, str] = {}
    stats: list[tuple[str, int, int]] = []
    for label, cps in groups:
        n_s2t = n_t2s = 0
        for cp in cps:
            ch = chr(cp)
            t = zh.to_traditional(ch)
            if t != ch:
                s2t[ch] = t
                n_s2t += 1
            s = zh.to_simplified(ch)
            if s != ch:
                t2s[ch] = s
                n_t2s += 1
        stats.append((label, n_s2t, n_t2s))
    return s2t, t2s, stats


def corpus_texts() -> list[str]:
    """语料正文（kind='passage' 的 text_orig），只读。"""
    import sqlite3

    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT text_orig FROM passages WHERE kind = 'passage'") if r[0]]
    finally:
        conn.close()


def verify_over_corpus(s2t: dict[str, str], t2s: dict[str, str],
                       texts: list[str]) -> tuple[int, int]:
    """穷举断言：长度不变却与逐字不同的实例必须为 0。

    逐字转换严格照 JS 侧的做法实现（查表，查不到就用原字），这样证明的是
    **将要运行的那份逻辑**，而不是另一套等价物。
    """
    uni: set[str] = set()
    bi: set[str] = set()
    tri: set[str] = set()
    for t in texts:
        uni.update(t)
        for i in range(len(t) - 1):
            bi.add(t[i:i + 2])
        for i in range(len(t) - 2):
            tri.add(t[i:i + 3])

    def perchar(s: str, table: dict[str, str]) -> str:
        return "".join(table.get(ch, ch) for ch in s)

    total = 0
    for label, pairs in (("单字", uni), ("二字组", bi), ("三字组", tri)):
        for direction, table, fn in (("简→繁", s2t, zh.to_traditional),
                                     ("繁→简", t2s, zh.to_simplified)):
            invariant_diff = []
            for s in pairs:
                whole = fn(s)
                if len(whole) == len(s) and whole != perchar(s, table):
                    invariant_diff.append((s, whole, perchar(s, table)))
            status = "OK" if not invariant_diff else "**失败**"
            print(f"  [{label}] {direction}: 长度不变却不同 {len(invariant_diff)} 例  {status}")
            for x in invariant_diff[:5]:
                print(f"        反例: {x!r}")
            total += len(invariant_diff)
    return total, len(uni) + len(bi) + len(tri)


def render_js(s2t: dict[str, str], t2s: dict[str, str], stats: list) -> str:
    def table(name: str, d: dict[str, str]) -> str:
        items = ",\n".join(f"{js_string(k)}:{js_string(v)}"
                           for k, v in sorted(d.items()))
        return f"  {name}: {{\n{items}\n  }}"

    stat_lines = "\n".join(
        f" *   {label:<6} 简→繁 {a:>5} 字 · 繁→简 {b:>5} 字" for label, a, b in stats)
    return f"""/* 繁简字符表 —— 由 scripts/site/gen_zh_table.py 生成，请勿手改。
 *
 * 来源：Windows kernel32.LCMapStringEx（zh-CN），与 search/zh.py 同一个系统 API。
 * 只收录「转换结果与输入不同」的字，因此表很小；查不到 = 该字繁简同形。
 *
 * 区段统计：
{stat_lines}
 *   简→繁 {len(s2t)} 字 · 繁→简 {len(t2s)} 字
 *
 * 注意：本表与 `search/zh.py` 必须逐字给出同样的答案（check_engine 会逐条对拍）。
 * 星形汉字（扩展 B 等）不在此表内：系统对它们原样放行，查不到即原样返回。
 * 判长度请数码位（[...s].length），不要用 .length。
 */
"use strict";
window.YindeEngine = window.YindeEngine || {{}};
window.YindeEngine.zhTable = {{
{table("s2t", s2t)},
{table("t2s", t2s)}
}};
"""


def main() -> int:
    print("读取语料正文（只读）…")
    texts = corpus_texts()
    print(f"  {len(texts):,} 条记录")

    print("\n生成字符表（枚举 CJK 区段 + 语料用字，逐字问系统）…")
    s2t, t2s, stats = build_table(texts)
    for label, a, b in stats:
        print(f"  {label:<16} 简→繁 {a:>5} · 繁→简 {b:>5}")
    print(f"  合计 简→繁 {len(s2t)} · 繁→简 {len(t2s)}")

    print("\n穷举回归自检（语料全部单字/二字组/三字组）…")
    bad, n_pairs = verify_over_corpus(s2t, t2s, texts)
    if bad:
        print(f"\n失败：{bad} 例「长度不变却与逐字不同」——逐字查表不成立，"
              f"需要改为词组例外表。未写出 {OUT_PATH.name}。")
        return 1
    print(f"  通过：{n_pairs:,} 个样本，0 例长度不变却不同 → 逐字查表充分")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render_js(s2t, t2s, stats), encoding="utf-8")
    print(f"\n已写出 {OUT_PATH}（{OUT_PATH.stat().st_size:,} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
