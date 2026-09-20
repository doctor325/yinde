"""Step 6 — SQLite 载入与查询。

原则：
- 数据库是纯派生产物：可由 data/processed/parsed_*.jsonl + sha256 全量重建（幂等：先清空再装）。
- 原始史料仍在 library，只读；本模块不写 library。
- 逐行流式载入（史记 ~11 万条也不占内存）；juan/section 首次出现即记入 juans/sections。

表：books/editions/files/juans/sections/passages/source_references/kr_chars/import_runs
（schema 全文亦在 database/schema.sql，两者保持一致）
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from . import config

# 第四阶段：段号计算与检索侧共用同一个函数，避免建库/查询两套口径。
# search.result_block 不反向依赖 scripts.pipeline，这个 import 不成环。
from search.result_block import _src_paragraph

# 第六点三阶段：section 溯源字段的兜底档与词表都取自 records（唯一真源）
from .records import DEFAULT_SECTION_CONFIDENCE, DEFAULT_SECTION_METHOD

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
  book_id    TEXT PRIMARY KEY,          -- KR2e0001
  book_dir   TEXT NOT NULL,             -- guoyu
  title      TEXT,                      -- 國語（TITLE 原样）
  family     TEXT,                      -- tls | sbck
  edition    TEXT,                      -- BASEEDITION
  import_run TEXT
);
CREATE TABLE IF NOT EXISTS editions (
  edition_id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id    TEXT NOT NULL REFERENCES books(book_id),
  code       TEXT,                      -- tls / SBCK
  witness    TEXT,                      -- WITNESS 原值（tls 系为空）
  note       TEXT
);
CREATE TABLE IF NOT EXISTS files (
  file_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id    TEXT NOT NULL REFERENCES books(book_id),
  file_name  TEXT NOT NULL,
  file_no    INTEGER,
  kind       TEXT DEFAULT 'txt',
  juan_prop  TEXT,                      -- 头部 JUAN 原值
  meta_json  TEXT,                      -- 全部 metadata（含未知键）
  sha256     TEXT,
  origin_path TEXT,                      -- 相对 library 的路径（追溯）
  UNIQUE(book_id, file_name)
);
CREATE TABLE IF NOT EXISTS juans (
  juan_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id   TEXT NOT NULL REFERENCES books(book_id),
  file_id   INTEGER REFERENCES files(file_id),
  label     TEXT,                       -- 语义卷（左传：隱公；SBCK 头部 JUAN）
  first_row INTEGER                     -- 该卷首次出现的行号
);
CREATE TABLE IF NOT EXISTS sections (
  section_id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id    TEXT NOT NULL REFERENCES books(book_id),
  file_id    INTEGER REFERENCES files(file_id),
  label      TEXT,                      -- 篇/节题（堯典 / 五帝本紀 / 三代世表）
  division   TEXT,                      -- 史记类目 紀/表/書/世家/傳
  first_row  INTEGER,
  status     TEXT,
  -- 第六点三阶段：这条 section 是怎么来的 + 它的区间右端。
  -- last_row 是**冗余的**（= 下一条 section 的 first_row-1，末条 = 文件末行），
  -- 存下来是为了能一眼看出区间有没有裂口/重叠，而不是每次现推。
  detection_method TEXT,                -- header|title|first-occurrence|interval|metadata|override
  confidence REAL,
  last_row   INTEGER,
  note       TEXT
);
CREATE TABLE IF NOT EXISTS passages (
  passage_id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id    TEXT NOT NULL REFERENCES books(book_id),
  file_id    INTEGER NOT NULL REFERENCES files(file_id),
  row_no     INTEGER NOT NULL,          -- 文件内绝对行号（供原文回溯）
  seq        INTEGER,                   -- 文件内记录顺序
  kind       TEXT NOT NULL,             -- page/heading/comment/part/noise/passage
  layer      TEXT NOT NULL,
  status     TEXT NOT NULL,
  juan       TEXT,
  section    TEXT,
  subsection TEXT,
  division   TEXT,
  ab         TEXT,
  text_orig  TEXT NOT NULL,             -- 原文原样（含 <pb:>、¶、&KR...;）
  normalized_text TEXT,
  char_start INTEGER,                   -- SBCK 行内切分偏移（row_no, char_start 可重建整行）
  char_end   INTEGER,
  pb_raw     TEXT,
  pb_block   TEXT, pb_page TEXT, pb_side TEXT, pb_edition TEXT,
  special_chars_json TEXT,              -- ["&KR0632;", ...]
  source_ref_json TEXT,
  notes_json TEXT
);
CREATE TABLE IF NOT EXISTS source_references (
  ref_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id     INTEGER REFERENCES files(file_id),
  source_row_no INTEGER,                -- comment 记录所在行
  target_passage_id INTEGER REFERENCES passages(passage_id),  -- 其后的正文条（文件尾无正文则 NULL）
  raw         TEXT,
  src_text    TEXT,
  prefix      TEXT,
  section_ref TEXT
);
CREATE TABLE IF NOT EXISTS kr_chars (
  kr_code     TEXT PRIMARY KEY,
  count       INTEGER DEFAULT 0,
  first_file  TEXT
);
CREATE TABLE IF NOT EXISTS import_runs (
  run_id      TEXT PRIMARY KEY,
  ran_at      TEXT,
  library     TEXT,
  books       INTEGER,
  txt_files   INTEGER,
  n_records   INTEGER,
  status      TEXT
);
-- 第四阶段：`# src:` 段落号的**窄派生表**。
-- 用途只有一个：给 search/result_block.py 的 _FileCache.segments() 快速取段落区间。
-- 为什么要单开一张表而不是给 passages 加列/直接读 source_ref_json：
--   段落区间要在**每次检索**里按文件取一次。原做法从 passages 读全部 source_ref_json
--   再逐条 json.loads + 正则，实测 6 个大文件 56.1ms；换成这张窄表（无宽列、走索引）
--   后是 3.3ms —— 快 17 倍，省下的时间占一次完整查询的 40% 上下。
-- paragraph_code 由 search.result_block._src_paragraph() 在建库时算好，
-- 与检索侧**同一个函数**，不存在两套口径。无需保存 code 为 NULL 的行：
-- segments() 对它们本来就是 continue，存不存结果一样。
CREATE TABLE IF NOT EXISTS src_paragraphs (
  file_id        INTEGER NOT NULL REFERENCES files(file_id),
  row_no         INTEGER NOT NULL,   -- `# src:` 所在行（与 passages.row_no 同口径）
  paragraph_code TEXT NOT NULL       -- 段号前两级，如 '68.1'
);
CREATE INDEX IF NOT EXISTS idx_srcpara_file_row ON src_paragraphs(file_id, row_no);

CREATE INDEX IF NOT EXISTS idx_pas_file ON passages(file_id, seq);
CREATE INDEX IF NOT EXISTS idx_pas_layer ON passages(layer, status);
CREATE INDEX IF NOT EXISTS idx_pas_book  ON passages(book_id, kind);
CREATE INDEX IF NOT EXISTS idx_pas_file_row ON passages(file_id, row_no, seq);
-- 第二阶段：FTS5 全文索引在 rebuild() 末尾动态创建（见 _fts_build），此处不建表。
"""

# 表删除顺序遵循外键依赖（清空从子表到父表）
_TABLES = ("kr_chars", "src_paragraphs", "source_references", "passages", "sections",
           "juans", "editions", "files", "books", "import_runs")


# 第六点三阶段加进 sections 的列。**必须在这里补**：SCHEMA 用的是
# `CREATE TABLE IF NOT EXISTS`，对已存在的旧库它一句话都不执行——不加这几行，
# 旧库会带着「没有 detection_method 的 sections」继续用，之后任何一句
# `SELECT detection_method` 直接抛 `no such column`。ALTER 是 O(1) 且只加不改，
# 旧行的新列是 NULL（＝未标注，读侧按未标注处理），不需要重跑管线。
_SECTIONS_ADDED_COLUMNS = (
    ("detection_method", "TEXT"),
    ("confidence", "REAL"),
    ("last_row", "INTEGER"),
    ("note", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """把旧库的表结构补齐到当前 SCHEMA（只加列，绝不改列/删列）。"""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(sections)")}
    if not have:                     # 新库：executescript 已建好，无需补
        return
    for name, decl in _SECTIONS_ADDED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE sections ADD COLUMN {name} {decl}")
    conn.commit()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else config.DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _fts_build(conn: sqlite3.Connection, stats: dict) -> None:
    """第二阶段：FTS5 全文索引（独立 passages_fts 虚拟表，不改 passages 原文）。

    索引来源：passages.kind='passage' 行的 normalized_text（检索派生文本）。
    text_orig 一律不进入索引、永不修改；命中后展示与追溯仍走 passages 原行。

    分词器选择（在 search 模块说明同步）：
    - trigram（SQLite >= 3.34）：按 3 字连续窗口切分，任意 >=3 字的子串/短语可命中，
      适合 CJK 无空格语言；2 字及以下的查询走 search 模块的 LIKE 补充路径。
    - 若环境不支持 trigram（旧 SQLite），回退 unicode61（整段汉字算一个 token，
      仅支持整段或首词查询），由 stats["fts"]["tokenizer"] 记录实际采用者。
    """
    start = time.perf_counter()
    probe = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='_probe_fts'").fetchone()
    if probe:
        conn.execute("DROP TABLE _probe_fts")
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe_fts USING fts5(x, tokenize='trigram')")
        tokenizer = "trigram"
    except sqlite3.OperationalError:
        tokenizer = "unicode61"
    conn.execute("DROP TABLE IF EXISTS _probe_fts")
    conn.execute("DROP TABLE IF EXISTS passages_fts")
    if tokenizer == "unicode61":
        try:
            conn.execute("CREATE VIRTUAL TABLE _probe_fts USING fts5(x)")
            conn.execute("DROP TABLE _probe_fts")
        except sqlite3.OperationalError:
            stats["fts"] = {"ok": False, "tokenizer": None, "docs": 0,
                            "build_ms": round((time.perf_counter() - start) * 1000),
                            "note": "此环境不支持 FTS5，检索将回退 LIKE 全扫"}
            return
    conn.execute(
        f"CREATE VIRTUAL TABLE passages_fts USING fts5("
        f"normalized_text, content='passages', content_rowid='passage_id', "
        f"tokenize='{tokenizer}')")
    conn.execute("INSERT INTO passages_fts(passages_fts) VALUES('rebuild')")
    conn.execute(
        "INSERT INTO passages_fts(rowid, normalized_text) "
        "SELECT passage_id, normalized_text FROM passages "
        "WHERE kind='passage' AND normalized_text IS NOT NULL AND normalized_text <> ''")
    conn.commit()
    # external-content 表的 count(*) 会读 content 表（含全部记录）；文档数须与
    # 插入谓词一致，故从 content 侧数（与索引内容严格等价，且快）。
    docs = conn.execute(
        "SELECT count(*) FROM passages "
        "WHERE kind='passage' AND normalized_text IS NOT NULL AND normalized_text <> ''"
    ).fetchone()[0]
    stats["fts"] = {"ok": True, "tokenizer": tokenizer, "docs": docs,
                    "build_ms": round((time.perf_counter() - start) * 1000),
                    "note": "索引来源 normalized_text；text_orig 永不改动"}

    # ---- 二元组辅助索引 passages_bg ----
    # normalized_text 逐「相邻两字」切成 token（空白处断开）、unicode61 分词：
    # 2 字查询（管仲/苏秦/城濮…）由 LIKE 全扫（~350ms）变为 FTS 点查（~10ms），
    # 结果集合与 LIKE '%词%' 语义等价（同一字符流、同一窗口规则）。
    # 索引来源仍是 normalized_text；text_orig 不参与。重建全程约 +8s。
    bg_start = time.perf_counter()
    conn.execute("DROP TABLE IF EXISTS passages_bg")
    try:
        conn.execute("CREATE VIRTUAL TABLE passages_bg "
                     "USING fts5(bg, tokenize='unicode61')")
        n = lo = 0
        while True:
            rows = conn.execute(
                "SELECT passage_id, normalized_text FROM passages "
                "WHERE kind='passage' AND normalized_text IS NOT NULL "
                "AND normalized_text <> '' AND passage_id > ? "
                "ORDER BY passage_id LIMIT 20000", (lo,)).fetchall()
            if not rows:
                break
            lo = rows[-1][0]
            payload = []
            for pid, txt in rows:
                bgs = []
                for run in re.split(r"\s+", txt.strip()):
                    if len(run) >= 2:
                        bgs.extend(run[i:i + 2] for i in range(len(run) - 1))
                if bgs:
                    payload.append((pid, " ".join(bgs)))
            conn.executemany("INSERT INTO passages_bg(rowid, bg) VALUES(?, ?)",
                             payload)
            n += len(payload)
        conn.commit()
        stats["fts_bg"] = {"ok": True, "docs": n,
                           "build_ms": round((time.perf_counter() - bg_start) * 1000),
                           "note": "相邻两字 token；与 LIKE '%词%' 集合等价"}
    except sqlite3.OperationalError as e:      # 极旧环境缺 FTS5 时降级
        stats["fts_bg"] = {"ok": False, "docs": 0, "note": str(e)[:120]}


def rebuild(db_path: Path | None = None) -> dict:
    """Step 6 入口：data/processed/parsed_*.jsonl → SQLite 全量重建，返回统计。"""
    conn = connect(db_path)
    cur = conn.cursor()
    for t in _TABLES:
        cur.execute(f"DELETE FROM {t}")
    # 重置 AUTOINCREMENT，保证每次全量重建 file_id/passage_id 从 1 起且稳定
    cur.execute("DELETE FROM sqlite_sequence WHERE name IN "
                "(SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%AUTOINCREMENT%')")
    conn.commit()

    stats = {"books": 0, "files": 0, "records": 0, "passages": 0, "pending": 0,
             "kr_codes": 0, "kr_occurrences": 0, "src_refs": 0,
             "juans": 0, "sections": 0, "src_paragraphs": 0,
             "sections_by_method": {}}
    book_ids: set[str] = set()

    cur_file_db_id: int | None = None
    cur_seq = 0
    cur_juan: str | None = None          # 已入库的最近 juan/section（供首现判断）
    cur_section: str | None = None
    cur_section_id: int | None = None    # 当前这条 section 的行 id（用于回填 last_row）
    cur_last_row = 0                     # 本文件见过的最大行号（区间右端）
    last_src = None                      # 未挂出的 src comment: (source_row_no, ref dict)

    def close_section(end_row: int) -> None:
        """给当前 section 回填区间右端 = 它下面最后一行（区间模型，见 manifest.py）。"""
        nonlocal cur_section_id
        if cur_section_id is not None:
            cur.execute("UPDATE sections SET last_row=? WHERE section_id=?",
                        (end_row, cur_section_id))
            cur_section_id = None

    def close_file():
        nonlocal cur_file_db_id, cur_seq, cur_juan, cur_section, last_src, cur_last_row
        if last_src is not None:
            # 文件尾部注释没有后续正文：target 置 NULL，出处仍保留
            row, ref = last_src
            cur.execute(
                "INSERT INTO source_references(file_id,source_row_no,target_passage_id,"
                "raw,src_text,prefix,section_ref) VALUES(?,?,NULL,?,?,?,?)",
                (cur_file_db_id, row, ref.get("raw"), ref.get("src_text"),
                 ref.get("prefix"), ref.get("section_ref")))
            last_src = None
        # 本文件最后一条 section 覆盖到文件末行
        close_section(cur_last_row)
        cur_file_db_id, cur_seq, cur_juan, cur_section = None, 0, None, None
        cur_last_row = 0

    def ensure_file(book_id: str, book_dir: str, orig_file: str, meta: dict,
                    sha256: str, family: str | None = None,
                    edition: str | None = None) -> int:
        nonlocal cur_file_db_id, cur_seq, cur_juan, cur_section, last_src
        if (book_id, orig_file) != (getattr(ensure_file, "_key", None)):
            close_file()
            if book_id not in book_ids:
                cur.execute(
                    "INSERT INTO books(book_id,book_dir,title,family,edition) "
                    "VALUES(?,?,?,?,?)",
                    (book_id, book_dir,
                     meta.get("TITLE") if meta else None,
                     family, edition))
                book_ids.add(book_id)
                stats["books"] += 1
            m = re_match_file_no(orig_file)
            cur.execute(
                "INSERT OR IGNORE INTO files(book_id,file_name,file_no,juan_prop,meta_json,"
                "sha256,origin_path) VALUES(?,?,?,?,?,?,?)",
                (book_id, orig_file, m,
                 (meta or {}).get("JUAN"),
                 json.dumps(meta or {}, ensure_ascii=False),
                 sha256, f"{book_dir}/{orig_file}"))
            cur.execute("SELECT file_id FROM files WHERE book_id=? AND file_name=?",
                        (book_id, orig_file))
            cur_file_db_id = cur.fetchone()["file_id"]
            stats["files"] += 1
            cur_seq = 0
            ensure_file._key = (book_id, orig_file)
        return cur_file_db_id

    ensure_file._key = None

    for jl in sorted(config.PROCESSED_DIR.glob("parsed_*.jsonl")):
        with open(jl, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                rec = row["rec"]
                fid = ensure_file(row["book_id"], row["book_dir"],
                                  row["original_file"], row.get("file_header_meta"),
                                  row.get("sha256", ""), row.get("family"),
                                  row.get("edition"))
                cur_seq += 1
                rno = rec["row_no"]
                if rno > cur_last_row:
                    cur_last_row = rno

                # juan/section 首现 → 记行（label 在同一文件内第一次出现时）
                rj = rec.get("juan") or None
                rs = rec.get("section") or None
                if rj and rj != cur_juan:
                    cur.execute("INSERT INTO juans(book_id,file_id,label,first_row) "
                                "VALUES(?,?,?,?)", (row["book_id"], fid, rj, rno))
                    stats["juans"] += 1
                    cur_juan = rj
                if rs and rs != cur_section:
                    # 上一条 section 到此为止：右端 = 本行前一行
                    close_section(rno - 1)
                    method = rec.get("section_method") or DEFAULT_SECTION_METHOD
                    conf = rec.get("section_confidence")
                    cur.execute(
                        "INSERT INTO sections(book_id,file_id,label,division,first_row,"
                        "status,detection_method,confidence) VALUES(?,?,?,?,?,?,?,?)",
                        (row["book_id"], fid, rs,
                         rec.get("division"), rno, rec.get("status"), method,
                         DEFAULT_SECTION_CONFIDENCE if conf is None else conf))
                    cur_section_id = cur.lastrowid
                    stats["sections"] += 1
                    cur_section = rs
                    stats.setdefault("sections_by_method", {})
                    stats["sections_by_method"][method] = \
                        stats["sections_by_method"].get(method, 0) + 1

                pb = rec.get("pb") or {}
                cur.execute(
                    "INSERT INTO passages(book_id,file_id,row_no,seq,kind,layer,status,juan,"
                    "section,subsection,division,ab,text_orig,normalized_text,char_start,"
                    "char_end,pb_raw,pb_block,pb_page,pb_side,pb_edition,special_chars_json,"
                    "source_ref_json,notes_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "?,?,?,?,?,?,?,?)",
                    (row["book_id"], fid, rno, cur_seq, rec["kind"], rec["layer"],
                     rec["status"], rec.get("juan"), rec.get("section"),
                     rec.get("subsection"), rec.get("division"), rec.get("ab"),
                     rec["text_orig"], rec.get("normalized_text"),
                     rec.get("char_start"), rec.get("char_end"),
                     rec.get("pb_raw"), pb.get("block"), pb.get("page"),
                     pb.get("side"), pb.get("edition"),
                     json.dumps(rec.get("special_chars") or [], ensure_ascii=False)
                     if rec.get("special_chars") else None,
                     json.dumps(rec.get("source_reference"), ensure_ascii=False)
                     if rec.get("source_reference") else None,
                     json.dumps(rec.get("notes") or [], ensure_ascii=False)
                     if rec.get("notes") else None))
                pid = cur.lastrowid
                stats["records"] += 1
                if rec["kind"] == "passage":
                    stats["passages"] += 1
                if rec["status"].startswith("pending"):
                    stats["pending"] += 1
                n_kr = len(rec.get("special_chars") or [])
                stats["kr_occurrences"] += n_kr

                if rec["kind"] == "comment" and rec.get("source_reference"):
                    last_src = (rno, rec["source_reference"])
                    # 第四阶段：把 `# src:` 的段号算好存进窄表，检索时不必再解析 JSON。
                    # 用检索侧同一个函数（search.result_block._src_paragraph），
                    # 保证建库与查询**不可能出现两套口径**。
                    code = _src_paragraph(
                        json.dumps(rec["source_reference"], ensure_ascii=False))
                    if code is not None:
                        cur.execute(
                            "INSERT INTO src_paragraphs(file_id,row_no,paragraph_code) "
                            "VALUES(?,?,?)", (fid, rno, code))
                        stats["src_paragraphs"] += 1
                elif rec["kind"] == "passage" and last_src is not None:
                    srow, ref = last_src
                    cur.execute(
                        "INSERT INTO source_references(file_id,source_row_no,"
                        "target_passage_id,raw,src_text,prefix,section_ref) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (fid, srow, pid, ref.get("raw"), ref.get("src_text"),
                         ref.get("prefix"), ref.get("section_ref")))
                    stats["src_refs"] += 1
                    last_src = None
    close_file()

    # kr 汇总（由已入库的 special_chars_json 聚合）
    cur.execute("""
        INSERT INTO kr_chars(kr_code, count, first_file)
        SELECT value AS kr, COUNT(*), MIN(f.file_name)
        FROM passages p
        JOIN files f ON f.file_id = p.file_id
        JOIN json_each(p.special_chars_json)
        GROUP BY value""")
    stats["kr_codes"] = cur.rowcount

    # 第二阶段：FTS5 全文索引（tokenizer 探测 + 填充；失败则 note 说明并回退）
    _fts_build(conn, stats)

    cur.execute(
        "INSERT INTO import_runs(run_id,ran_at,library,books,txt_files,n_records,status) "
        "VALUES(?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, datetime.datetime.now().isoformat(timespec="seconds"),
         str(config.LIBRARY_DIR), len(book_ids), stats["files"],
         stats["records"], "ok"))
    conn.commit()
    conn.close()
    return stats


def re_match_file_no(name: str) -> int | None:
    """KRxxxx_NNN.txt -> NNN；其他返回 None（与 kanripo_header.file_no_of 同规则）。"""
    import re
    m = re.search(r"_(\d+)\.txt$", name)
    return int(m.group(1)) if m else None
