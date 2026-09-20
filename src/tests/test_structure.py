"""Step 10 — 集中规则（pb/org 标题/左传 A-B/括号注/分层/归一化），纯逻辑。"""
import unittest

from scripts.pipeline import config
from scripts.pipeline.structure import (
    clean_title,
    file_layer_defaults,
    is_org_heading,
    make_normalized,
    parse_pb,
    pending_split_parens,
    zuozhuan_classify,
)


class TestPb(unittest.TestCase):
    def test_parse(self):
        d = parse_pb("<pb:KR2a0001_tls_100-2a>")
        self.assertEqual(d["book_id"], "KR2a0001")
        self.assertEqual(d["edition"], "tls")
        self.assertEqual(d["block"], "100")
        self.assertEqual(d["page"], "2")
        self.assertEqual(d["side"], "a")

    def test_parse_sbck(self):
        d = parse_pb("<pb:KR2e0001_SBCK_000-1b>")
        self.assertEqual(d["page"], "1")
        self.assertEqual(d["side"], "b")

    def test_non_strict_marker(self):
        # 异常标记不抛错、不猜测，raw 原样返回
        d = parse_pb("<pb:KR2e0001_SBCK_000-1x>")
        self.assertEqual(d["raw"], "<pb:KR2e0001_SBCK_000-1x>")


class TestOrgHeading(unittest.TestCase):
    def test_h2_division(self):
        lvl, code, title = is_org_heading("** 1 紀")
        self.assertEqual((lvl, code, title), ("h2", "1", "紀"))

    def test_h2_shangshu(self):
        lvl, code, title = is_org_heading("** 1 《堯典》")
        self.assertEqual(title, "堯典")

    def test_h3(self):
        lvl, code, title = is_org_heading("*** 2.1　《三代世表》")
        self.assertEqual((lvl, code, title), ("h3", "2.1", "三代世表"))

    def test_plain_line_not_heading(self):
        self.assertEqual(is_org_heading("元年春王正月。"), ("", None, None))


class TestZuozhuan(unittest.TestCase):
    def test_ab_head(self):
        kind, d = zuozhuan_classify("B《傳》")
        self.assertEqual(kind, "heading")
        self.assertEqual(d["ab"], "B")
        self.assertIsNone(d["code"])

    def test_ab_code_head_title_kept(self):
        # 回归：标题必须在 m.end() 之后，不得被 .*$ 吞掉
        kind, d = zuozhuan_classify("A1.1《隱公元年經》")
        self.assertEqual(kind, "heading")
        self.assertEqual(d["ab"], "A")
        self.assertEqual(d["code"], "1.1")
        self.assertEqual(clean_title(d["title"]), "隱公元年經")

    def test_item(self):
        kind, d = zuozhuan_classify("A1.1.1元年春王正月。")
        self.assertEqual(kind, "passage")
        self.assertEqual(d["ab"], "A")
        self.assertEqual(d["code"], "1.1.1")
        self.assertEqual(d["text"], "元年春王正月。")

    def test_item_b1(self):
        kind, d = zuozhuan_classify("B1惠公元妃孟子。")
        self.assertEqual(kind, "passage")
        self.assertEqual(d["code"], "1")

    def test_plain(self):
        kind, d = zuozhuan_classify("惠公元妃孟子。")
        self.assertEqual(kind, "none")


class TestParens(unittest.TestCase):
    def test_split_balanced(self):
        pieces = pending_split_parens("穆王將征犬戎(注文甲/注文乙)¶")
        self.assertEqual(len(pieces), 3)
        self.assertEqual(pieces[0][3], "main")
        self.assertEqual(pieces[1][3], "paren")
        self.assertEqual(pieces[1][0], "(注文甲/注文乙)")
        # 偏移连续：可拼回原行
        self.assertEqual("".join(p[0] for p in pieces), "穆王將征犬戎(注文甲/注文乙)¶")

    def test_unbalanced_returns_whole(self):
        pieces = pending_split_parens("正文(未閉合¶")
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0][3], "main")

    def test_consecutive_parens(self):
        pieces = pending_split_parens("甲(一)乙(二)")
        tags = [p[3] for p in pieces]
        self.assertEqual(tags, ["main", "paren", "main", "paren"])

    def test_no_paren(self):
        pieces = pending_split_parens("純正文¶")
        self.assertEqual(len(pieces), 1)


class TestLayers(unittest.TestCase):
    def test_overrides(self):
        self.assertEqual(file_layer_defaults("shangshu", 59, "tls", {})[0], "appendix")
        self.assertEqual(file_layer_defaults("guoyu", 0, "sbck", {})[0], "preface")
        self.assertEqual(file_layer_defaults("zhanguoce", 0, "sbck", {})[0], "preface")

    def test_sbck_fallback(self):
        layer, status, _ = file_layer_defaults("某書", 0, "sbck", {})
        self.assertEqual(layer, "preface")
        self.assertEqual(status, "pending_section")  # 未确认首文件→待定

    def test_main(self):
        self.assertEqual(file_layer_defaults("guoyu", 1, "sbck", {})[0], "main")


class TestNormalized(unittest.TestCase):
    def test_passage(self):
        n = make_normalized("<pb:KR2a0001_tls_100-2a>　　元年春王正月。¶", "passage", "main")
        self.assertEqual(n, "元年春王正月。")

    def test_kr_kept_in_normalized(self):
        n = make_normalized("鄭人伐&KR0632;。¶", "passage", "main")
        self.assertEqual(n, "鄭人伐&KR0632;。")

    def test_nonpassage_none(self):
        self.assertIsNone(make_normalized("** 1 紀", "heading", "structure"))


if __name__ == "__main__":
    unittest.main()
