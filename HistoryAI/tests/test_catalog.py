"""第六点三阶段 6.3-A —— 语料目录（corpus_catalog.json）与其加载器。

目录是「加一本书」唯一的入口，它写错的形式还很隐蔽（JSON 重复键会被静默丢弃、
复制一条改一半会让两个 ID 指向同一个磁盘目录），所以校验必须常驻测试，而不是
只在写目录那天手工试一遍。

这里只测**与语料无关**的部分：校验规则、查询、家族回落。目录与磁盘的对账
（planned / uncatalogued）依赖本机是否有 HistoryLibrary，不在这里断言。
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline import catalog as C                            # noqa: E402
from tests import _tmp                                               # noqa: E402


def write(tmp: Path, obj) -> str:
    p = tmp / "catalog.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


class CatalogLoadTest(unittest.TestCase):
    def setUp(self):
        self.cat = C.load()
        self.raw = json.loads(C.CATALOG_PATH.read_text(encoding="utf-8"))

    def test_loads_and_counts(self):
        self.assertEqual(self.cat.version, C.SCHEMA_VERSION)
        # 4 部先秦 + 24 部正史：二十四史一本不缺，是本阶段的验收前提
        self.assertEqual(len(self.cat.books), 28)
        self.assertEqual(sum(1 for b in self.cat.books.values()
                             if b.category == "正史"), 24)
        self.assertEqual(sum(1 for b in self.cat.books.values()
                             if b.category == "先秦文獻"), 4)

    def test_key_is_kanripo_id_everywhere(self):
        for bid, b in self.cat.books.items():
            self.assertEqual(bid, b.book_id)
            self.assertTrue(C.KR_ID_RE.match(bid), bid)
            self.assertEqual(b.family_expected in C.FAMILIES, True)

    def test_dir_unique_and_lookup(self):
        dirs = [b.dir for b in self.cat.books.values()]
        self.assertEqual(len(dirs), len(set(dirs)))
        for d in dirs:
            self.assertIsNotNone(self.cat.by_dir(d))

    def test_every_book_has_recall_flag(self):
        # 人工确认位：不是「库里有」就算 verified，得有召回用例背书。
        # 6.3 末（19 部入库）：verified 19 = 原 7 部 + 本阶段两批 12 部；
        # 未验证的恰好是尚未下载的 9 部正史 —— 两边都写书名，比数个数强：
        # 「哪天多出一部没背书的书」或「哪部书漏进召回」都会在这里顶出来。
        verified = [b.title for b in self.cat.books.values() if b.recall_verified]
        self.assertEqual(sorted(verified), sorted([
            # 先秦文獻 4 + 秦汉 3 + 三国 1 + 两晋 1 + 南北朝 10
            "尚書", "春秋左傳", "國語", "戰國策",
            "史記", "前漢書", "後漢書",
            "三國志", "晉書",
            "宋書", "南齊書", "梁書", "陳書", "魏書", "北齊書", "周書",
            "隋書", "南史", "北史",
        ]))
        unverified = [b.title for b in self.cat.books.values() if not b.recall_verified]
        self.assertEqual(sorted(unverified), sorted([
            "舊唐書", "新唐書", "舊五代史", "新五代史", "宋史",
            "遼史", "金史", "元史", "明史",
        ]))

    def test_era_groups_cover_all_six_dynasty_groups(self):
        groups = {b.era_group for b in self.cat.books.values()}
        for g in ("先秦", "秦汉", "三国", "两晋", "南北朝", "隋唐", "五代",
                  "宋", "元", "明"):
            self.assertIn(g, groups)

    def test_patterns_reference_defined_names(self):
        for b in self.cat.books.values():
            for p in self.cat.patterns_for(b):
                self.assertIn(p.name, C.PATTERNS)

    def test_patterns_fall_back_to_family_defaults(self):
        # 没写 title_patterns 的书一律用家族的（前漢書/後漢書/三國志/晉書都是 WYG 族）
        for d in ("qianhanshu", "houhanshu", "sanguozhi", "jinshu"):
            b = self.cat.by_dir(d)
            self.assertEqual(b.title_patterns, ())          # 都没自己声明
            self.assertEqual([p.name for p in self.cat.patterns_for(b)],
                             list(self.cat.families["wyg"]["title_patterns"]), d)

    def test_book_declaration_replaces_family_default(self):
        # 声明是**替换**不是追加：摘掉家族默认里误伤的模式时也要管用。
        # 与 LayerRuleTest 同一手法：把目录写到临时文件再 load。
        tmp = Path(__file__).resolve().parent / "_tmp_patterns"
        tmp.mkdir(exist_ok=True)
        try:
            obj = json.loads(C.CATALOG_PATH.read_text(encoding="utf-8"))
            obj["books"]["KR2a0012"]["title_patterns"] = ["wyg_ming_paren"]
            C.load.cache_clear()
            cat = C.load(write(tmp, obj))
            names = [p.name for p in cat.patterns_for(cat.by_dir("sanguozhi"))]
            self.assertEqual(names, ["wyg_ming_paren"])     # 不含家族默认的 wyg_di_n
        finally:
            for f in tmp.glob("*"):
                f.unlink()
            tmp.rmdir()
            C.load.cache_clear()

    def test_juan_as_section_declared_only_where_measured(self):
        # 卷题是不是篇名粒度是**实测出来的**书级事实，不是家族默认：
        # 三國志正文没有篇题行（不声明就整卷 0 section → FAIL）→ true；
        # 晉書有 `帝紀第N`/`志第N`/`列傳第N` 篇题 → false（与前漢書/後漢書一致）。
        self.assertTrue(self.cat.by_dir("sanguozhi").juan_as_section)
        self.assertFalse(self.cat.by_dir("jinshu").juan_as_section)
        others = [b.title for b in self.cat.books.values()
                  if b.juan_as_section and b.dir != "sanguozhi"]
        self.assertEqual(others, [])

    def test_file_overrides_migrated_verbatim(self):
        # 6.2 的三条特例原样迁入 JSON，note 保留人工复核痕迹
        self.assertEqual(self.cat.file_override("shangshu", 59)["layer"], "appendix")
        self.assertEqual(self.cat.file_override("guoyu", 0)["layer"], "preface")
        self.assertEqual(self.cat.file_override("zhanguoce", 0)["status"], "ok")
        self.assertIn("韋昭", self.cat.file_override("guoyu", 0)["note"])
        self.assertIsNone(self.cat.file_override("shiji", 1))
        self.assertIsNone(self.cat.file_override("nosuchbook", 1))

    def test_titles_unique_for_publish_gate(self):
        # 6.3-L 的 REAL_TITLES 从这里派生：书名重复会让闸门名单少一项
        titles = self.cat.titles()
        self.assertEqual(len(titles), len(set(titles)))


class CatalogValidationTest(unittest.TestCase):
    """写错的目录必须当场报错，且报在点子上。"""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_catalog"
        self.tmp.mkdir(exist_ok=True)
        self.raw = json.loads(C.CATALOG_PATH.read_text(encoding="utf-8"))
        C.load.cache_clear()

    def tearDown(self):
        for f in self.tmp.glob("*"):
            f.unlink()
        self.tmp.rmdir()
        C.load.cache_clear()

    def _bad(self, mutate, *expect_fragments):
        import copy
        obj = copy.deepcopy(self.raw)
        mutate(obj)
        path = write(self.tmp, obj)
        with self.assertRaises(C.CatalogError) as cm:
            C.load(path)
        msg = str(cm.exception)
        for frag in expect_fragments:
            self.assertIn(frag, msg)
        return msg

    def test_kanripo_id_mismatch(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(kanripo_id="KR2a0015"),
                  "kanripo_id", "KR2a0012")

    def test_unknown_pattern_name(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(
            title_patterns=["wyg_di_n", "cjk_ming_paren"]), "未定义的模式")

    def test_family_default_pattern_also_checked(self):
        self._bad(lambda r: r["families"]["tls"].update(title_patterns=["nope"]),
                  "families[tls]")

    def test_duplicate_dir(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(dir="jinshu"), "dir")

    def test_bad_family(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(family_expected="ywg"),
                  "family_expected")

    def test_bad_override_layer(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(
            file_overrides={"1": {"layer": "mainn", "status": "ok"}}), "LAYER_VALUES")

    def test_bad_override_status(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(
            file_overrides={"1": {"layer": "main", "status": "fine"}}), "STATUS_VALUES")

    def test_override_file_no_must_be_digit(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(
            file_overrides={"1a": {"layer": "main", "status": "ok"}}), "文件号")

    def test_missing_required_field(self):
        self._bad(lambda r: r["books"]["KR2a0012"].pop("era_group"), "era_group")

    def test_unknown_field_caught(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(titel="x"), "titel")

    def test_recall_verified_must_be_bool(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(recall_verified="yes"),
                  "recall_verified")

    def test_juan_as_section_must_be_bool(self):
        # 写成字符串会让 bool() 悄悄为真：三國志 true、晉書 false 的差别不该靠猜
        self._bad(lambda r: r["books"]["KR2a0015"].update(juan_as_section="false"),
                  "juan_as_section")

    def test_duplicate_json_key_caught(self):
        # json 默认会把重复键静默丢弃，只剩后一个 —— 这里必须报出来
        txt = C.CATALOG_PATH.read_text(encoding="utf-8").replace(
            '"KR2e0001": {', '"KR2e0001": {}, "KR2e0001": {', 1)
        p = self.tmp / "dup.json"
        p.write_text(txt, encoding="utf-8")
        with self.assertRaises(C.CatalogError) as cm:
            C.load(str(p))
        self.assertIn("重复的键", str(cm.exception))

    def test_valid_catalog_passes(self):
        self.assertIsNotNone(C.load(write(self.tmp, self.raw)))

    def test_missing_file_raises_but_try_load_returns_none(self):
        # try_load 是回滚路径：目录文件不在时，调用方回落到 6.2 的内置规则
        absent = str(self.tmp / "nope.json")
        with self.assertRaises(C.CatalogError):
            C.load(absent)
        self.assertIsNone(C.try_load(absent))

    # ---- 层默认规则（6.3-C②）----

    def test_unknown_family_key_caught(self):
        # `layer_default`（单数、没人读）就是这么混进来的：声明了没有执行者
        self._bad(lambda r: r["families"]["wyg"].update(layer_default="main"),
                  "families[wyg]", "layer_default")

    def test_bad_layer_rule_when(self):
        self._bad(lambda r: r["families"]["tls"].update(
            layer_defaults=[{"when": "whenever", "layer": "main", "status": "ok"}]),
            "when=", "catalog.LAYER_WHEN")

    def test_bad_layer_rule_layer(self):
        self._bad(lambda r: r["families"]["tls"].update(
            layer_defaults=[{"layer": "mainn", "status": "ok"}]), "LAYER_VALUES")

    def test_bad_layer_rule_status(self):
        self._bad(lambda r: r["families"]["tls"].update(
            layer_defaults=[{"layer": "main", "status": "fine"}]), "STATUS_VALUES")

    def test_juan_part_needs_resolver(self):
        self._bad(lambda r: r["families"]["wyg"].update(
            layer_defaults=[{"when": "juan_part", "status": "ok", "note": "x"},
                            {"layer": "main", "status": "ok"}]), "resolver")

    def test_resolver_only_with_juan_part(self):
        self._bad(lambda r: r["families"]["tls"].update(
            layer_defaults=[{"layer": "main", "status": "ok",
                             "resolver": "part_layer_of"}]), "resolver")

    def test_layer_defaults_must_end_with_catch_all(self):
        # 末条不是 when=always 的话，漏网文件会掉回内置默认 → 行为随目录在不在而变
        self._bad(lambda r: r["families"]["tls"].update(
            layer_defaults=[{"when": "file_zero", "layer": "preface",
                             "status": "ok"}]), "兜底")

    def test_family_needs_layer_defaults(self):
        self._bad(lambda r: r["families"]["tls"].pop("layer_defaults"),
                  "缺 layer_defaults")


class LayerRuleTest(unittest.TestCase):
    """层默认的**执行者是 structure.file_layer_defaults**，真源在 catalog。

    这里证明的就是本阶段要的那件事：改行为＝改 JSON，代码零改动。
    """
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_layer"
        self.tmp.mkdir(exist_ok=True)
        self.raw = json.loads(C.CATALOG_PATH.read_text(encoding="utf-8"))
        self.saved_path = C.CATALOG_PATH
        C.CATALOG_PATH = self.tmp / "catalog.json"
        C.CATALOG_PATH.write_text(json.dumps(self.raw, ensure_ascii=False),
                                  encoding="utf-8")
        C.load.cache_clear()

    def tearDown(self):
        C.CATALOG_PATH = self.saved_path
        for f in self.tmp.glob("*"):
            f.unlink()
        self.tmp.rmdir()
        C.load.cache_clear()

    def _reload(self, mutate):
        obj = json.loads(json.dumps(self.raw, ensure_ascii=False))
        mutate(obj)
        C.CATALOG_PATH.write_text(json.dumps(obj, ensure_ascii=False),
                                  encoding="utf-8")
        C.load.cache_clear()

    def test_json_rule_drives_layer_defaults(self):
        from scripts.pipeline.structure import file_layer_defaults
        # 原地不动：tls 族的兜底是 main
        self.assertEqual(file_layer_defaults("guoyu", 3, "tls", {})[0], "main")
        # 只改 JSON，不改代码：同一调用换出 appendix / 换个 status
        self._reload(lambda r: r["families"]["tls"].update(
            layer_defaults=[{"layer": "appendix", "status": "pending_section",
                             "note": "试验规则"}]))
        layer, status, note = file_layer_defaults("guoyu", 3, "tls", {})
        self.assertEqual((layer, status, note), ("appendix", "pending_section", "试验规则"))

    def test_book_override_beats_family_rule(self):
        from scripts.pipeline.structure import file_layer_defaults
        # 家族规则说 main，但 guoyu _000 有 file_overrides → 以人工特例为准
        self._reload(lambda r: r["families"]["sbck"].update(
            layer_defaults=[{"layer": "main", "status": "ok", "note": "家族"}]))
        layer, status, note = file_layer_defaults("guoyu", 0, "sbck", {})
        self.assertEqual(layer, "preface")
        self.assertIn("國語解敘", note)

    def test_wyg_juan_part_rule_interpolates_note(self):
        from scripts.pipeline.structure import file_layer_defaults
        layer, _, note = file_layer_defaults(
            "qianhanshu", 1, "wyg", {"JUAN": "御製題辭"})
        self.assertEqual(layer, "preface")
        self.assertIn("御製題辭", note)

    def test_missing_catalog_falls_back_to_builtin(self):
        from scripts.pipeline.structure import file_layer_defaults
        C.CATALOG_PATH.unlink()          # 目录不在 → 6.2 冻结快照
        C.load.cache_clear()
        self.assertEqual(file_layer_defaults("guoyu", 0, "sbck", {})[0], "preface")
        self.assertEqual(file_layer_defaults("shangshu", 59, "tls", {})[0], "appendix")
        self.assertEqual(file_layer_defaults("某書", 0, "sbck", {})[1],
                         "pending_section")
        self.assertEqual(file_layer_defaults("某書", 1, None, {})[0], "unknown")


class CatalogVolumeTest(unittest.TestCase):
    """卷级登记（6.4 §4/§5）：expected / declared / witness / volume_notes。

    这一组守的是**数字的来源是否可信**。卷级字段错了不会报错，它只会让页面
    安静地显示成另一个数 —— 而页面上那个数正是用户判断「史书里有没有」的依据。
    """

    def setUp(self):
        self.tmp = Path(_tmp.mkdtemp(prefix="catalog_vol_"))
        self.raw = json.loads(C.CATALOG_PATH.read_text(encoding="utf-8"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, mutate):
        import copy
        obj = copy.deepcopy(self.raw)
        mutate(obj)
        return C.load(write(self.tmp, obj))

    def _bad(self, mutate, *expect_fragments):
        with self.assertRaises(C.CatalogError) as cm:
            self._load(mutate)
        msg = str(cm.exception)
        for frag in expect_fragments:
            self.assertIn(frag, msg)
        return msg

    def test_partial_books_registered_as_declared_short(self):
        """§5：5 部上游残缺的正史必须登记成 declared < expected。

        这三条数字（30/65、35/50、22/100）是本阶段的产品承诺 —— 页面上的
        「《北齊書》35/50 卷」直接来自它们。改错任何一个，搜索页就会对着
        一个残缺版本说「已收全」。
        """
        cat = C.load()
        want = {"KR2a0012": (30, 65), "KR2a0015": (33, 130),
                "KR2a0021": (35, 50), "KR2a0023": (49, 85),
                "KR2a0025": (22, 100)}
        for bid, (declared, expected) in want.items():
            b = cat.books[bid]
            self.assertEqual((b.declared_volumes, b.expected_volumes),
                             (declared, expected), bid)
            self.assertLess(b.declared_volumes, b.expected_volumes, bid)
            self.assertEqual(b.volume_witness, "corpus", bid)

    def test_complete_books_have_equal_numbers(self):
        """完整书 declared == expected —— 否则会凭空多出「残缺」。"""
        cat = C.load()
        for bid in ("KR2a0007", "KR2a0009", "KR2a0024"):
            b = cat.books[bid]
            self.assertEqual(b.declared_volumes, b.expected_volumes, bid)

    def test_witness_none_books_have_no_numbers(self):
        """先秦四书：底本以篇为单位，登记卷数就是自相矛盾。"""
        cat = C.load()
        for bid in ("KR1b0001", "KR1e0001", "KR2e0001", "KR2e0003"):
            b = cat.books[bid]
            self.assertEqual(b.volume_witness, "none", bid)
            self.assertIsNone(b.expected_volumes, bid)

    def test_volumes_view_shape(self):
        """`volumes()` 是 manifest 取数的那一层，键名改了会静默拿到 None。"""
        b = C.load().books["KR2a0012"]
        v = b.volumes()
        self.assertEqual(set(v), {"expected", "declared", "witness", "notes",
                                 "note"})
        self.assertEqual(v["witness"], "corpus")

    def test_declared_over_expected_is_error(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(declared_volumes=99),
                  "declared_volumes", "expected_volumes")

    def test_witness_none_forbids_numbers(self):
        self._bad(lambda r: r["books"]["KR2e0001"].update(declared_volumes=21),
                  "volume_witness", "none")

    def test_corpus_witness_requires_both_numbers(self):
        self._bad(lambda r: r["books"]["KR2a0012"].pop("declared_volumes"),
                  "declared_volumes")

    def test_unknown_witness_caught(self):
        self._bad(lambda r: r["books"]["KR2a0012"].update(volume_witness="guess"),
                  "volume_witness", "guess")

    def test_volume_notes_key_must_be_digit(self):
        """volume_notes 的键是**卷号**，写成「卷105」就对不上 gaps 里的整数。"""
        self._bad(lambda r: r["books"]["KR2a0019"].update(
            volume_notes={"卷105": "说明"}), "volume_notes")

    def test_volume_notes_must_explain(self):
        self._bad(lambda r: r["books"]["KR2a0019"].update(
            volume_notes={"105": ""}), "volume_notes")

    def test_editions_registry_required(self):
        """版本登记表（§11）：底本名与来源必须能查到，否则页面上只有个空标签。"""
        cat = C.load()
        self.assertEqual(set(cat.editions), {"wyg", "sbck", "tls"})
        for key, e in cat.editions.items():
            self.assertEqual(e["family"], key)
            self.assertTrue(e["name"], key)
            self.assertTrue(e["source"], key)

    def test_edition_of_returns_book_scoped_id(self):
        """`edition_id` 是「书-家族」，因为同一个 WYG 在本库里有 14 部不同的书；
        只用 family 会让 14 部书共用一个版本号，页面无从区分。"""
        cat = C.load()
        ed = cat.edition_of(cat.books["KR2a0012"])
        self.assertEqual(ed["edition_id"], "KR2a0012-wyg")
        self.assertEqual(ed["family"], "wyg")
        self.assertEqual(ed["kanripo_id"], "KR2a0012")
        self.assertTrue(ed["edition_name"])

    def test_missing_edition_registration_caught(self):
        self._bad(lambda r: r["editions"].pop("sbck"),
                  "sbck")


if __name__ == "__main__":
    unittest.main(verbosity=2)
