"""全文检索主逻辑（SQLite FTS5 + 短词补充路径）。

检索语料：passages.kind='passage' 行的 normalized_text（第一阶段确立的检索派生
文本）；text_orig 只用于展示/追溯，永不进索引、永不被改。

路径选择（输入先经 zh.to_traditional 转繁体）：
- 任一空白分隔词 >= 3 字 → FTS5 trigram（passages_fts）：子串式命中，词间隐式 AND；
  同查询里的 <3 字词作为 LIKE 附加约束参与。
- 全部为 2 字词（管仲/城濮/苏秦…）→ bigram 辅助表 passages_bg（相邻两字 token，
  与 LIKE 集合语义等价，见 sqlite_store._fts_build）：点查 ~10ms 取代全表 ~350ms。
- 含 1 字词，或 passages_bg 缺席（旧库未重建）→ LIKE 补充路径（全扫本地实测 ~350ms，
  1 字词没有索引可走；README 性能节记录）。
- 环境若确认无 FTS5（stats.fts.ok=False）则所有查询走 LIKE。

出处字段一律取数据库真实值，缺失 = None（前端显示「暂无」）；
禁止用 KRxxxx_NNN.txt 文件名推测语义章节（Kanripo 文件号 ≠ 卷次）。
"""
from __future__ import annotations

import json
import re

from search import zh

PAGE_SIZE_MAX = 100
PAGE_SIZE_DEFAULT = 20
_WS_RE = re.compile(r"\s+")
_LIKE_ESC = re.compile(r"[%_\\]")

# 常用书名的用户友好写法（简/繁）。书目集合本身仍由 books 表自动发现，
# 此处只是把用户口语名映射到书号，不新增/猜测任何书。
_SHORT_NAMES = {
    "尚书": "KR1b0001", "尚書": "KR1b0001", "书经": "KR1b0001",
    "左传": "KR1e0001", "左傳": "KR1e0001", "春秋左传": "KR1e0001",
    "春秋左傳": "KR1e0001", "春秋": "KR1e0001",
    "史记": "KR2a0001", "史記": "KR2a0001",
    "国语": "KR2e0001", "國語": "KR2e0001",
    "战国策": "KR2e0003", "戰國策": "KR2e0003",
}

_COLS = ("p.passage_id, p.file_id, p.row_no, p.seq, p.kind, p.layer, p.status, p.juan, p.section, "
         "p.subsection, p.division, p.ab, p.text_orig, p.normalized_text, "
         "p.pb_raw, p.pb_block, p.pb_page, p.pb_side, p.special_chars_json, "
         "p.source_ref_json, b.book_id, b.title, b.family, b.edition, "
         "f.file_name, f.sha256, f.origin_path")
_JOIN = ("FROM passages p "
         "JOIN files f ON f.file_id = p.file_id "
         "JOIN books b ON b.book_id = f.book_id")


def _like_cond(term: str) -> tuple[str, str]:
    def esc(t: str) -> str:
        return _LIKE_ESC.sub(lambda m: "\\" + m.group(0), t)
    return f"p.normalized_text LIKE ? ESCAPE '\\'", f"%{esc(term)}%"


def plan_query(q: str) -> tuple[list[str], list[str]]:
    """分词：返回 (长词>=3字, 短词<3字)。输入为去首尾空白后的串。"""
    words = [w for w in _WS_RE.split(q) if w]
    return ([w for w in words if len(w) >= 3],
            [w for w in words if len(w) < 3])


def fts_match_text(long_terms: list[str]) -> str:
    """长词 → FTS5 短语串（双引号包裹，引号内双写转义；空格 = 隐式 AND）。"""
    return " ".join('"' + t.replace('"', '""') + '"' for t in long_terms)


def resolve_book(cur, param: str | None) -> str | None:
    """把用户书名（简/繁/目录名/书号）解析为 book_id；识别不了抛 ValueError。

    别名从 books 表现场构建（自动发现，不硬编码书目）。
    """
    if not param:
        return None
    key = _WS_RE.sub("", param).strip().strip("《》")
    if not key:
        return None
    aliases: dict[str, str] = dict(_SHORT_NAMES)
    for r in cur.execute("SELECT book_id, book_dir, title FROM books ORDER BY book_id"):
        for c in (r["book_id"], r["book_dir"], r["title"],
                  zh.to_simplified(r["title"] or "")):
            if c:
                aliases.setdefault(_WS_RE.sub("", c), r["book_id"])
    bid = aliases.get(key) or aliases.get(zh.to_traditional(key))
    if bid is None:
        known = "、".join(sorted({v for v in aliases.values()})) or "—"
        raise ValueError(f"未识别的史书：{param}（可用：{known}）")
    return bid


def resolve_edition(param: str | None) -> str | None:
    if not param:
        return None
    p = _WS_RE.sub("", param).lower()
    return p if p in ("tls", "sbck") else None


def fts_tokenizer(cur) -> str | None:
    """passages_fts 实际采用的 tokenizer（'trigram'/'unicode61'/None）。"""
    r = cur.execute(
        "SELECT sql FROM sqlite_master WHERE name='passages_fts'").fetchone()
    if not r:
        return None
    m = re.search(r"tokenize='(\w+)'", r["sql"] or "")
    return m.group(1) if m else None


def has_bigram_fts(cur) -> bool:
    """二元组辅助表 passages_bg 是否在场（2 字词快路可用）。"""
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE name='passages_bg'").fetchone() is not None


def _shape_row(r) -> dict:
    def j(v):
        try:
            return json.loads(v) if v else None
        except (TypeError, ValueError):
            return None
    sp = j(r["special_chars_json"])
    page = (r["pb_page"] or "") + (r["pb_side"] or "")
    return {
        # ---- 出处（前端优先展示；无则 None → UI 显示「暂无」）----
        "book_title": r["title"],            # 國語（TITLE 原样）
        "book_id": r["book_id"],
        "juan": r["juan"], "section": r["section"], "subsection": r["subsection"],
        "division": r["division"], "ab": r["ab"],
        "edition": r["edition"],             # SBCK / tls（BASEEDITION 原样）
        "family": r["family"],               # sbck | tls
        "file_name": r["file_name"],
        "file_sha256": r["sha256"],
        "origin_path": r["origin_path"],
        "page": page or None,                # 页码+半叶，如 5a
        "pb_block": r["pb_block"],
        "pb_raw": r["pb_raw"],
        "source_ref": j(r["source_ref_json"]),
        # ---- 记录本身 ----
        "passage_id": r["passage_id"], "row_no": r["row_no"],
        "kind": r["kind"], "layer": r["layer"], "status": r["status"],
        "text_orig": r["text_orig"],         # 原文原样（最终展示依据）
        "normalized_text": r["normalized_text"],
        "special_chars": sp,
        "commentary_candidate": r["layer"] == "commentary_candidate",
    }


def _query(cur, where: list[str], args: list, order: str,
           page: int, page_size: int) -> tuple[int, list]:
    cond = " AND ".join(where)
    total = cur.execute(
        f"SELECT count(*) {_JOIN} WHERE {cond}", args).fetchone()[0]
    rows = cur.execute(
        f"SELECT {_COLS} {_JOIN} WHERE {cond} {order} LIMIT ? OFFSET ?",
        args + [page_size, (page - 1) * page_size]).fetchall()
    return total, rows


def _fts_query(cur, table: str, match: str, where: list[str], args: list,
               page: int, page_size: int) -> tuple[int, list]:
    """通用 FTS5 命中查询（trigram 与 bigram 共用）。

    FTS5 的 MATCH 不能放进带 alias 的复合查询里展开，故先取命中 rowid + bm25，
    再与 passages/files/books join（BM25 越小越靠前）。
    """
    fts_from = (f"FROM (WITH hits AS (SELECT rowid AS pid, bm25({table}) AS score "
                f"FROM {table} WHERE {table} MATCH ?) SELECT pid, score FROM hits) h "
                f"JOIN passages p ON p.passage_id = h.pid "
                f"JOIN files f ON f.file_id = p.file_id "
                f"JOIN books b ON b.book_id = f.book_id")
    cond = " AND ".join(where)
    total = cur.execute(
        f"SELECT count(*) FROM (SELECT h.pid {fts_from} WHERE {cond})",
        [match] + args).fetchone()[0]
    rows = cur.execute(
        f"SELECT {_COLS} {fts_from} WHERE {cond} "
        f"ORDER BY h.score, b.book_id, f.file_no, p.row_no LIMIT ? OFFSET ?",
        [match] + args + [page_size, (page - 1) * page_size]).fetchall()
    return total, rows


def run_search(cur, q: str, book: str | None = None, edition: str | None = None,
               page: int = 1, page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    """执行检索。参数非法抛 ValueError（API 层转 400）。

    路径：>=3 字词 → trigram；纯 2 字词且 bigram 表在场 → bigram；其余 → LIKE。
    """
    if page < 1:
        raise ValueError("页码从 1 开始")
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)
    q_trad = zh.to_traditional((q or "").strip())
    if not q_trad:
        raise ValueError("请提供搜索关键词")
    bid = resolve_book(cur, book)          # None = 全部
    edition = resolve_edition(edition)
    long_terms, short_terms = plan_query(q_trad)
    # 只有 trigram 能保证子串命中；unicode61（整段汉字一个 token）下走 LIKE 更可靠
    use_fts = fts_tokenizer(cur) == "trigram" and bool(long_terms)
    pure_two = bool(short_terms) and all(len(t) == 2 for t in short_terms)
    use_bg = not use_fts and pure_two and has_bigram_fts(cur)

    where, args = ["p.kind = 'passage'"], []
    if bid:
        where.append("b.book_id = ?"); args.append(bid)
    if edition:
        where.append("LOWER(b.family) = ?"); args.append(edition)

    if use_fts:
        # 长词走 FTS；同查询的 <3 字词仍作 AND 约束（少见，代价可接受）
        for t in short_terms:
            cond, arg = _like_cond(t)
            where.append(cond); args.append(arg)
        total, rows = _fts_query(cur, "passages_fts",
                                 fts_match_text(long_terms), where, args,
                                 page, page_size)
        mode = "fts"
    elif use_bg:
        # 2 字词是子串式点查，与 LIKE 集合等价；不附加 LIKE（会退回全扫）
        total, rows = _fts_query(cur, "passages_bg",
                                 fts_match_text(short_terms), where, args,
                                 page, page_size)
        mode = "bigram"
    else:
        # 无 FTS / bigram 缺席 / 含 1 字词：全部 LIKE（1 字词没有索引可走）
        for t in long_terms + short_terms:
            cond, arg = _like_cond(t)
            where.append(cond); args.append(arg)
        order = "ORDER BY b.book_id, f.file_no, p.row_no, p.seq"
        total, rows = _query(cur, where, args, order, page, page_size)
        mode = "like"

    return {
        "q": q.strip(), "q_traditional": q_trad,
        "mode": mode,
        "book": bid or "全部", "edition": edition or "全部",
        "total": total, "page": page, "page_size": page_size,
        "items": [_shape_row(r) for r in rows],
    }
