"""Recall Invariant 的机制（第六点四阶段 §15）—— 冻结与判定的往返。

**为什么这些要单独测**：冻结出的文件如果**加载不上**，召回跑分照样全绿 ——
只是那条断言根本没跑。这是最坏的一类失效：安全检查静默失效，报告上写着 PASS。
判据是「冻进去的片段这次还在不在」，所以两端（写 / 读）必须对得上，
而且要能证明它**真的会红**。

ⓘ 这里不碰真库：`invariant_violations()` 是纯函数，`_attach_invariant()` 只读一个
JSON 文件。真库上的往返在 `tests/recall.py` 的跑分里，那里一次跑完 373 条。
"""
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock  # noqa: F401  (显式导入：`unittest.mock` 不是自动可见的子模块)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import _tmp  # noqa: E402
from tests import recall  # noqa: E402


def blk(book_id, file_name, a, b):
    return {"book_id": book_id, "file_name": file_name, "row_first": a, "row_last": b}


class TestProvOf(unittest.TestCase):
    def test_prov_is_a_source_coordinate(self):
        """存 (书, 文件, 起行, 止行)，**不存 rowid**。

        rowid 只在这一个 history.db 里有效，重建库就变；文件号 + 行号是从源文件
        直接读出来的，只要源文件没改就永远指同一段原文。冻结的基线必须能活过
        一次重建库 —— 否则它守的是什么都不清楚。
        """
        got = recall.prov_of(blk("KR2a0007", "KR2a0007_020.txt", 5, 9))
        self.assertEqual(got, ("KR2a0007", "KR2a0007_020.txt", 5, 9))

    def test_prov_falls_back_to_title(self):
        """没有 book_id 时用书名 —— 缺字段不该变成空元组（那会让所有块互相相等）。"""
        got = recall.prov_of({"book_title": "前漢書", "file_name": "f.txt",
                              "row_first": 1, "row_last": 2})
        self.assertEqual(got[0], "前漢書")


class TestViolations(unittest.TestCase):
    """**只进不退**：语料只增不减，老结果不该消失。"""

    def _res(self, *blocks):
        return {"results": list(blocks)}

    def test_all_present_is_clean(self):
        case = {"expect_prov": [("KR2a0007", "f.txt", 5, 9)]}
        res = self._res(blk("KR2a0007", "f.txt", 5, 9))
        self.assertEqual(recall.invariant_violations(case, res), [])

    def test_missing_passage_is_reported(self):
        """召回退化的样子：以前返回的段这次不返回了。"""
        case = {"expect_prov": [("KR2a0007", "f.txt", 5, 9),
                                ("KR2a0009", "g.txt", 1, 2)]}
        res = self._res(blk("KR2a0009", "g.txt", 1, 2))
        v = recall.invariant_violations(case, res)
        self.assertEqual(len(v), 1)
        self.assertIn("f.txt", v[0])
        self.assertIn("召回退化", v[0])

    def test_order_is_not_part_of_the_assertion(self):
        """顺序不在包含断言的范围里 —— 排序微调不该判成失败。

        §15 要的是 `expected ⊆ actual`，不是「顺序也一样」。把顺序也冻进去，
        任何一次打分权重调整都会红一片，那时人就开始忽略这个检查了。
        """
        case = {"expect_prov": [("B", "b.txt", 1, 1), ("A", "a.txt", 1, 1)]}
        res = self._res(blk("A", "a.txt", 1, 1), blk("B", "b.txt", 1, 1))
        self.assertEqual(recall.invariant_violations(case, res), [])

    def test_no_frozen_expectation_is_skipped(self):
        """没冻过的用例（负例、篇名用例）不判 —— 不是「通过」，是「不适用」。"""
        self.assertEqual(recall.invariant_violations({}, self._res()), [])


class TestAttach(unittest.TestCase):
    """写出来的文件必须读得回来。**这一条就是「安全检查会不会静默失效」的测试。**

    `_attach_invariant(cases)` 读的是固定的 `CASES_DIR/invariant.json`，不收路径参数
    —— 那就换个目录给它读，**不为了让测试好写而给生产代码加参数**。
    """

    def setUp(self):
        self.tmp = _tmp.mkdtemp()
        self.dir = Path(self.tmp)
        self._patch = unittest.mock.patch.object(recall, "CASES_DIR", self.dir)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _freeze(self, obj):
        (self.dir / recall.INVARIANT_FILE).write_text(
            obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False),
            encoding="utf-8")

    def test_round_trip(self):
        self._freeze({"note": "x", "count": 2,
                      "cases": {"c1": [["KR2a0007", "f.txt", 5, 9]],
                                "c2": [["KR2a0009", "g.txt", 1, 2]]}})
        cases = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
        self.assertEqual(recall._attach_invariant(cases), 2)
        self.assertEqual(cases[0]["expect_prov"], [("KR2a0007", "f.txt", 5, 9)])
        self.assertNotIn("expect_prov", cases[2])   # 没冻过的用例不该被塞一个空列表

    def test_broken_file_degrades_loudly(self):
        """文件坏了 → 本次不判，并**打印出来**。静默跳过等于假装判过了。"""
        self._freeze("{ not json")
        cases = [{"id": "c1"}]
        self.assertEqual(recall._attach_invariant(cases), 0)

    def test_missing_file_is_not_an_error(self):
        """还没冻过（本阶段之前）不该让跑分红 —— 那会让第一次引入它的人以为坏了。"""
        cases = [{"id": "c1"}]
        self.assertEqual(recall._attach_invariant(cases), 0)

    def test_ids_are_attached_as_tuples(self):
        """必须是 tuple 不是 list：`invariant_violations` 用集合去比对，
        list 不可哈希 —— 这个错会在**跑分跑到一半**才炸，所以在这里钉住。"""
        self._freeze({"cases": {"c1": [["A", "f", 1, 2]]}})
        cases = [{"id": "c1"}]
        recall._attach_invariant(cases)
        self.assertIsInstance(cases[0]["expect_prov"][0], tuple)
        # 真跑一遍判定，证明这个形状可用
        self.assertEqual(recall.invariant_violations(
            cases[0], {"results": [blk("A", "f", 1, 2)]}), [])

    def test_loading_cases_through_the_real_loader(self):
        """端到端：`load_cases()` 读到基线文件、贴回用例、且**不把它当用例文件**。

        这一条覆盖的是加载顺序 —— `_attach_invariant` 必须在返回前跑完，
        否则用例上根本没有 expect_prov，判定被静默跳过（跑分全绿）。
        """
        (self.dir / "t.json").write_text(json.dumps(
            {"version": 1, "cases": [{"id": "t1", "query": "x"}]},
            ensure_ascii=False), encoding="utf-8")
        self._freeze({"cases": {"t1": [["A", "f", 1, 2]]}})
        cases = recall.load_cases()
        self.assertEqual([c["id"] for c in cases], ["t1"])
        self.assertEqual(cases[0]["expect_prov"], [("A", "f", 1, 2)])


class TestPayloadBuilder(unittest.TestCase):
    """基线的**写入端**必须能跑。

    这不是假想的风险：`datetime.now(timezone.utc)` 曾写在 `freeze_run` 的最后
    一行而 `timezone` 没 import —— 373 条用例跑完、十几分钟过去，才在最后一句
    NameError，结果全丢。所以写入端单独成函数、开跑前先空跑一次，并在下面钉住。
    """

    def test_empty_payload_is_buildable(self):
        d = json.loads(recall._invariant_payload([]))
        self.assertEqual(d["count"], 0)
        self.assertEqual(d["cases"], {})
        self.assertTrue(d["frozen_at"], "frozen_at 不能是空的")
        self.assertIn("不要手改", d["note"])

    def test_shape_matches_what_attach_reads(self):
        """写出来的 `cases` 结构必须正好是 `_attach_invariant` 读的那种。"""
        d = json.loads(recall._invariant_payload(
            [{"id": "c1", "prov": [["A", "f.txt", 1, 2]]},
             {"id": "c2", "prov": [["B", "g.txt", 3, 4]]}]))
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["cases"]["c1"], [["A", "f.txt", 1, 2]])

    def test_freeze_smoke_tests_the_writer_first(self):
        """`freeze_run` 必须在**开跑之前**先空跑一次写入端。

        只加函数不加这次调用，等于什么都没防 —— 错还是会在十几分钟后才炸。
        """
        src = (ROOT / "tests" / "recall.py").read_text(encoding="utf-8")
        body = src.split("def freeze_run(")[1]
        self.assertIn("_invariant_payload([])", body.split("cases = load_cases()")[0],
                      "freeze_run 必须先空跑 _invariant_payload([]) 再开始跑用例")


class TestReportShowsWhetherItJudged(unittest.TestCase):
    """报告必须**自报**这次判了多少条不变量。

    基线文件缺失或读不了时，跑分照样全绿、报告长得一模一样 —— 那是安全检查
    静默失效，比不检查更糟（报告上还写着 PASS）。所以「判了几条」要印在报告里，
    而不是让人从「没红」去推断。
    """

    RESULT = {"id": "x-01", "query": "齊桓公", "group": "A", "status": "PASS",
              "hit_total": 1, "total": 1, "baseline": 1, "baseline_blocks": 1,
              "note": "", "trad": "齊桓公", "corpus_hits": 1, "exec_mode": "fts",
              "match_types": ["text"], "books": ["國語"], "klass": "-", "why": "",
              "audits": [], "got_status": "hit", "scope_status": "hit",
              "summary": "", "scope_range": "", "rank": None}

    def _md(self, frozen_n):
        return recall.render([dict(self.RESULT)], [], True, True, frozen_n)

    def test_header_reports_the_frozen_count(self):
        t = self._md(1)
        self.assertIn("1 条带冻结基线", t)
        self.assertNotIn("Recall Invariant 的射程", t)   # 全覆盖时不必解释射程

    def test_partial_coverage_explains_the_range(self):
        """有没判的用例时，报告要说明那几类**为什么**不判。

        不然读者会以为「PASS」覆盖了全部 373 条 —— 实际上四态那 13 条压根
        不走这条断言。
        """
        t = self._md(0)
        self.assertIn("射程", t)
        for kw in ("负例", "四态用例", "超长结果用例"):
            self.assertIn(kw, t)


class TestCaseLoaderSkipsInvariant(unittest.TestCase):
    """`invariant.json` 不是用例文件（它的 `cases` 是 dict 不是 list）。

    不加这条排除，加载器会把 dict 的**键**当用例迭代，`c["id"]` 直接 TypeError ——
    而且是在读文件阶段炸，整个跑分跑不起来。
    """

    def test_it_is_not_treated_as_cases(self):
        self.assertEqual(recall.INVARIANT_FILE, "invariant.json")
        src = (ROOT / "tests" / "recall.py").read_text(encoding="utf-8")
        self.assertIn('if p.name != INVARIANT_FILE', src,
                      "load_cases() 必须排除 invariant.json")

    def test_frozen_file_shape_if_present(self):
        p = ROOT / "tests" / "search_cases" / recall.INVARIANT_FILE
        if not p.is_file():
            self.skipTest("还没冻结过（--freeze-invariant）")
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertIsInstance(d.get("cases"), dict, "cases 必须是 dict（id → 片段表）")
        for cid, provs in d["cases"].items():
            self.assertEqual(len(provs[0]), 4, f"{cid} 的来源坐标不是 4 元组")


if __name__ == "__main__":
    unittest.main()
