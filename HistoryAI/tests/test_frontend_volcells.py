"""前端卷级渲染（第六点四阶段 §6）—— `app.js` 那三个渲染函数的冒烟测试。

**为什么这个文件存在**：`scripts/site/check_engine.py` 的 14 项检查全部是
「Python 侧有什么，JS 侧是不是也有」的对拍（`dual-text`／`search`／`result-block`…），
它们**不渲染 DOM**。所以 `volCells()` / `volChip()` / `volumeTotalsCard()` /
`scopeCard()` 这四段新代码在自动化里本来是**零覆盖**的 —— 一个模板字面量里漏了
反引号、或者 `num(undefined)` 渲染成 `NaN`，检查全绿，浏览器白屏。

这里把 `app.js` 当模块载进 node（**掐掉末尾的 `route(); headerStats(); footBooks();`
三行启动调用** —— 我们要的是渲染函数，不是启动流程），用构造好的输入调它们，
把产出的 HTML 拿回 Python 断言。

判的是**产物**，不是实现：
  · 每一格必须正好 3 个 <td>（`#/coverage` 的表头是 12 列，少一列整张表就错位）
  · 「没有数字」的三种情形必须长得不一样（未提供 / 无卷级模型 / 有数字）
  · 任何一格都不许渲染出 `undefined` / `NaN` / `null` 字样

最后一条看着像洁癖，其实是模板字面量最容易出的错：字段名写错时 JS 不报错，
它把 `undefined` 印在页面上，而页面上的 `undefined` 用户会当成乱码 ——
比「—」糟得多。
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import _tmp                                               # noqa: E402

APP_JS = ROOT / "frontend" / "app.js"

# 启动调用，必须在 import 前掐掉：route() 一跑就要 DOM，而这个测试测的是渲染函数。
BOOTSTRAP = ("\nroute();\nheaderStats();\nfootBooks();\n")

HARNESS = r"""
// 最小全局桩：app.js 顶层有一句 window.addEventListener 和两处 localStorage 读。
globalThis.window = { addEventListener() {}, onSize: null };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.document = { querySelector: () => null, addEventListener() {} };

const { volCells, volChip, volumeTotalsCard, scopeCard, partialBanner,
        renderNotImported, isPlannedBook } = await import("./app.module.mjs");

/* ---- §25 情况四：在册未入库的书 ------------------------------------------
 * `api()` 在 API 模式下走 fetch（`window.HistoryAIBoot` 是空的，默认就是 API
 * 模式），所以一个 fetch 桩就足以把这段端到端跑起来 —— 不需要 DOM：
 * `renderDiagnosis` 只往 `box.innerHTML` 写，传个空对象进去即可。
 *
 * 桩里**记下每一个请求**，且非 diagnose 一律抛错：万一哪天「计划书」被送去了
 * /api/search，这里会红，而不是等到用户看见一个「未识别的史书」错误框。 */
const CALLS = [];
const PLANNED_ID = "KR2a0038";
const PLANNED_TITLES = ["舊唐書", "新唐書", "舊五代史", "新五代史",
                        "宋史", "遼史", "金史", "元史", "明史"];
globalThis.fetch = async url => {
  CALLS.push(String(url));
  const u = new URL(String(url), "http://x");
  if (!u.pathname.endsWith("/api/diagnose"))
    throw new Error("计划书不该请求这个接口：" + u.pathname);
  const book = u.searchParams.get("book") || "";
  const payload = book === PLANNED_ID ? {
    q: "齊桓公", q_traditional: "齊桓公", book: null, book_title: "明史",
    edition: null, layers: [{ ok: true, title: "语料在册", detail: "在语料目录里，status=planned" },
                            { ok: false, title: "已入库", detail: "还没进 history.db" }],
    planned_books: PLANNED_TITLES, downloaded_not_imported: [],
    summary: "《明史》尚未下载，还没进检索范围（全书 332 卷） —— 这不是「这本书里"
             + "没有「齊桓公」」，是这本书还搜不了。已收录 19 部，未收录 9 部。",
    result_status: "not_imported",
    scope: { book: { title: "明史", edition: "文淵閣四庫全書本", volume_status: "planned",
                     declared: 332, expected: 332, available: null,
                     range_text: "332/332 卷", expected_missing: [], caveat: "还没导入。" } },
  } : {
    // 不限史书、全库无结果 —— 与真 API 的载荷同形（实测 2026-09-14）。
    // 状态是 partial_no_hit 而**不是** complete_no_hit：库里本来就有 5 部残卷，
    // 「全都没有」这句话从库的层面就断言不了。
    q: "忽必烈", q_traditional: "忽必烈", book: null, edition: null, layers: [],
    planned_books: PLANNED_TITLES, downloaded_not_imported: [],
    summary: "在已经收录进库的 19 部史书里，没有「忽必烈」（0 段）。其中 5 部底本"
             + "本身就残缺（《三國志》30/65 卷…）。",
    result_status: "partial_no_hit",
    scope: { status: "partial_no_hit", books_in_scope: 19, book: null,
             not_imported: PLANNED_TITLES,
             incomplete: [{ title: "三國志", available: 30, expected: 65,
                            range_text: "30/65 卷（底本收至卷30，通行本共 65 卷）" }],
             detail: "全库 19 部：5 部底本残缺、9 部尚未入库 —— "
                     + "无法断言「全部史料里都没有」" },
  };
  return { ok: true, json: async () => payload };
};

// 等一个宏任务：renderDiagnosis 是 async，里面要跨几个微任务才写完 box.innerHTML。
const settle = () => new Promise(r => setTimeout(r, 0));
const drive = async (book) => {
  const box = { innerHTML: "" };
  renderNotImported(box, { q: "齊桓公", book, edition: "" });
  await settle();
  return box.innerHTML;
};

const P = (av, ex, decl) => ({ known: true, status: "partial", available: av,
                               expected: ex, declared: decl, witness: "corpus",
                               coverage_ratio: av / ex });
const C = (av, ex, witness) => ({ known: true, status: "complete", available: av,
                                  expected: ex, declared: ex, witness,
                                  coverage_ratio: av / ex });

const cat = {
  available: true, volume_available: true, volume_reason: null,
  volume_generated_at: "2026-09-14T11:30:00Z", volume_note: "（口径说明）",
  volume_totals: {
    in_library: { books: 19, expected: 1275, available: 1013,
                  ratio: 1013 / 1275, by_status: { complete: 10, partial: 5, unknown: 4 },
                  upstream_partial: ["三國志", "晉書", "北齊書", "隋書", "北史"] },
    planned: { books: 9, expected: 1938, upstream_partial: ["舊唐書", "新唐書", "遼史"] },
  },
};

const out = {
  no_data: volCells(undefined),
  null_data: volCells(null),
  no_model: volCells({ known: false, status: "unknown" }),
  partial: volCells(P(35, 50, 35)),
  complete_exact: volCells(C(36, 36, "corpus")),
  complete_gap: volCells(C(113, 114, "corpus")),
  complete_declared: volCells(C(130, 130, "declared")),
  planned: volCells({ known: true, status: "planned", available: null,
                      expected: 332, declared: 332, coverage_ratio: null,
                      witness: "declared" }),
  totals_null: volumeTotalsCard(null),
  totals_no_snapshot: volumeTotalsCard({ available: true, volume_available: false,
                                         volume_reason: "卷级快照读不了" }),
  totals_real: volumeTotalsCard(cat),
  scope_null: scopeCard(null),
  scope_partial_book: scopeCard({ book: { title: "北齊書", edition: "文淵閣四庫全書本",
    volume_status: "partial", declared: 35, expected: 50, available: 35,
    range_text: "35/50 卷（底本收至卷35，通行本共 50 卷）",
    expected_missing: [36, 37, 38, 39, 40, 41, 42],
    caveat: "未收录的部分没有参与检索。" } }),
  scope_library: scopeCard({ books_in_scope: 19,
    incomplete: [{ title: "三國志", range_text: "30/65 卷" }],
    not_imported: ["明史", "元史"] }),

  banner_none: partialBanner(null),
  banner_no_book: partialBanner({ books_in_scope: 19 }),
  banner_complete: partialBanner({ book: { title: "陳書", volume_status: "complete",
                                           range_text: "36/36 卷" } }),
  banner_partial: partialBanner({ book: { title: "北齊書", volume_status: "partial",
    declared: 35, expected: 50, range_text: "35/50 卷（底本收至卷35，通行本共 50 卷）",
    expected_missing: [36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50] } }),
  banner_partial_no_range: partialBanner({ book: { title: "北史",
    volume_status: "partial", declared: 22, expected: 100, expected_missing: [] } }),
};

// ---- §25 情况四的两个判据：路由去向、以及「分母」有没有说出来 ------------
// 用 renderNotImported 驱动**任意** diagnose 载荷：它是导出的入口里唯一会先把
// searchState.lastRes 记好再交给 renderDiagnosis 的，而 renderDiagnosis 的竞态
// 守卫比的正是那个字段的同一性 —— 直接从外面调 renderDiagnosis 会被守卫挡掉。
out.not_imported_card = await drive(PLANNED_ID);   // 计划书 → 「尚未收录」
out.unscoped_no_hit_card = await drive("");        // 全库无结果 → 必须报出分母

const predicate = [
  isPlannedBook(PLANNED_ID, [{ book_id: PLANNED_ID }]),   // 计划书 → true
  isPlannedBook("KR2a0021", [{ book_id: PLANNED_ID }]),   // 在库书 → false
  isPlannedBook("", [{ book_id: PLANNED_ID }]),           // 「全部史书」→ false
  isPlannedBook(PLANNED_ID, []),                          // 演示模式（拿不到目录）→ false
  isPlannedBook(PLANNED_ID, null),                        // 目录字段缺失 → false
];

console.log(JSON.stringify({ html: out, calls: CALLS, predicate }));
"""


class VolCellsRenderTest(unittest.TestCase):
    """在 node 里真跑一遍渲染函数，把 HTML 拿回来判。"""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("没有 node —— 跳过前端渲染冒烟测试")
        src = APP_JS.read_text(encoding="utf-8")
        if BOOTSTRAP not in src:
            raise AssertionError(
                "app.js 末尾的启动调用变了（route/headerStats/footBooks）—— "
                "这个测试靠掐掉它们来 import 渲染函数，先把 BOOTSTRAP 同步过来")
        src = src.replace(BOOTSTRAP, "\n") + (
            "\nexport { volCells, volChip, volumeTotalsCard, scopeCard,"
            " partialBanner, renderNotImported, isPlannedBook };\n")
        cls.tmp = Path(_tmp.mkdtemp(prefix="volcells_"))
        (cls.tmp / "app.module.mjs").write_text(src, encoding="utf-8")
        (cls.tmp / "run.mjs").write_text(HARNESS, encoding="utf-8")
        p = subprocess.run(["node", "run.mjs"], cwd=cls.tmp, capture_output=True,
                           text=True, encoding="utf-8", timeout=120)
        if p.returncode != 0:
            raise AssertionError(f"node 跑渲染函数失败：\n{p.stderr[:2000]}")
        data = json.loads(p.stdout)
        cls.out, cls.calls, cls.predicate = data["html"], data["calls"], data["predicate"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------- 通用判据
    def test_no_cell_renders_undefined_or_nan(self):
        """**每一格**都不许出现 undefined / NaN / null 字样。

        模板字面量里字段名写错时 JS 不报错，它把 `undefined` 印在页面上。
        用户在页面上看到 `undefined` 会当成乱码 —— 比看到一个「—」糟得多。
        """
        for name, html in self.out.items():
            for bad in ("undefined", "NaN", "null"):
                self.assertNotIn(bad, html, f"{name} 渲染出了 {bad}：{html[:200]}")

    def _tds(self, html):
        return html.count("<td")

    def test_every_row_is_exactly_three_cells(self):
        """`#/coverage` 的表头是 12 列，volCells 必须正好补 3 个 <td>。

        少一个整张表就错位 —— 而错位的表**看起来是正常的**，只是每一列的意思
        都往后挪了一格，比报错更难发现。
        """
        for name in ("no_data", "null_data", "no_model", "partial",
                     "complete_exact", "complete_gap", "complete_declared", "planned"):
            self.assertEqual(self._tds(self.out[name]), 3, f"{name} 不是 3 个 <td>")

    # ------------------------------------------------- 三种「没有数字」要分开
    def test_missing_data_says_not_provided(self):
        """我们**没拿到**这一格（静态演示模式 / 快照缺书）→「未提供」。

        这与「这部书没有卷级模型」是两回事，混起来会让演示站上每本书都显示
        「无卷级模型」—— 那是句关于这部书的假话，而我们其实只是没数据。
        """
        for k in ("no_data", "null_data"):
            self.assertIn("未提供", self.out[k], k)

    def test_no_volume_model_is_its_own_words(self):
        """先秦四书（sbck 底本以篇为单位）→「无卷级模型」，不是「未提供」。"""
        h = self.out["no_model"]
        self.assertIn("无卷级模型", h)
        self.assertNotIn("未提供", h)

    def test_planned_book_shows_no_measured_number(self):
        """未入库的书一段都没数过，所以「已收」只能是「—」，不能是 0。

        写 0 等于说「这本书我们收了 0 卷」—— 事实是**没量过**。
        """
        h = self.out["planned"]
        self.assertIn("—", h)
        self.assertNotIn(">0", h)

    # ----------------------------------------------------------------- 数字
    def test_partial_row_numbers(self):
        h = self.out["partial"]
        self.assertIn("35 / 50", h)
        self.assertIn("70.0%", h)
        self.assertIn("残缺", h)

    def test_complete_row_numbers(self):
        self.assertIn("36 / 36", self.out["complete_exact"])
        self.assertIn("100.0%", self.out["complete_exact"])
        self.assertIn("完整", self.out["complete_exact"])

    def test_complete_with_explained_gap_says_why(self):
        """魏書 113/114 却是「完整」—— 那 1 卷的差额**必须带解释**。

        不解释的话，页面上「113 / 114」配「完整」看起来就是我们丢了一卷，
        或者我们的状态判定坏了。实际是源文件把卷一百五拆成了「之一…之四」，
        文件头认不出卷题，人在 catalog 里逐卷豁免的。
        """
        h = self.out["complete_gap"]
        self.assertIn("113 / 114", h)
        self.assertIn("完整", h)
        self.assertIn("说明", h)          # tooltip 里那句「已在语料目录里逐卷说明」

    def test_declared_witness_says_manual(self):
        """史記 130/130 是**人工登记**的（tls 底本无卷级证据）。

        那个数字必须带着「这是人登的」这句话走，否则会被读成机器数出来的。
        """
        self.assertIn("人工登记", self.out["complete_declared"])

    # --------------------------------------------------------------- 总账
    def test_totals_card_absent_when_no_catalog(self):
        """演示模式拿不到 catalog → 整块不画，绝不画一个空壳。"""
        self.assertEqual(self.out["totals_null"], "")

    def test_totals_card_degraded_says_so(self):
        """快照读不到时说「读不到」，**不显示 0 卷**。

        0 和「读不到」在用户眼里是天差地别的两句话。
        """
        h = self.out["totals_no_snapshot"]
        self.assertIn("不可用", h)
        self.assertNotIn("已收 <b>0", h)

    def test_totals_card_uses_manifest_numbers(self):
        """总账的数字来自 manifest，页面不重算 —— 1013 / 1275 / 79.5%。"""
        h = self.out["totals_real"]
        self.assertIn("1,013", h)
        self.assertIn("1,275", h)
        self.assertIn("79.5%", h)
        self.assertIn("残缺", h)          # 5 部残缺要列出来

    # ----------------------------------------------------------- 收录范围卡
    def test_scope_card_is_empty_without_scope(self):
        self.assertEqual(self.out["scope_null"], "")

    def test_scope_card_shows_range_for_partial_book(self):
        """§25 情况三：残缺书必须显示当前收录范围（《北齊書》35/50 卷）。"""
        h = self.out["scope_partial_book"]
        self.assertIn("北齊書", h)
        self.assertIn("35/50", h)

    def test_scope_card_library_lists_incomplete_and_missing(self):
        h = self.out["scope_library"]
        self.assertIn("19", h)
        self.assertIn("三國志", h)
        self.assertIn("明史", h)

    # --------------------------------------------- 有结果时的残缺提示（§28）
    def test_banner_only_for_partial(self):
        """**只在残缺时画**。完整史书也画一条，等于每次搜索都挂免责声明 ——
        说多了等于没说，而且会让真正残缺的那 5 部失去分量。"""
        self.assertEqual(self.out["banner_none"], "")
        self.assertEqual(self.out["banner_no_book"], "")
        self.assertEqual(self.out["banner_complete"], "")

    def test_banner_says_results_come_from_partial_source(self):
        """§28 的关键句：**下面的结果全部来自已收录的卷**。

        搜《北齊書》拿到「高歡」的结果，用户很自然会以为那就是全部 ——
        结果是对的，结论是错的。这句话就是防这个的。
        """
        h = self.out["banner_partial"]
        self.assertIn("北齊書", h)
        self.assertIn("35/50", h)
        self.assertIn("15", h)              # 缺 15 卷
        self.assertIn("全部来自已收录的卷", h)
        self.assertIn("不等于", h)          # 「不等于『北齊書里只有这些』」

    def test_banner_falls_back_to_numbers_without_range_text(self):
        """服务端没给 range_text 时用 declared/expected 兜底，**不显示空白**。"""
        h = self.out["banner_partial_no_range"]
        self.assertIn("22/100", h)

    # ------------------------------------- 在册未入库的书（§25 情况四）
    def test_planned_book_never_reaches_the_search_api(self):
        """查一部还没入库的书，**只许问 /api/diagnose**。

        `/api/search` 那边 `engine.resolve_book` 遇到库外的书会抛「未识别的史书」
        —— engine 没错，它的职责就只管库里的书。但用户看到的会是一个错误框，
        等于把「我还没收这本书」说成了「这次检索坏了」。fetch 桩对非 diagnose
        请求直接抛错，所以这条路一旦被走通，这个断言会红。
        """
        self.assertTrue(self.calls, "一次请求都没有 —— 桩没被用上")
        for u in self.calls:
            self.assertIn("/api/diagnose", u, f"不该请求：{u}")

    def test_predicate_truth_table(self):
        """判据本身：空 id 和「拿不到目录」都必须是 false。

        演示模式（`plannedBooks` 为空）下把什么都判成「未入库」的话，公开站上
        每一本书都会变成「这部史书尚未收录」。
        """
        self.assertEqual(self.predicate, [True, False, False, False, False])

    def test_not_imported_card_says_not_collected(self):
        """§25 情况四的正面判据：说「尚未收录」，且带上它有多少卷。

        绝不能出现「没有找到相关史料」这种把「我没有」说成「史书里没有」的句子，
        也不能出现「正文中没有」（§9 明令替换掉的那句话）。
        """
        h = self.out["not_imported_card"]
        self.assertIn("这部史书尚未收录", h)
        self.assertIn("明史", h)
        self.assertIn("332", h)                  # 全书 332 卷，用户要知道尺度
        self.assertNotIn("没有找到相关史料", h)
        self.assertNotIn("正文中没有", h)

    def test_unscoped_no_hit_reports_the_corpus_boundary(self):
        """全库搜不到时，页面上必须**同时**出现分母（19 部）与未收的那批书。

        这是 §28 在「不限史书」这条路上的落点：搜一个词、全库没结果，最容易让
        用户读成「史书里没有」的写法就是只说一句「没有找到」。

        状态必须是 partial_no_hit 而不是 complete_no_hit —— 库里本来就有 5 部
        残卷，从库的层面就断言不了「全都没有」。这条断言同时钉住标题映射：
        四态里任何一个映射写错，用户看到的第一句话就是错的。
        """
        h = self.out["unscoped_no_hit_card"]
        self.assertIn("所收版本不完整", h)
        self.assertIn("全库 <b>19</b> 部", h)          # 分母
        self.assertIn("另有 9 部正史尚未导入", h)       # 未入库的那 9 部（>8 只给数）
        self.assertIn("三國志", h)                     # 残缺的那 5 部具名
        self.assertNotIn("没有找到相关史料", h)
        # 计划书的卡讲的是**那一本**（scope.book 分支），不该再报全库的账。
        self.assertNotIn("另有", self.out["not_imported_card"])


if __name__ == "__main__":
    unittest.main()
