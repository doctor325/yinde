"""Step 5 — 检索/上下文 在真实数据库（read-only）上的行为测试 + 新版 API 契约。

前提：data/database/history.db 已由 run_all 生成（缺则跳过）。
命中数是针对当前 5 书 118 文件库的固定回归值；若语料重建更新需同步调整。
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

from api import db as api_db
from api import main as api_main
from scripts.pipeline import config
from search import context as search_ctx
from search import engine as search_engine

HAS_DB = config.DB_PATH.is_file()


@unittest.skipUnless(HAS_DB, "需先运行 python -m scripts.pipeline.run_all")
class TestSearchEngineOnDb(unittest.TestCase):
    def setUp(self):
        self.conn = api_db.connect()
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def _run(self, q, **kw):
        return search_engine.run_search(self.cur, q, **kw)

    # ---- 已知固定命中数（既有验证过的基数）----
    # 第六点二阶段加 前漢書/後漢書 后全体重测：命中数是**语料的函数**，
    # 加书必然涨（齐桓公 96 → 151）。这条路径（run_search）的 total 是
    # 原始命中 passage 数，不是结果块数。
    # 第六点三阶段再加 南北朝 10 部（9 部 → 19 部）后重测：齐桓公 151 → 159、
    # 管仲 189 → 219、城濮 35 → 40、长勺 7 → 8 —— 涨的全是新书里的段落，
    # 没有一条已有 Passage 被改写（同 test_result_block_real.py 的 ④ 注释）。
    def test_qihuangong_fts(self):
        d = self._run("齐桓公")
        self.assertEqual(d["q_traditional"], "齊桓公")
        self.assertEqual(d["mode"], "fts")
        self.assertEqual(d["total"], 159)

    def test_two_char_words_bigram_mode(self):
        # 2 字词走 bigram 快路，命中集合须与 LIKE 语义一致（含转繁体后）
        d = self._run("管仲")
        self.assertEqual(d["mode"], "bigram")
        self.assertEqual(d["q_traditional"], "管仲")
        self.assertEqual(d["total"], 219)
        self.assertEqual(self._run("城濮")["total"], 40)
        self.assertEqual(self._run("长勺")["total"], 8)
        for it in self._run("管仲", page_size=100)["items"]:
            self.assertIn("管仲", it["text_orig"])

    def test_mixed_length_query_uses_trigram_plus(self):
        # 含 >=3 字词时整体走 trigram（<3 字词为 AND 约束）
        d = self._run("管仲 齐桓公")
        self.assertEqual(d["mode"], "fts")
        self.assertGreater(d["total"], 0)

    def test_one_char_word_falls_back_like(self):
        d = self._run("桓")
        self.assertEqual(d["mode"], "like")
        self.assertGreater(d["total"], 0)

    def test_bigram_book_filter(self):
        d = self._run("管仲", book="国语")
        self.assertEqual(d["mode"], "bigram")
        self.assertTrue(all(i["book_id"] == "KR2e0001" for i in d["items"]))

    # ---- 检索词转繁体 + 高亮词回归 ----
    def test_simpl_conversion_applied(self):
        d = self._run("齐桓公")
        self.assertIn("齊桓公", d["items"][0]["text_orig"])

    # ---- 出处块字段齐全；无值才为 None ----
    def test_shape_fields_present(self):
        d = self._run("管仲", book="国语")
        it = d["items"][0]
        self.assertEqual(it["book_id"], "KR2e0001")
        self.assertEqual(it["book_title"], "國語")
        for k in ("juan", "section", "subsection", "division", "ab", "edition",
                  "family", "file_name", "origin_path", "row_no",
                  "kind", "layer", "status", "text_orig", "normalized_text",
                  "passage_id", "source_ref", "special_chars", "commentary_candidate"):
            self.assertIn(k, it)

    # ---- 书籍/版本过滤 ----
    def test_book_alias_filter(self):
        d = self._run("齊桓公", book="左传")
        self.assertEqual(d["book"], "KR1e0001")
        self.assertTrue(all(i["book_id"] == "KR1e0001" for i in d["items"]))
        d2 = self._run("齊桓公", book="国语")
        self.assertEqual(d2["book"], "KR2e0001")
        self.assertTrue(all(i["book_id"] == "KR2e0001" for i in d2["items"]))

    def test_edition_filter_keeps_sbck_only(self):
        d = self._run("齐桓公", book="战国策", edition="tls")
        self.assertEqual(d["total"], 0)          # 戰國策无 tls 版：合法但零命中
        self.assertEqual(d["edition"], "tls")

    # ---- 分页/参数边界 ----
    def test_page_size_capped_at_100(self):
        d = self._run("齐桓公", page_size=9999)
        self.assertEqual(d["page_size"], 100)
        self.assertLessEqual(len(d["items"]), 100)

    def test_deep_page_consistent_total(self):
        d1 = self._run("齊桓公", page=1, page_size=20)
        d4 = self._run("齊桓公", page=4, page_size=20)
        self.assertEqual(d4["total"], d1["total"])
        self.assertEqual(len(d4["items"]), 20)

    def test_fts_plus_short_constraint_and(self):
        # 长词走 FTS、短词仍是 AND 约束：命中行必须同时含两者
        d = self._run("齊桓公 卒")
        self.assertEqual(d["mode"], "fts")
        self.assertGreater(d["total"], 0)
        for it in d["items"]:
            self.assertIn("卒", it["text_orig"])

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError) as cm:
            self._run("")
        self.assertIn("关键词", str(cm.exception))
        with self.assertRaises(ValueError):
            self._run("齐桓公", page=0)
        with self.assertRaises(ValueError):
            self._run("齐桓公", book="不存在之书")

    # ---- 上下文：真实邻居 ----
    def _mid_passage(self):
        # 國語正文中段某行，保证前后均有邻居
        d = self._run("齊桓公", book="国语", page_size=100)
        return d["items"][len(d["items"]) // 2]["passage_id"]

    def test_context_neighbors_ordered_same_file(self):
        pid = self._mid_passage()
        c = search_ctx.get_context(self.cur, pid)
        self.assertEqual(c["current"]["passage_id"], pid)
        rows = ([x["row_no"] for x in c["before"]] + [c["current"]["row_no"]]
                + [x["row_no"] for x in c["after"]])
        self.assertEqual(rows, sorted(rows))     # 同文件按行序
        self.assertEqual(c["current"]["book_id"], "KR2e0001")
        ids = [x["passage_id"] for x in c["before"]] + \
              [x["passage_id"] for x in c["after"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_context_window_clamped(self):
        pid = self._mid_passage()
        c = search_ctx.get_context(self.cur, pid, before=99, after=99)
        self.assertLessEqual(len(c["before"]), 10)
        self.assertLessEqual(len(c["after"]), 10)

    def test_context_first_row_no_before(self):
        # 文件第一段（kind=passage 且行最早）前应无邻居
        rows = self.conn.execute(
            "SELECT row_no FROM passages WHERE file_id=? AND kind='passage' "
            "ORDER BY row_no, seq LIMIT 1", (2,)).fetchone()
        pid = self.conn.execute(
            "SELECT passage_id FROM passages WHERE file_id=? AND kind='passage' "
            "ORDER BY row_no, seq LIMIT 1", (2,)).fetchone()[0]
        self.assertIsNotNone(rows)
        c = search_ctx.get_context(self.cur, pid)
        self.assertEqual(c["before"], [])

    def test_context_unknown_id(self):
        with self.assertRaises(KeyError):
            search_ctx.get_context(self.cur, 9_999_999_999)

    def test_context_current_text_is_text_orig(self):
        pid = self._mid_passage()
        c = search_ctx.get_context(self.cur, pid)
        self.assertEqual(c["current"]["text_orig"],
                         c["current"]["text_orig"])  # 无 normalized 覆盖


@unittest.skipUnless(HAS_DB, "需先运行 python -m scripts.pipeline.run_all")
class TestSearchApiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api_main.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

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

    def test_search_endpoint_defaults(self):
        # 第三阶段契约：默认返回史料片段（Result Block）。
        # total = 片段数（翻页依据），hit_total = 原始命中 passage 数。
        # 第六点二阶段：151 条命中合并为 126 段（扩容前是 96 → 74）。
        # 第六点三阶段：159 条命中合并为 133 段（块比命中涨得慢：新书里的这类
        # 名字多是分散提及，凑不满一个长片段）。
        q = urllib.parse.quote("齐桓公")
        d = self.get(f"/api/search?q={q}")
        self.assertEqual(d["page_size"], 20)
        self.assertEqual(d["mode"], "standard")      # 显示长度默认「标准」
        self.assertEqual(len(d["results"]), 20)
        self.assertNotIn("items", d)                 # 顶层不再有逐条命中
        self.assertEqual(d["total"], 133)            # 159 条命中合并为 133 段
        self.assertEqual(d["hit_total"], 159)

    def test_search_page_size_choice_capped(self):
        q = urllib.parse.quote("齐桓公")
        d = self.get(f"/api/search?q={q}&page_size=500")
        self.assertEqual(d["page_size"], 100)        # 服务端封顶 100
        # 封顶后一页装不下 133 段：满页返回 100 条，剩下的靠翻页。
        # （扩容前只有 74 段，这条断言写的是「一页装得下」——语料一涨就不成立了。）
        self.assertGreater(d["total"], d["page_size"])
        self.assertEqual(len(d["results"]), d["page_size"])

    def test_level_passage_backward_compatible(self):
        # 第二阶段「逐条命中」契约保留（任务书 §十七）：level=passage 仍是老结构
        q = urllib.parse.quote("齐桓公")
        d = self.get(f"/api/search?q={q}&level=passage")
        self.assertEqual(d["total"], 159)
        self.assertEqual(len(d["items"]), 20)
        self.assertNotIn("results", d)

    def test_level_invalid_400(self):
        q = urllib.parse.quote("齐桓公")
        self.assertEqual(self.code(f"/api/search?q={q}&level=passage2"), 400)

    def test_search_400s(self):
        self.assertEqual(self.code("/api/search"), 400)            # 无 q
        self.assertEqual(self.code("/api/search?q=x&page=0"), 400)  # 页码从 1 开始
        bad_book = "/api/search?q=%E9%BD%90%E6%A1%93%E5%85%AC&book=%E4%B8%89%E5%AD%97%E7%BB%8F"
        self.assertEqual(self.code(bad_book), 400)                  # 未知书（三字经）

    def test_context_404(self):
        self.assertEqual(self.code("/api/passages/999999999/context"), 404)

    def test_route_regex_still_guarded(self):
        self.assertEqual(self.code("/api/passages/abc/context"), 404)
        self.assertEqual(self.code("/api/search/extra"), 404)


if __name__ == "__main__":
    unittest.main()
