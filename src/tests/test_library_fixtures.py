"""Step 10 — 真实史料上的分段抽查（需 HistoryLibrary 在场，缺则跳过）。"""
import unittest
from pathlib import Path

from scripts.pipeline import config
from scripts.pipeline.segmentation import segment_file

LIB = config.LIBRARY_DIR


def file_path(book: str, name: str) -> Path | None:
    p = LIB / book / name
    return p if p.is_file() else None


@unittest.skipUnless(LIB.is_dir(), f"library 不存在：{LIB}")
class TestRealFiles(unittest.TestCase):
    def _records(self, book, name):
        return segment_file(file_path(book, name))[1]

    # ---- tls 家族 ----
    def test_shangshu_appendix_059(self):
        recs = self._records("shangshu", "KR1b0001_059.txt")
        layers = {r.layer for r in recs if r.kind == "passage"}
        self.assertEqual(layers, {"appendix"})

    def test_shiji_divisions_and_noise(self):
        recs = self._records("shiji", "KR2a0001_201.txt")
        kinds = {r.kind for r in recs}
        self.assertIn("noise", kinds)          # 页底 '　　¶' 残行归 noise
        divs = {r.division for r in recs if r.division}
        self.assertTrue(divs)
        # 正文行号与文本都有（原始行不丢）
        mains = [r for r in recs if r.kind == "passage" and r.layer == "main"]
        self.assertGreater(len(mains), 100)
        self.assertTrue(all(r.text_orig for r in mains))

    def test_zuozhuan_ab(self):
        recs = self._records("zuozhuan", "KR1e0001_001.txt")
        head = [r for r in recs if r.kind == "heading"]
        self.assertTrue(any("隱公" in (r.juan or "") for r in head))
        # A1.1《隱公元年經》：年份卷题 section 带出（回归测试）
        self.assertTrue(any(r.section == "隱公元年經" for r in recs))
        abA = [r for r in recs if r.ab == "A" and r.kind == "passage"]
        abB = [r for r in recs if r.ab == "B" and r.kind == "passage"]
        self.assertGreater(len(abA), 50)
        self.assertGreater(len(abB), 50)
        # ¶ 断句句子完整入 passage
        self.assertTrue(any("元年春王正月" in r.text_orig for r in recs))

    def test_tls_comment_src(self):
        recs = self._records("shangshu", "KR1b0001_001.txt")
        srcs = [r for r in recs if r.kind == "comment" and r.source_reference]
        self.assertGreater(len(srcs), 0)
        self.assertTrue(srcs[0].source_reference["raw"].startswith("# src:"))

    # ---- SBCK 家族 ----
    def test_guoyu_preface_file(self):
        recs = self._records("guoyu", "KR2e0001_000.txt")
        self.assertEqual({r.layer for r in recs if r.kind == "passage"}, {"preface"})

    def test_guoyu_paren_split(self):
        recs = self._records("guoyu", "KR2e0001_001.txt")
        cands = [r for r in recs if r.layer == "commentary_candidate"]
        self.assertGreater(len(cands), 50)      # 韦昭注括号
        # 偏移连续可整行还原
        by_row = {}
        for r in recs:
            by_row.setdefault(r.row_no, []).append(r)
        multi = [(no, rs) for no, rs in by_row.items() if len(rs) > 1]
        self.assertGreater(len(multi), 10)
        no, rs = multi[0]
        rs.sort(key=lambda r: r.char_start or 0)
        joined = "".join(r.text_orig for r in rs)
        self.assertIn("穆王將征犬戎", joined)

    def test_zhanguoce_part_blocks(self):
        recs = self._records("zhanguoce", "KR2e0003_000.txt")
        parts = [r for r in recs if r.kind == "part"]
        self.assertGreater(len(parts), 0)
        layers = {r.layer for r in recs if r.kind == "passage"}
        # 序/跋/目録都按 FILE 段名分开，绝不与正文混
        self.assertTrue({"preface", "backmatter", "toc"} <= layers or
                        len(layers & {"preface", "toc", "backmatter"}) >= 2)

    def test_pb_inheritance(self):
        # tls 页码行与正文行内标记都应被保留在某条记录上
        recs = self._records("shiji", "KR2a0001_201.txt")
        pb_rows = [r for r in recs if r.pb_raw]
        self.assertGreater(len(pb_rows), 10)
        self.assertTrue(all("<pb:" in r.pb_raw for r in pb_rows))

    def test_kr_codes_preserved_in_guoyu(self):
        recs = self._records("guoyu", "KR2e0001_002.txt")
        kr = [r for r in recs if r.special_chars]
        self.assertGreater(len(kr), 0)
        self.assertTrue(all(c.startswith("&KR") and c.endswith(";")
                            for r in kr for c in r.special_chars))


if __name__ == "__main__":
    unittest.main()
