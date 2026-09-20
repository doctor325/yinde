-- HistoryAI 数据库结构（SQLite）
-- 说明：
--   * 本 schema 是 scripts/pipeline/sqlite_store.py 中 SCHEMA 的镜像，两者需保持一致；
--     sqlite_store.connect() 每次启动都会执行同一份 CREATE TABLE IF NOT EXISTS。
--   * 数据库为纯派生产物：可由 data/processed/parsed_*.jsonl 全量重建
--     （python -m scripts.pipeline.run_all）。
--   * 原始史料一律不写入本库；files.origin_path 指向 library 内相对路径用于回溯。

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
  detection_method TEXT,                -- header|title|first-occurrence|interval|metadata|override
  confidence REAL,                      -- 1.0 人工/属性，0.9 结构正则，0.8 显式标记，0.5 兜底
  last_row   INTEGER,                   -- 区间右端（下一条 first_row-1；末条=文件末行）
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
-- 段落区间要在**每次检索**里按文件取一次；原做法从 passages 读全部 source_ref_json
-- 再逐条 json.loads + 正则（实测 6 个大文件 56.1ms），换成这张窄表后是 3.3ms。
-- paragraph_code 由 search.result_block._src_paragraph() 在建库时算好，
-- 与检索侧**同一个函数**，不存在两套口径。
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
-- FTS5 全文索引（passages_fts）由 sqlite_store._fts_build 在 rebuild() 末尾动态创建：
--   CREATE VIRTUAL TABLE passages_fts USING fts5(
--     normalized_text, content='passages', content_rowid='passage_id',
--     tokenize='trigram' | 'unicode61' 按环境探测)
-- 内容仅 kind='passage' 行的 normalized_text；text_orig 永不改动。
-- 2 字词快路：passages_bg（相邻两字 token，unicode61）同样由 _fts_build 动态创建，
-- 与 LIKE '%词%' 集合等价；两表均为派生产物，可随 rebuild() 随时重建。
