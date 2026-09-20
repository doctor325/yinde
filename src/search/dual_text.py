"""第四阶段：繁简双轨显示。

语料正文是繁体（`text_orig`），用户大多按简体阅读。本模块把繁体**派生**出一份
简体文本，供前端在「只看繁体 / 只看简体 / 繁简对照」之间切换。

三条硬约束
----------
1. **`text_orig` 永不被覆盖**（§2.2）。派生结果放在**另一个字段**里，原文
   一字不动地留在 `text` / `text_orig`。数据库也不写：简体是请求时算的，
   不是入库的。
2. **确定性，不调 LLM**（§2.3）。转换只用系统字符映射（`search.zh`），
   同一个字永远得到同一个结果，可复现、可审计。
3. **转换不可靠就保留原字符**。判据是「长度必须不变」：繁简映射是逐字映射，
   长度一变就说明这次转换把字增删了（合字/词形替换等），此时**整段回退原文**，
   宁可显示繁体，也不显示一份被系统悄悄改过的「简体版」史料。

除长度外不再做别的「校正」：`於` 不在 zh-CN 的繁简映射表里，`游於雍林`
转出来仍是 `游於雍林`（不是 `游于雍林`）—— 这是**如实**，不是缺陷。
系统没给的对应关系，我们不去猜（§八.4）。
"""
from __future__ import annotations

from search import zh

# 前端的三种显示方式。默认只看繁体：原文是史料的本来面目，简体只是阅读辅助。
MODES = ("orig", "simplified", "both")
DEFAULT_MODE = "orig"


def simplify(text: str) -> dict:
    """繁体 → 简体，返回 {text, ok, changed}。

    `ok=False` 表示这次转换不可靠（长度变了），调用方应当继续用原文。
    `changed` 是**逐字比对**出来的改动字数，供前端提示「本站显示的简体为
    程序转换所得」，让用户知道哪些字被动过。
    """
    src = text or ""
    if not src:
        return {"text": src, "ok": True, "changed": 0}
    out = zh.to_simplified(src)
    if len(out) != len(src) or _has_lone_surrogate(out):
        # 长度变了 = 逐字对齐不成立，无法保证「只换了字形」→ 整段回退。
        # 孤立代理项（U+D800–DFFF 落单）同理：那是**非法 Unicode**，长度可能
        # 恰好不变（系统映射把非 BMP 字截成一个码元时就是如此），但发出去
        # 浏览器只能显示「�」。两条都算「这次转换不可靠」。
        return {"text": src, "ok": False, "changed": 0}
    changed = sum(1 for a, b in zip(src, out) if a != b)
    return {"text": out, "ok": True, "changed": changed}


def _has_lone_surrogate(s: str) -> bool:
    return any(0xD800 <= ord(c) <= 0xDFFF for c in s)


def attach(block: dict, mode: str = DEFAULT_MODE, src_key: str = "text") -> dict:
    """给一个片段加繁简字段。**就地改副本，不动 src_key 指向的原文。**

    加出来的字段（都只在请求结果里，不落库）：
        text_simplified  派生的简体正文；转换不可靠时等于原文
        simplified_ok    转换是否可靠（false 时前端应回退到繁体）
        simplified_chars 被改动的字数（0 表示这段本来就是简体/无对应字）
        text_mode        这次请求希望怎么显示（原样回传，前端据此渲染）
    """
    if mode not in MODES:
        raise ValueError(f"未知的繁简显示方式：{mode}（可用：{'、'.join(MODES)}）")
    src = block.get(src_key) or ""
    res = simplify(src)
    block = dict(block)
    block["text_mode"] = mode
    block["text_simplified"] = res["text"]
    block["simplified_ok"] = res["ok"]
    block["simplified_chars"] = res["changed"]
    return block
