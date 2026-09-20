"""第六点三阶段 6.3-F —— 繁简与 Unicode 回归，**全库口径**。

为什么判据要落到「整个库一条不漏」而不是抽几个样本：`search/dual_text.simplify`
的回退是**静默**的 —— 转换不可靠时返回原文（繁体），用户看不出这里本该有简体。
样本里恰好没有那个字，测试就一直绿。6.3 一次加 12 部正史，生僻字、擴展區字
（Ext-B 的 𫝊/𤣥 之类）比先秦几部密得多，抽样的置信度会随语料变大而**下降**。
实测代价：全表扫描 0.6 秒、全表 simplify 2.0 秒 —— 不是一个需要省的开销。

三件事：
  ① 全库 `text_orig` 与 `normalized_text` 的孤立代理项 = 0，判据**复用**
     `search/dual_text.py:51` 的 `_has_lone_surrogate`，不另写一份；
  ② 每一条 passage 过一遍 `simplify`：`ok=False` 必须一字不差回退原文，
     `ok=True` 必须长度不变且不含孤立代理项。回退条数实测为 0，见下；
  ③ Python ↔ JS 的繁简表对拍：样本取**语料里真有的**星形字与 `&KR…;` 实体行
     （不是手编的），经 `scripts.site.check_engine` 的 node 通道比 sha256 摘要。

③ 与 `check_engine` 的 ①②（全量摘要、语料全部单字）分工不同：那边证明两边
在**当前**语料上一致，这边钉的是**让那边有意义的样本还在**（星形字与实体行）。
"""
import hashlib
import json
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline import config                                    # noqa: E402
from scripts.site import check_engine                                  # noqa: E402
from search import dual_text, zh                                       # noqa: E402

HAS_DB = config.DB_PATH.is_file()
TMP = Path(__file__).resolve().parent / "_tmp_unicode"

# 星形字（>U+FFFF）在语料里的**实测基数**：99 个不同的字、4,222 条 passage
# （6.3 第一批，库内 9 部书）。语料只增不减，所以这是下限而非等值断言 ——
# 真掉下去了，说明扫描或导入出了问题，而不是「本来就少」。
ASTRAL_CHARS_FLOOR = 99
ASTRAL_ROWS_FLOOR = 4000


def is_astral(ch: str) -> bool:
    return ord(ch) > 0xFFFF


def ro_conn():
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def why(text: str) -> str:
    """把可疑字符串打印成可读的形状：码位 + 片段。"""
    head = text[:40]
    return f"{head!r}（{len(text)} 字，码位 {' '.join(hex(ord(c)) for c in head[:12])}…）"


@unittest.skipUnless(HAS_DB, "需先运行 python -m scripts.pipeline.run_all")
class TestCorpusUnicode(unittest.TestCase):
    """全库扫描只做一次，三个用例共用结果。"""

    @classmethod
    def setUpClass(cls):
        conn = ro_conn()
        try:
            cls.rows = conn.execute(
                "SELECT passage_id, kind, text_orig, normalized_text FROM passages"
                " ORDER BY passage_id").fetchall()
        finally:
            conn.close()
        cls.astral_rows = [r for r in cls.rows if any(is_astral(c) for c in r["text_orig"])]
        cls.kr_rows = [r for r in cls.rows if "&KR" in r["text_orig"]]
        cls.astral_chars = sorted({c for r in cls.astral_rows
                                   for c in r["text_orig"] if is_astral(c)})

    # ---- ① 孤立代理项 -------------------------------------------------

    def test_no_lone_surrogate_in_whole_db(self):
        """孤立代理项（U+D800–DFFF 落单）是**非法 Unicode**：JSON 一序列化就成
        `\\udXXX`，浏览器只能显示「�」。判据用 dual_text 自己那个 —— 判据分两处
        写，就会有一天两处不一致而没人知道哪处是对的。
        """
        bad_orig = [(r["passage_id"], r["text_orig"]) for r in self.rows
                    if dual_text._has_lone_surrogate(r["text_orig"])]
        bad_norm = [(r["passage_id"], r["normalized_text"]) for r in self.rows
                    if dual_text._has_lone_surrogate(r["normalized_text"] or "")]
        self.assertEqual(
            bad_orig, [], f"text_orig 含孤立代理项 {len(bad_orig)} 条，"
            f"例：{[(p, why(t)) for p, t in bad_orig[:3]]}")
        self.assertEqual(
            bad_norm, [], f"normalized_text 含孤立代理项 {len(bad_norm)} 条，"
            f"例：{[(p, why(t)) for p, t in bad_norm[:3]]}")

    def test_replacement_char_is_reported_not_asserted(self):
        """U+FFFD（替换字符）**不断言为 0**：它若是语料里真有的字符，那是转录的
        事实，我们照实显示；把它判成错误会逼着下游去「修」原文（§2.2 只读）。
        但它的数量要报出来 —— 突然涨了就说明解码环节变了。
        """
        hits = [r for r in self.rows if "�" in r["text_orig"]]
        if hits:
            print(f"   提示：{len(hits)} 条正文含 U+FFFD（语料原文如此，不判失败），"
                  f"例 pid={hits[0]['passage_id']} {why(hits[0]['text_orig'])}")

    def test_astral_material_still_present(self):
        """星形字材料还在，上面那条断言才不是空的。"""
        self.assertGreaterEqual(len(self.astral_chars), ASTRAL_CHARS_FLOOR,
                                f"语料里的星形字只剩 {len(self.astral_chars)} 个"
                                f"（实测基数 {ASTRAL_CHARS_FLOOR}）")
        self.assertGreaterEqual(len(self.astral_rows), ASTRAL_ROWS_FLOOR)

    # ---- ② 全表 simplify ----------------------------------------------

    def test_simplify_never_mangles_a_passage(self):
        """419,387 条正文逐条过 simplify，只认两条不变量：
        `ok=False` ⇒ 一字不差回退原文；`ok=True` ⇒ 长度不变且无孤立代理项。
        """
        fallbacks, mangled, n = [], [], 0
        for r in self.rows:
            if r["kind"] != "passage":
                continue
            src = r["text_orig"]
            res = dual_text.simplify(src)
            n += 1
            if not res["ok"]:
                fallbacks.append((r["passage_id"], src))
                if res["text"] != src:
                    mangled.append((r["passage_id"], src, res["text"]))
            elif len(res["text"]) != len(src) or dual_text._has_lone_surrogate(res["text"]):
                mangled.append((r["passage_id"], src, res["text"]))
        print(f"   过 simplify {n:,} 条：回退 {len(fallbacks)}，畸形 {len(mangled)}")
        self.assertGreater(n, 400000)                     # 别把「库是空的」当通过
        self.assertEqual(
            mangled, [], f"simplify 改坏了 {len(mangled)} 条正文（长度变了或含孤代理项），"
            f"例：{[(p, why(a), why(b)) for p, a, b in mangled[:3]]}")
        # 回退 = 用户拿不到简体版，是**静默降级**：实测为 0。真出现时先看 pid 是不是
        # 语料真有的怪字符，再决定是放宽判据还是接受降级 —— 但不要直接删这条断言，
        # 那会把「简体悄悄没了」重新变回不可见。
        self.assertEqual(
            fallbacks, [], f"有 {len(fallbacks)} 条正文的简体转换不可靠、已静默回退繁体，"
            f"例：{[(p, why(t)) for p, t in fallbacks[:3]]}")

    # ---- ③ Python ↔ JS -------------------------------------------------

    def _samples(self) -> list[str]:
        """对拍样本：语料里的星形字 + 含星形字的正文 + 含 `&KR…;` 的正文
        ＋ check_engine 的固定样本（含 ASTRAL_SAMPLES）。全部来自数据，
        没有一个是手编的 —— 手编的样本测不出「语料里长出来的怪字符」。
        """
        return (self.astral_chars
                + [r["text_orig"] for r in self.astral_rows[:20]]
                + [r["text_orig"] for r in self.kr_rows[:20]]
                + list(check_engine.ASTRAL_SAMPLES) + list(check_engine.MULTI_SAMPLES))

    @unittest.skipUnless(sys.platform == "win32" and zh.to_simplified("齊") == "齐",
                         "非 Windows / 无系统简繁映射：search.zh 恒等回退，对拍没有信息量")
    @unittest.skipUnless(shutil.which("node") is not None, "找不到 node，无法跑 JS 侧")
    def test_python_and_js_agree_on_rare_glyphs(self):
        samples = self._samples()
        TMP.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(TMP, ignore_errors=True))
        path = TMP / "samples.json"
        path.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
        try:
            js = check_engine.run_node("zh-table", samples=path)
        except SystemExit as exc:                      # node 侧非零退出
            self.fail(f"node 侧失败：{exc}")

        py_t = [zh.to_traditional(s) for s in samples]
        py_s = [zh.to_simplified(s) for s in samples]
        h_py, h_js = hashlib.sha256(), hashlib.sha256()
        for i, (t, s) in enumerate(zip(py_t, py_s)):
            h_py.update(f"{i}|".encode())
            h_py.update(t.encode("utf-16-be", "surrogatepass"))
            h_py.update(s.encode("utf-16-be", "surrogatepass"))
            h_js.update(f"{i}|".encode())
            h_js.update(js["toTraditional"][i].encode("utf-16-be", "surrogatepass"))
            h_js.update(js["toSimplified"][i].encode("utf-16-be", "surrogatepass"))
        bad_t = [i for i, (a, b) in enumerate(zip(py_t, js["toTraditional"])) if a != b]
        bad_s = [i for i, (a, b) in enumerate(zip(py_s, js["toSimplified"])) if a != b]
        print(f"   样本 {len(samples)} 条（星形字 {len(self.astral_chars)} 个）："
              f"简→繁不符 {len(bad_t)}，繁→简不符 {len(bad_s)}，"
              f"摘要 {'一致' if h_py.hexdigest() == h_js.hexdigest() else '**不一致**'}")

        # 表是空的（恒等回退）时上面的「全对」毫无信息量 —— 本用例的前提要说清
        self.assertGreater(js["size"]["s2t"], 1000, f"JS 侧简→繁表没加载起来：{js['size']}")
        self.assertGreater(js["size"]["t2s"], 1000, f"JS 侧繁→简表没加载起来：{js['size']}")
        self.assertEqual(
            bad_t + bad_s, [],
            "Python 与 JS 在星形字/实体行上不一致："
            + "；".join(f"[{i}] 样本 {why(samples[i])} py={py_t[i]!r}/{py_s[i]!r} "
                        f"js={js['toTraditional'][i]!r}/{js['toSimplified'][i]!r}"
                        for i in (bad_t + bad_s)[:3]))
        self.assertEqual(h_py.hexdigest(), h_js.hexdigest())


class TestAstralSampleListIsKept(unittest.TestCase):
    """`check_engine.ASTRAL_SAMPLES` **不得因罕见而删除**（计划书 6.3-F 原话）。

    这几条是历史上踩过的坑：Python 侧曾把含星形字的串截断（尾巴少一截、单字
    甚至只剩孤立代理项），JS 只好照抄那个 bug 才「一致」；根因修好后才把它们
    摆进样本表。删掉它们不会让任何东西变红 —— 这正是要用一条断言钉住的原因。
    """

    def test_samples_still_there_and_still_astral(self):
        astral = [s for s in check_engine.ASTRAL_SAMPLES if any(is_astral(c) for c in s)]
        self.assertGreaterEqual(len(astral), 5,
                                "ASTRAL_SAMPLES 被删空了：星形字回归会重新变成"
                                "「靠样本里不出现星形字来回避」")
        for s in check_engine.ASTRAL_SAMPLES:
            self.assertIn(s, check_engine.MULTI_SAMPLES, f"{s!r} 没进对拍样本表")


if __name__ == "__main__":
    unittest.main(verbosity=2)
