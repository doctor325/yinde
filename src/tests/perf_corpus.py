"""第六点二阶段 Step 9 —— 语料规模性能基线（扩容前 / 扩容后同一张表对比）。

用法：  python tests/perf_corpus.py [--label 扩容前]
前提：  history.db 已由 run_all 生成。

与 tests/perf_phase3.py 的分工：
- perf_phase3 走 **HTTP loopback**，量的是「用户可感的端到端」；
- 本脚本走 **进程内直调** `search_result_blocks`，量的是「引擎本身」。

为什么要分开：本机在持续 CPU 负载下会**整体降频且不恢复**（实测 P2 基线自己从 4.9ms
涨到 6.2ms）。进程内直调没有 HTTP 解析、没有 socket 往返，同一进程里交替跑小查询就能
看出机器当前状态，扩容前后两张表才可比。性能数字必须能区分「语料变大了」和「机器变慢了」。

量六类查询（计划书 §27）：普通人物 / 中频词 / 高频词 / 单字 / 多关键词 / 篇名。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline import config                      # noqa: E402
from search import result_block as RB                    # noqa: E402
from search import zh                                    # noqa: E402

# (查询, 类别)。覆盖计划书 §27 点名的六类。
QUERIES = [
    ("齊桓公", "普通人物"),
    ("管仲", "普通人物"),
    ("將軍", "中频词"),
    ("大夫", "中频词"),
    ("曰", "高频词"),
    ("王", "高频词"),
    ("之", "单字"),
    ("齊桓公 卒", "多关键词"),
    ("秦始皇本紀", "篇名"),
    ("五帝本紀", "篇名"),
]

# 对照组：一个恒定的小查询。它自己不该随语料扩容而显著变化——
# 若它也在涨，涨的是机器/环境，不是语料。
CONTROL = "齊桓公"

# 繁简转换探针：与 tests/recall.py:50 同一组。转换一旦静默回退成恒等函数，
# 简体输入会「搜得到 0 条」而不是报错，本表的命中数就全是假的。
ZH_PROBE = [("齐", "齊"), ("郑", "鄭"), ("苏", "蘇"), ("张", "張"), ("晋", "晉")]


def zh_works() -> bool:
    return all(zh.to_traditional(s) == t for s, t in ZH_PROBE)


def db_stats() -> dict:
    """库规模：文件字节、页数、各表行数、索引体积。"""
    path = config.DB_PATH
    size = path.stat().st_size
    con = sqlite3.connect(path)
    try:
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        page_count = con.execute("PRAGMA page_count").fetchone()[0]
        counts = {}
        for t in ("books", "files", "juans", "sections", "passages",
                  "source_references", "kr_chars", "src_paragraphs"):
            try:
                counts[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                counts[t] = None
        # FTS 影子表的体积（dbstat 未必可用，回退到按表求和 page 数）
        fts_bytes = 0
        try:
            rows = con.execute(
                "SELECT name, sum(pgsize) FROM dbstat "
                "WHERE name LIKE 'passages_%' GROUP BY name").fetchall()
            fts_bytes = sum(r[1] for r in rows)
        except sqlite3.Error:
            fts_bytes = None
        by_book = con.execute(
            "SELECT b.title, b.family, count(p.passage_id) FROM passages p "
            "JOIN books b ON b.book_id = p.book_id GROUP BY 1,2 ORDER BY 3 DESC").fetchall()
    finally:
        con.close()
    return {"bytes": size, "page_size": page_size, "page_count": page_count,
            "counts": counts, "fts_bytes": fts_bytes, "by_book": by_book}


def timed(con, q: str, mode: str = "standard", page_size: int = 20):
    """一次完整检索。返回 (毫秒, 结果 dict)。"""
    t0 = time.perf_counter()
    res = RB.search_result_blocks(con, q, page=1, page_size=page_size, mode=mode)
    return (time.perf_counter() - t0) * 1000, res


def bench(con, q: str, n: int = 3) -> tuple:
    """跑 n 次取最小值与中位数：本机降频只会让数字变大，最小值最接近引擎真实成本。"""
    times, last = [], None
    for _ in range(n):
        ms, res = timed(con, q)
        times.append(ms)
        last = res
    return min(times), statistics.median(times), last


def peak_memory(con, q: str) -> int:
    """单独量一次峰值内存。

    必须与计时**分开**跑：tracemalloc 会给每次分配挂钩子，实测把「管仲」从 199ms
    拖到 2s 量级，叠在计时上量出来的就不是引擎成本了（第一版踩过这个坑）。
    """
    tracemalloc.start()
    try:
        RB.search_result_blocks(con, q, page=1, page_size=20, mode="standard")
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="语料规模性能基线")
    ap.add_argument("--label", default="", help="这批数字的标签，如「扩容前」")
    ap.add_argument("--json", default="", help="把结果另存为 JSON（供前后对比）")
    args = ap.parse_args(argv)

    if not config.DB_PATH.is_file():
        print(f"缺 history.db：先 python -m scripts.pipeline.run_all（{config.DB_PATH}）")
        return 1

    if not zh_works():
        print("注意：繁简转换不可用，查询词可能未转繁体，本报告的命中数不可信。")

    st = db_stats()
    print(f"=== 语料规模基线{'（' + args.label + '）' if args.label else ''} ===")
    print(f"库文件      {st['bytes'] / 1048576:.1f} MB"
          f"（{st['page_count']:,} 页 × {st['page_size']:,} B）")
    for k, v in st["counts"].items():
        if v is not None:
            print(f"  {k:<20}{v:>10,}")
    if st["fts_bytes"]:
        print(f"  FTS 影子表合计      {st['fts_bytes'] / 1048576:.1f} MB")
    print("  按书：")
    for title, family, n in st["by_book"]:
        print(f"    {title:<12}{family or '-':<6}{n:>10,}")

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 每个查询先建一次 sec_idx——真实请求里 search_result_blocks 内部自己建，
    # 这里不预先注入，保证量到的是完整路径。
    rows = []
    try:
        print(f"\n对照（小查询「{CONTROL}」，用来判断机器是否在降频）")
        print("-" * 78)
        c_before, _, _ = bench(cur, CONTROL, n=3)
        print(f"  起始 {c_before:.0f}ms")

        print(f"\n{'查询':<14}{'类别':<10}{'命中':>8}{'片段':>8}"
              f"{'最小':>9}{'中位':>9}{'峰值内存':>11}")
        print("-" * 78)
        for q, kind in QUERIES:
            ms_min, ms_p50, res = bench(cur, q, n=3)
            peak = peak_memory(cur, q)
            rows.append({"query": q, "kind": kind, "ms_min": ms_min, "ms_p50": ms_p50,
                         "peak_bytes": peak, "hit_total": res["hit_total"],
                         "total": res["total"], "mode": res["mode"]})
            print(f"{q:<14}{kind:<10}{res['hit_total']:>8,}{res['total']:>8,}"
                  f"{ms_min:>8.0f}ms{ms_p50:>8.0f}ms{peak / 1048576:>10.2f}M")

        c_after, _, _ = bench(cur, CONTROL, n=3)
        print("-" * 78)
        print(f"对照 结束 {c_after:.0f}ms"
              f"（起始 {c_before:.0f}ms，漂移 {(c_after - c_before) / max(c_before, 1) * 100:+.0f}%）")
        if c_after > c_before * 1.5:
            print("  ← 对照查询自己就涨了 50% 以上：机器在降频，"
                  "下面的大数字里有一部分不是语料造成的。报数字时必须写上这句。")
    finally:
        con.close()

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"label": args.label, "db": {k: v for k, v in st.items() if k != "by_book"},
             "by_book": [list(r) for r in st["by_book"]],
             "control_before_ms": c_before, "control_after_ms": c_after,
             "queries": rows},
            ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        print(f"\n已写出 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
