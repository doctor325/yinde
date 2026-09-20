"""具名标题模式注册表（第六点三阶段 6.3-C③ 的地基）。

**这里是标题形态正则的唯一注册处。** 语料目录（`corpus_catalog.json`）里的
`title_patterns` 只写**模式名**，不写正则：正则依赖 `config.PARA_CHAR`、CJK 字形类
这些代码常量，把正则搬进 JSON 会立刻多出第二份真源，改一处忘一处。

分工（计划书 §13「规则集中、不散落单书分支」）：

- 形态（正则）在本文件，属**代码**：写成一条模式，描述一种版式长什么样。
- 用不用、给谁用，在 `corpus_catalog.json`，属**数据**：加一本同版式的书＝加一条
  JSON 引用已有模式名，代码零改动；只有出现**没见过的版式**才在这里加模式。

`PATTERNS` 每项带 `method` / `confidence`，直接喂给 sections 的溯源字段
（`detection_method` / `confidence`，见 6.3-C③）：正则命中给 0.9，带条件的弱形态
（如 `wyg_ming_paren`）给 0.6，属性/人工给 1.0，首现兜底 0.5。

搬家的历史：这些正则原来内联在 `segmentation.py` 里（`WYG_SECTION_RE` 等），
6.3 原样搬到这里并保留原有注释，`segmentation` 改为 import —— 行为一字未变，
只是所有权集中了。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class TitlePattern:
    """一条具名模式：正则 + 它会产出什么 + 可信度。"""
    name: str
    regex: re.Pattern
    method: str            # title | structure  —— 产出 section 还是只作结构行
    confidence: float
    gate: str = ""         # "" | "main_layer" | 由书的 title_patterns 决定是否启用
    note: str = ""


# 汉字字形类。**不能用 `[一-鿿]`**：那是 U+4E00–U+9FFF，只覆盖基本区，而四庫本
# 的题名里夹着扩展区字形——`谷永杜鄴𫝊第五十五` 的 𫝊(U+2B74A)、`劉𤣥劉盆子列傳第一`
# 的 𤣥(U+2F9E5)。基本区写法一碰到就整条不匹配，实测前漢書 11 个文件、後漢書 9 个
# 文件因此一条 section 都没认出来（篇题没认出来 → 整卷正文没有篇名可归）。
CJK_CHAR = r"[㐀-䶿一-鿿豈-﫿𠀀-𿿿]"

# 长度上限也给到 20：`嚴朱吾丘主父徐嚴終王賈傳` 就 11 字，`{1,8}` 装不下。
# 正文行不会误中——判据要求「缩进 + 全是汉字 + 第N[上下]」占满整行，正文段落长得多。
WYG_SECTION_RE = re.compile(
    rf"^[　\s]{{1,8}}(?P<name>{CJK_CHAR}{{1,20}}第[一二三四五六七八九十百]+[上下]?)"
    rf"(?:[(（][^)）]*[)）])?{config.PARA_CHAR}?\s*$"
)

WYG_SIKU_RE = re.compile(rf"^欽定四庫全書[　\s]*[^　\s]*{config.PARA_CHAR}?\s*$")

# 「一个篇名 + 一个短括号组」占满整行的形态（《三國志》卷一 `武帝(操)¶`）。
#
# 与 WYG_SECTION_RE 的区别：没有 `第N`，篇名后面直接跟一个括号组。括号里是**单字
# 或极短**的名（操／備／權），不是注文——所以括号内容限死 ≤3 字，且不含 曰/按
# 这类注文起首字。放宽会立刻误吃 WYG 的行内注整行（`　秦(按此則懷王死…)¶`）。
#
# **当前没有书声明它**，但不要删：三國志实测下来「一行的名字 + 括号」多是与兄弟
# 傳名并列的卷目行（`呂布　張邈(陳登)　臧洪(陳容)¶`，见下），卷级粒度才是该书
# 真实的篇名粒度，所以它的 section 改用卷题（catalog 的 `juan_as_section`）。
# 留在这里是因为 audit_sections 的候选表会打印「这一行命中了哪些已注册模式」——
# 模式是**词汇表**，有它才能把 `武帝(操)` 这类行指认为「已有模式可认，只是没人
# 声明」，而不是当成从没见过的形态。
#
# 形态边界是实测出来的（三國志全书）：行首**没有缩进**（`武帝(操)¶` 行首就是
# 「武」），所以 `^[　\s]{1,8}` 这种「必有缩进」的假设在这里不成立——原式因此
# 全书 0 命中。只把缩进改成 {0,8} 会命中 99 行，其中约 90 行是正文（`徙天子都
# 長安焚燒洛陽宮室悉發掘陵墓取寶物` 之类）；把 name 收到 ≤6 字、括号收到 1–3 字
# 后全书 12 命中：8 条在 _000 的目録（toc 层，被层门挡掉）、4 条在正文
# （武帝(操)/文帝(丕)/明帝(叡)/劉放(孫資)）。
#
# **只给声明的书用**（catalog 的 books[].title_patterns），不设家族默认：前漢書/
# 後漢書里同样长相的行全部落在 `_000` 的 `JUAN 目錄`（toc 层，正文层 0 行），
# 拿它当家族默认会反过来污染目录层。
WYG_MING_PAREN_RE = re.compile(
    rf"^[　\s]{{0,8}}(?P<name>{CJK_CHAR}{{1,6}})[(（](?![曰按])[^)）]{{1,3}}[)）]"
    rf"{config.PARA_CHAR}?\s*$"
)

# SBCK 明文卷首题候选（確認度足够高的形态才给 ok；其余 pending_section）
GUOYU_SECTION_RE = re.compile(r"^(周|魯|齊|晉|鄭|楚|吳|越)語(上|中|下)?第[一二三四五六七八九十]+")
GUOYU_HEAD_RE = re.compile(r"^.{0,12}韋氏解")

# 戰國策（SBCK 鮑彪校注本）的明文标题形态——逐文件扫描 000–010 得出：
#
#   卷首题  `戰國䇿西周卷第一¶`         国名写在题内（卷第十写三个：宋衛中山）
#   国别题  `　　西周(漢志河南洛陽…)¶`   缩进两格，恰一个括号组，括号里是地理沿革注
#   章數行  `　　　　　凡六章¶`          一国策的结束标记（鮑彪本统计章数）
#   卷末题  `戰國䇿宋衛中山卷第十終¶`
#
# **不能只按「国名 + 括号」认国别题**：实测 11 条真国别题之外，还有 11 条正文行
# 长得一模一样（`齊(彪謂臏非武流也…)¶`、`　秦(按此則懷王死…)¶`——都是鲍彪注被
# 行内括号切出来的片段恰好停在行首）。区分靠语料自身的结构，不靠文本长相：
#   ① 国名必须是**本卷卷首题声明过**的国名。file 001 是總目 + 西周卷第一正文，
#      它的總目行 `　　東周(凡二十/二章)¶` 因此不会被误认（卷首题 `戰國䇿卷第一`
#      没写国名，声明集为空）。
#   ② 每卷声明**开一次门**：卷首先开，此后要等一行 `凡N章¶` 再开。实测 11 条真
#      国别题全部落在门口，11 条伪标题全部在门外（最近的分界标记都在百行以外）。
# 这两条都在 §13「规则集中、不散落单书分支」之下：形态与判据都写在这里，
# `_emit_content` 只调用。
ZHANGUOCE_STATES = "東周|西周|秦|齊|楚|趙|魏|韓|燕|宋|衛|中山"
ZHANGUOCE_JUAN_RE = re.compile(
    rf"^[　\s]*戰國.(?P<states>(?:{ZHANGUOCE_STATES})+){config.PARA_CHAR}?"
    rf"卷第(?P<no>[一二三四五六七八九十百]+)(?P<end>終)?{config.PARA_CHAR}?\s*$"
)
ZHANGUOCE_SECTION_RE = re.compile(
    rf"^[　\s]*(?P<state>{ZHANGUOCE_STATES})"
    rf"(?P<gloss>[(（][^)）]*[)）])?{config.PARA_CHAR}?\s*$"
)
ZHANGUOCE_CHAPTERS_RE = re.compile(
    rf"^[　\s]*凡[一二三四五六七八九十百]+章{config.PARA_CHAR}?\s*$"
)

# tls 系明文篇题（史記 `1.1《五帝本紀》`；无星标行时的兼容形态）
SHIJI_PLAIN_RE = re.compile(r"^(\d+\.\d+)[《〈](.+)$")


def build_wyg_juan_re(title: str, prefixes: list[str] | None = None) -> re.Pattern | None:
    """WYG 卷题正则：`　前漢書卷一上¶`。命中时 `group("juan")` 取卷次（`卷一上`）、
    `group("prefix")` 取命中的书名/志名前缀（`前漢書` / `魏志`）。

    前缀默认取文件头 TITLE 属性（不硬编码书名，§13）；`prefixes` 给形态不同的书用
    —— 三國志的分卷题写 `魏志卷一`/`蜀志卷一`/`吳志卷一`，书名前缀不出现在卷题里，
    只在 catalog 的 `juan_prefixes` 里声明。

    卷次**不靠「从行首切掉书名长度」**算：三國志的前缀不是书名（`魏志` ≠ `三國志`），
    那样切出来的是残字。改由正则自己的 `juan` 组给，前缀是谁都切得对。

    前缀单独成组是给调用方判「这个前缀是不是冗余的书名」的：前漢書的卷标签按既有
    惯例去掉书名记 `卷一上`（库内 115 条都是这个形状），而三國志的 `魏志` 不是书名、
    是卷次身份的一部分（魏/蜀/吳 三志各自从卷一数起），去掉就分不出来了。
    """
    names = [p for p in (prefixes or []) if p] or ([title] if title else [])
    if not names:
        return None
    alts = "|".join(_expand_variants(n) for n in names)
    return re.compile(
        rf"^[　\s]*(?P<prefix>{alts})(?P<juan>[卷巻][一二三四五六七八九十百]+[上下]?)"
        rf"{config.PARA_CHAR}?\s*$"
    )


# 同一处写法在语料里不统一，这是**语料自己的事实**，不是我们猜的形态：
#   卷(U+5377) / 巻(U+5DFB)：晉書 001 写 `晉書巻一`、033 写 `晉書卷三十三`，同一部
#                             书里混用；三國志 001 的卷题用 卷、卷末考證题用 巻。
#   晉(U+6649) / 晋(U+664B)：晉書 011 / 019 的考證题写 `晋書卷十一考證`。
# 只在**匹配用**的正则里放宽；原文一字不改（text_orig 永远原样）。产出的标签再经
# normalize_label 归一，免得同一卷因写法不同在 sections 表里裂成两条。
_VARIANT_CLASSES = {"卷": "卷巻", "晉": "晉晋"}


def _expand_variants(name: str) -> str:
    """书名/前缀逐个字符展开成变体字符类：`晉書` → `[晉晋]書`。"""
    return "".join(f"[{_VARIANT_CLASSES[c]}]" if c in _VARIANT_CLASSES else re.escape(c)
                   for c in name)


def normalize_label(label: str) -> str:
    """标签归一：`魏志巻九` → `魏志卷九`、`晋書卷十一` → `晉書卷十一`。

    只作用于**派生标签**（section / juan 的名字），text_orig 不动。不归一的代价
    是同一卷裂成两条 section：三國志每卷的卷末题与卷首题写法可能不同（卷/巻），
    而 section 是按「标签变了」建的。
    """
    for variant, std in (("巻", "卷"), ("晋", "晉")):
        label = label.replace(variant, std)
    return label


# 卷题的溯源档位。它不在 PATTERNS 里（正则要按书拼前缀，见 build_wyg_juan_re），
# 但可靠性与 WYG_SECTION_RE 同级——`X卷N` 占满整行，正文行不会长这样。
WYG_JUAN_CONFIDENCE = 0.9


PATTERNS: dict[str, TitlePattern] = {
    p.name: p for p in (
        TitlePattern("wyg_di_n", WYG_SECTION_RE, "title", 0.9, "main_layer",
                     "WYG 篇题 `　高帝紀第一上`（紀/志/表/列傳同形），可带行内注尾"),
        TitlePattern("wyg_ming_paren", WYG_MING_PAREN_RE, "title", 0.6, "main_layer",
                     "WYG `武帝(操)` 单名带注；当前无书声明（三國志实测以卷题为粒度）"),
        TitlePattern("wyg_siku", WYG_SIKU_RE, "structure", 1.0, "",
                     "`欽定四庫全書` 丛书题，结构行不是史料"),
        TitlePattern("guoyu_head", GUOYU_HEAD_RE, "title", 0.9, "",
                     "國語卷首题（`…韋氏解` 解题署）；卷末版心题无此署，不声明 section"),
        TitlePattern("guoyu_volume", GUOYU_SECTION_RE, "structure", 0.9, "",
                     "國語卷题 `周語上第一`；卷首/卷末同形，由 guoyu_head 分流"),
        TitlePattern("zhanguoce_juan", ZHANGUOCE_JUAN_RE, "title", 0.9, "",
                     "戰國策卷首题/卷末题 `戰國䇿西周卷第一[終]`"),
        TitlePattern("zhanguoce_state", ZHANGUOCE_SECTION_RE, "title", 0.9, "",
                     "戰國策国别题；须落「本卷声明过的国名 + 门开着」两重判据内"),
        TitlePattern("zhanguoce_chapters", ZHANGUOCE_CHAPTERS_RE, "structure", 1.0, "",
                     "戰國策章數行 `凡六章`，一国策结束标记（开门）"),
        TitlePattern("shiji_plain", SHIJI_PLAIN_RE, "title", 0.9, "",
                     "tls 明文篇题 `1.1《五帝本紀》`（无星标行时的兼容候选）"),
    )
}
