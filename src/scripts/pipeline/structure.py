"""集中解析规则：行分类、分层默认、pb 解析、括号注切分、normalized 生成。

设计约束（第一阶段原则）：
- 能可靠确认的才给确定值；不能确认的一律 unknown / pending_*。
- 原始文本永远原样保留（text_orig 含 <pb:...>、¶、&KR...;）。
- 所有“猜测”性质的判定只产生候选（commentary_candidate / pending_section），
  由人工在前端确认，绝不静默覆盖。
"""
from __future__ import annotations

import re
import sys

from . import catalog, config
from .records import Record
from .title_patterns import PATTERNS

# ----------------------------------------------------------------- pb

PB_FULL_RE = re.compile(r"<pb:[^>]*>")


def parse_pb(marker: str) -> dict | None:
    """<pb:KR2e0001_SBCK_000-1b> -> {raw, book_id, edition, block, page, side}"""
    m = config.PB_SPLIT_RE.match(marker)
    if not m:
        return {"raw": marker}
    return {
        "raw": marker,
        "book_id": m.group(1),
        "edition": m.group(2),
        "block": m.group(3),
        "page": m.group(4),
        "side": m.group(5),
    }


def find_pb(text: str) -> list[tuple[str, int]]:
    """返回 (原始标记串, 起始偏移) 列表。"""
    return [(m.group(0), m.start()) for m in PB_FULL_RE.finditer(text)]


def strip_pb(text: str) -> str:
    return PB_FULL_RE.sub("", text)


# ----------------------------------------------------------------- 头注释/出处

SRC_RE = re.compile(r"^#\s*src:\s*(.*)$")
COMMENT_KEY_RE = re.compile(r"^#\s*([a-zA-Z_]+):\s*(.*)$")


def parse_src_line(line: str) -> dict | None:
    m = SRC_RE.match(line)
    if not m:
        return None
    rest = m.group(1).strip()
    prefix = rest.split()[0] if rest.split() else ""
    return {"raw": line, "src_text": rest, "prefix": prefix, "section_ref": rest[len(prefix):].lstrip(", ").strip() or None}


def classify_comment_line(line: str) -> tuple[str, dict | None]:
    """返回 ('src'|'other', 解析结果)。"""
    if SRC_RE.match(line):
        return "src", parse_src_line(line)
    return "other", None


# ------------------------------------------------------------ 文件层默认

# 文件内分段（部分）段名 → 语义层。
#
# 段名有两个来源，都取自 kanripo 自己的属性行（不是我们的猜测）：
#   `#+PROPERTY: FILE SB02n0044-000戰國策校注-序.` → 取最后一段 `序`（SBCK 系）
#   `#+PROPERTY: JUAN 卷一上考證`                  → 整值（WYG 系）
#
# WYG 的一卷正文之后紧接同一卷的「考證」（校勘記），所以一个文件里 JUAN 会切换
# 四次（卷一上/卷一上考證/卷一下/卷一下考證）；`卷一上` 这种认不出层的值必须
# **把层还原成文件默认**，否则考證段之后的正文会一路带着 appendix 走到底。
#
# 判定按关键词包含、不按全等（WYG 的段名带卷次），顺序从具体到笼统：
# `敘例考證` 必须先中 考證（appendix）而不是 敘例（preface）。
PART_LAYER_RULES = (
    ("考證", "appendix"), ("考証", "appendix"), ("箚子", "appendix"),
    ("敘例", "preface"), ("叙例", "preface"), ("凡例", "preface"),
    ("御製", "preface"), ("御制", "preface"), ("提要", "preface"),
    ("自序", "preface"),
    ("目録", "toc"), ("目录", "toc"), ("目錄", "toc"),
    ("後跋", "backmatter"), ("跋", "backmatter"),
    ("序", "preface"), ("叙", "preface"), ("敘", "preface"),
)


def part_layer_of(label: str | None) -> str | None:
    """段名 → 语义层；认不出返回 None（调用方还原成文件层默认）。"""
    if not label:
        return None
    t = label.strip().strip("[]")
    for key, layer in PART_LAYER_RULES:
        if key in t:
            return layer
    return None


# 家族层默认里「层名由函数算」的解析器：JSON 按名字引用（catalog.LAYER_RESOLVERS
# 是同一份名字清单，供目录校验用），实现留在本模块——part_layer_of 在这里。
LAYER_RESOLVERS = {"part_layer_of": part_layer_of}

_warned_no_catalog = False


def _warn_no_catalog() -> None:
    """catalog 不可用时只吼一次：接着跑的是 6.2 的冻结规则，不是 JSON 里那套。"""
    global _warned_no_catalog
    if not _warned_no_catalog:
        _warned_no_catalog = True
        print("** corpus_catalog.json 不可用（缺失或校验不过）：层默认回落到 6.2 "
              "内置规则，目录里的文件特例/家族规则**没有生效**。",
              file=sys.stderr)


def _apply_layer_rules(rules, file_no: int | None, juan: str) -> tuple[str, str, str] | None:
    """按**声明顺序**取第一条命中的家族规则 → (layer, status, note)；都不中 → None。"""
    for r in rules:
        when = r.get("when", "always")
        layer = r.get("layer")
        if when == "juan_part":
            resolver = LAYER_RESOLVERS.get(r.get("resolver") or "")
            layer = resolver(juan) if resolver else None
            if not layer:
                continue
        elif when == "file_zero":
            if file_no != 0:
                continue
        elif when != "always":
            continue
        return (layer, r.get("status") or "ok", (r.get("note") or "").format(JUAN=juan or ""))
    return None


def file_layer_defaults(book_dir: str, file_no: int | None, family: str | None,
                        metadata: dict) -> tuple[str, str, str]:
    """按文件级证据给出默认 layer/status，返回 (layer, status, note)。

    证据优先级：
    1. 该书的文件级特例：catalog `books[<id>].file_overrides`（人工确认过的，
       如 尚書 _059 逸篇附集；键是文件号的字符串）
    2. 家族层默认规则：catalog `families[<family>].layer_defaults`，按声明顺序
       —— WYG 族：首段名（`#+PROPERTY: JUAN`）自己就说明了这一文件是什么：
       御製詩/提要/自序 → preface，考證跋語/箚子 → appendix，`卷N` → 正文。
       实测 227 个文件全部据此判对，无需逐本登记（§13）。
       —— SBCK 族 _000 → preface/pending_section。
    3. catalog 不可用（文件缺失/校验不过）→ 6.2 内置规则，并在 stderr 吼一声。
       家族未知（family=None）也走这里 → unknown/pending_section，不猜。

    规则搬进 JSON 的收益：加一本新书要改的层默认，**只动 corpus_catalog.json**。
    签名与返回值形状不变（test_overrides / test_sbck_fallback / test_main 照旧）。
    """
    juan = (metadata or {}).get("JUAN") or ""
    cat = catalog.try_load()
    if cat is not None:
        ov = cat.file_override(book_dir, file_no)
        if ov:
            return (ov["layer"], ov["status"],
                    ov.get("note") or f"{book_dir} 文件 {file_no} 人工特例")
        hit = _apply_layer_rules(cat.layer_defaults(family), file_no, juan)
        if hit:
            return hit
    else:
        _warn_no_catalog()
    return _builtin_layer_defaults(book_dir, file_no, family, juan)


def _builtin_layer_defaults(book_dir: str, file_no: int | None, family: str | None,
                            juan: str) -> tuple[str, str, str]:
    """**6.2 的冻结快照**，只在 catalog 不可用（或家族未知）时兜底。

    刻意不与 corpus_catalog.json 同步：它是「回滚到 6.2」时该有的样子，不是第二
    份可维护的真源。改规则请改 JSON，别改这里。
    """
    overrides = {
        # 尚書 _059 为逸篇附录（Readme 目次 59.x 段，人工复核过）
        ("shangshu", 59): ("appendix", "ok", "尚書逸篇附集(Readme 目次 59.x)"),
        # 國語/戰國策 _000 均为序文件（正文首行有书名序题与署名字样）
        ("guoyu", 0): ("preface", "ok", "《國語解敘》韋昭序"),
        ("zhanguoce", 0): ("preface", "ok", "劉向《戰國策序》及奏言"),
    }
    key = (book_dir, file_no)
    if key in overrides:
        return overrides[key]
    if family == "wyg":
        layer = part_layer_of(juan)
        if layer:
            return (layer, "ok", f"WYG 首段名「{juan}」")
        return ("main", "ok", "WYG 卷正文")   # `卷一上` / `卷二` 之类：正常卷正文
    if family == "sbck" and file_no == 0:
        return ("preface", "pending_section", "SBCK 首文件疑为序（未在例外表确认）")
    if family in ("tls", "sbck"):
        return ("main", "ok", "正文")
    return ("unknown", "pending_section", "家族未知")


# ------------------------------------------------------------ 行分类

# SBCK 括号注/括注：半角 ( 与全角 （ 都保留（原样不入 normalized），
# 用 PENDING 拆分标记，见 split_parenthetical()
PAREN_OPEN_RE = re.compile(r"[(（]")
PAREN_CLOSE_RE = re.compile(r"[)）]")


def _top_level_parens(text: str) -> list[tuple[int, int, str, str]]:
    """返回行内顶层括号段 [(start, end, open_char, close_char)]；括号不配对/无内容不拆。"""
    pairs = []
    stack: list[tuple[int, str]] = []
    for i, ch in enumerate(text):
        if ch in "(":
            stack.append((i, ch))
        elif ch in ")":
            if not stack:
                continue  # 孤立右括号：保留原文不处理
            s, o = stack.pop()
            if (o == "(") != (ch == ")"):
                # 全角半角混配（异常），标记但按半角优先；不深度处理
                pairs.append((s, i, o, ch))
            else:
                pairs.append((s, i, o, ch))
    return pairs


def pending_split_parens(text: str) -> list[tuple[str, int, int, str]]:
    """将含顶层括号的一行切成 [(片段, start, end, 标记), ...]。

    标记: "main" | "paren"
    规则：括号内容整体作为 commentary_candidate 片段，绝不猜测注者。
    不配对括号 → 整行返回 main（宁可 pending_line 也不猜）。
    """
    pairs = _top_level_parens(text)
    if not pairs:
        return [(text, 0, len(text), "main")]
    # 简单括号嵌套：用配对算法确保闭合顺序（这里仅需顶层）
    parts = []
    cursor = 0
    for (s, e, _o, _c) in sorted(pairs):
        # 只接受“外层”段：若前一段未闭合（出现交叠）则保守跳过
        if s < cursor:
            return [(text, 0, len(text), "main")]
        if s > cursor:
            parts.append((text[cursor:s], cursor, s, "main"))
        parts.append((text[s:e + 1], s, e + 1, "paren"))
        cursor = e + 1
    if cursor < len(text):
        parts.append((text[cursor:], cursor, len(text), "main"))
    return parts


# ------------------------------------------------------------ 标题/结构判定

# 左传：A=經 B=傳 前缀条目/卷题（编号格式不一：B1、A1.1、A1.1.1 —— 全部容错）
ZZ_AB_HEAD = re.compile(r"^([AB])[《〈].*$")                                  # B《傳》
# 卷题正则只吃编号 + 《 头，标题文字留在 m.end() 之后（否则 .*$ 会吞掉全行标题）
ZZ_AB_CODE_HEAD = re.compile(r"^([AB])(\d+(?:\.\d+)*)(?=[《〈])")            # A1.1《隱公元年經》
ZZ_AB_ITEM = re.compile(r"^([AB])(\d[\d.]*?)([^0-9.].*)$")                   # A1.1.1元年春王正月。 / B1惠公元妃...
ZZ_PLAIN_YEAR = re.compile(r"^(\d+)\.(\d+)(.*)$")


def zuozhuan_classify(line: str) -> tuple[str, dict]:
    """(kind, {ab, code, text}) | (None, {}) 表示不属于 A/B 结构。"""
    m = ZZ_AB_HEAD.match(line)
    if m:
        return "heading", {"ab": m.group(1), "code": None}
    m = ZZ_AB_CODE_HEAD.match(line)
    if m:
        return "heading", {"ab": m.group(1), "code": m.group(2), "title": line[m.end():]}
    m = ZZ_AB_ITEM.match(line)
    if m:
        return "passage", {"ab": m.group(1), "code": m.group(2), "text": m.group(3)}
    return "none", {}


def is_org_heading(line: str) -> tuple[str, str | None, str | None]:
    """org 标题 -> (级别, code, 标题文字)。

    例: '** 1 紀' -> ('h2','1','紀')
        '** 1 《堯典》' -> ('h2','1','堯典')
        '*** 2.1　《三代世表》' -> ('h3','2.1','三代世表')
    """
    m = config.ORG_H3_RE.match(line)
    if m:
        title = clean_title(m.group(2)) if m.group(2) else None
        return "h3", m.group(1), title
    m = config.ORG_H2_RE.match(line)
    if m:
        title = clean_title(m.group(2)) if m.group(2) else None
        return "h2", m.group(1), title
    return "", None, None


def clean_title(raw: str) -> str:
    """《五帝本紀》/ 〈堯典〉 -> 五帝本紀/堯典；正文标题中夹杂空白与序号归并。"""
    t = raw.strip()
    t = re.sub(r"[《〈》〉]", "", t)
    t = re.sub(r"\s+", "", t)
    return t


# 唯一一条「编号 + 书名号」形态的篇题模式（史記）。形态若再多一条，改这里成元组。
_PLAIN_TITLE_PATTERN = "shiji_plain"


def title_candidates(patterns: tuple = ()) -> list[re.Pattern]:
    """「编号 + 书名号」形态的明文篇题（史記 `1.1《五帝本紀》`），逐条尝试。

    `patterns` 传本书的声明（catalog.patterns_for(book)）：**书没声明就不试**。
    title_patterns 声明的是「本书会出现哪些标题形态」；没声明却照样去匹配，认错
    的代价是那行被当标题行、从此不进正文索引（heading 不进 FTS）。空 tuple 只在
    catalog 不可用时出现 → 回落到 6.2 行为（史記那一份）。

    调用方按 `m.group(2)` 取标题文字，所以将来同等形态的模式可以直接加进注册表。
    """
    if patterns and all(p.name != _PLAIN_TITLE_PATTERN for p in patterns):
        return []
    return [PATTERNS[_PLAIN_TITLE_PATTERN].regex]


# ------------------------------------------------------------ normalized

def make_normalized(text_orig: str, kind: str, layer: str) -> str | None:
    """派生检索文本：去除 <pb:...> 与 ¶、行首全角空格，其余原样（绝不复写原字段）。"""
    if kind not in ("passage", "comment"):
        return None
    t = strip_pb(text_orig)
    t = t.replace(config.PARA_CHAR, "")
    t = t.strip()
    t = re.sub(r"^[　 ]+", "", t)
    return t or None
