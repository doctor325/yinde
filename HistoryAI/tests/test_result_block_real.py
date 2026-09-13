"""第三阶段 Step 8 —— Result Block 在**真实语料**上的回归。

与 test_result_block.py 的分工：那边用合成小库钉住规则，这边用真实库钉住
「跑起来是什么样」。断言全部基于**不变量**，不硬编码 passage_id（语料一重建
就变）；硬编码的只有已确认过的命中数基数（与 test_search_api.py 同源）。

覆盖三个基准词：齐桓公（trigram 路径）、管仲、黄帝（bigram 路径）。

同时验证任务书 §三.3 的零写入约束：HistoryLibrary/kanripo 下所有文件在检索
前后 mtime 与大小均不得变化。
"""
import os
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline import config                             # noqa: E402
from search import result_block as RB                           # noqa: E402
from search import zh                                           # noqa: E402

HAS_DB = config.DB_PATH.is_file()

# (查询词, 原始命中数, {模式: 片段数})——已人工核对过的基数。
# 第六点一阶段重测：片段数比之前多得多（齐桓公 standard 23 → 74）。不是退化，
# 是以前算错了——取数窗口只有 600 行，一个文件里超出窗口的命中全部没进组装
# （实测「將軍」1142 处命中只组装出 139 处），片段数因此被系统性低估。
#
# 第六点二阶段测了两轮，两轮**改的东西不一样**，不能混着看：
#   ① 先修跨篇界（只动块边界，不动 Passage）：命中数一个没变，片段数两个方向
#      都有——黄帝 short 80→115（篇界把并过头的大块切开），齐桓公 standard
#      74→73（合并接缝不再吞邻篇的行，块短一行就少并进一块）。
#   ② 再加 前漢書/後漢書（语料本身变大）：命中数**必然**涨（齐桓公 96→151），
#      片段数跟着涨。§26 那句「Passage 本身一个字没动」仍然成立——变的是库里
#      有几部书，不是哪一条 Passage 被改写；这也是下面 hit_total 必须重测的原因：
#      它是语料的函数，不是引擎的指标。
#   ③ 第六点三阶段第一批：再加 三國志/晉書（7 部 → 9 部）。齐桓公 151→154、
#      管仲 189→201、黄帝 170→173，三个模式各自 +2~+11。涨的全部是新书里
#      提到这三个人名的段落（三國志/晉書 引管子、述齊桓公事），不是已有的
#      哪些 Passage 被改写。第二批（南北朝 10 部）落地后还要再测一轮。
#   ④ 第六点三阶段第二批：再加 南北朝 10 部（9 部 → 19 部）。齐桓公 154→159、
#      管仲 201→219、黄帝 173→206，命中涨的全是南北朝诸史里的段落。同样地，
#      涨的是「库里多了几部书」，不是「哪条 Passage 被改写」——本阶段一个字
#      没动过已入库的正文（重建只增行）。block 数在 long 模式涨得比 short 慢：
#      新书里这些名字多是分散提及，不足一个长片段，被并进邻块而不新起块。
BASELINE = {
    "齐桓公": (159, {"short": 145, "standard": 133, "long": 122}),
    "管仲": (219, {"short": 187, "standard": 170, "long": 162}),
    "黄帝": (206, {"short": 157, "standard": 127, "long": 102}),
}
MODES = ("short", "standard", "long")


def ro_conn():
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


@unittest.skipUnless(HAS_DB, "需先运行 python -m scripts.pipeline.run_all")
class TestResultBlockRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = ro_conn()
        cls.cur = cls.conn.cursor()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def search(self, q, mode="standard", page=1, page_size=100):
        return RB.search_result_blocks(self.cur, q, None, None, page, page_size, mode)

    def all_blocks(self, q, mode="standard"):
        """翻完所有页，返回全部片段（验证分页不重复、不遗漏）。"""
        out, page = [], 1
        while True:
            d = self.search(q, mode, page, 20)
            if not d["results"]:
                break
            out += d["results"]
            if len(out) >= d["total"]:
                break
            page += 1
        return out

    # ---- 基数 ----
    def test_baseline_counts(self):
        for q, (hits, per_mode) in BASELINE.items():
            with self.subTest(q=q):
                for mode, n in per_mode.items():
                    d = self.search(q, mode)
                    self.assertEqual(d["hit_total"], hits, f"{q} 命中数")
                    self.assertEqual(d["total"], n, f"{q} {mode} 片段数")
                    self.assertLessEqual(d["total"], d["hit_total"],
                                         "片段数不可能多于原始命中数")

    # ---- 正文 ----
    def test_text_is_exact_concatenation_of_passages(self):
        """片段正文 = 它列出的那些 passage 的 text_orig 依序相接，一字不差。"""
        for q in ("齐桓公", "管仲"):
            for b in self.all_blocks(q)[:20]:
                with self.subTest(q=q, block=b["block_id"]):
                    rows = self.cur.execute(
                        "SELECT passage_id, text_orig FROM passages WHERE passage_id IN (%s)"
                        % ",".join("?" * len(b["passage_ids"])), b["passage_ids"]).fetchall()
                    by_id = {r["passage_id"]: r["text_orig"] for r in rows}
                    expect = "".join(by_id[pid] for pid in b["passage_ids"])
                    self.assertEqual(b["text"], expect)
                    self.assertEqual(b["n_chars"], len(expect))

    def test_no_parser_metadata_in_block_text(self):
        """片段正文只有史料原文：`# src:`、`# dating:` 等解析器行一律不进来。

        注意 `<pb:…>` 与它们不同——那是 tls 原文自带的页码标记，常常就长在
        正文行首（`<pb:KR2a0001_tls_100-2a>黃帝者，¶` 整条是一行 passage），
        属于第一阶段已入库的 text_orig，必须原样保留（任务书 §十）。
        """
        for q in ("齐桓公", "黄帝"):
            for b in self.all_blocks(q)[:20]:
                with self.subTest(q=q, block=b["block_id"]):
                    for marker in ("# src:", "# dating:"):
                        self.assertNotIn(marker, b["text"])
                    self.assertFalse(b["text"].lstrip().startswith("#"),
                                     "解析器注释行不得进入正文")

    def test_hits_are_actually_in_the_block(self):
        """每个片段里必须有真命中（否则就是白给的结果）。"""
        for q in ("齐桓公", "管仲", "黄帝"):
            trad = zh.to_traditional(q)
            for b in self.all_blocks(q)[:20]:
                with self.subTest(q=q, block=b["block_id"]):
                    self.assertGreaterEqual(b["match_count"], 1)
                    self.assertIn(trad, b["text"])

    # ---- 边界 ----
    def test_block_never_crosses_file(self):
        for q in ("齐桓公", "管仲"):
            for b in self.all_blocks(q):
                with self.subTest(q=q, block=b["block_id"]):
                    fids = {r["file_id"] for r in self.cur.execute(
                        "SELECT DISTINCT file_id FROM passages WHERE passage_id IN (%s)"
                        % ",".join("?" * len(b["passage_ids"])), b["passage_ids"])}
                    self.assertEqual(fids, {b["file_id"]})

    def test_block_never_crosses_layer(self):
        for q in ("齐桓公", "管仲"):
            for b in self.all_blocks(q)[:30]:
                with self.subTest(q=q, block=b["block_id"]):
                    layers = {r["layer"] for r in self.cur.execute(
                        "SELECT DISTINCT layer FROM passages WHERE passage_id IN (%s)"
                        % ",".join("?" * len(b["passage_ids"])), b["passage_ids"])}
                    self.assertEqual(layers, {b["layer"]})

    def test_passage_ids_follow_reading_order(self):
        """片段内的记录必须按 (row_no, seq) 递增——读起来才连续。"""
        for q in ("齐桓公", "管仲", "黄帝"):
            for b in self.all_blocks(q)[:20]:
                with self.subTest(q=q, block=b["block_id"]):
                    rows = self.cur.execute(
                        "SELECT passage_id, row_no, seq FROM passages WHERE passage_id IN (%s)"
                        % ",".join("?" * len(b["passage_ids"])), b["passage_ids"]).fetchall()
                    pos = {r["passage_id"]: (r["row_no"], r["seq"]) for r in rows}
                    keys = [pos[pid] for pid in b["passage_ids"]]
                    self.assertEqual(keys, sorted(keys))

    # ---- 分页 ----
    def test_pagination_has_no_gap_or_overlap(self):
        for q, (_, per_mode) in BASELINE.items():
            with self.subTest(q=q):
                blocks = self.all_blocks(q)
                self.assertEqual(len(blocks), per_mode["standard"])
                ids = [b["block_id"] for b in blocks]
                self.assertEqual(len(ids), len(set(ids)), "翻页出现重复片段")

    def test_block_ids_are_unique(self):
        for q in ("齐桓公", "管仲", "黄帝"):
            blocks = self.all_blocks(q)
            ids = [b["block_id"] for b in blocks]
            self.assertEqual(len(ids), len(set(ids)), f"{q} 有重复 block_id")

    # ---- 展开 ----
    def test_expand_more_context_if_available(self):
        """能展开的片段，展开读到的行必须真在同一段史料内、且不重叠。"""
        checked = 0
        for q in ("齐桓公", "管仲", "黄帝"):
            for mode in ("short", "standard"):
                for b in self.search(q, mode, 1, 100)["results"]:
                    if not (b["more_before"] or b["more_after"]):
                        continue
                    checked += 1
                    with self.subTest(q=q, block=b["block_id"]):
                        seen = set(b["passage_ids"])
                        before = RB.expand_block(self.cur, b["hit_passage_id"], "before", 5,
                                                 b["first_passage_id"], None)
                        after = RB.expand_block(self.cur, b["hit_passage_id"], "after", 5,
                                                None, b["last_passage_id"])
                        for row in before["rows"] + after["rows"]:
                            self.assertNotIn(row["passage_id"], seen,
                                             "展开读到了片段里已有的记录（重叠）")
                    if checked >= 8:
                        break
        self.assertGreater(checked, 0, "真实语料里应当存在可展开的片段")

    def test_expand_rows_are_contiguous_and_same_layer(self):
        checked = 0
        for q in ("齐桓公", "管仲"):
            for b in self.search(q, "standard", 1, 100)["results"]:
                if not b["more_after"]:
                    continue
                r = RB.expand_block(self.cur, b["hit_passage_id"], "after", 10,
                                    None, b["last_passage_id"])
                if not r["rows"]:
                    continue
                checked += 1
                with self.subTest(q=q, block=b["block_id"]):
                    pids = [x["passage_id"] for x in r["rows"]]
                    rows = self.cur.execute(
                        "SELECT passage_id, row_no, seq, layer FROM passages "
                        "WHERE passage_id IN (%s)" % ",".join("?" * len(pids)), pids).fetchall()
                    meta = {x["passage_id"]: x for x in rows}
                    self.assertTrue(all(meta[p]["layer"] == b["layer"] for p in pids))
                    keys = [(meta[p]["row_no"], meta[p]["seq"]) for p in pids]
                    self.assertEqual(keys, sorted(keys))
                    # 第一条必须紧接在片段末条之后
                    last = self.cur.execute(
                        "SELECT row_no, seq FROM passages WHERE passage_id = ?",
                        (b["last_passage_id"],)).fetchone()
                    self.assertGreater(keys[0], (last["row_no"], last["seq"]))
                if checked >= 5:
                    break
        self.assertGreater(checked, 0, "真实语料里应当存在可展开的片段")


@unittest.skipUnless(HAS_DB, "需先运行 python -m scripts.pipeline.run_all")
class TestPhase6_1RealData(unittest.TestCase):
    """第六点一阶段在真实语料上的回归：篇名命中、match_type、has_more、total 真实性。"""

    @classmethod
    def setUpClass(cls):
        cls.conn = ro_conn()
        cls.cur = cls.conn.cursor()
        cls.idx = RB._section_index(cls.cur)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def search(self, q, mode="standard", page=1, page_size=100):
        return RB.search_result_blocks(self.cur, q, None, None, page, page_size, mode)

    def all_blocks(self, q, page_size=100, max_pages=None):
        """翻完所有页（或前 max_pages 页）。每页都要重跑一遍全量组装——「之」一页
        2.2s，翻满 100 页要四分钟，所以只对中等频率的词要求翻到底。"""
        out, page = [], 1
        while True:
            d = self.search(q, page=page, page_size=page_size)
            out += d["results"]
            if not d["has_more"] or (max_pages and page >= max_pages):
                return out, d
            page += 1

    def labels_of(self, b):
        return {RB._section_at(self.idx, *self.cur.execute(
            "SELECT file_id, row_no FROM passages WHERE passage_id=?", (pid,)
        ).fetchone()) for pid in b["passage_ids"]}

    # ---- 篇名命中 ----
    def test_section_query_returns_that_section(self):
        for label in ("秦始皇本紀", "五帝本紀", "秦本紀"):
            with self.subTest(label=label):
                d = self.search(label)
                hit = [b for b in d["results"] if b["section"] == label]
                self.assertTrue(hit, f"搜「{label}」应当返回这一篇")
                self.assertIn(hit[0]["match_type"], ("section", "both"))

    def test_section_block_anchors_at_first_body_row(self):
        """锚点是区间内首条正文——标题行不进片段正文，从它起读才是这一篇的开头。"""
        d = self.search("秦始皇本紀")
        b = [x for x in d["results"] if x["section"] == "秦始皇本紀"][0]
        want = self.cur.execute(
            "SELECT MIN(row_no) AS r FROM passages WHERE file_id=? AND kind='passage' "
            "AND row_no >= (SELECT first_row FROM sections WHERE file_id=? AND label=?)",
            (b["file_id"], b["file_id"], "秦始皇本紀")).fetchone()["r"]
        self.assertEqual(b["row_first"], want)

    def test_section_and_text_hits_are_ordered(self):
        """§6：正文命中排在篇名命中之前；match_type 只有三种取值。"""
        for q in ("五帝本紀", "秦本紀", "之"):
            with self.subTest(q=q):
                rank = {"text": 0, "both": 1, "section": 2}
                kinds = [b["match_type"] for b in self.search(q)["results"]]
                self.assertTrue(set(kinds) <= set(rank), f"未知 match_type: {kinds}")
                self.assertEqual(kinds, sorted(kinds, key=lambda k: rank[k]))

    def test_no_duplicate_passage_across_blocks(self):
        """§7：一篇正文不得在结果里出现两次（正文块与篇名块撞车时要合并）。"""
        for q, mp in (("秦本紀", None), ("五帝本紀", None), ("之", 3)):
            with self.subTest(q=q):
                blocks, _ = self.all_blocks(q, page_size=100, max_pages=mp)
                pids = [p for b in blocks for p in b["passage_ids"]]
                self.assertEqual(len(pids), len(set(pids)), "跨块出现重复 passage")

    # ---- 回填 ----
    def test_section_is_backfilled_on_shiji(self):
        """史記正文行的 passages.section 是 NULL；块的 section 由区间推出来。

        没有这一项，史記的结果读不出「这是哪一篇」，§24 的篇章溯源就落空。
        """
        blocks = self.search("之")["results"]
        filled = [b for b in blocks if b["section"]]
        self.assertGreater(len(filled), 50, "史記/國語的多数块应当带得出篇名")

    # ---- total / has_more ----
    def test_high_frequency_total_is_truthful(self):
        """高频词的 total 必须是真的：翻完所有页恰好拿到 total 个，不多不少不重复。

        `total`/`hit_total` 的**精确值不写死**：它们随语料长大（第六点二阶段加
        前漢書/後漢書后 大夫 1443→3531 命中、1116→2994 块），写死就是在测试里
        埋一颗「下次加书必红」的雷。这里断言的是两个只随**缩水**才失效的量：
        高频词至少要有上千命中（语料还在），以及 块数 ≤ 命中数（合并只会减不会增）。
        """
        blocks, last = self.all_blocks("大夫", page_size=100)
        self.assertEqual(len(blocks), last["total"])
        self.assertFalse(last["truncated"], "解除 600 上限后不该再报截断")
        self.assertGreater(last["hit_total"], 1000, "大夫 是高频词，命中数不该是个小数字")
        self.assertLessEqual(last["total"], last["hit_total"],
                             "块是把命中并起来的，块数不可能多于命中数")
        self.assertLess(last["total"], last["hit_total"],
                        "大夫 有一行多处命中/跨行合并，块数应当**严格小于**命中数")

    def test_has_more_matches_total(self):
        for q, size in (("大夫", 100), ("大夫", 30), ("之", 100)):
            page = 1
            while True:
                d = self.search(q, page=page, page_size=size)
                with self.subTest(q=q, size=size, page=page):
                    self.assertEqual(d["has_more"], page * size < d["total"],
                                     "has_more 必须与 total 一致，否则前端会漏翻或多翻")
                if not d["has_more"]:
                    break
                page += 1
                if q == "之":            # 9906 块，翻满三页足够证明规律
                    break

    def test_total_can_exceed_hit_total_for_section_hits(self):
        """篇名命中没有对应正文行，所以 total 可以大于 hit_total——不是算错。

        两种情形都要在：

        ① **纯篇名命中**：全库没有一行正文写到这个篇名，于是 total=1、hit_total=0。
           换用 漢書 的篇名，是因为扩容后 史記 的篇名被漢書正文引到了——
           「秦始皇本紀」已有 2 处正文命中（漢書 里提史記 篇名），不再是纯篇名命中。
        ② **篇名 + 正文**：「秦始皇本紀」现在 total=3 > hit_total=2，差的那 1 就是
           篇名块本身。这两条合起来才证明 total 与 hit_total 是两套口径，不是同一个数。
        """
        d = self.search("五行志第七上")
        self.assertEqual(d["hit_total"], 0)
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["results"][0]["match_type"], "section")

        d = self.search("秦始皇本紀")
        self.assertGreater(d["hit_total"], 0)
        self.assertEqual(d["total"], d["hit_total"] + 1,
                         "总数应比正文命中多出篇名块本身")
        self.assertIn("section", {b["match_type"] for b in d["results"]})

    # ---- 第六点二阶段：跨篇界块归零（正向断言）----
    def assemble_all(self, q, mode="standard"):
        """一次性组装**全部**块，不走分页。

        与 search_result_blocks 内部同一条路（同样截 MAX_HITS_PER_QUERY）：分页
        会把整个查询重跑 page 次，「之」13000 个块要翻 130 页、每页 4 秒，单测跑
        不动。这里只跑一遍组装。

        为什么不用 `self.search(q)["results"]`（第一页）：这一条正是旧的
        `test_known_limit_blocks_may_cross_section_boundary` 失灵的原因——它只
        看首页 100 块，130 → 106 这种半吊子修复照样绿。
        """
        rows = RB._fetch_hits(self.cur, zh.to_traditional(q), None, None)
        hits = [(r["passage_id"], r["file_id"], r["row_no"], r["seq"],
                 r["score"] if r["score"] is not None else 0.0)
                for r in rows[:RB.MAX_HITS_PER_QUERY]]
        idx = RB._section_index(self.cur)
        return RB.build_result_blocks(self.cur, hits, mode, idx)["blocks"]

    def test_known_limit_blocks_may_cross_section_boundary(self):
        """第六点二阶段已修复：块**不得**横跨两篇（正向断言，不是「别超 5%」）。

        旧短板：`passages.section` 只在标题行有值、不向下传播，正文行的
        section/subsection/ab 三列全是 NULL，于是 boundaryKey 对所有正文行都是
        null、相邻两篇在判断上「同段」。修法两步（缺一不可）：
          ① `_row_key` 用 `sections.first_row` 区间表给无键行回填篇名键；
          ② `_fit_hi` 在**合并接缝**上补做同一套 `_can_take` 判定——撑大的那一截
             从没经过 `_can_take`，实测这才是大头（只修 ① 只消除 18%）。

        代价（§26 已确认接受）：史記/國語的块从「段」粒度变粗到「篇」粒度。
        Passage 本身一个字没动，只是「哪几条 Passage 放进同一个展示块」变了。
        """
        checked = cross = 0
        offenders = []
        for q in ("之", "大夫", "齊桓公"):
            for b in self.assemble_all(q):
                checked += 1
                labs = self.labels_of(b)
                if len(labs) > 1:
                    cross += 1
                    if len(offenders) < 5:
                        offenders.append(f"{q}@{b['file_id']}:{b['row_first']} {sorted(labs)}")
        self.assertGreater(checked, 1000, "样本太小，这条断言证明不了什么")
        self.assertEqual(cross, 0,
                         f"{checked} 个块里有 {cross} 个横跨篇界：{offenders}")


@unittest.skipUnless(HAS_DB, "需先运行 python -m scripts.pipeline.run_all")
class TestLibraryUntouched(unittest.TestCase):
    """任务书 §三.3：HistoryLibrary/kanripo 零写入。检索不得改动原始史料。"""

    def _snapshot(self):
        base = config.LIBRARY_DIR
        out = {}
        if not base.is_dir():
            return out
        for p in base.rglob("*"):
            if p.is_file():
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
        return out

    def test_search_does_not_write_library(self):
        if not config.LIBRARY_DIR.is_dir():
            self.skipTest("资料库目录不存在")
        before = self._snapshot()
        self.assertGreater(len(before), 0, "资料库应有文件可供比对")
        conn = ro_conn()
        try:
            for q in ("齐桓公", "管仲", "黄帝", "城濮"):
                RB.search_result_blocks(conn.cursor(), q, None, None, 1, 20, "standard")
            b = RB.search_result_blocks(conn.cursor(), "齐桓公", None, None, 1, 1,
                                        "long")["results"][0]
            RB.expand_block(conn.cursor(), b["hit_passage_id"], "both", 20)
        finally:
            conn.close()
        after = self._snapshot()
        self.assertEqual(before, after, "HistoryLibrary/kanripo 在检索过程中被改动了")


if __name__ == "__main__":
    unittest.main()
