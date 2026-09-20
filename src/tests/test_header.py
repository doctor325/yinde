"""Step 10 — 文件头解析（纯逻辑，无外部依赖）。"""
import unittest

from scripts.pipeline.kanripo_header import FileHeader, file_no_of, family_of, split_header

SBCK_HEAD = """# -*- mode: mandoku-view -*-
#+TITLE: 國語
#+DATE: 2015-09-10 23:24:10.199131
#+PROPERTY: ID KR2e0001
#+PROPERTY: JUAN 0
#+PROPERTY: BASEEDITION SBCK
#+PROPERTY: WITNESS SBCK
#+PROPERTY: FILE SB02n0041-001國語-卷第一.
#+WEIRD_KEY: 保留的未知键
"""

TLS_HEAD = """#+TITLE: 春秋左傳
#+DATE: 2016-08-10 10:06:43
#+PROPERTY: ID KR1e0001
#+PROPERTY: BASEEDITION tls
#+PROPERTY: JUAN 0
"""


class TestSplitHeader(unittest.TestCase):
    def test_sbck_keys_and_body_boundary(self):
        header, body = split_header(SBCK_HEAD + "正文行¶\n第二行")
        self.assertEqual(header.metadata["TITLE"], "國語")
        self.assertEqual(header.metadata["ID"], "KR2e0001")
        self.assertEqual(header.metadata["BASEEDITION"], "SBCK")
        self.assertEqual(header.metadata["FILE"], "SB02n0041-001國語-卷第一.")
        # 未知键原样保留（不猜不丢）
        self.assertEqual(header.metadata["WEIRD_KEY"], "保留的未知键")
        self.assertIn("#+WEIRD_KEY", header.raw_header)
        self.assertEqual(body, "正文行¶\n第二行")
        self.assertEqual(header.mode_line, "# -*- mode: mandoku-view -*-")

    def test_body_starts_at_first_non_hash(self):
        header, body = split_header(TLS_HEAD)
        self.assertEqual(header.metadata["BASEEDITION"], "tls")
        self.assertEqual(header.metadata["JUAN"], "0")

    def test_no_header(self):
        header, body = split_header("純正文")
        self.assertEqual(header.metadata, {})
        self.assertEqual(body, "純正文")

    def test_file_no_of(self):
        self.assertEqual(file_no_of("KR2e0001_001.txt"), 1)
        self.assertEqual(file_no_of("KR2a0001_100.txt"), 100)
        self.assertIsNone(file_no_of("Readme.org"))

    def test_family_of(self):
        h = FileHeader(metadata={"BASEEDITION": "SBCK"})
        self.assertEqual(family_of(h), "sbck")
        h = FileHeader(metadata={"BASEEDITION": "tls"})
        self.assertEqual(family_of(h), "tls")
        h = FileHeader(metadata={})
        self.assertIsNone(family_of(h))


if __name__ == "__main__":
    unittest.main()
