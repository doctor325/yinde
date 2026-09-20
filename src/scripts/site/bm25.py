"""FTS5 bm25 的精确复现，以及现网索引那两个「被放大的总量」的推导。

背景（全部实测，不是推测）
--------------------------
`scripts/pipeline/sqlite_store.py:189-197` 建 FTS 索引时连做两个动作：

    ① INSERT INTO passages_fts(passages_fts) VALUES('rebuild')   -- 按 content 表全量建索引
    ② INSERT INTO passages_fts(rowid, normalized_text) SELECT ... WHERE kind='passage' ...

② 插入的行 ① 已经索引过一遍。FTS5 把这次重插当成「替换」：**倒排与 `%_docsize`
都是干净的**（docsize 按 id 去重，词频也对），但 bm25 用的那两个**总量**被累加了两次：

    nRow_eff   = 全部内容行数 + 再次插入的行数 = 224,822 + 203,308 = 428,130
    nToken_eff = Σdl(全部) + Σdl(再次插入的) = 1,097,047 + 1,097,047 = 2,194,094
    avgdl      = nToken_eff / nRow_eff      = 5.124831243…

后果：**现网的 bm25 与教科书公式对不上**。同一份文本，用干净的方式重建索引
（只 rebuild、不重插）得到的分数与本文公式不同，差 9% 左右 —— 这不是
「公式记错了」，而是现网索引自身的统计量如此。实测对照见
`docs/phase5_consistency.md`。

两个总量都得按**建表动作**算，不能只看影子表：`%_docsize` 里 dl>0 的行有 196,617 条，
而被重插的有 203,308 条 —— 差在「文本非空」不等于「有 token」：1~2 字的 passage 文本
非空，但产生 0 个 trigram（dl = max(0, len-2) = 0）。所以：

    nRow_eff   = docsize 行数 + 重插行数        = 224,822 + 203,308 = 428,130
    nToken_eff = Σdl(rebuild) + Σdl(重插行)     = 1,097,047 + 1,097,047 = 2,194,094

重插行的 Σdl 也看不出重复（dl 按行存，重插不翻倍），但它**等于全部 Σdl**：
非重插行的文本必为空、无非零 token。这个等式由 `verify_against_sqlite()` 兜底。

**注意不是所有 FTS 表都这样翻倍**：`passages_bg` 是独立表（`fts5(bg, tokenize='unicode61')`，
没有 `content=`），管线只 INSERT 一次，总量就是 docsize 的字面值。两套算法分开写，
调用方按表的建法选一个 —— 选错会被 `verify_against_sqlite()` 当场抓住。

多词查询
--------
FTS5 的 bm25 把各词项的分数**相加**（实测 `"齊桓公" "管仲"` 那种查询逐行吻合）。
但 `engine.run_search` 只在 `use_fts` 路径里把 **>=3 字**的词放进 MATCH
（`engine.py:196-215`：<3 字的词改走 `_like_cond` 附加约束），所以 MATCH 里每个词项
都切得出 trigram。

切不出 token 的只有 **<3 字**的词项：trigram 分词器**不把标点当分隔符**（这正是
`docsize == max(0, len-2)` 对全部 224,822 行都成立的原因），所以 `"。。。"` 是一个
合法的 token（只是语料里没有，命中 0 行），而 `"管仲"` 切不出 token ——
FTS5 会把这种「空短语」从查询里**丢掉**：实测 `MATCH '"齊桓公" "管仲"'` 返回的正是
齊桓公 的 96 行，看起来像没写 管仲。因此计分时**不能**拿文本子串计数直接当 f，
得按「该词项是否真的产出了 token」来定。

公式（k1、b 是 FTS5 的编译期常量）：

    score = -idf * (f*(k1+1)) / (f + k1*(1-b) + k1*b*dl/avgdl)
    idf   = ln((nRow_eff - df + 0.5) / (df + 0.5))

f=0 时该词项不贡献分数（FTS5 也是这么做的：匹配行里 f≥1，但多词查询中某个词可以
在行内出现 0 次——trigram 索引下若查询含多个词，行可能只命中其中一部分）。
"""
from __future__ import annotations

import math
import sqlite3

K1 = 1.2
B = 0.75


# 管线重插的行（sqlite_store.py:196-197 的 SELECT，同文件 201-204 又数了一遍）。
# 这个谓词一变，两个总量就不再成立 —— 所以它是这里唯一的外部依赖，
# 并由 verify_against_sqlite() 兜底。
INSERTED_WHERE = ("kind='passage' AND normalized_text IS NOT NULL "
                  "AND normalized_text <> ''")


def inserted_ids(conn: sqlite3.Connection,
                 where: str = INSERTED_WHERE) -> list[int]:
    """被重插一遍的 passage_id。"""
    return [r[0] for r in conn.execute(
        f"SELECT passage_id FROM passages WHERE {where}")]


def _sum_dl(conn: sqlite3.Connection, fts: str) -> tuple[int, int]:
    """影子表字面值：(docsize 行数, Σdl)。"""
    rows = conn.execute(f"SELECT sz FROM '{fts}_docsize'").fetchall()
    if not rows:
        raise ValueError(f"{fts}_docsize 为空，无法推导总量")
    dls = [int.from_bytes(r[0], "big") for r in rows]
    return len(dls), sum(dls)


def totals_rebuilt_twice(fts: str, conn: sqlite3.Connection,
                         n_inserted: int | None = None) -> tuple[int, int]:
    """`rebuild` + 再插一遍建出来的表（管线的 passages_fts）。

    fts 是 FTS5 表名（影子表名一律 <fts>_docsize）。
    n_inserted 不传时按 INSERTED_WHERE 现数。
    """
    n_doc, sum_dl = _sum_dl(conn, fts)
    if n_inserted is None:
        n_inserted = conn.execute(
            f"SELECT count(*) FROM passages WHERE {INSERTED_WHERE}").fetchone()[0]
    # 重插行的 Σdl：dl 按行存，重插不翻倍，所以这部分只能按机制补一次。
    # 它等于全部 Σdl —— 未被重插的行文本必为空，没有非零 token。
    return n_doc + n_inserted, sum_dl * 2


def totals_single_build(fts: str, conn: sqlite3.Connection) -> tuple[int, int]:
    """只 INSERT 一次建出来的独立表（passages_bg）。"""
    return _sum_dl(conn, fts)


def avgdl_of(n_row_eff: int, n_token_eff: int) -> float:
    return n_token_eff / n_row_eff


def idf(n_row_eff: int, df: int) -> float:
    return math.log((n_row_eff - df + 0.5) / (df + 0.5))


def score(n_row_eff: int, avgdl: float, df: int, dl: int, f: int) -> float:
    """返回**正**分数（SQLite 的 bm25() 返回负值，越小越靠前）。f=0 返回 0。"""
    if f <= 0:
        return 0.0
    return idf(n_row_eff, df) * (f * (K1 + 1)) / (
        f + K1 * (1 - B) + K1 * B * dl / avgdl)


def docsize(fts: str, conn: sqlite3.Connection) -> dict[int, int]:
    """{rowid: dl}。"""
    return {i: int.from_bytes(b, "big")
            for i, b in conn.execute(f"SELECT id, sz FROM '{fts}_docsize'")}


def verify_against_sqlite(fts: str, conn: sqlite3.Connection,
                          terms: tuple[str, ...], n_row: int, n_token: int,
                          sample: int = 400, tol: float = 1e-9) -> list[str]:
    """拿真实 bm25() 对拍本方公式，返回不一致的明细（空列表 = 全部一致）。

    n_row / n_token 由调用方按建表方式选 `totals_rebuilt_twice` 或
    `totals_single_build` 传入 —— 这就是本函数要验的东西：选错就全错。
    df 由 FTS 自己数（不靠扫字符串）。df=0 的词项 FTS5 不参与计分，跳过。
    """
    avgdl = avgdl_of(n_row, n_token)
    dls = docsize(fts, conn)
    bad: list[str] = []
    for term in terms:
        df = conn.execute(f"SELECT count(*) FROM {fts} WHERE {fts} MATCH ?",
                          (f'"{term}"',)).fetchone()[0]
        if df == 0:
            continue
        got = conn.execute(f"SELECT rowid, bm25({fts}) FROM {fts} "
                           f"WHERE {fts} MATCH ? ORDER BY rowid LIMIT ?",
                           (f'"{term}"', sample)).fetchall()
        for rid, sc in got:
            text = conn.execute("SELECT normalized_text FROM passages WHERE passage_id=?",
                                (rid,)).fetchone()
            if text is None:
                continue
            f_cnt = (text[0] or "").count(term)
            calc = score(n_row, avgdl, df, dls.get(rid, 0), f_cnt)
            if abs(calc + sc) > tol * max(1.0, abs(sc)):
                bad.append(f"{term} rowid={rid} dl={dls.get(rid)} f={f_cnt} "
                           f"sqlite={sc:.9f} 公式={-calc:.9f}")
    return bad
