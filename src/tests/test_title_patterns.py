"""第六点三阶段 6.3-C③ / 6.3-E —— 卷题规则：变体字形、卷题正则、卷题当篇名粒度。

这批规则是**三國志/晉書导入时实测出来**的，不是照着计划书猜的；它们的两条纪律在
这里钉住：

1. **匹配放宽、原文不改**：卷(U+5377)/巻(U+5DFB)、晉(U+6649)/晋(U+664B) 在同一部
   语料里混用（晉書 001 写 `晉書巻一`、033 写 `晉書卷三十三`；三國志卷首题用 卷、
   卷末考證题用 巻）。放宽只发生在正则里，`text_orig` 一字不动，派生标签再归一。
2. **卷题是不是篇名粒度由书声明**（catalog 的 `juan_as_section`）：三國志正文里
   没有篇题行，不声明就整卷 0 section（= manifest 的 FAIL）；前漢書/後漢書每卷
   跟着若干 `第N` 篇题，卷题只推进卷次，行为不许变。

`TestJuanAsSection` 用**真语料形状的临时文件**跑 `segment_file`：不需要
HistoryLibrary 在场，也能端到端验证「catalog 声明 → 分段 → section 标签」这条链。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline import title_patterns as T                        # noqa: E402
from scripts.pipeline.segmentation import segment_file                  # noqa: E402

TMP = Path(__file__).resolve().parent / "_tmp_titlepatterns"

# 三國志 001 的真实开头（实测摘录）：卷首题 + 撰者行 + 卷目行（傳名并列、有的带
# 括号注、有的不带），然后正文直接开始 —— 全书正文层没有一行能当 section。
SGZ_BODY = """# -*- mode: mandoku-view -*-
#+TITLE: 三國志
#+PROPERTY: ID KR2a0012
#+PROPERTY: BASEEDITION WYG
#+PROPERTY: JUAN 卷一
<pb:KR2a0012_WYG_001-1a>¶
欽定四庫全書¶
　魏志卷一¶
晉著作郎巴西中正安漢陳　壽撰¶
　呂布　張邈(陳登)　臧洪(陳容)¶
夏侯惇字元讓沛國譙人夏侯嬰之後也¶
　魏志巻一考證¶
呂布字奉先五原九原人也監本作九原人¶
"""

# 晉書 001 的真实开头：卷题（巻）→ 御撰 → 篇题 `帝紀第一` → 傳名行 → 正文。
JS_BODY = """# -*- mode: mandoku-view -*-
#+TITLE: 晉書
#+PROPERTY: ID KR2a0015
#+PROPERTY: BASEEDITION WYG
#+PROPERTY: JUAN 卷一
<pb:KR2a0015_WYG_001-1a>¶
欽定四庫全書¶
　晉書巻一¶
唐　太　宗　文　皇　帝　御　撰¶
　帝紀第一¶
宣帝¶
宣皇帝諱懿字仲達河内溫縣孝敬里人姓司馬氏其¶
"""


def make_file(book_dir: str, name: str, text: str) -> Path:
    d = TMP / book_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


def sections_of(recs):
    """复刻 sqlite_store 的 sections 构造：label 一变就是新 section。"""
    out, cur = [], None
    for r in recs:
        if r.section and r.section != cur:
            out.append((r.row_no, r.section, r.section_method, r.section_confidence))
            cur = r.section
    return out


def text_at(recs, needle: str):
    hits = [r for r in recs if needle in r.text_orig]
    assert hits, f"语料里没有 {needle}"
    return hits[0]


class TestVariantLabels(unittest.TestCase):
    """卷/巻、晉/晋：匹配放宽 + 派生标签归一。"""

    def test_normalize_label(self):
        self.assertEqual(T.normalize_label("魏志巻九"), "魏志卷九")
        self.assertEqual(T.normalize_label("晋書卷十一"), "晉書卷十一")
        self.assertEqual(T.normalize_label("魏志卷九"), "魏志卷九")   # 幂等
        self.assertEqual(T.normalize_label("志第一"), "志第一")      # 无变体不动

    def test_expand_variants(self):
        self.assertEqual(T._expand_variants("魏志"), "魏志")
        self.assertEqual(T._expand_variants("晉書"), "[晉晋]書")

    def test_juan_re_matches_both_volume_glyphs(self):
        re_ = T.build_wyg_juan_re("晉書")
        for line in ("　晉書巻一¶", "晉書卷三十三¶", "晋書巻十一¶"):
            m = re_.match(line)
            self.assertIsNotNone(m, line)
            self.assertTrue(m.group("juan").startswith(("卷", "巻")), line)

    def test_juan_re_prefix_takes_juan_from_its_own_group(self):
        # 三國志的卷题前缀是 `魏志` 而不是书名 `三國志`：卷次必须由正则自己的
        # juan 组给，不能靠「切掉书名长度」——那样切出来是残字。
        re_ = T.build_wyg_juan_re("三國志", ["三國志", "魏志", "蜀志", "吳志"])
        m = re_.match("　魏志卷二十一¶")
        self.assertEqual(m.group("juan"), "卷二十一")
        self.assertIsNone(re_.match("　呂布　張邈(陳登)¶"))

    def test_juan_re_does_not_eat_kaozheng_heading(self):
        # `魏志巻一考證¶` 是卷末考證题（正文行），不是卷题：认成卷题会把考證
        # 整段并进上一卷的区间。
        re_ = T.build_wyg_juan_re("三國志", ["魏志"])
        self.assertIsNone(re_.match("　魏志巻一考證¶"))

    def test_juan_re_needs_a_name(self):
        self.assertIsNone(T.build_wyg_juan_re(""))       # 无 TITLE 也无前缀 → 不猜

    def test_ming_paren_is_vocabulary_not_a_default(self):
        # 形态边界：行首无缩进、括号里 ≤3 字且不是注文起首字、行内没有别的东西
        m = T.WYG_MING_PAREN_RE.match("武帝(操)¶")
        self.assertEqual(m.group("name"), "武帝")
        self.assertIsNone(T.WYG_MING_PAREN_RE.match("　秦(按此則懷王死於…)¶"))
        self.assertIsNone(T.WYG_MING_PAREN_RE.match("呂布　張邈(陳登)　臧洪(陳容)¶"))

    def test_registry_names_unique_and_self_consistent(self):
        for name, p in T.PATTERNS.items():
            self.assertEqual(name, p.name)
            self.assertIn(p.method, ("title", "structure"))
        self.assertEqual(T.PATTERNS["wyg_ming_paren"].confidence, 0.6)


class TestJuanAsSection(unittest.TestCase):
    """catalog 声明 → 分段行为：三國志要 section，晉書不要。"""

    def tearDown(self):
        for p in sorted(TMP.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        if TMP.exists():
            TMP.rmdir()

    def test_sanguozhi_juan_becomes_section(self):
        p = make_file("sanguozhi", "KR2a0012_001.txt", SGZ_BODY)
        _, recs, _ = segment_file(p)
        secs = sections_of(recs)
        self.assertEqual([s[1] for s in secs], ["魏志卷一"])
        rowno, label, method, conf = secs[0]
        self.assertEqual(label, "魏志卷一")
        self.assertEqual(method, "title")
        self.assertEqual(conf, T.WYG_JUAN_CONFIDENCE)
        # 卷题行之后（含卷目行、考證题）的正文都归这一卷
        self.assertEqual(text_at(recs, "夏侯惇字元讓").section, "魏志卷一")
        self.assertEqual(text_at(recs, "呂布字奉先").section, "魏志卷一")
        # juan 标签保留 `魏志` 前缀：魏/蜀/吳 三志各自从卷一数起，去掉就分不出来了
        self.assertEqual(text_at(recs, "魏志卷一").juan, "魏志卷一")

    def test_juan_glyph_variant_normalized_in_section_label(self):
        # 卷末考證题写 `魏志巻N`（巻 U+5DFB）时，不得裂出第二条 section
        body = SGZ_BODY.replace("　魏志卷一¶", "　魏志巻一¶")
        p = make_file("sanguozhi", "KR2a0012_002.txt", body)
        _, recs, _ = segment_file(p)
        self.assertEqual([s[1] for s in sections_of(recs)], ["魏志卷一"])

    def test_jinshu_juan_does_not_become_section(self):
        p = make_file("jinshu", "KR2a0015_001.txt", JS_BODY)
        _, recs, _ = segment_file(p)
        # 篇名粒度仍是 `第N` 篇题（与前漢書/後漢書一致），卷题只记 juan
        self.assertEqual([s[1] for s in sections_of(recs)], ["帝紀第一"])
        juan_hit = text_at(recs, "晉書巻一")
        self.assertEqual(juan_hit.juan, "卷一")               # 巻 → 卷，书名前缀去掉
        self.assertIsNone(juan_hit.section)                   # 卷题不声明 section
        self.assertIsNone(text_at(recs, "御　撰").section)     # 篇题之前无归属
        self.assertEqual(text_at(recs, "宣皇帝諱懿").section, "帝紀第一")

    def test_book_title_prefix_stripped_from_juan_label(self):
        # 前漢書形状：卷题前缀就是书名 → juan 记 `卷一上`（库内 115 条 juan 的既有形状），
        # 篇名仍由 `第N` 篇题给，卷题不当 section（juan_as_section 缺省 false）
        body = """# -*- mode: mandoku-view -*-
#+TITLE: 前漢書
#+PROPERTY: ID KR2a0007
#+PROPERTY: BASEEDITION WYG
#+PROPERTY: JUAN 卷一上
<pb:KR2a0007_WYG_001-1a>¶
欽定四庫全書¶
　前漢書卷一上¶
　高帝紀第一上¶
高祖為人隆準而龍顏¶
"""
        p = make_file("qianhanshu", "KR2a0007_001.txt", body)
        _, recs, _ = segment_file(p)
        self.assertEqual([s[1] for s in sections_of(recs)], ["高帝紀第一上"])
        self.assertEqual(text_at(recs, "前漢書卷一上").juan, "卷一上")
        self.assertEqual(text_at(recs, "高祖為人").section, "高帝紀第一上")

    def test_no_catalog_no_juan_section(self):
        # 目录不在册的书目录（book=None）→ 不许凭空给卷题加 section
        p = make_file("nosuchbook", "KR2a9999_001.txt",
                      SGZ_BODY.replace("KR2a0012", "KR2a9999"))
        _, recs, _ = segment_file(p)
        self.assertEqual(sections_of(recs), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
