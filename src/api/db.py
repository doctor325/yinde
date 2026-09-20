"""API 只读查询层：每个请求开一个只读连接（数据库 = 派生物，绝不写库）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.pipeline import config


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else config.DB_PATH
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------ 书/文件
# 计数不做「每行一个相关子查询」（5 书 118 文件 × 多谓词 = 22 万行上重复扫多轮，
# 实测 /api/books ~0.9s）。改为：passages 单次 GROUP BY 聚合（全表一趟 <50ms），
# Python 侧按主键合并，输出字段与旧契约完全一致。

_BOOKS_BASE = """
SELECT b.book_id, b.book_dir, b.title, b.family, b.edition,
       (SELECT COUNT(*) FROM files f WHERE f.book_id = b.book_id) AS files
FROM books b ORDER BY b.book_id
"""
_BOOK_AGG = """
SELECT book_id,
       COUNT(*) AS records,
       COUNT(*) FILTER (WHERE kind = 'passage' AND layer = 'main') AS main_passages,
       COUNT(*) FILTER (WHERE kind = 'passage'
                          AND (layer IS NULL OR layer = 'main')) AS body_rows,
       COUNT(*) FILTER (WHERE status LIKE 'pending%') AS pending,
       COUNT(*) FILTER (WHERE kind = 'comment' AND source_ref_json IS NOT NULL)
         AS src_blocks,
       COUNT(*) FILTER (WHERE special_chars_json IS NOT NULL) AS kr_rows
FROM passages GROUP BY book_id
"""

# 篇名覆盖（§15 / §18 Section Coverage Audit 的数据面）。
# section 是**区间模型**：一条 section 覆盖它所在文件内自 first_row 起至该文件下
# 一条 section（最后一条到文件末）。所以「正文行有没有归篇」＝与所在文件第一条
# section 的行号比大小，与 passages.section 字段无关。
#
# 写法上**只数缺口、不数全量**：缺口＝本文件第一条 section 之前的正文行，用
# idx_pas_file_row 做 (file_id, row_no < fr) 的索引 seek 就够了，实测 7 ms；
# 写成「连接全表再 FILTER(row_no >= fr)」要扫 42 万行，225 ms，白花 30 倍。
# 已归篇 = body_rows - 缺口（body_rows 在 _BOOK_AGG 那一趟里顺带数出来）。
#
# 缺口分两笔，因为 INNER JOIN 天然漏掉「整个文件一条 section 都没有」那种
# （國語 003/007 各有一整卷）——只数前一笔会把它们算成已归篇（实测差 1129 行）。
_UNCOVERED_AGG = """
SELECT p.book_id, COUNT(*) AS uncovered_rows
FROM passages p
JOIN (SELECT file_id, MIN(first_row) AS fr FROM sections GROUP BY file_id) s
  ON s.file_id = p.file_id AND p.row_no < s.fr
WHERE p.kind = 'passage' AND (p.layer IS NULL OR p.layer = 'main')
GROUP BY p.book_id
"""
# 第二笔：完全没有 section 的文件，正文行全是缺口。这类文件极少（实测 4 个：
# 前漢書 020/029、國語 003/007），所以先把「有 section 的文件」取成集合（1010
# 行的 sections 表，一次 DISTINCT 就够），剩下的文件逐个按 file_id 走索引数。
# **写成一条带 NOT EXISTS 的 JOIN 会慢 10 倍**（实测 225 ms → 3570 ms）：优化器
# 从 passages 侧驱动，42 万行每行都去判一次子查询。
_SECTION_FILES = "SELECT DISTINCT file_id FROM sections"
_ALL_FILES = "SELECT file_id, book_id FROM files"
_BODY_ROWS_IN_FILE = ("SELECT COUNT(*) FROM passages WHERE file_id=? "
                      "AND kind='passage' AND (layer IS NULL OR layer='main')")
_SECTION_AGG = "SELECT book_id, COUNT(*) AS sections FROM sections GROUP BY book_id"
_JUAN_AGG = "SELECT book_id, COUNT(*) AS juans FROM juans GROUP BY book_id"


def list_books(cur) -> list[dict]:
    base = {r["book_id"]: dict(r) for r in cur.execute(_BOOKS_BASE)}
    for r in cur.execute(_BOOK_AGG):
        base[r["book_id"]].update(dict(r))
    for sql in (_SECTION_AGG, _JUAN_AGG):
        for r in cur.execute(sql):
            base[r["book_id"]].update(dict(r))
    # 缺口两笔相加：有 section 的文件里「第一条 section 之前」的行（一条 SQL）
    # ＋ 完全无 section 的文件里的全部正文行（逐文件按索引数）
    for r in cur.execute(_UNCOVERED_AGG):
        base[r["book_id"]]["uncovered_rows"] = r["uncovered_rows"]
    with_sec = {r[0] for r in cur.execute(_SECTION_FILES)}
    # 先 list() 落定：循环里还要用同一个 cursor 跑别的查询，边迭代边 execute
    # 会把外层结果集冲掉（实测表现是这四个文件的缺口全部漏掉）
    for fid, bid in list(cur.execute(_ALL_FILES)):
        if fid in with_sec:
            continue
        n = cur.execute(_BODY_ROWS_IN_FILE, (fid,)).fetchone()[0]
        if n:
            d = base[bid]
            d["uncovered_rows"] = d.get("uncovered_rows", 0) + n
    out = []
    for d in base.values():
        d.setdefault("records", 0); d.setdefault("main_passages", 0)
        d.setdefault("body_rows", 0); d.setdefault("uncovered_rows", 0)
        d.setdefault("pending", 0); d.setdefault("src_blocks", 0)
        d.setdefault("kr_rows", 0)
        d.setdefault("sections", 0); d.setdefault("juans", 0)
        if not d["sections"]:
            d["uncovered_rows"] = d["body_rows"]
        d["covered_rows"] = d["body_rows"] - d["uncovered_rows"]
        # 覆盖状态与 scripts/pipeline/manifest.py 同一套判据（§18）：一本书一条
        # 篇名都没认出来＝FAIL；缺口大于 10% ＝WARN；其余 OK。
        # 两边口径必须一致，否则审计表和页面上会给出两个答案。
        cov = (d["covered_rows"] / d["body_rows"]) if d["body_rows"] else 1.0
        d["section_coverage"] = round(cov, 4)
        if d["body_rows"] and not d["sections"]:
            d["coverage_status"] = "FAIL"
        elif cov < 0.90:
            d["coverage_status"] = "WARN"
        else:
            d["coverage_status"] = "OK"
        out.append(d)
    return out


def get_book(cur, book_id: str) -> dict | None:
    r = cur.execute("SELECT * FROM books WHERE book_id = ?", (book_id,)).fetchone()
    return dict(r) if r else None


_FILES_BASE = """
SELECT f.file_id, f.book_id, f.file_name, f.file_no, f.juan_prop, f.sha256,
       f.origin_path, f.meta_json
FROM files f
"""
_FILE_AGG = """
SELECT file_id,
       COUNT(*) AS records,
       COUNT(*) FILTER (WHERE kind = 'passage' AND layer = 'main') AS main,
       COUNT(*) FILTER (WHERE kind = 'passage'
                        AND layer = 'commentary_candidate') AS commentary,
       COUNT(*) FILTER (WHERE status LIKE 'pending%') AS pending,
       COUNT(*) FILTER (WHERE layer = 'preface') AS preface,
       COUNT(*) FILTER (WHERE layer IN ('backmatter', 'toc')) AS back,
       COUNT(*) FILTER (WHERE special_chars_json IS NOT NULL) AS kr_rows,
       MIN(pb_block) FILTER (WHERE pb_block IS NOT NULL) AS pb_lo,
       MAX(pb_block) FILTER (WHERE pb_block IS NOT NULL) AS pb_hi
FROM passages GROUP BY file_id
"""
_SRCREF_AGG = "SELECT file_id, COUNT(*) n FROM source_references GROUP BY file_id"
_FILES_WHERE = " WHERE f.book_id = ?"


def _merge_files(cur, base_rows: list[dict]) -> list[dict]:
    ids = [d["file_id"] for d in base_rows] or [-1]
    agg = {r["file_id"]: dict(r) for r in cur.execute(
        _FILE_AGG + " HAVING file_id IN (%s)" % ",".join("?" * len(ids)), ids)}
    refs = dict(cur.execute(
        _SRCREF_AGG + " HAVING file_id IN (%s)" % ",".join("?" * len(ids)), ids))
    out = []
    for d in base_rows:
        a = agg.get(d["file_id"], {})
        a.setdefault("records", 0); a.setdefault("main", 0)
        a.setdefault("commentary", 0); a.setdefault("pending", 0)
        a.setdefault("preface", 0); a.setdefault("back", 0)
        a.setdefault("kr_rows", 0)
        d.update(a)
        d["src_refs"] = refs.get(d["file_id"], 0)
        d["pb_range"] = f"{d['pb_lo']}-{d['pb_hi']}" if d.get("pb_lo") else None
        d.pop("pb_lo", None); d.pop("pb_hi", None)
        out.append(d)
    return out


def list_files(cur, book_id: str) -> list[dict]:
    base = [dict(r) for r in cur.execute(_FILES_BASE + _FILES_WHERE + " ORDER BY f.file_no",
                                         (book_id,))]
    return _merge_files(cur, base)


def get_file(cur, file_id: int) -> dict | None:
    r = cur.execute(_FILES_BASE + " WHERE f.file_id = ?", (file_id,)).fetchone()
    if not r:
        return None
    d = _merge_files(cur, [dict(r)])[0]
    try:
        import json
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
    except ValueError:
        d["meta"] = {}
    d["kind_layer_counts"] = [dict(x) for x in cur.execute(
        "SELECT kind, layer, status, COUNT(*) n FROM passages WHERE file_id = ? "
        "GROUP BY kind, layer, status ORDER BY kind, layer", (file_id,))]
    return d


# ------------------------------------------------------------------ 记录

_FILTER_SQL = {"kind": "kind = ?", "layer": "layer = ?", "status": "status = ?",
               "section": "section = ?", "juan": "juan = ?", "q": "text_orig LIKE ?"}


def list_passages(cur, file_id: int, kind=None, layer=None, status=None,
                  q=None, offset: int = 0, limit: int = 100) -> dict:
    where, args = ["file_id = ?"], [file_id]
    for key, val in (("kind", kind), ("layer", layer), ("status", status)):
        if val:
            where.append(_FILTER_SQL[key])
            args.append(val)
    if q:
        where.append("text_orig LIKE ?")
        args.append(f"%{q}%")
    cond = " AND ".join(where)
    total = cur.execute(f"SELECT COUNT(*) FROM passages WHERE {cond}", args).fetchone()[0]
    rows = cur.execute(
        f"SELECT * FROM passages WHERE {cond} ORDER BY row_no, seq "
        f"LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return {"total": total, "offset": offset, "limit": limit,
            "rows": [dict(r) for r in rows]}


def get_passage(cur, passage_id: int) -> dict | None:
    r = cur.execute("SELECT * FROM passages WHERE passage_id = ?",
                    (passage_id,)).fetchone()
    return dict(r) if r else None
