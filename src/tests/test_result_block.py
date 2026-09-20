"""第三阶段 Step 7 —— Result Block（史料片段）组装与 API 契约测试。

分两部分：
  TestResultBlockUnit      小库/合成数据就能验的组装规则（不依赖真实语料）
  TestResultBlockApi       HTTP 契约（真实的本地 API，只读库）

真实语料上的回归（齐桓公 / 管仲 / 黄帝）在 tests/test_result_block_real.py。

不变量（任务书 §三 / §八 / §十 / §十二 / §二十四）：
  * 片段正文 = 若干 kind='passage' 记录的 text_orig **依序相接**，一字不改
  * 绝不跨 file_id；绝不混 layer；结构键变化即止
  * 合并后 total ≤ hit_total（同段多命中只展示一次）
"""
import json
import sqlite3
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import main as api_main                                # noqa: E402
from scripts.pipeline import config                             # noqa: E402
from search import result_block as RB                           # noqa: E402

HAS_DB = config.DB_PATH.is_file()
QIHUANGONG = "齐桓公"


def ro_conn():
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


# --------------------------------------------------------------- 合成小库

SCHEMA = """
CREATE TABLE books (book_id TEXT PRIMARY KEY, title TEXT, edition TEXT, family TEXT);
CREATE TABLE files (file_id INTEGER PRIMARY KEY, book_id TEXT, file_name TEXT,
                    file_no INTEGER, origin_path TEXT);
CREATE TABLE passages (
  passage_id INTEGER PRIMARY KEY, file_id INTEGER, book_id TEXT,
  row_no INTEGER, seq INTEGER, kind TEXT, layer TEXT, status TEXT,
  juan TEXT, section TEXT, subsection TEXT, division TEXT, ab TEXT,
  text_orig TEXT, normalized_text TEXT, source_ref_json TEXT,
  pb_block TEXT, pb_page TEXT, pb_side TEXT);
CREATE TABLE passages_fts (passage_id UNINDEXED);
-- 第四阶段：段号窄派生表。真实库里由建库时算好；合成库里由 make_db 用
-- _src_paragraph（同一个函数）从 passages.source_ref_json 派生，口径一致。
CREATE TABLE src_paragraphs (file_id INTEGER, row_no INTEGER, paragraph_code TEXT);
CREATE INDEX idx_srcpara ON src_paragraphs(file_id, row_no);
-- 第六点一阶段：篇名区间表。`passages.section` 只标在标题行上、不向下传播，
-- 归属关系在这里（first_row 起，到同文件下一个 first_row 为止）。
CREATE TABLE sections (
  section_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT, file_id INTEGER,
  label TEXT, division TEXT, first_row INTEGER, status TEXT);
"""


def _p(pid, fid, row, seq, text, kind="passage", layer="main",
       section=None, subsection=None, ab=None, juan="卷一", src=None,
       pb_page=None, pb_side=None):
    return (pid, fid, "KR1e0001", row, seq, kind, layer, "ok", juan, section,
            subsection, None, ab, text, text, src, None, pb_page, pb_side)


def make_db(rows, files=None, books=None, sections=None):
    """内存库：只放组装需要的表与列（引擎不参与，命中直接给）。

    sections 是 [(file_id, label, first_row[, division])]，book_id 由 files 表推。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO books VALUES ('KR1e0001','尚書','tls','tls')")
    for r in (files or [(1, "f1"), (2, "f2")]):
        conn.execute("INSERT INTO files VALUES (?,?,?,?,?)",
                     (r[0], "KR1e0001", r[1], r[0], f"kanripo/{r[1]}.txt"))
    for r in books or []:
        conn.execute("INSERT INTO books VALUES (?,?,?,?)", r)
    for s in sections or []:
        fid, label, first = s[0], s[1], s[2]
        div = s[3] if len(s) > 3 else None
        bid = conn.execute("SELECT book_id FROM files WHERE file_id = ?",
                           (fid,)).fetchone()[0]
        conn.execute("INSERT INTO sections (book_id, file_id, label, division,"
                     " first_row, status) VALUES (?,?,?,?,?,'ok')",
                     (bid, fid, label, div, first))
    conn.executemany(
        "INSERT INTO passages VALUES (" + ",".join("?" * 19) + ")", rows)
    # 段号窄表：与建库侧同一条规则（_src_paragraph），只不过数据源是合成库
    for r in conn.execute("SELECT file_id, row_no, source_ref_json FROM passages"
                          " WHERE source_ref_json IS NOT NULL"):
        code = RB._src_paragraph(r["source_ref_json"])
        if code is not None:
            conn.execute("INSERT INTO src_paragraphs VALUES (?,?,?)",
                         (r["file_id"], r["row_no"], code))
    conn.commit()
    return conn


def hit(pid, fid, row, seq, score=0.0):
    return (pid, fid, row, seq, score)


class TestResultBlockUnit(unittest.TestCase):
    """组装规则：边界、合并、排序、出口结构。"""

    def blocks(self, conn, hits, mode="standard"):
        return RB.build_result_blocks(conn.cursor(), hits, mode)["blocks"]

    # ---- 结构键边界 ----
    def test_stops_at_section_change(self):
        conn = make_db([
            _p(1, 1, 1, 1, "甲甲甲甲甲", section="堯典"),
            _p(2, 1, 2, 1, "乙乙乙乙乙", section="堯典"),
            _p(3, 1, 3, 1, "丙丙丙丙丙", section="舜典"),   # section 变 → 边界
            _p(4, 1, 4, 1, "丁丁丁丁丁", section="舜典"),
        ])
        b = self.blocks(conn, [hit(1, 1, 1, 1)])[0]
        self.assertEqual(b["passage_ids"], [1, 2])         # 不含 3/4
        self.assertEqual(b["text"], "甲甲甲甲甲乙乙乙乙乙")

    def test_stops_at_layer_change(self):
        conn = make_db([
            _p(1, 1, 1, 1, "正文一", layer="main"),
            _p(2, 1, 2, 1, "正文二", layer="main"),
            _p(3, 1, 3, 1, "注文一", layer="commentary_candidate"),
            _p(4, 1, 4, 1, "注文二", layer="commentary_candidate"),
        ])
        b = self.blocks(conn, [hit(1, 1, 1, 1)])[0]
        self.assertEqual(b["passage_ids"], [1, 2])
        self.assertEqual(b["layer"], "main")

    def test_never_crosses_file(self):
        conn = make_db([
            _p(1, 1, 1, 1, "第一篇"),
            _p(9, 2, 2, 1, "第二篇"),      # 另一文件，紧邻 row_no 也不得越界
        ])
        b = self.blocks(conn, [hit(1, 1, 1, 1)])[0]
        self.assertEqual(b["passage_ids"], [1])
        self.assertEqual(b["file_id"], 1)

    def test_stops_at_subsection_and_ab(self):
        conn = make_db([
            _p(1, 1, 1, 1, "傳文一", subsection="17.1", ab="B"),
            _p(2, 1, 2, 1, "傳文二", subsection="17.1", ab="B"),
            _p(3, 1, 3, 1, "經文", subsection="17.1", ab="A"),   # 经/传不混
            _p(4, 1, 4, 1, "傳文三", subsection="17.2", ab="B"),  # 条目号变
        ])
        b = self.blocks(conn, [hit(1, 1, 1, 1)])[0]
        self.assertEqual(b["passage_ids"], [1, 2])

    # ---- 解析元数据行 ----
    def test_parser_metadata_rows_pass_through(self):
        """`# src:` / `<pb:>` 行透明穿过：不打断扩展，也不进片段正文。"""
        conn = make_db([
            _p(1, 1, 1, 1, "前句。"),
            _p(2, 1, 2, 1, "# src: SHIJI 004.41.1", kind="comment", layer="structure"),
            _p(3, 1, 3, 1, "<pb:KR2a0001_tls_100-150a>", kind="page", layer="structure"),
            _p(4, 1, 4, 1, "後句。"),
        ])
        b = self.blocks(conn, [hit(4, 1, 4, 1)])[0]
        self.assertEqual(b["passage_ids"], [1, 4])       # 元数据行不在其中
        self.assertEqual(b["text"], "前句。後句。")       # 也没被写进正文
        self.assertNotIn("# src:", b["text"])
        self.assertNotIn("<pb:", b["text"])

    def test_src_paragraph_number_is_boundary(self):
        """史記体例：无 section 时，`# src:` 段号变化即段落边界。

        `# src:` 管辖它之后、下一条 `# src:` 之前的 passage 行；段号只取前两级，
        所以 004.42.1 与 004.42.3 同段。
        """
        def src(pid, row, code):
            return _p(pid, 1, row, 1, f"# src: SHIJI {code}", kind="comment",
                      layer="structure", src=json.dumps({"section_ref": f"{code}, ed. X 1959"}))
        conn = make_db([
            _p(1, 1, 1, 1, "四〇甲"),        # 首条 # src: 之前，无段落号证据
            src(101, 2, "004.40.1"),
            _p(2, 1, 3, 1, "四一甲"),
            _p(3, 1, 4, 1, "四一乙"),
            src(102, 5, "004.42.1"),
            _p(4, 1, 6, 1, "四二甲"),
            _p(5, 1, 7, 1, "四二乙"),
        ])
        b = self.blocks(conn, [hit(5, 1, 7, 1)])[0]
        self.assertEqual(b["passage_ids"], [4, 5])      # 只管到 004.42 的自己
        self.assertEqual(b["text"], "四二甲四二乙")
        b2 = self.blocks(conn, [hit(3, 1, 4, 1)])[0]
        # 回过头读也不越进 004.42；首条 # src: 之前的那行归入它后面那一段
        self.assertEqual(b2["passage_ids"], [1, 2, 3])

    def test_src_paragraph_not_using_dating_annotation(self):
        """不许到处抓数字：只认开头那一个段号（`# dating:` 那类注解一律无证据）。"""
        self.assertIsNone(RB._src_paragraph(json.dumps({"section_ref": "dating: 6220卿有札書"})))
        self.assertIsNone(RB._src_paragraph(json.dumps({"section_ref": "no digits here"})))
        self.assertIsNone(RB._src_paragraph(json.dumps({"section_ref": "ZUO Xi 17.5.6 (643 B.C.)"})))
        self.assertIsNone(RB._src_paragraph(json.dumps({"section_ref": "5"})), "孤立数字不算段号")
        self.assertEqual(RB._src_paragraph(json.dumps({"section_ref": "004.41.2, ed. X"})), "004.41")
        self.assertEqual(RB._src_paragraph(json.dumps({"section_ref": "ZUO 17.5.6 (643 B.C.)"})), "17.5")
        self.assertEqual(RB._src_paragraph(json.dumps({"section_ref": "SHIJI 28.70.3 1393/94"})), "28.70")
        self.assertIsNone(RB._src_paragraph(None))
        self.assertIsNone(RB._src_paragraph("{不是 JSON"))

    def test_structure_field_beats_src_number(self):
        """左傳/尚書 的 section 比 `# src:` 更粗：有结构字段时不按段号切。"""
        conn = make_db([
            _p(1, 1, 1, 1, "僖公十七年一", section="僖公十七年"),
            _p(2, 1, 2, 1, "僖公十七年二", section="僖公十七年"),
            _p(3, 1, 3, 1, "# src: ZUO Xi 17.5.6", kind="comment", layer="structure",
               src=json.dumps({"section_ref": "ZUO Xi 18.1.1"})),   # 更细的段号
            _p(4, 1, 4, 1, "僖公十七年三", section="僖公十七年"),
        ])
        b = self.blocks(conn, [hit(1, 1, 1, 1)])[0]
        self.assertEqual(b["passage_ids"], [1, 2, 4])   # 段号没有把它切碎

    # ---- 合并 ----
    def test_adjacent_hits_merge_into_one_block(self):
        conn = make_db([
            _p(1, 1, 1, 1, "甲乙丙"),
            _p(2, 1, 2, 1, "丁戊己"),
            _p(3, 1, 3, 1, "庚辛壬"),
        ])
        blocks = self.blocks(conn, [hit(1, 1, 1, 1), hit(2, 1, 2, 1), hit(3, 1, 3, 1)])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["match_count"], 3)
        self.assertEqual(blocks[0]["n_passages"], 3)

    def test_merged_text_is_exact_concatenation(self):
        conn = make_db([
            _p(1, 1, 1, 1, "甲¶"), _p(2, 1, 2, 1, "乙¶"), _p(3, 1, 3, 1, "丙¶"),
        ])
        b = self.blocks(conn, [hit(1, 1, 1, 1), hit(3, 1, 3, 1)])[0]
        rows = conn.execute(
            "SELECT text_orig FROM passages WHERE passage_id IN (%s) ORDER BY row_no, seq"
            % ",".join("?" * len(b["passage_ids"])), b["passage_ids"]).fetchall()
        self.assertEqual(b["text"], "".join(r["text_orig"] for r in rows))

    # ---- 边界与硬上限 ----
    def test_max_chars_is_hard(self):
        conn = make_db([_p(i, 1, i, 1, "字" * 100) for i in range(1, 31)])
        b = self.blocks(conn, [hit(15, 1, 15, 1)], mode="short")[0]
        self.assertLessEqual(b["n_chars"], RB.MODE_LIMITS["short"]["max_chars"])

    def test_mode_length_monotonic(self):
        conn = make_db([_p(i, 1, i, 1, "字" * 50) for i in range(1, 61)])
        lens = [self.blocks(conn, [hit(30, 1, 30, 1)], mode=m)[0]["n_chars"]
                for m in ("short", "standard", "long")]
        self.assertLess(lens[0], lens[1])
        self.assertLess(lens[1], lens[2])

    def test_file_edges_do_not_wrap(self):
        conn = make_db([_p(1, 1, 1, 1, "首句"), _p(2, 1, 2, 1, "次句")])
        b = self.blocks(conn, [hit(1, 1, 1, 1)])[0]
        self.assertEqual(b["row_first"], 1)
        self.assertFalse(b["more_before"])             # 已到文件开头

    # ---- 出口结构 ----
    def test_block_shape(self):
        conn = make_db([
            _p(1, 1, 1, 1, "甲", pb_page="100", pb_side="a"),
            _p(2, 1, 2, 1, "乙", pb_page="101", pb_side="b"),
        ])
        b = RB._public_block(self.blocks(conn, [hit(1, 1, 1, 1)])[0])
        for k in ("block_id", "hit_passage_id", "file_id", "row_first", "row_last",
                  "passage_ids", "match_count", "text", "juan", "section", "layer",
                  "pb_first", "pb_last", "n_passages", "n_chars",
                  "more_before", "more_after", "first_passage_id", "last_passage_id"):
            self.assertIn(k, b, f"缺字段 {k}")
        self.assertEqual(b["first_passage_id"], 1)
        self.assertEqual(b["last_passage_id"], 2)
        self.assertEqual((b["pb_first"], b["pb_last"]), ("100a", "101b"))
        self.assertNotIn("rows", b)          # 内部中间态不外泄（也不可 JSON 化）
        self.assertNotIn("marks", b)
        self.assertNotIn("score", b)

    def test_reach_edges_detects_truncation(self):
        """被展示长度截断（而非读到尽头）时才提示可展开。"""
        conn = make_db([_p(i, 1, i, 1, "字" * 200) for i in range(1, 11)])
        blocks = self.blocks(conn, [hit(5, 1, 5, 1)], mode="short")
        self.assertTrue(blocks[0]["more_after"] or blocks[0]["more_before"])
        # 短到能全读下时，两侧都到头 → 不给展开按钮
        conn2 = make_db([_p(1, 1, 1, 1, "甲"), _p(2, 1, 2, 1, "乙")])
        b2 = self.blocks(conn2, [hit(1, 1, 1, 1)], mode="short")[0]
        self.assertFalse(b2["more_before"])
        self.assertFalse(b2["more_after"])


# --------------------------------------------------------------- 篇名检索

FILLER = "其言曰，古之為道者，貴一而賤萬，故能成其大。" * 3       # 每行约 66 字
# 篇名区间要**隔得开**：同文件里两篇的开头若落在同一屏（standard 900 字）内，
# 组装时会被合并成一块——那是真实且正确的行为（一片里连着两篇开头），但验不到
# 「篇名命中」的排序与归属。所以这里每行给足 264 字，一行就顶掉近三分之一的屏。
FILLER_LONG = "其言曰，古之為道者，貴一而賤萬，故能成其大。" * 12


def section_db():
    """合成一文件五篇：标题行有 section 值，正文行 section 为 NULL。

    这正是真实史記/國語的样子——`passages.section` 只标在标题行上、不向下传播，
    所以「这一段的篇名是什么」必须从 sections.first_row 区间推。

    正文行一律用不含任何篇名的填充文字，好让「篇名命中」单独可测；篇名与正文
    撞车的情形另由 dedup_db() 造。
    """
    rows, secs, r = [], [], 1
    for label in ("五帝本紀", "夏本紀", "秦本紀", "秦始皇本紀", "禮書"):
        rows.append(_p(r, 1, r, 0, label + "第", kind="heading", section=label))
        secs.append((1, label, r))                   # 区间从标题行起
        r += 1
        for _ in range(8):
            rows.append(_p(r, 1, r, 0, FILLER_LONG))
            r += 1
    return make_db(rows, files=[(1, "f1")], sections=secs)


def dedup_db():
    """篇名出现在**别的篇的正文里**：五帝本紀 的正文提到「夏本紀」这个篇名。

    这个夹具在第六点二阶段**换了含义**，值得写下来：修复前正文块不受篇界约束、
    一路合并到装够字数为止，于是它跨过 row 5 的篇题、把 夏本紀 的篇首也吞进去，
    篇名锚点正好落在块里 → 去重逻辑把它标成 `both`。那时这条用例是绿的，绿在
    **一个跨篇界的块**上（正是 §24–26 要消灭的东西）。修复后两者各归各篇：
    正文块停在 五帝本紀 末行，篇名块单独列出，谁也不是 `both`。
    """
    rows = [
        _p(1, 1, 1, 0, "五帝本紀第一", kind="heading", section="五帝本紀"),
        _p(2, 1, 2, 0, FILLER),
        _p(3, 1, 3, 0, "夏本紀云云，此處言及篇名。" + FILLER),
        _p(4, 1, 4, 0, FILLER),
        _p(5, 1, 5, 0, "夏本紀第二", kind="heading", section="夏本紀"),
        _p(6, 1, 6, 0, "夏禹，名曰文命。" + FILLER),
        _p(7, 1, 7, 0, FILLER),
    ]
    return make_db(rows, files=[(1, "f1")],
                   sections=[(1, "五帝本紀", 1), (1, "夏本紀", 5)])


def dedup_same_section_db():
    """篇名出现在**自己这一篇的正文里**，且就在篇首锚点行上。

    这才是 `both` 的本义：这一块既是「夏本紀」这个篇名的命中点，又含正文命中。
    """
    rows = [
        _p(1, 1, 1, 0, "五帝本紀第一", kind="heading", section="五帝本紀"),
        _p(2, 1, 2, 0, FILLER),
        _p(3, 1, 3, 0, FILLER),
        _p(4, 1, 4, 0, FILLER),
        _p(5, 1, 5, 0, "夏本紀第二", kind="heading", section="夏本紀"),
        _p(6, 1, 6, 0, "夏本紀云云，此處言及篇名。" + FILLER),
        _p(7, 1, 7, 0, FILLER),
    ]
    return make_db(rows, files=[(1, "f1")],
                   sections=[(1, "五帝本紀", 1), (1, "夏本紀", 5)])


class TestSectionMatchUnit(unittest.TestCase):
    """篇名命中、去重、match_type、上限——都在合成库上钉死。"""

    def search(self, conn, q, mode="standard", page_size=100):
        return RB.search_result_blocks(conn.cursor(), q, None, None, 1,
                                       page_size, mode)

    def setUp(self):
        self.conn = section_db()

    def test_section_hit_anchors_at_first_body_row(self):
        """锚点是区间内**首条正文**，不是标题行——标题行不进片段正文。"""
        d = self.search(self.conn, "夏本紀")
        sec = [b for b in d["results"] if b["match_type"] == "section"]
        self.assertTrue(sec, "篇名「夏本紀」应当有篇名命中")
        b = sec[0]
        self.assertEqual(b["section"], "夏本紀")
        self.assertEqual(b["row_first"], 11)           # 10 是标题行，正文从 11 起
        self.assertNotIn("夏本紀第", b["text"])        # 标题不进正文

    def test_section_label_backfills_text_block(self):
        """正文行的 section 列是 NULL，块的 section 由区间推出来（否则读不出「哪一篇」）。"""
        d = self.search(self.conn, "古之為道者")
        self.assertTrue(d["results"])
        self.assertNotIn("夏本紀", d["results"][0]["text"])
        self.assertIn(d["results"][0]["section"],
                      {"五帝本紀", "夏本紀", "秦本紀", "秦始皇本紀"})

    def test_section_hits_read_in_book_order(self):
        """「本紀」命中四篇，展示按书中行序——一篇篇顺着读，不按匹配度跳。"""
        d = self.search(self.conn, "本紀")
        labels = [b["section"] for b in d["results"]]
        self.assertEqual(labels, ["五帝本紀", "夏本紀", "秦本紀", "秦始皇本紀"])
        self.assertTrue(all(b["match_type"] == "section" for b in d["results"]),
                        "篇名不在正文里，不该出现正文命中")

    def test_text_hits_come_before_section_hits(self):
        """§6：正文命中排在篇名命中之前。"""
        d = self.search(dedup_db(), "夏本紀")
        rank = {"text": 0, "both": 1, "section": 2}
        kinds = [b["match_type"] for b in d["results"]]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: rank[k]))

    def test_section_dedup_marks_both_without_duplicate(self):
        """篇名块与正文块撞在同一条记录上 → 合成一块并标 both，不出现重复 Passage。"""
        d = self.search(dedup_same_section_db(), "夏本紀")
        both = [b for b in d["results"] if b["match_type"] == "both"]
        self.assertEqual(len(both), 1, "「夏本紀」既是篇名又是正文，应当正好一块标 both")
        pids = [p for b in d["results"] for p in b["passage_ids"]]
        self.assertEqual(len(pids), len(set(pids)), "跨块出现重复的 passage")

    def test_section_dedup_does_not_bridge_two_sections(self):
        """篇名出现在**别篇**正文里：不合并成 both，正文块也不许跨过篇题。

        第六点二阶段跨篇界修复的正向断言（合成数据版）：修复前两个块被合成一个
        跨篇界的块，修复后各归各篇。
        """
        d = self.search(dedup_db(), "夏本紀")
        self.assertEqual([b["match_type"] for b in d["results"]], ["text", "section"])
        text, sec = d["results"]
        self.assertEqual((text["row_first"], text["row_last"]), (2, 4),
                         "正文块应当停在 五帝本紀 的末行，不吞掉下一篇的篇首")
        self.assertEqual(text["section"], "五帝本紀")
        self.assertEqual(sec["section"], "夏本紀")
        self.assertEqual(sec["row_first"], 6)

    def _crowded_db(self):
        """MAX_SECTION_BLOCKS 个长名 + 1 个精确匹配，且精确匹配垫在文件最末。"""
        n = RB.MAX_SECTION_BLOCKS
        rows, secs = [], []
        for i in range(n):
            rows.append(_p(i + 1, 1, i + 1, 0, f"共名篇第{i}"))
            secs.append((1, f"共名篇長名{i:02d}", i + 1))    # 长名占满前 50 位
        rows.append(_p(n + 1, 1, n + 1, 0, "結尾一篇"))
        secs.append((1, "共名篇", n + 1))                    # 精确匹配垫底
        return make_db(rows, files=[(1, "f1")], sections=secs)

    def test_section_truncated_flag(self):
        """篇名命中超过 MAX_SECTION_BLOCKS 时如实报 section_truncated。"""
        d = self.search(self._crowded_db(), "共名篇")
        self.assertTrue(d["section_truncated"], "命中超过上限时必须如实标注")

    def test_section_truncation_keeps_the_best(self):
        """截断按匹配度而非文件序：精确匹配即使垫底也要活下来。

        否则排在第 51 位的那篇恰好就是用户要找的那篇，界面却只说「结果太多」。
        这里直接验锚点集合——展示层会把相邻锚点并成一个块（那是另一回事）。
        """
        conn = self._crowded_db()
        idx = RB._section_index(conn.cursor())
        hits, trunc = RB._section_hits(conn.cursor(), "共名篇", None, None, idx)
        self.assertTrue(trunc)
        self.assertEqual(len(hits), RB.MAX_SECTION_BLOCKS)
        self.assertEqual(hits[0][2], RB.MAX_SECTION_BLOCKS + 1,
                         "精确匹配的一条必须排在锚点表首位")

    def test_empty_when_neither_text_nor_section(self):
        """两边都没有才叫空结果；字段仍要齐备（前端不必判 undefined）。"""
        d = self.search(self.conn, "董卓")
        self.assertEqual(d["total"], 0)
        self.assertFalse(d["has_more"])
        self.assertFalse(d["section_truncated"])
        self.assertEqual(d["results"], [])


class TestBlockInvariantsUnit(unittest.TestCase):
    """第六点一阶段新增的三条不变量，合成库上先钉一遍。"""

    def setUp(self):
        # 密排正文：同一处段落里处处是「甲」，逼出「窗口装不下」「块会连成长链」两件事。
        # 行数必须超过 WINDOW_HARD_CAP，否则验不到分批取数。
        self.conn = make_db([_p(i, 1, i, 0, "甲" * 30)
                             for i in range(1, RB.WINDOW_HARD_CAP * 2 + 1)],
                            files=[(1, "f1")])

    def search(self, q, mode="short", page_size=100):
        return RB.search_result_blocks(self.conn.cursor(), q, None, None, 1,
                                       page_size, mode)

    def test_every_hit_lands_in_some_block(self):
        """**没有命中被静默丢掉。** 取数窗口只有 WINDOW_HARD_CAP 行，命中跨度
        超过它时必须分批取数；只取一屏的话窗口外的命中会人间蒸发（实测
        「將軍」1142 处命中只组装出 139 处），而 truncated 还是 False。"""
        cur = self.conn.cursor()
        hits = RB._fetch_hits(cur, "甲", None, None)
        self.assertGreater(len(hits), RB.WINDOW_HARD_CAP,
                           "用例前提：命中行跨度必须超过一屏，否则验不到分批")
        out = RB.build_result_blocks(cur, [(r["passage_id"], r["file_id"], r["row_no"],
                                            r["seq"], r["score"] or 0.0) for r in hits],
                                     "short", None)
        covered = {p for b in out["blocks"] for p in b["passage_ids"]}
        self.assertEqual({r["passage_id"] for r in hits} - covered, set(),
                         "有命中没有出现在任何片段里")

    def test_no_duplicate_passage_across_blocks(self):
        out = RB.build_result_blocks(
            self.conn.cursor(), [(i, 1, i, 0, 0.0) for i in range(1, 401)],
            "short", None)
        pids = [p for b in out["blocks"] for p in b["passage_ids"]]
        self.assertEqual(len(pids), len(set(pids)), "同一段正文被展示了两遍")

    def test_blocks_respect_mode_limits(self):
        """块是「一屏」，不是「这一段的全量」。合并相邻窗口也要守上限——实测
        不设限时一个 standard 块能长到 8768 字（上限 900）。"""
        for mode in ("short", "standard", "long"):
            lim = RB.MODE_LIMITS[mode]
            out = RB.build_result_blocks(
                self.conn.cursor(), [(i, 1, i, 0, 0.0) for i in range(1, 401)],
                mode, None)
            for b in out["blocks"]:
                with self.subTest(mode=mode, block=b["block_id"]):
                    self.assertLessEqual(b["n_chars"], lim["max_chars"])
                    self.assertLessEqual(b["n_passages"], lim["max_passages"])

    def test_every_block_contains_a_hit(self):
        """片段存在的理由是「让人看见命中」；不含命中的片段是白给的结果。"""
        out = RB.build_result_blocks(
            self.conn.cursor(), [(i, 1, i, 0, 0.0) for i in range(1, 401)],
            "short", None)
        for b in out["blocks"]:
            with self.subTest(block=b["block_id"]):
                self.assertIn("甲", b["text"])

    def test_has_more_is_consistent_with_total(self):
        d = self.search("甲", page_size=10)
        total = d["total"]
        self.assertEqual(d["has_more"], 10 < total)
        last = RB.search_result_blocks(self.conn.cursor(), "甲", None, None,
                                       (total + 9) // 10, 10, "short")
        self.assertFalse(last["has_more"], "末页不该说还有下一页")


# --------------------------------------------------------------- expand

class TestExpandBlock(unittest.TestCase):
    """按需展开：从片段边界向外取，不重叠、不跨段。"""

    def setUp(self):
        self.conn = make_db([
            _p(1, 1, 1, 1, "一"), _p(2, 1, 2, 1, "二"), _p(3, 1, 3, 1, "三"),
            _p(4, 1, 4, 1, "四"), _p(5, 1, 5, 1, "五"), _p(6, 1, 6, 1, "六"),
            _p(7, 1, 7, 1, "七", section="別篇"),      # 同文件的另一篇 → 段落边界
        ])
        self.cur = self.conn.cursor()

    def test_expand_after_from_block_tail(self):
        r = RB.expand_block(self.cur, 3, "after", 2, None, 3)   # 从第 3 条往后
        self.assertEqual([x["passage_id"] for x in r["rows"]], [4, 5])
        self.assertEqual(r["added"], 2)
        self.assertFalse(r["reaches_tail"])
        self.assertEqual(r["next_after_passage_id"], 5)         # 下次的锚点

    def test_expand_stops_at_section_boundary(self):
        r = RB.expand_block(self.cur, 5, "after", 10, None, 5)
        self.assertEqual([x["passage_id"] for x in r["rows"]], [6])  # 不含第 7 条
        self.assertTrue(r["reaches_tail"])
        # 从第 7 条往回想读回上一篇，同样不许越界
        r2 = RB.expand_block(self.cur, 7, "before", 10, 7, None)
        self.assertEqual(r2["rows"], [])
        self.assertTrue(r2["reaches_head"])

    def test_expand_before_returns_reading_order(self):
        r = RB.expand_block(self.cur, 5, "before", 2, 5, None)
        self.assertEqual([x["passage_id"] for x in r["rows"]], [3, 4])
        self.assertFalse(r["reaches_head"])

    def test_expand_both(self):
        r = RB.expand_block(self.cur, 4, "both", 1)
        self.assertEqual([x["passage_id"] for x in r["rows"]], [3, 5])

    def test_expand_errors(self):
        with self.assertRaises(KeyError):
            RB.expand_block(self.cur, 999999, "after", 5)
        with self.assertRaises(ValueError):
            RB.expand_block(self.cur, 3, "sideways", 5)

    def test_expand_rejects_non_passage_record(self):
        conn = make_db([
            _p(1, 1, 1, 1, "正文"),
            _p(2, 1, 2, 1, "# src: X", kind="comment", layer="structure"),
        ])
        with self.assertRaises(ValueError):
            RB.expand_block(conn.cursor(), 2, "after", 5)

    def test_count_is_clamped(self):
        r = RB.expand_block(self.cur, 3, "after", 10 ** 6)
        self.assertLessEqual(r["count"], 100)


# --------------------------------------------------------------- HTTP 契约

@unittest.skipUnless(HAS_DB, "需先运行 python -m scripts.pipeline.run_all")
class TestResultBlockApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api_main.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return json.load(r)

    def code(self, path):
        try:
            self.get(path)
            return 200
        except urllib.error.HTTPError as e:
            return e.code

    def search(self, **kw):
        return self.get("/api/search?" + urllib.parse.urlencode(kw))

    def test_default_is_standard_block(self):
        """默认 mode=standard；total/hit_total **不写死**。

        精确值由语料决定，加书就变（第六点二阶段 齐桓公 74→126 块、96→151 命中）。
        这里验接口形状与量级；精确基线在 test_result_block_real.py 的 BASELINE，
        改语料后由 docs/phase6_2_report.md 的复现步骤重测。
        """
        d = self.search(q=QIHUANGONG)
        self.assertEqual(d["mode"], "standard")
        self.assertEqual(len(d["results"]), 20)
        self.assertGreater(d["hit_total"], 50, "齐桓公 是高频查询，命中不该是个小数字")
        self.assertLessEqual(d["total"], d["hit_total"],
                             "块是把命中并起来的，块数不可能多于命中数")

    def test_modes_change_block_granularity(self):
        """三种显示长度：越短块越多（装不下就断开），命中总数不受影响。"""
        short = self.search(q=QIHUANGONG, mode="short")
        long_ = self.search(q=QIHUANGONG, mode="long")
        self.assertGreater(short["total"], long_["total"],
                           "短模式装得少，应当切出更多块")
        # 命中总数与显示长度无关，必须一致
        self.assertEqual(short["hit_total"], long_["hit_total"])
        self.assertLess(short["limits"]["max_chars"], long_["limits"]["max_chars"])

    def test_bad_mode_and_book_400(self):
        self.assertEqual(self.code("/api/search?" + urllib.parse.urlencode(
            {"q": QIHUANGONG, "mode": "huge"})), 400)
        self.assertEqual(self.code("/api/search?" + urllib.parse.urlencode(
            {"q": QIHUANGONG, "book": "三字经"})), 400)

    def test_pagination_total_is_exact(self):
        """翻完所有页：页数之和 = total，末页不满，全程不重复——一页不落地走到底。"""
        first = self.search(q=QIHUANGONG, page_size=10, page=1)
        total = first["total"]
        ids, page = [], 1
        while True:
            d = self.search(q=QIHUANGONG, page_size=10, page=page)
            self.assertEqual(d["total"], total, "翻页时 total 变了")
            ids += [b["block_id"] for b in d["results"]]
            if not d["has_more"]:
                break
            page += 1
            self.assertLess(page, 200, "has_more 一直为真，翻不到头")
        self.assertEqual(len(ids), total, "逐页取回的片段数与 total 不符")
        self.assertEqual(len(ids), len(set(ids)), "翻页出现重复片段")
        self.assertEqual(len(set(ids)), total)

    def test_page_beyond_last_is_empty_not_an_error(self):
        """越界页返回空列表、total 不变、has_more 为假——不是异常，也不是「还有更多」。"""
        d = self.search(q=QIHUANGONG, page_size=10, page=999999)
        self.assertEqual(d["results"], [])
        self.assertEqual(d["total"], self.search(q=QIHUANGONG, page_size=10)["total"])
        self.assertFalse(d["has_more"])

    def test_block_expand_endpoint(self):
        b = self.search(q=QIHUANGONG, mode="long")["results"][0]
        if not (b["more_before"] or b["more_after"]):
            self.skipTest("该片段已读全，无需展开（不是失败）")
        d = self.get(f"/api/blocks/{b['hit_passage_id']}?direction=after&count=3"
                     f"&after_passage_id={b['last_passage_id']}")
        self.assertIn("rows", d)
        self.assertEqual(d["added"], len(d["rows"]))

    def test_block_expand_404_and_400(self):
        # 参数非法 → 400（先校验，不必查库）；记录不存在 → 404
        self.assertEqual(self.code("/api/blocks/999999999?direction=sideways"), 400)
        self.assertEqual(self.code("/api/blocks/999999999"), 404)
        d = self.search(q=QIHUANGONG, page_size=1)
        pid = d["results"][0]["hit_passage_id"]
        self.assertEqual(self.code(f"/api/blocks/{pid}?direction=sideways"), 400)


if __name__ == "__main__":
    unittest.main()
