"""卷级覆盖（第六点四阶段 §4/§5/§13）—— 判据的单元测试。

`volume.audit()` 是**纯函数**（不查库），所以这里全部离线跑：给一个 measure()
形状的字典，看它得出什么结论。真实语料上的核对在 `tests/recall.py` 的
`coverage_scope.json` 那 13 条里，那里跑的是真库。

为什么这些判据值得逐条钉住：卷数错了**不会报错**，它只是安静地显示成另一个数字。
「三國志 30/65」写成「65/65」时，页面上一切正常，而用户会以为三国志搜不到的人
是真的不存在。这个阶段的所有价值都在这些数字准不准上，所以边界要一条条测。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.pipeline import volume  # noqa: E402


def m(numbers, spans=None, gaps=None, unwitnessed=None, dups=None,
      header=None, juan=None, non_volume=None):
    """构造一个 measure() 形状的字典。只填被 audit() 读的那些字段。"""
    nums = sorted(numbers)
    return {
        "book_id": "TEST", "numbers": nums, "count": len(nums),
        "span": (nums[0], nums[-1]) if nums else None,
        "gaps": gaps if gaps is not None else
                [n for n in range(1, (nums[-1] if nums else 1) + 1)
                 if n not in set(nums)],
        "splits": spans or {}, "true_dups": dups or [],
        "sources": {"header": header if header is not None else len(nums),
                    "juan": juan or 0},
        "by_number": {}, "owners": {}, "unwitnessed": unwitnessed or [],
        "non_volume_headers": non_volume or [], "body_files": 0,
    }


def codes(row):
    return sorted(p["code"] for p in row["problems"])


def levels(row):
    return {p["code"]: p["level"] for p in row["problems"]}


class TestCoercion(unittest.TestCase):
    def test_cn_to_int(self):
        for s, want in [("七", 7), ("十", 10), ("十五", 15), ("二十", 20),
                        ("一百", 100), ("一百五", 105), ("一百零五", 105),
                        ("一百三十", 130), ("12", 12), ("3", 3)]:
            self.assertEqual(volume.cn_to_int(s), want, s)

    def test_cn_to_int_rejects_junk(self):
        for s in ("", "卷", "abc", "一百五x"):
            self.assertIsNone(volume.cn_to_int(s), s)

    def test_parse_volume_label(self):
        """返回 (卷号, 卷内分片)。分片是**同一个卷号**的两个文件，不是两卷 ——
        前漢書 卷一上/卷一下 = 卷一。`之一` 保留前缀原文，便于人对照源文件。
        """
        for raw, num, part in [
                ("卷三十四", 34, ""), ("晉書卷七", 7, ""),
                ("魏書卷一百五之一", 105, "之一"),
                ("前漢書卷一上", 1, "上"), ("前漢書卷一下", 1, "下"),
                ("魏志卷一", 1, ""), ("巻十八", 18, ""),
                ("明史卷12", 12, "")]:
            got = volume.parse_volume_label(raw)
            self.assertIsNotNone(got, raw)
            self.assertEqual(got, (num, part), raw)

    def test_parse_rejects_non_volume(self):
        # 魏書 `_105.txt` 的文件头就是这个 —— 它**不是**卷题，认出来会把
        # 「前上十志啓」当成一卷。
        for raw in ("前上十志啓", "", "卷", "目録", "御製詩"):
            self.assertIsNone(volume.parse_volume_label(raw), raw)


class TestAuditComplete(unittest.TestCase):
    def test_full_book_is_complete(self):
        row = volume.audit(m(range(1, 101)), 100, 100, "corpus")
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["problems"], [])
        self.assertEqual(row["coverage_ratio"], 1.0)
        self.assertEqual(row["declared_ratio"], 1.0)
        self.assertEqual(row["available"], 100)

    def test_partial_book(self):
        """三國志的真实形状：通行本 65 卷，底本只到 30。"""
        row = volume.audit(m(range(1, 31)), 65, 30, "corpus")
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["problems"], [])          # 不是故障，是事实
        self.assertEqual(row["coverage_ratio"], round(30 / 65, 4))
        self.assertEqual(row["declared_ratio"], round(30 / 65, 4))

    def test_coverage_ratio_uses_measured_not_declared(self):
        """§13：覆盖率的分母是通行本，分子是**实测** —— 不是登记值。

        两者在正常情况下相等（下面那条 count-mismatch 会保证），但一旦不等，
        页面上显示的必须是真数出来的那个。用 declared 会让「登记了却没收进来」
        伪装成已收。
        """
        # 声明 100 卷、只数出 90 卷，差额用 volume_notes 逐卷豁免
        notes = {str(n): "源文件如此" for n in range(91, 101)}
        row = volume.audit(m(range(1, 91)), 100, 100, "corpus", notes)
        self.assertEqual(row["available"], 90)
        self.assertEqual(row["coverage_ratio"], round(90 / 100, 4))
        self.assertEqual(row["declared_ratio"], 1.0)   # 底本自称是完整的
        self.assertNotEqual(row["coverage_ratio"], row["declared_ratio"])

    def test_volume_notes_exempt_missing(self):
        """魏書 卷一百五：人能解释的缺口不算故障。

        注意**豁免不影响 status**：派「缺口有说明」只把 error 降掉，
        不改 declared == expected 这个事实（魏書是 complete）。
        """
        notes = {"105": "源文件把卷题拆成之一…之四，文件头认不出"}
        row = volume.audit(m(list(range(1, 105)) + list(range(106, 115))),
                           114, 114, "corpus", notes)
        self.assertEqual(codes(row), [])
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["explained_gaps"], [105])
        self.assertEqual(row["available"], 113)


class TestAuditProblems(unittest.TestCase):
    def test_e1_count_mismatch_is_error(self):
        """声明 50 卷、实测 35 卷，没有任何说明 —— 这就是北齊書若漏登记的样子。"""
        row = volume.audit(m(range(1, 36), gaps=list(range(36, 51))),
                           50, 50, "corpus")
        self.assertIn("count-mismatch", codes(row))
        self.assertEqual(levels(row)["count-mismatch"], "error")

    def test_e2_out_of_range_is_error(self):
        """收进来的比声明的还多：卷号超出声明范围。"""
        row = volume.audit(m(range(1, 52)), 50, 50, "corpus")
        self.assertIn("out-of-range", codes(row))

    def test_e3_true_duplicate_is_error(self):
        row = volume.audit(m(range(1, 51), dups=[18]), 50, 50, "corpus")
        self.assertIn("true-duplicate", codes(row))

    def test_e4_gap_unexplained_is_error(self):
        """中间缺卷：声明 50 卷，卷 20 和 21 没有。"""
        row = volume.audit(m([n for n in range(1, 51) if n not in (20, 21)]),
                           50, 50, "corpus")
        self.assertIn("gap-unexplained", codes(row))

    def test_e5_unwitnessed_file_is_warn_not_error(self):
        """正文文件既没有卷题、也不在任何一卷的证据里 —— 机器说不出它是哪一卷。

        这是 warn 不是 error：语料本身没问题，只是**我们认不出**，要人看一眼。
        """
        row = volume.audit(m(range(1, 11), unwitnessed=[99]), 10, 10, "corpus")
        self.assertIn("unwitnessed-file", codes(row))
        self.assertEqual(levels(row)["unwitnessed-file"], "warn")

    def test_e6_over_expected_is_error(self):
        """声明的卷数比通行本还多 —— 多收了不存在的卷。"""
        row = volume.audit(m(range(1, 111)), 100, 110, "corpus")
        self.assertIn("over-expected", codes(row))

    def test_declared_missing_with_corpus_witness_is_error(self):
        row = volume.audit(m(range(1, 11)), 10, None, "corpus")
        self.assertIn("no-declared", codes(row))


class TestWitnesses(unittest.TestCase):
    def test_none_witness_has_no_volumes(self):
        """先秦四书：sbck 底本以篇为单位，没有卷级模型。

        **必须回 unknown，不能回 partial** —— 「没量过」不等于「残缺」，
        搞混会让整库的 no-hit 全变成 partial_no_hit（§5 的注）。
        """
        row = volume.audit(m([], header=0), None, None, "none")
        self.assertEqual(row["status"], "unknown")
        self.assertIsNone(row["available"])
        self.assertIsNone(row["coverage_ratio"])
        self.assertEqual(row["problems"], [])

    def test_declared_witness_says_it_is_manual(self):
        """史記：tls 底本里没有卷级证据，卷数是人工登记的。

        那句 info 必须跟着数字走，否则页面上的 130/130 会被读成机器数出来的。
        """
        row = volume.audit(m([], header=0), 130, 130, "declared")
        self.assertEqual(row["status"], "complete")
        self.assertIn("no-witness", codes(row))
        self.assertEqual(levels(row)["no-witness"], "info")
        self.assertIn("人工登记", row["note"])

    def test_declared_witness_can_be_partial(self):
        row = volume.audit(m([], header=0), 130, 100, "declared")
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["coverage_ratio"], round(100 / 130, 4))

    def test_none_witness_forbids_expected(self):
        """没有卷级模型却登记了卷数 —— 那是自相矛盾，按 unknown 处理。"""
        row = volume.audit(m([]), None, 50, "none")
        self.assertEqual(row["status"], "unknown")


class TestAuditPlanned(unittest.TestCase):
    def test_planned_book_never_claims_measured(self):
        """未入库的书：只有登记，没有实测。数字必须诚实。"""
        row = volume.audit_planned("KR2a0030", 332, 332, "")
        self.assertEqual(row["status"], "planned")
        self.assertIsNone(row["available"])       # 一段都没数过
        self.assertEqual(row["numbers"], [])
        self.assertEqual(row["gaps"], [])
        self.assertIsNone(row["coverage_ratio"])

    def test_planned_with_upstream_shortfall_warns(self):
        """新唐書：通行本 225 卷，上游数字化只到 75。

        这条 warn 是给**今天**看的 —— 书还没进来，但「将来导入也是残的」
        这个事实现在就要让人看见，否则导入之后又是一次「正文中没有」。
        """
        row = volume.audit_planned("KR2a0010", 225, 75, "上游止于卷七十五上")
        self.assertEqual(row["status"], "planned")
        self.assertIn("upstream-partial", codes(row))
        self.assertEqual(levels(row)["upstream-partial"], "warn")
        self.assertEqual(row["declared_ratio"], round(75 / 225, 4))

    def test_planned_without_volumes(self):
        row = volume.audit_planned("TEST", None, None, "")
        self.assertEqual(row["status"], "planned")
        self.assertEqual(row["problems"][0]["code"], "not-imported")


class TestScopeOf(unittest.TestCase):
    """页面与诊断读的那一层。规矩：读不到就回 unknown，**绝不猜 complete**。"""

    SNAP = {"available": True, "reason": None, "generated_at": "T", "totals": {},
            "books": {"B1": {"book_id": "B1", "title": "某書", "status": "partial",
                             "expected": 50, "declared": 35, "available": 35,
                             "witness": "corpus", "numbers": list(range(1, 36)),
                             "gaps": []}},
            "planned": {"明史": {"title": "明史", "status": "planned",
                                "expected": 332, "declared": 332,
                                "available": None, "witness": "declared",
                                "numbers": []}}}

    def test_missing_snapshot_degrades_to_unknown(self):
        s = volume.scope_of({"available": False, "reason": "没有快照",
                             "books": {}, "planned": {}}, "B1")
        self.assertEqual(s["status"], "unknown")
        self.assertFalse(s["known"])
        self.assertIsNone(s["expected"])
        self.assertIn("快照", s["reason"])

    def test_unknown_book_is_not_complete(self):
        """快照里没有这本书（新导入还没重建 manifest）→ unknown，不是 complete。"""
        s = volume.scope_of(self.SNAP, "NOPE")
        self.assertEqual(s["status"], "unknown")
        self.assertFalse(s["known"])

    def test_partial_book_reports_range(self):
        s = volume.scope_of(self.SNAP, "B1")
        self.assertEqual(s["status"], "partial")
        self.assertEqual((s["declared"], s["expected"]), (35, 50))
        self.assertTrue(s["known"])
        # 通行本有、库里没有的卷号（只对 witness=corpus 给）
        self.assertEqual(s["expected_missing"], list(range(36, 51)))

    def test_planned_book_by_title(self):
        s = volume.scope_of(self.SNAP, None, "明史")
        self.assertEqual(s["status"], "planned")
        self.assertTrue(s["known"])
        self.assertEqual(s["expected"], 332)

    def test_planned_book_has_no_expected_missing(self):
        """未入库的书「缺哪些卷」是数不出来的 —— 列出来就是造谣。"""
        s = volume.scope_of(self.SNAP, None, "明史")
        self.assertEqual(s["expected_missing"], [])


class TestHitScope(unittest.TestCase):
    """**有结果**时的收录范围（§28 的另一半）。

    §25 的四种情况全部是「搜不到」时说什么。搜到了的那一半同样会误判：
    搜《北齊書》得到「高歡」的结果，用户自然会以为那就是全部 —— 而缺的
    15 卷根本不在检索范围里。结果是对的，结论是错的。
    """

    @classmethod
    def setUpClass(cls):
        import sqlite3
        from scripts.pipeline import config
        if not config.DB_PATH.is_file():
            raise unittest.SkipTest("没有 history.db")
        cls.con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        cls.con.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def _scope(self, bid):
        from search import diagnose
        return diagnose.hit_scope(self.con.cursor(), bid)

    def test_partial_book_reports_range_with_hits(self):
        s = self._scope("KR2a0021")                      # 北齊書 35/50
        self.assertEqual(s["status"], "hit")
        self.assertEqual(s["book"]["volume_status"], "partial")
        self.assertIn("35/50", s["book"]["range_text"])
        self.assertEqual(len(s["book"]["expected_missing"]), 15)

    def test_complete_book_gives_no_warning_material(self):
        """完整史书照样给 scope（页面自己决定画不画），但**没有残缺可讲**。"""
        s = self._scope("KR2a0019")                      # 陳書 36/36
        self.assertEqual(s["status"], "hit")
        self.assertEqual(s["book"]["volume_status"], "complete")
        self.assertEqual(s["book"]["expected_missing"], [])

    def test_no_book_filter_has_no_scope(self):
        """全库检索不给 —— 那样每次搜索都挂一条免责声明，说多了等于没说。"""
        self.assertIsNone(self._scope(None))

    def test_unknown_book_is_none_not_a_guess(self):
        """解析不了的书 id：回 None，**不编一个 complete 出来**。"""
        self.assertIsNone(self._scope("NOSUCHBOOK"))


class TestArtifactFields(unittest.TestCase):
    """`volume_coverage.json` 的字段（计划书 §4 逐字列了 11 个）。

    这些字段**不是装饰**：`edition_id` / `edition_name` / `source` 是「这个数字
    是从哪部底本上数出来的」的答案，`kanripo_id` 是回溯到源仓库的把手。少了它们，
    「三國志 30 卷」就只是一个数 —— 说不出是哪一部的 30 卷，而同一部书换一个底本
    卷数就变（§11：Book 与 Edition 是两个实体）。
    """

    def _cat(self):
        from scripts.pipeline import catalog
        return catalog.try_load()

    def test_self_id_fills_the_spec_fields(self):
        cat = self._cat()
        if cat is None:
            self.skipTest("没有 corpus_catalog.json")
        from scripts.pipeline import manifest
        b = cat.books["KR2a0007"]
        got = manifest._self_id(
            {"book_id": "KR2a0007", "editions": [cat.edition_of(b)]}, cat.books)
        self.assertEqual(got["kanripo_id"], "KR2a0007")
        self.assertEqual(got["dynasty"], b.dynasty)
        self.assertEqual(got["edition_id"], "KR2a0007-wyg")
        self.assertIn("四庫", got["edition_name"])
        self.assertTrue(got["source"], "source 不能空 —— 那是「数字从哪来」的答案")

    def test_self_id_prefers_the_row_over_the_catalog(self):
        """未入库的书没有 Book 可查，dynasty / kanripo_id 只能从行上取。"""
        from scripts.pipeline import manifest
        got = manifest._self_id(
            {"book_id": "KR2a0038", "dynasty": "明", "kanripo_id": "KR2a0038",
             "editions": [{"edition_id": "KR2a0038-wyg",
                           "edition_name": "文淵閣四庫全書本", "source": "S"}]}, {})
        self.assertEqual(got["dynasty"], "明")
        self.assertEqual(got["kanripo_id"], "KR2a0038")
        self.assertEqual(got["edition_id"], "KR2a0038-wyg")

    def test_self_id_never_invents(self):
        """取不到就留空 —— 空字符串页面上显示「未登记」，编一个假值看不出来。"""
        from scripts.pipeline import manifest
        got = manifest._self_id({}, {})
        self.assertEqual(set(got.values()), {""})
        self.assertEqual(sorted(got), ["dynasty", "edition_id", "edition_name",
                                       "kanripo_id", "source"])


if __name__ == "__main__":
    unittest.main()
