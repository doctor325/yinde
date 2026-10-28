"""Phase 7-E：检索词的字形变体扩展。

为什么需要这一层
----------------
检索前会把查询 `zh.to_traditional` 一次（`question.py`、`engine.py`、`result_block.py`
都这么做）。但**语料里简繁/异体是并存的**，实测（19 部 / 670,473 段）：

    302 个字存在「转换后即漏段」，这些字的原形共 86,692 段，
    其中 86,198 段（99.4%）在用户输入原形时被漏掉。
    例：「内」10,820 段 vs 「內」742 段，**两形零重叠**；
        输入「弑」只得 247 段（「弒」的），386 段全部漏掉。

**`zh.to_traditional` 本身没有错** —— 它做的正是简→繁，且被
`scripts/site/check_engine.py` 第 2 项以**逐字节相等**的强度对拍保护着。
错的层次是**调用方式**：把转换结果当「替换」（replacement），
而语料并存时正确语义是「扩展」（expansion）。

本模块只做一件事：给一个检索词，返回**它自己 + 它的另一种字形**（去重、保序）。
调用方把结果按 OR 处理即可。

边界（实测，别误用）
--------------------
本模块解决的是**字形层**漏召回。它**不**解决「多字整串」的漏召回：
question 轨在无实体时把整句当一个主题词做子串匹配，那是**切词层**的问题，
需要另一个改动（见 docs/phase7_benchmark_findings.md §9.3）。
"""
from __future__ import annotations

from search import dual_text
from search import zh


def term_variants(term: str) -> list[str]:
    """返回该词的等价字形（含原词，去重、保序）。

    两个方向都试：`to_traditional` 覆盖「用户打简体」，`dual_text.simplify`
    覆盖「用户打繁体而语料里也有简体形」。

    简体方向特意复用 `dual_text.simplify`：它带**长度不变**的可靠性判据，
    长度一变即整串回退原文（`dual_text.py:41-46`）。这样就不会因为一次
    不可靠的转换而把用户没写的词塞进检索式 —— 那等于替用户改问题。
    """
    t = term or ""
    if not t:
        return []
    out = [t]
    conv = zh.to_traditional(t)
    if conv and conv not in out:
        out.append(conv)
    simp = dual_text.simplify(t)["text"]
    if simp and simp not in out:
        out.append(simp)
    return out


def term_variants_many(terms: list[str]) -> list[str]:
    """一组词的变体并集（去重、保序）。顺序 = 输入顺序，每个词后跟它的变体。"""
    out: list[str] = []
    for t in terms:
        for v in term_variants(t):
            if v not in out:
                out.append(v)
    return out


def query_tokens(raw: str) -> list[str]:
    """把用户原话切成检索用的 token（按空白）。"""
    return [w for w in (raw or "").split() if w]


def keyword_groups(raw: str) -> list[list[str]]:
    """Keep original spelling and phrase boundaries: OR within a token, AND across tokens."""
    return [term_variants(t) for t in query_tokens(raw)]


def query_variants(raw: str) -> list[str]:
    """**检索式字形变体的唯一来源。**

    为什么要有这个函数（Phase 7 审查 F1）：此前 block 轨在 `_fetch_hits` 里
    直接调 `term_variants`，question 轨在 `query_expansion.expand` 里另写了一套
    （按空白切 + 只做 `to_traditional` 方向）。两套规则不同源，会让
    「普通搜索能搜到、问答搜索搜不到」这种分叉悄悄出现。
    现在两条轨都走这里，变体语义只有一份定义。

    **按 token 切，不并入整串**：整串变体对"语料里真有一模一样的一整句"
    才有意义，属未经验证的额外行为，已移除（单 token 查询不受影响）。
    """
    return term_variants_many(query_tokens(raw))
