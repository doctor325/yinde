"""第四阶段真实问题测试（任务书 §14 Step 8）。

不只查 HTTP 200 —— 每个问题都断言：意图、实体、扩展词、真实命中内容、
片段完整性、跨层/跨文件。数据来自真实语料（只读），不伪造命中。

跑法（在 HistoryAI 目录下，需先启动 api.main）：
    PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/test_phase4_questions.py
"""
import json
import sqlite3
import sys
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.pipeline import config  # noqa: E402

BASE = "http://127.0.0.1:8600"


def ask(q, **kw):
    kw["q"] = q
    kw.setdefault("level", "question")
    with urllib.request.urlopen(BASE + "/api/search?" + urllib.parse.urlencode(kw)) as r:
        return json.loads(r.read().decode("utf-8"))


def events(d):
    return d["aggregation"]["events"]


def all_text(d):
    """全部事件的片段正文拼接（含扩展读入的上下文）。"""
    return "".join(b["text"] for e in events(d) for b in e["blocks"])


def ent_names(d):
    return [e["entity"] for e in d["question"]["entities"]]


def db_count(word):
    """库里有几段正文含这个词（只读）。给「探针词必须真的不在库」用。"""
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM passages WHERE text_orig LIKE ?",
                            ("%" + word + "%",)).fetchone()[0]
    finally:
        conn.close()


def rank_of(d, needle):
    """某句话第一次出现在第几个事件里（1 起；没出现返回 None）。

    「召回了」不等于「有用」—— 答句排到第 30 个事件里，用户是看不到的，
    所以下面的相关性断言都用这个，而不是 all_text 里找得到就算过。
    """
    for i, e in enumerate(events(d), 1):
        if any(needle in b["text"] for b in e["blocks"]):
            return i
    return None


class Phase4QuestionStructure(unittest.TestCase):
    """契约与片段完整性（与具体问题无关）。"""

    def test_response_contract(self):
        d = ask("齐桓公是怎么死的？")
        for k in ("q", "level", "mode", "text_mode", "question", "expanded",
                  "results", "aggregation", "notes", "disclaimer"):
            self.assertIn(k, d)
        self.assertEqual(d["level"], "question")
        self.assertFalse(d["expanded"]["all_terms"] == [], "没有扩展出任何检索词")

    def test_text_is_text_orig(self):
        """片段正文必须逐字来自 passages.text_orig，不允许任何改写。"""
        d = ask("齐桓公是怎么死的？")
        conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        try:
            n = 0
            for e in events(d):
                for b in e["blocks"]:
                    for pid in b["passage_ids"]:
                        got = conn.execute(
                            "SELECT text_orig FROM passages WHERE passage_id = ?",
                            (pid,)).fetchone()
                        self.assertIsNotNone(got, f"passage {pid} 不存在")
                        n += 1
            self.assertGreater(n, 0, "一个片段都没有")
        finally:
            conn.close()

    def test_blocks_are_complete_and_nonoverlapping(self):
        d = ask("齐桓公是怎么死的？")
        seen = []
        for e in events(d):
            self.assertTrue(e["blocks"], f"{e['event_id']} 是空事件")
            for b in e["blocks"]:
                self.assertEqual(b["text_mode"], "orig")
                self.assertTrue(b["text"].strip(), "空片段")
                self.assertLessEqual(b["row_first"], b["row_last"])
                self.assertEqual(len(b["text"]), b["n_chars"],
                                 "n_chars 与正文字数不符")
                self.assertEqual(b["n_passages"], len(b["passage_ids"]))
                self.assertTrue(b["why"], "片段没有说明为什么相关")
                seen.append((b["file_id"], b["row_first"], b["row_last"]))
        # 同一文件内的片段不能重叠。事件按相关度排、不是按行号排，
        # 所以要先按行号排回来再比。
        last = {}
        for fid, lo, hi in sorted(seen):
            if fid in last:
                self.assertGreater(lo, last[fid], "同一文件的片段重叠了")
            last[fid] = hi

    def test_events_carry_book_and_structure(self):
        d = ask("管仲是怎么死的？")
        for e in events(d):
            self.assertIn("relevance", e)
            self.assertTrue(e["book_title"])
            self.assertTrue(e["book_id"])
            self.assertTrue(e["event_id"].startswith("E"))
            self.assertEqual(e["n_blocks"], len(e["blocks"]))
        ids = [e["event_id"] for e in events(d)]
        self.assertEqual(ids, [f"E{i}" for i in range(1, len(ids) + 1)],
                         "event_id 不连续")


class Phase4RealQuestions(unittest.TestCase):
    """七个真实问题（任务书 §14）。"""

    def test_qihuan_gong_death(self):
        d = ask("齐桓公是怎么死的？")
        self.assertIn("death", d["question"]["intents"])
        self.assertEqual(ent_names(d), ["齊桓公"])
        self.assertEqual(rank_of(d, "齊桓公卒"), 1, "齊桓公卒 不在第一个事件里")
        books = {s["book_title"] for s in d["aggregation"]["sources"]}
        self.assertGreater(len(books), 1, f"只有一部书：{books}")

    def test_guanzhong_death(self):
        d = ask("管仲是怎么死的？")
        self.assertIn("death", d["question"]["intents"])
        self.assertEqual(ent_names(d), ["管仲"])
        # 第六点三阶段导入三國志后 E1 变了一条**引文**：魏書「庚申令」里曹操引
        # 管子（「鬭士食於功則卒輕於死」）。它同时含 管仲 与 卒，还多含一个「死」，
        # 于是多吃一档弱意图词（引擎自己的 detail：实体:管仲 14.0 ＋ 意图:卒 8.0
        # ＋ 弱意图词['死'] 1.5 ＋ 实体+意图同段 15.0 = 38.5），压过左傳两条真写
        # 「管仲卒」的段落（37.0，现为 E2/E3）。这是**权重设计的副作用**，不是检索
        # 坏了：答案仍在 3 个事件内可见。按计划书 §16 与「先测后议」，没有擅自改
        # 权重 —— 先如实记在这里，是否收紧弱意图词由扩容后的整体验收决定。
        self.assertLessEqual(rank_of(d, "管仲卒"), 3,
                             "管仲卒 掉出了前 3 个事件（今天实测第 2）")
        self.assertNotIn("董卓", all_text(d))

    def test_chonger_exile(self):
        d = ask("重耳流亡")
        # 重耳是晉文公的已核实别名，所以实体栏记的是规范名 —— 这正是要的：
        # 检索用别名召回，展示归到规范名，用户看到的是「晉文公」而不是孤立字面。
        self.assertEqual(ent_names(d), ["晉文公"])
        self.assertEqual(d["question"]["entities"][0]["matched"], "重耳")
        r = rank_of(d, "重耳")
        self.assertIsNotNone(r, "没召回重耳的原文")
        self.assertLessEqual(r, 3, f"重耳 的原文排到了第 {r} 个事件")
        txt = all_text(d)
        self.assertTrue("狄" in txt or "十二年" in txt, "没召回流亡的原文")

    def test_shang_yang_reform(self):
        d = ask("商鞅变法")
        r = rank_of(d, "商鞅")
        self.assertIsNotNone(r, "没召回商鞅的原文")
        self.assertLessEqual(r, 2, f"商鞅 的原文排到了第 {r} 个事件")

    def test_mugong_baili_xi(self):
        d = ask("秦穆公和百里奚")
        self.assertEqual(sorted(ent_names(d)), ["百里奚", "秦穆公"])
        r = rank_of(d, "百里奚")
        self.assertIsNotNone(r, "没召回百里奚的原文")
        self.assertLessEqual(r, 3, f"百里奚 的原文排到了第 {r} 个事件")
        both = [b for e in events(d) for b in e["blocks"]
                if "百里奚" in b["text"] and "穆公" in b["text"]]
        self.assertTrue(both, "两人同段的段落没排进结果")
        self.assertLessEqual(
            next(i for i, e in enumerate(events(d), 1)
                 if any(b in e["blocks"] for b in both)), 3,
            "两人同段的段落没排到前面")

    def test_cooccurrence_note_matches_corpus(self):
        """「语料中没有任何一段同时写着 A 与 B」这种否定结论必须是**真的**。

        踩过的坑：判定只看规范名「秦穆公」，而语料里写的是「秦繆公」（穆公的
        已核实别名），于是系统对外宣称「没有一段同时写着」，其实語料里有 2 段。
        系统可以少说，不能说错 —— 所以这里拿 SQL 独立数一遍来对。
        """
        for q, forms_a, forms_b in (
                ("秦穆公和百里奚", ["秦穆公", "繆公", "穆公"], ["百里奚"]),
                ("管仲和鲍叔牙", ["管仲", "管夷吾", "管子", "夷吾"],
                 ["鮑叔牙", "鮑叔"])):
            d = ask(q)
            note = next((n for n in d["notes"] if "同时写着" in n), None)
            self.assertIsNotNone(note, f"{q}：没给出同段与否的说明")
            if "没有任何一段" not in note:
                continue
            conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
            try:
                wa = " OR ".join(["text_orig LIKE ?"] * len(forms_a))
                wb = " OR ".join(["text_orig LIKE ?"] * len(forms_b))
                n = conn.execute(
                    f"SELECT COUNT(*) FROM passages WHERE kind='passage'"
                    f" AND ({wa}) AND ({wb})",
                    tuple(f"%{t}%" for t in forms_a + forms_b)).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n, 0, f"{q}：说明写着没有同段，语料里却有 {n} 段")

    def test_chengpu_battle(self):
        d = ask("城濮之战")
        r = rank_of(d, "城濮")
        self.assertIsNotNone(r, "没召回城濮的原文")
        self.assertLessEqual(r, 2, f"城濮 的原文排到了第 {r} 个事件")

    def test_no_three_kingdoms(self):
        """语料外的历史时期 —— 必须如实返回空，不编造。

        探针词换过两次，两次都是它自己红着报出来的：
          董卓  → 姜維  ：第六点二阶段加 後漢書，董卓进了语料（157 处正文命中，
                          还成了召回集里的正例 qh-person-16）；
          姜維  → 安祿山：第六点三阶段加 三國志，姜維进了语料（42 段）。
        安祿山只属于尚未收录的舊唐書/新唐書：第二批要加的南北朝 10 部与隋書
        （止于 618 年）都写不到他，所以这个名字能撑到那 9 部正史真的入库为止。

        下面那条断言是防复发的闸门：探针词一旦进语料，这条用例会在**当次**就
        红着告诉你换词，而不是悄悄变成一条永远为真的空断言。
        """
        probe = "安祿山"
        n_corpus = db_count(probe)
        self.assertEqual(n_corpus, 0,
                         f"探针词「{probe}」已在语料中（{n_corpus} 段）：它不能再充当"
                         f"「语料确实没有」的探针，请改这条用例的 probe")
        d = ask(probe)
        self.assertEqual(d["counts"]["entity_pool"], 0)
        self.assertEqual(d["counts"]["fallback_pool"], 0)
        self.assertEqual(events(d), [])
        self.assertEqual(all_text(d), "")
        self.assertTrue(any("不编造" in n for n in d["notes"]),
                        f"空结果没有说明：{d['notes']}")
        # 「安祿山」三个字允许出现在 question.raw / 扩展词里（那是在复述用户的问题），
        # 绝不允许出现在任何**片段正文**里 —— 正文只能是 text_orig。

    def test_ambiguous_huan_gong(self):
        """光杆「桓公」分不清齊/魯/秦/東周桓公 —— 不能当成某一位。"""
        d = ask("桓公")
        self.assertEqual(ent_names(d), ["桓公"],
                         "把光杆「桓公」解析成了某个具体君主")
        why = d["question"]["entities"][0]["why"]
        self.assertNotIn("齊桓公", why, "悄悄补成了齊桓公")


class Phase4TextModes(unittest.TestCase):
    def test_simplified_keeps_orig(self):
        """简体是**派生**的：text 永远是繁体原文，text_simplified 是另存的一份。"""
        from search import dual_text
        d = ask("齐桓公是怎么死的？", text="simplified")
        self.assertEqual(d["text_mode"], "simplified")
        for e in events(d):
            for b in e["blocks"]:
                self.assertTrue(b["simplified_ok"], "简体转换失败却没说")
                self.assertEqual(b["text_simplified"],
                                 dual_text.simplify(b["text"])["text"],
                                 "片段里的简体与转换函数的结果不一致")
                self.assertEqual(len(b["text"]), len(b["text_simplified"]),
                                 "简繁转换改变了长度")
        self.assertIn("齐桓公", d["terms_simplified"])

    def test_both_mode_has_both(self):
        d = ask("管仲是怎么死的？", text="both")
        b = events(d)[0]["blocks"][0]
        self.assertEqual(d["text_mode"], "both")
        self.assertTrue(b["text"] and b["text_simplified"])

    def test_bad_text_mode_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            ask("管仲", text="日本語")
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
