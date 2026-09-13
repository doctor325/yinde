"""Section 结构只读诊断（第六点三阶段 6.3-C③）。

回答一个问题：**这本书的篇名，哪些没认出来？** 覆盖率百分比只说「缺了多少行」，
说不出「缺的是哪种形态、该给哪条模式」。扩容时新书的版式与旧书不同（第一本
WYG 正史 前漢書 的長題名 + 扩展区字形、三國志的 `　武帝(操)` 傳名），全靠这个
工具把「长得像篇名却没成 section 的行」揪出来，再决定**只改 JSON**（给这本书
加一条已有模式）还是**要加新模式**（改代码，同时在 title_patterns.py 注册）。

用法：

    python -m scripts.pipeline.audit_sections --book sanguozhi
    python -m scripts.pipeline.audit_sections --book sanguozhi --file 1
    python -m scripts.pipeline.audit_sections --book sanguozhi --grep 武帝
    python -m scripts.pipeline.audit_sections --compare qianhanshu,houhanshu,sanguozhi

判据（都用**已入库的事实**，不重新解析语料，因此与线上一致）：

- 正文行：`kind='passage'` 且 `layer='main'`（与 manifest 同一口径）。
- 候选篇名行：正文行里「缩进 + 全汉字（可带一个 ≤6 字的括号组）+ 无句读」且占满
  整行的。这正是 WYG 篇题/傳名的长相；段落正文长得多，不会误中。
- 未归篇行：正文层里 `row_no` 小于本文件第一条 section 的 first_row 的行——区间
  模型下它们无所归，成见有三类（结构使然 / 源转录缺卷首题 / 篇题识别失败），
  见 manifest.py 模块注释。
- 区间裂口：正文层里不落在任何一条 `[first_row, last_row]` 内的行。`last_row` 为
  NULL（旧库未重跑）时这项报 0 并提示重建。

候选表按「未命中任何已知模式的新形态」优先、再看跨文件重复度排序——真篇题会在
目録/正文/考證里重复出现，而误报的正文行通常只出现一次。每条候选都标出它**命中
了哪些已注册模式**：`wyg_ming_paren` 这三个字就是「给三國志加这条模式」的答案。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict

from . import config
from .title_patterns import CJK_CHAR, PATTERNS

# 候选篇名行：缩进 + 全汉字 +（可选）短括号组 + 无句读，占满整行
CAND_RE = re.compile(
    rf"^(?P<indent>[　\s]{{1,8}})(?P<name>{CJK_CHAR}{{1,20}})"
    rf"(?P<paren>[(（][^)）]{{0,6}}[)）])?{config.PARA_CHAR}?\s*$"
)

BODY_SQL = "p.kind='passage' AND p.layer='main'"


def _col(row: sqlite3.Row, name: str, default=None):
    """容错取列：6.3 新加的列在**没有重跑过管线的旧库**里不存在。

    本工具是只读诊断，宁可把旧库如实报成「未标注」，也不能因为少一列就崩掉
    ——那正好把「该重建了」这件事变成看不见。
    """
    try:
        return row[name]
    except (IndexError, KeyError):
        return default


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _book_row(con, book_dir: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM books WHERE book_dir=?", (book_dir,)).fetchone()


def _sections(con, book_id: str) -> list[sqlite3.Row]:
    return list(con.execute(
        "SELECT s.*, f.file_no FROM sections s LEFT JOIN files f ON f.file_id=s.file_id "
        "WHERE s.book_id=? ORDER BY s.file_id, s.first_row", (book_id,)))


def _body_rows(con, book_id: str) -> list[sqlite3.Row]:
    return list(con.execute(
        "SELECT p.file_id, p.row_no, p.text_orig, f.file_no FROM passages p "
        "JOIN files f ON f.file_id=p.file_id WHERE p.book_id=? AND " + BODY_SQL +
        " ORDER BY p.file_id, p.row_no", (book_id,)))


def matched_patterns(text: str) -> list[str]:
    """这一行命中了哪些已注册的模式（决定「只改 JSON」能不能解决）。"""
    return [p.name for p in PATTERNS.values() if p.regex.match(text)]


def analyze(con, book_dir: str) -> dict:
    book = _book_row(con, book_dir)
    if book is None:
        raise SystemExit(f"库里没有书目录 {book_dir!r}"
                         f"（先跑 run_all；磁盘上有目录不等于已入库）")
    bid = book["book_id"]
    secs = _sections(con, bid)
    rows = _body_rows(con, bid)

    # 每文件的 section 区间（first_row 起、last_row 止；NULL 表示未回填）
    by_file: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for s in secs:
        by_file[s["file_id"]].append(s)

    first_of_file: dict[int, int] = {}
    labels_by_file: dict[int, set[str]] = {}
    for fid, ss in by_file.items():
        first_of_file[fid] = min(s["first_row"] for s in ss)
        labels_by_file[fid] = {s["label"] for s in ss}

    # 区间覆盖（用 last_row；缺失则退化成「>= 首条 first_row」）
    covers: dict[int, list[tuple[int, int]]] = {}
    for fid, ss in by_file.items():
        spans = []
        for s in ss:
            right = _col(s, "last_row")
            spans.append((s["first_row"], 10 ** 9 if right is None else right))
        covers[fid] = spans

    unassigned: list[dict] = []      # section 之前
    gaps: list[dict] = []            # 区间裂口
    cands: dict[str, dict] = {}      # 文本 -> {count, files, rows, patterns}
    for r in rows:
        fid, rno, text = r["file_id"], r["row_no"], r["text_orig"]
        f0 = first_of_file.get(fid)
        if f0 is None or rno < f0:
            unassigned.append({"file_no": r["file_no"], "row_no": rno,
                               "text": text[:60]})
            continue
        if not any(lo <= rno <= hi for lo, hi in covers.get(fid, ())):
            gaps.append({"file_no": r["file_no"], "row_no": rno, "text": text[:60]})
        m = CAND_RE.match(text)
        if not m or m.group("name") in labels_by_file.get(fid, ()):
            continue
        key = text.strip()
        e = cands.setdefault(key, {"text": text, "name": m.group("name"),
                                   "count": 0, "files": set(), "rows": [],
                                   "patterns": matched_patterns(text)})
        e["count"] += 1
        e["files"].add(r["file_no"])
        if len(e["rows"]) < 3:
            e["rows"].append(f'{r["file_no"]}:{rno}')

    methods: dict[str, int] = defaultdict(int)
    low = 0
    for s in secs:
        methods[_col(s, "detection_method") or "untagged"] += 1
        conf = _col(s, "confidence")
        if conf is not None and conf <= 0.6:
            low += 1

    return {
        "book_id": bid, "book_dir": book_dir, "title": book["title"],
        "family": book["family"], "edition": book["edition"],
        "files": len({r["file_id"] for r in rows}),
        "body_rows": len(rows), "sections": secs, "by_file": by_file,
        "methods": dict(methods), "low_confidence": low,
        "unassigned": unassigned, "gaps": gaps,
        "cands": sorted(cands.values(),
                        key=lambda e: (bool(e["patterns"]), -len(e["files"]),
                                       -e["count"], e["text"])),
    }


def print_book(a: dict, file_no: int | None = None, grep: str | None = None,
               top: int = 30) -> None:
    print(f'\n=== {a["title"]}（{a["book_dir"]} / {a["book_id"]}）'
          f'  家族={a["family"]} 版本={a["edition"]}')
    print(f'  文件 {a["files"]}  正文行 {a["body_rows"]}  '
          f'sections {len(a["sections"])}  低置信 {a["low_confidence"]}')
    print("  篇题来源：" + "、".join(f"{k} {v}" for k, v in sorted(a["methods"].items())))
    if a["methods"].get("untagged"):
        print("    ** 有未标注 section —— 旧库没重跑过，跑一次 run_all 补齐溯源字段")

    for fid, ss in sorted(a["by_file"].items()):
        fno = ss[0]["file_no"]
        if file_no is not None and fno != file_no:
            continue
        print(f'  --- 文件 {fno}：{len(ss)} 条 section')
        for s in ss:
            c = _col(s, "confidence")
            lr = _col(s, "last_row")
            print(f'      {s["first_row"]:>6}-{"—" if lr is None else lr:<6} '
                  f'[{_col(s, "detection_method") or "?":<16}'
                  f'{"—" if c is None else f"{c:.1f}"}] {s["label"]}')

    if file_no is None:
        if a["unassigned"]:
            print(f'  --- 未归篇行（正文层、早于本文件首条 section）：'
                  f'{len(a["unassigned"])} 行，样例：')
            for u in a["unassigned"][:8]:
                print(f'      {u["file_no"]}:{u["row_no"]}  {u["text"]!r}')
        if a["gaps"]:
            print(f'  --- ** 区间裂口 {len(a["gaps"])} 行（不落在任何 section 区间内）：')
            for g in a["gaps"][:8]:
                print(f'      {g["file_no"]}:{g["row_no"]}  {g["text"]!r}')

    cands = a["cands"]
    if grep:
        cands = [c for c in cands if grep in c["text"]]
    print(f'  --- 未识别候选篇名行（共 {len(cands)} 种'
          + (f'，grep {grep!r}' if grep else '') + f'，列前 {top}）：')
    if not cands:
        print("      （无——正文里没有「像篇名却没成 section」的行）")
    for c in cands[:top]:
        pat = ("命中 " + "/".join(c["patterns"])) if c["patterns"] else "**新形态**"
        print(f'      ×{c["count"]:<3} {len(c["files"])} 文件  {c["text"].strip()!r}'
              f'  {pat}  {" ".join(c["rows"])}')
    print("  说明：候选排序＝「新形态」优先，其次跨文件重复度。"
          "命中已有模式却没成 section 的，多半是被层门控挡住了（目録/考證），"
          "属预期噪声；**新形态**才需要决定「加一条 JSON 引用已有模式」还是"
          "「在 title_patterns.py 注册新模式」。")


def print_compare(con, dirs: list[str]) -> None:
    print("\n=== 并排对比（决定新书要不要自己的 title_patterns）")
    print(f'{"书":<10}{"文件":>5}{"正文行":>9}{"sections":>9}{"低置信":>7}'
          f'{"未归篇行":>9}{"裂口":>6}{"候选":>6}  篇题来源')
    for d in dirs:
        a = analyze(con, d)
        src = "、".join(f"{k} {v}" for k, v in sorted(a["methods"].items()))
        print(f'{a["title"]:<10}{a["files"]:>5}{a["body_rows"]:>9}'
              f'{len(a["sections"]):>9}{a["low_confidence"]:>7}'
              f'{len(a["unassigned"]):>9}{len(a["gaps"]):>6}{len(a["cands"]):>6}  {src}')
    print("\n  逐书的新形态候选（未命中任何已注册模式）：")
    for d in dirs:
        a = analyze(con, d)
        new = [c for c in a["cands"] if not c["patterns"]]
        print(f'  {a["title"]}：{len(new)} 种'
              + ("" if not new else "，样例 " + "; ".join(
                  f'{c["text"].strip()!r}×{c["count"]}' for c in new[:5])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Section 结构只读诊断（6.3-C③）")
    ap.add_argument("--book", help="书目录名（如 sanguozhi）")
    ap.add_argument("--compare", help="逗号分隔的多个书目录名，并排对比")
    ap.add_argument("--file", type=int, default=None, help="只看某个文件号")
    ap.add_argument("--grep", default=None, help="只看含该串的候选行")
    ap.add_argument("--top", type=int, default=30, help="候选最多列几条")
    args = ap.parse_args(argv)

    if not args.book and not args.compare:
        ap.error("至少要给 --book 或 --compare")
    if not config.DB_PATH.is_file():
        print(f"没有数据库：{config.DB_PATH}（先跑 python -m scripts.pipeline.run_all）",
              file=sys.stderr)
        return 2

    con = _connect()
    try:
        if args.compare:
            print_compare(con, [d.strip() for d in args.compare.split(",") if d.strip()])
        if args.book:
            print_book(analyze(con, args.book), args.file, args.grep, args.top)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
