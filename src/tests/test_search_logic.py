"""Step 5 — 检索模块纯逻辑测试（不碰数据库/文件系统）：分词路径、短语转义、
书名/版本别名、LIKE 转义、简繁映射入口。

真实数据上的命中数断言见 test_search_api.py。
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from search import zh
from search.engine import (
    _like_cond,
    fts_match_text,
    plan_query,
    resolve_book,
    resolve_edition,
)


class TestPlanQuery(unittest.TestCase):
    def test_long_short_split(self):
        long_, short = plan_query("齊桓公 卒")
        self.assertEqual(long_, ["齊桓公"])
        self.assertEqual(short, ["卒"])

    def test_single_long(self):
        long_, short = plan_query("城濮之戰")
        self.assertEqual(long_, ["城濮之戰"])
        self.assertEqual(short, [])

    def test_two_short_words(self):
        long_, short = plan_query("管仲 城濮")
        self.assertEqual(long_, [])
        self.assertEqual(short, ["管仲", "城濮"])

    def test_whitespace_ignored(self):
        long_, _ = plan_query("  齊桓公   ")
        self.assertEqual(long_, ["齊桓公"])


class TestFtsMatchText(unittest.TestCase):
    def test_phrases(self):
        self.assertEqual(fts_match_text(["齊桓公", "宋襄公"]),
                         '"齊桓公" "宋襄公"')

    def test_quote_escaped_doubled(self):
        # FTS5 引号转义为双写（本库语料不含引号，防御性）
        self.assertEqual(fts_match_text(['a"b']), '"a""b"')


class TestLikeEsc(unittest.TestCase):
    def test_percent_underscore_escaped(self):
        cond, arg = _like_cond("x%_y")
        self.assertEqual(cond, "p.normalized_text LIKE ? ESCAPE '\\'")
        self.assertEqual(arg, "%x\\%\\_y%")

    def test_plain(self):
        _, arg = _like_cond("管仲")
        self.assertEqual(arg, "%管仲%")


class TestZh(unittest.TestCase):
    @unittest.skipUnless(zh.to_traditional("齐") == "齊",
                         "系统无简繁映射（恒等回退环境）")
    def test_sim_to_trad(self):
        out = zh.to_traditional("齐桓公城濮长勺鸿门苏秦")
        self.assertEqual(out, "齊桓公城濮長勺鴻門蘇秦")

    @unittest.skipUnless(zh.to_simplified("齊") == "齐",
                         "系统无简繁映射（恒等回退环境）")
    def test_trad_to_sim(self):
        self.assertEqual(zh.to_simplified("戰國策"), "战国策")


def _fake_books_cursor():
    """最小 books 表（只含别名发现所需列），不碰真实库。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE books (book_id TEXT, book_dir TEXT, title TEXT)")
    conn.execute("INSERT INTO books VALUES ('KR2e0001','guoyu','國語')")
    conn.execute("INSERT INTO books VALUES ('KR2e0003','zhanguoce','戰國策')")
    return conn.cursor()


class TestResolveBook(unittest.TestCase):
    def setUp(self):
        self.cur = _fake_books_cursor()

    def test_none_or_blank(self):
        self.assertIsNone(resolve_book(self.cur, None))
        self.assertIsNone(resolve_book(self.cur, ""))
        self.assertIsNone(resolve_book(self.cur, "  "))

    def test_book_id(self):
        self.assertEqual(resolve_book(self.cur, "KR2e0003"), "KR2e0003")

    def test_db_title_and_dir(self):
        self.assertEqual(resolve_book(self.cur, "國語"), "KR2e0001")
        self.assertEqual(resolve_book(self.cur, "guoyu"), "KR2e0001")

    def test_simpl_short_name_map(self):
        self.assertEqual(resolve_book(self.cur, "国语"), "KR2e0001")
        self.assertEqual(resolve_book(self.cur, "战国策"), "KR2e0003")

    def test_bookmark_brackets_and_space(self):
        self.assertEqual(resolve_book(self.cur, "《 國語 》"), "KR2e0001")

    def test_short_name_net_of_mapping(self):
        # 「尚書」本身已在繁体短名表，无需系统映射
        self.assertEqual(resolve_book(self.cur, "尚書"), "KR1b0001")

    def test_unknown_raises_with_hint(self):
        with self.assertRaises(ValueError) as cm:
            resolve_book(self.cur, "三字经")
        self.assertIn("未识别的史书", str(cm.exception))
        self.assertIn("可用", str(cm.exception))


class TestResolveEdition(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(resolve_edition(None))
        self.assertIsNone(resolve_edition(""))

    def test_valid_lowercase(self):
        self.assertEqual(resolve_edition("SBCK"), "sbck")
        self.assertEqual(resolve_edition("Tls"), "tls")

    def test_invalid_ignored(self):
        self.assertIsNone(resolve_edition("sbck2"))


if __name__ == "__main__":
    unittest.main()
