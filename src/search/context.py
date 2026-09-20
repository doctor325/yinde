"""上下文接口：当前 passage 的同文件真实相邻段落（前 n / 后 n）。

上下文窗口 = 同一原始文件内按 (row_no, seq) 相邻的 kind='passage' 记录
（SBCK 行内切分的多条记录按 seq 衔接）。全部来自数据库真实查询，
绝不由模型补写。越界/文件边界处自然截短。
"""
from __future__ import annotations

from search.engine import _COLS, _JOIN, _shape_row

MAX_WINDOW = 10


def _neighbor_sql():
    return (f"SELECT {_COLS} {_JOIN} WHERE p.file_id = ? AND p.kind='passage' "
            f"AND (p.row_no, p.seq) {op}")


def get_context(cur, passage_id: int, before: int = 3, after: int = 3) -> dict:
    """返回 {"current": {...}, "before": [...], "after": [...]}。

    记录不存在抛 KeyError；不存在不代表坏数据——上下文窗口自然截短。
    """
    before = min(max(before, 0), MAX_WINDOW)
    after = min(max(after, 0), MAX_WINDOW)
    cur_ = cur.execute(
        f"SELECT {_COLS} {_JOIN} WHERE p.passage_id = ?", (passage_id,)).fetchone()
    if not cur_:
        raise KeyError(passage_id)
    cur_row = cur_["row_no"]
    cur_seq = cur_["seq"]
    file_id = cur_["file_id"]

    before_rows = []
    if before:
        before_rows = cur.execute(
            f"SELECT {_COLS} {_JOIN} WHERE p.file_id = ? AND p.kind='passage' "
            f"AND ((p.row_no < ?) OR (p.row_no = ? AND p.seq < ?)) "
            f"ORDER BY p.row_no DESC, p.seq DESC LIMIT ?",
            (file_id, cur_row, cur_row, cur_seq, before)).fetchall()
        before_rows.reverse()

    after_rows = []
    if after:
        after_rows = cur.execute(
            f"SELECT {_COLS} {_JOIN} WHERE p.file_id = ? AND p.kind='passage' "
            f"AND ((p.row_no > ?) OR (p.row_no = ? AND p.seq > ?)) "
            f"ORDER BY p.row_no, p.seq LIMIT ?",
            (file_id, cur_row, cur_row, cur_seq, after)).fetchall()

    return {"current": _shape_row(cur_),
            "before": [_shape_row(r) for r in before_rows],
            "after": [_shape_row(r) for r in after_rows]}
