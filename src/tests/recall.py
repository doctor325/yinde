"""第六阶段 — 搜索召回跑分器（可执行 harness，不是 unittest）。

为什么需要它：判断「搜索好不好」不能靠「随便输个人名试试」。第六阶段开工前
正是这么测的——在公开演示站上搜「楚庄王」返回空，就断定搜索引擎有问题。
实测下来本地真实语料里楚庄王有 41 段、郑庄公 13 段、秦始皇 22 段，全部命中；
真正的空原因是**演示站里根本没有真实史料**（语料覆盖问题，不是搜索问题）。
这个误判之所以发生，就是因为缺一份可复现的召回基线。

本文件把计划书 §9（测试集）与 §10（八维分类）落地：对 tests/search_cases/
下按时代分文件的用例集（§21）里的每条 case 跑真实检索，并把异常归到下列八类
之一。**分类不出来的不硬塞**，一律标 UNKNOWN 并列进报告——分类器要是会把已知
答案分错，它就只是在输出噪音。

    Coverage       该词（原形与繁简转换形都）在检索列里 instr 计数为 0 —— 语料确实没有
    Search         原文存在、转换正常，检索却返回 0 —— 引擎问题
    Normalization  繁体形有命中而简体原形无命中，且 zh 转换是恒等回退 —— 繁简环节
    Ranking        有命中，但期望片段不在 top-N（「召回了」不等于「有用」）
    Display        hit_total > 0 但组装不出块 —— 展示层一块都产不出来
    Pagination     产得出块，但**拿不到全部**：触发组装上限（truncated），
                   或末页翻不到、has_more 与实际不符（§9）
    Passage        块里的 text 与库中 text_orig 对不上（缺行/截断/改写）（§15）
    Provenance     块缺溯源字段（书/文件/行号），读者无从知道这段从哪来（§18/§24）

后三维是第六点一阶段按 §20 新增的。它们与前三项的区别在于**判据是机械的**：
Pagination 真去翻末页，Passage 逐字对库，Provenance 查字段是否为空——
不靠「看起来挺全」。

跑法（在 HistoryAI 目录下）：
    PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/recall.py [--report PATH]

不加 --report 只打印，不落盘。基线报告见 docs/phase6_recall.md。
"""
import contextlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api import db                      # noqa: E402
from search import diagnose             # noqa: E402
from search import engine               # noqa: E402
from search import result_block, zh     # noqa: E402
from scripts.pipeline import config     # noqa: E402

# 归因（八维分类）与三条地面真值查询的**唯一**实现在 search/diagnose.py：测试与
# API 共用一份，页面上的说法和报告里的说法不会再各说各话（6.3-H）。下面三个名字
# 保留下来只为少改本文件的调用点，行为一律以 diagnose 为准。
_empty_class = diagnose.classify_empty
corpus_count = diagnose.corpus_count
_terms = diagnose.terms_of
zh_works = diagnose.zh_works

# 用例集是**一个目录**，按时代分文件（§21）。第六点二阶段之前是单个
# search_cases.json，83 条全挤在一起；语料从 5 部先秦书扩到 7 部（含秦汉）之后，
# 一个扁平文件既看不出「哪条是现有语料的、哪条是新语料的」，也没法只跑一个时代。
# 文件名按字典序拼接（preqin < qin_han < …），顺序稳定，跨文件 case 的下标可复现。
CASES_DIR = Path(__file__).resolve().parent / "search_cases"
DEFAULT_REPORT = ROOT / "docs" / "phase6_recall.md"

# 繁简探针（ZH_PROBE）随归因实现搬到了 search/diagnose.py。

# 排名判定的默认窗口。答句排到第 30 个结果里，用户是看不到的——沿用
# tests/test_phase4_questions.py 的 rank_of 思路，而不是「找得到就算过」。
TOP_N_DEFAULT = 10

# 分类标签（八维，§20）
COVERAGE = "Coverage"
SEARCH = "Search"
NORMALIZATION = "Normalization"
RANKING = "Ranking"
DISPLAY = "Display"
PAGINATION = "Pagination"
PASSAGE = "Passage"
PROVENANCE = "Provenance"
UNKNOWN = "UNKNOWN"
DIMS = (COVERAGE, SEARCH, NORMALIZATION, RANKING, DISPLAY,
        PAGINATION, PASSAGE, PROVENANCE, UNKNOWN)

# 块里必须能回答「这段从哪来」的字段（§18/§24）。缺一个，读者就无从回溯。
PROV_KEYS = ("book_title", "file_name", "row_first", "row_last", "passage_ids")
# 自检③用的探针词：它必须**真的不在库**。见 selftest() 里那段说明。
#
# 这个位置已经红过三次，每次都是同一件事：语料长了，探针词被填上。
#   董卓（先秦 5 部时代确实没有）→ 加 後漢書 后 157 段
#   → 改「坑儒」→ 第六点三阶段加南北朝 10 部后 3 段（宋書/陳書/隋書 各 1）
#   → 改「朱元璋」（明史，catalog 里排在最末的 planned 书）
# 教训：探针要挑**路线图上最后一本尚未入库的书**里的词，而不是随手挑个「现在没有」的；
# 守卫本身不能省 —— 它一红就说明有别的负例正被同一件事悄悄证伪，本阶段正是它先红，
# 才顺藤查出 4 条负例（含 坑儒 自己）已被新书填上。
SELFTEST_ABSENT = "朱元璋"
# 自检③b 用的探针：**全库有、这本书没有**。用「坑儒 × 史記」——正是本次被填上的那个词，
# 收窄到史記后仍是 0（史記 用「阬術士」，不用「坑儒」）。它守的是回归：
# 带 book/edition 的用例若拿全库计数当地面真值，这种空会被误判成 Search（引擎故障）。
SELFTEST_SCOPED = ("坑儒", "史記")
# SQLite 的变量上限（老版本 999，新版 32766）。按 500 分批，两边都够安全。
SQL_CHUNK = 500


def top_rank(res: dict, needle: str, top_n: int):
    """needle 第一次出现在第几个块里（1 起）；没出现返回 None。"""
    for i, b in enumerate(res["results"][:top_n], 1):
        if needle in b.get("text", ""):
            return i
    return None


def passage_audit(cur, res: dict) -> list:
    """块的 text 必须**逐字**等于其 passage_ids 对应行的 text_orig 相接（§15）。

    这是「返回完整 Passage 而非 snippet」唯一站得住的判据：不逐字对拍，就只能
    靠肉眼看片段长短，而「看起来挺全」不是证据。任何缺行、截断、改写、
    顺序错乱都会在这里显形。
    """
    bad = []
    blocks = res.get("results") or []
    if not blocks:
        return []
    pids = [p for b in blocks for p in (b.get("passage_ids") or [])]
    if not pids:
        return ["结果块没有 passage_ids，无法核对文本来源"]
    texts: dict = {}
    for i in range(0, len(pids), SQL_CHUNK):
        chunk = pids[i:i + SQL_CHUNK]
        ph = ",".join("?" * len(chunk))
        texts.update(dict(cur.execute(
            f"SELECT passage_id, text_orig FROM passages "
            f"WHERE passage_id IN ({ph})", chunk).fetchall()))
    for i, b in enumerate(blocks):
        bp = b.get("passage_ids") or []
        want = "".join(texts.get(p) or "" for p in bp)
        got = b.get("text") or ""
        if want != got:
            bad.append(f"块[{i}]（{b.get('book_title')}/{b.get('file_name')} "
                       f"行 {b.get('row_first')}）文本与库中原文不符："
                       f"应 {len(want)} 字、实 {len(got)} 字")
    return bad


def provenance_audit(res: dict) -> list:
    """每块都要能回答「出自哪本书、哪个文件、哪一行」（§18/§24）。"""
    bad = []
    for i, b in enumerate(res.get("results") or []):
        missing = [k for k in PROV_KEYS if b.get(k) in (None, "", [])]
        if missing:
            bad.append(f"块[{i}] 缺溯源字段 {missing}")
        elif b.get("n_passages") != len(b["passage_ids"]):
            bad.append(f"块[{i}] n_passages={b.get('n_passages')} 与 "
                       f"passage_ids={len(b['passage_ids'])} 不符")
    return bad


def pagination_audit(cur, case: dict, res: dict) -> list:
    """翻到末页，确认**真的还有内容**（§9）。

    这是解除组装上限之前唯一能抓住问题的地方：旧实现里 total 说有 4454 块，
    而末页返回 0 块 —— 光看第一页一切正常。所以判据是「真去翻」，
    不是「total 与 has_more 自洽」（那样两边是同一个数字，永远自洽）。
    """
    bad = []
    total, ps = res["total"], res["page_size"]
    expect_more = res["page"] * ps < total
    if bool(res.get("has_more")) != expect_more:
        bad.append(f"has_more={res.get('has_more')} 与 page*page_size<total "
                   f"（{res['page']}×{ps} < {total} = {expect_more}）不符")
    if total <= ps:
        return bad                     # 一页装得下，没有「末页」可翻
    last = (total + ps - 1) // ps
    try:
        r2 = result_block.search_result_blocks(
            cur, case["query"], book=case.get("book"), edition=case.get("edition"),
            page=last, page_size=ps, mode=case.get("mode", "standard"),
            text_mode=case.get("text_mode", "orig"))
    except Exception as e:                                  # noqa: BLE001
        bad.append(f"翻到末页（第 {last} 页）抛异常 {type(e).__name__}: {e}")
        return bad
    if not r2.get("results"):
        bad.append(f"末页（第 {last} 页，共 {total} 块）返回 0 块 —— 后面的块翻不到")
    return bad


def recall_ok(case: dict, res: dict) -> bool:
    """这条 case 算不算「召回到了」。

    默认看 hit_total > 0（正文命中）。`recall: "section"` 的条目看的是**篇名命中**
    ——那一类 hit_total 本来就是 0，拿它判会把功能正常的篇名检索判成失败。
    """
    if case.get("recall") == "section":
        want = case.get("expect_match_type") or "section"
        return any(b.get("match_type") in (want, "both")
                   for b in res.get("results") or [])
    return res["hit_total"] > 0


# ---------------------------------------------------------- Recall Invariant
# 第六点四阶段 §15：把「命中数 == 基线」这种**定数断言**换成**包含断言**。
#
# 定数断言为什么必须换掉：语料每加一部书，命中数就变，`baseline_hits` 立刻漂移。
# 漂移不是失败（报告里也是这么写的），可它天天响，响到最后没人看 —— 一个没人看的
# 告警等于没有告警。反过来，「上次返回过的这一段，这次还得返回」是**单调**的：
# 语料只增不减，新数据只会带来新结果，不会让老结果消失。它只在真出问题时才红。
#
# 存的是 `(book_id, file_name, row_first, row_last)` 而不是 `passage_id`：
# rowid 只在**这一个** history.db 里有效，重建库（run_all 重跑）就会变；文件号 +
# 行号是从源文件直接读出来的，只要源文件没改就永远指同一段原文。§15 说的
# `expected_passage_ids ⊆ actual_passage_ids`，落地上就是这个包含关系。
INVARIANT_IN = 8        # 每条用例最多冻结前 8 段 —— 够抓住召回退化，又不至于
                        # 把「排序微调」也判成失败（顺序不在包含断言的范围里）
INVARIANT_FILE = "invariant.json"   # 机器生成的基线，与手写用例分开放


def prov_of(block: dict) -> tuple:
    return (block.get("book_id") or block.get("book_title") or "",
            block.get("file_name") or "",
            block.get("row_first"), block.get("row_last"))


def invariant_violations(case: dict, res: dict) -> list:
    """冻结过的期望片段，这次还都在结果里吗。**只进不退**。"""
    want = case.get("expect_prov")
    if not want:
        return []
    got = {prov_of(b) for b in (res.get("results") or [])}
    missing = [w for w in want if tuple(w) not in got]
    if not missing:
        return []
    shown = "；".join(f"{m[1]} 行 {m[2]}-{m[3]}" for m in missing[:3])
    return [f"冻结的 {len(want)} 段里有 {len(missing)} 段这次没返回（{shown}）"
            f" —— 召回退化（语料只增不减，老结果不该消失）"]


# 冻结动作只有一处实现：`freeze_run()`（在文件末尾，`--freeze-invariant` 调它）。
# 这里曾经另有一个 freeze_invariant() 辅助函数，写的是**另一套** JSON 结构。
# 两个写同一个基线文件的函数 = 两套基线，早晚会写出后者覆盖前者的东西。


# §28 的机器判据：这些词组**不许被当成结论**。判据不是「出现即禁止」——
# not_imported 的正确措辞里就带着它（「这不是「这本书里没有」，是这本书还搜不了」），
# 那是**否定**它，正是我们想要的句子。所以看它前面几个字里有没有否定词。
_BANNED_ABSENCE = ("正文中没有", "史书中没有", "书里没有", "语料中没有")
_NEGATORS = ("不是", "不等于", "并非", "而不是", "不能", "无从", "无法")


def _asserted_absence(summary: str) -> str | None:
    """摘要里有没有**未被否定**的「…没有」断言。返回第一个越界词组。"""
    for phrase in _BANNED_ABSENCE:
        start = 0
        while True:
            i = summary.find(phrase, start)
            if i < 0:
                break
            before = summary[max(0, i - 12):i]
            if not any(n in before for n in _NEGATORS):
                return phrase
            start = i + len(phrase)
    return None


def _classify_status(cur, case: dict, out: dict) -> dict:
    """四态用例：产品给出的 result_status 必须与期望相符（6.4 §25）。

    只认 `diagnose()` 的结论 —— 那是页面与 API 共用的唯一实现。这里再判一次
    「按语料该不该是 partial」就是第二套说法，迟早和产品说的不一样。
    """
    try:
        d = diagnose.diagnose(cur, case["query"], book=case.get("book"),
                              edition=case.get("edition"))
    except ValueError as e:
        out.update(status="FAIL", klass=COVERAGE,
                   why=f"诊断抛异常（书名认不出？）{e}")
        out["got_status"] = None
        return out
    got = d.get("result_status")
    want = case["expect_status"]
    sc = d.get("scope") or {}
    out["got_status"] = got
    out["scope_status"] = sc.get("status")
    out["summary"] = d.get("summary")
    b = sc.get("book") or {}
    out["scope_range"] = b.get("range_text") or b.get("upstream_text") or ""
    if got != want:
        out.update(status="FAIL", klass=COVERAGE,
                   why=f"四态应为 {want}，实得 {got}（{d.get('summary')}）")
        return out
    # 情况三的要求不止「状态对」，还要**给出当前收录范围**（§25）：报 partial
    # 却不告诉用户收了多少卷，用户还是不知道缺在哪。
    # 整库范围（book=None）没有单一 range_text，它的范围体现在 incomplete 名单上 ——
    # 「全库 5 部残缺」本身就是范围，两种给法都算数。
    if want == "partial_no_hit" and not (out["scope_range"]
                                        or sc.get("incomplete")):
        out.update(status="FAIL", klass=COVERAGE,
                   why="报了 partial_no_hit 却没给出收录范围"
                       "（既没有 range_text，也没有残缺书名单）")
        return out
    # §28：这两态下的措辞不许**断言**「书里没有」。
    # 注意是断言，不是出现 —— not_imported 的正确措辞恰恰是
    # 「这不是「这本书里没有」，是这本书还搜不了」，那句话里就带着这个词组。
    # 判据是看它有没有被否定（前面几个字里有没有「不是」「不等于」）。
    if want in ("partial_no_hit", "not_imported"):
        bad = _asserted_absence(d.get("summary") or "")
        if bad:
            out.update(status="FAIL", klass=COVERAGE,
                       why=f"措辞越界：「{bad}」被当成结论说了出来（§28 禁止）")
            return out
    out["why"] = f"四态 {got} 相符" + (f"，收录范围「{out['scope_range']}」"
                                       if out["scope_range"] else "")
    return out


def classify(cur, case: dict, zh_ok: bool) -> dict:
    """跑一条 case 并给出八维判定。"""
    q = case["query"]
    trad = zh.to_traditional(q)
    terms = _terms(trad)
    # 地面真值必须与**这次检索的范围**一致：带 book/edition 的用例，语料计数也要
    # 跟着过滤。否则「这本书里确实没有」会被判成 Search（引擎故障）——第六点三
    # 阶段第二批收窄到单书的 4 条负例正是这么被全体判错的。解析器与检索侧同一份
    # （engine.resolve_book / resolve_edition），不另立一套书名口径。
    try:
        bid = engine.resolve_book(cur, case.get("book"))
        edi = (engine.resolve_edition(case["edition"])
               if case.get("edition") else None)
    except ValueError:
        bid = edi = None            # 认不出的书名/版片：交给检索侧报同一个错
    n_corpus = corpus_count(cur, terms, bid, edi)
    page, ps = case.get("page", 1), case.get("page_size", 20)

    out = {"query": q, "trad": trad, "corpus_hits": n_corpus,
           "hit_total": 0, "total": 0, "exec_mode": "-", "truncated": False,
           "section_truncated": False, "match_types": [], "audits": [],
           "books": [], "rank": None, "klass": "-", "status": "PASS", "why": ""}

    # 搜索结果四态（6.4 §14/§15）：这类用例问的不是「命中几段」，而是
    # 「**产品如实说了什么**」—— 未导入的书它必须报 not_imported，残缺的书必须报
    # partial_no_hit 并给出收录范围。所以判据走 diagnose()（产品真正调用的那一份），
    # 不走 search_result_blocks：后者对未导入的书直接抛 ValueError，
    # 根本走不到「怎么措辞」这一步。
    if case.get("expect_status"):
        return _classify_status(cur, case, out)

    try:
        res = result_block.search_result_blocks(
            cur, q, book=case.get("book"), edition=case.get("edition"),
            page=page, page_size=ps, mode=case.get("mode", "standard"),
            text_mode=case.get("text_mode", "orig"))
    except Exception as e:                                  # noqa: BLE001
        out.update(status="FAIL", klass=SEARCH,
                   why=f"检索抛异常 {type(e).__name__}: {e}")
        return out

    out["hit_total"] = res["hit_total"]
    out["total"] = res["total"]
    out["exec_mode"] = res["exec_mode"]
    out["truncated"] = res["truncated"]
    out["section_truncated"] = res.get("section_truncated", False)
    out["match_types"] = sorted({b.get("match_type") or "text"
                                 for b in res["results"]})
    out["books"] = sorted({b["book_title"] for b in res["results"]})

    hit = recall_ok(case, res)
    if not case.get("must_hit", True):
        # 负例对照：预期为空，且空的原因必须与 expect_class 相符
        if hit:
            out.update(status="FAIL", klass=SEARCH,
                       why=f"预期无命中，实得 {res['hit_total']} 段 / "
                           f"{res['total']} 块")
            return out
        want = case.get("expect_class")
        klass = _empty_class(cur, q, n_corpus, zh_ok, bid, edi)
        out["klass"] = klass
        if want and klass != want:
            out.update(status="FAIL",
                       why=f"空的归因是 {klass}，期望 {want}")
        else:
            out["why"] = f"预期为空，归因 {klass} 相符"
        return out

    # must_hit = True
    if not hit:
        klass = _empty_class(cur, q, n_corpus, zh_ok, bid, edi)
        out.update(status="FAIL", klass=klass,
                   why=f"应命中却返回 0；语料实有 {n_corpus} 段")
        return out

    # 有命中，继续查 Display / Pagination / Passage / Provenance / Ranking
    if res["total"] == 0:
        out.update(status="FAIL", klass=DISPLAY,
                   why=f"命中 {res['hit_total']} 段却组装不出结果块")
        return out

    audits = []                        # 逐维机械判据的结论，全部进报告
    # Pagination：组装上限（现在是安全阀，不该再触发）
    if res["truncated"]:
        out.update(status="WARN", klass=PAGINATION,
                   why=f"命中 {res['hit_total']} 段，触发组装上限，结果不完整")
        return out
    if case.get("check_pagination"):
        bad = pagination_audit(cur, case, res)
        audits.append((PAGINATION, bad))
        if bad:
            out.update(status="FAIL", klass=PAGINATION, why="；".join(bad[:3]))
            out["audits"] = _pack(audits)
            return out
    # Passage：块文本与库中原文逐字对拍。**每条都跑**，不是只跑 passage 组——
    # 「返回完整 Passage」是要在每条查询上都成立的保证，抽查两条证明不了什么。
    bad = passage_audit(cur, res)
    audits.append((PASSAGE, bad))
    if bad:
        out.update(status="FAIL", klass=PASSAGE, why="；".join(bad[:3]))
        out["audits"] = _pack(audits)
        return out
    # Provenance：溯源字段齐备
    bad = provenance_audit(res)
    audits.append((PROVENANCE, bad))
    if bad:
        out.update(status="FAIL", klass=PROVENANCE, why="；".join(bad[:3]))
        out["audits"] = _pack(audits)
        return out

    # Recall Invariant（§15）：冻结过的片段必须还在。排在最后，因此前面几维
    # 都过了才判它 —— 一条用例只报**最先**出问题的那一维，不叠报。
    out["_prov_top"] = [list(prov_of(b)) for b in res["results"][:INVARIANT_IN]]
    bad = invariant_violations(case, res)
    audits.append(("RecallInvariant", bad))
    if bad:
        out.update(status="FAIL", klass=PASSAGE, why="；".join(bad[:3]))
        out["audits"] = _pack(audits)
        return out

    if case.get("top_needle"):
        top_n = case.get("top_n", TOP_N_DEFAULT)
        r = top_rank(res, case["top_needle"], top_n)
        out["rank"] = r
        if r is None:
            out.update(status="FAIL", klass=RANKING,
                       why=f"期望片段「{case['top_needle']}」不在前 {top_n} 块内")
            out["audits"] = _pack(audits)
            return out
    out["audits"] = _pack(audits)
    return out


def _pack(audits) -> list:
    """审计结论 → 报告用的短标记。没跑的维度不写，不假装跑过。"""
    return [f"{dim}:{'通过' if not bad else f'{len(bad)} 处'}" for dim, bad in audits]


def _stub_result() -> dict:
    """一个形状合法但零命中的检索结果，用于自检时注入故障。"""
    return {"q": "", "q_traditional": "", "mode": "stub", "text_mode": "orig",
            "terms_simplified": [], "exec_mode": "stub", "book": None,
            "edition": None, "hit_total": 0, "page": 1, "page_size": 20,
            "total": 0, "match_count_sum": 0, "truncated": False,
            "limits": {}, "results": []}


def selftest() -> int:
    """注入已知故障，验证分类器真的分得出来。

    全绿的跑分报告本身证明不了任何事——一个永远返回 PASS/Coverage 的分类器
    看起来一模一样。所以这里把检索结果强行置空，看它能不能把
    「语料有、引擎找不到」判成 Search，把「只有转换后才查得到」判成
    Normalization，把「繁简回退」判成 UNKNOWN。

    ①②④ 依赖的是「库里一定有齐桓公」，加多少书都不会失效；③ 与 ③b 依赖
    「库里（或某本书里）一定没有某个词」，而**语料是会长的**——所以它不能写死结论，
    当场数一遍再决定（见 SELFTEST_ABSENT 那段）。
    """
    orig_search = result_block.search_result_blocks
    orig_trad = zh.to_traditional
    cur = db.connect()
    # 自检③的探针词必须真的不在库里。第六点二阶段这条自己红过一次：
    # 原来写的是 董卓（先秦 5 部里确实没有），加入 後漢書 之后它有了 157 段
    # ——同一次扩容也把 preqin 的 neg-01（也是董卓）从负例迁成了正例。
    # 教训一样：**负例的成立与否随语料而变**，所以这里当场数一遍再决定。
    n_absent = corpus_count(cur, [SELFTEST_ABSENT])
    cases = [
        # ① 原形（繁体）本身在库里有命中，引擎却返回 0 → 引擎问题
        ("自我检测① 繁体原形有命中却返回 0", "齊桓公", True, "Search", None),
        # ② 简体原形在库里数不到、转换后才数得到 → 繁简环节
        ("自我检测② 只有转换形才有命中", "齐桓公", True, "Normalization", None),
    ]
    bad = []
    if n_absent:
        print(f"  [FAIL] 自检③的探针词「{SELFTEST_ABSENT}」已在库中（{n_absent} 段）："
              f"它不能再充当「语料确实没有」的探针，请改 tests/recall.py 的 "
              f"SELFTEST_ABSENT")
        bad.append("自检③探针词已入库")
    else:
        # ③ 语料确实没有 → 覆盖问题（不能误报成引擎问题）
        cases.append((f"自我检测③ 语料确实没有（{SELFTEST_ABSENT}）",
                      SELFTEST_ABSENT, True, "Coverage", None))
    # ③b 带书过滤：全库有、这本书没有 → 同样是 Coverage，不能判成 Search。
    # 判据当场数：全库计数必须 >0、该书计数必须 =0，否则这条探针本身失效。
    q_sc, bk_sc = SELFTEST_SCOPED
    try:
        bid_sc = engine.resolve_book(cur, bk_sc)
    except ValueError as e:
        bid_sc = None
        print(f"  [FAIL] 自检③b 的探针书「{bk_sc}」认不出来：{e}")
        bad.append("自检③b 探针书不存在")
    if bid_sc is not None:
        n_all = corpus_count(cur, _terms(zh.to_traditional(q_sc)))
        n_bk = corpus_count(cur, _terms(zh.to_traditional(q_sc)), bid_sc)
        if n_all and not n_bk:
            cases.append((f"自我检测③b 带书过滤（{q_sc} × {bk_sc}：全库 {n_all} 段、"
                          f"该书 0 段）", q_sc, True, "Coverage", bk_sc))
        else:
            print(f"  [FAIL] 自检③b 的探针失效（{q_sc}：全库 {n_all} 段、{bk_sc} "
                  f"{n_bk} 段）—— 需要「全库有、该书没有」的组合，请改 "
                  f"SELFTEST_SCOPED")
            bad.append("自检③b探针失效")
    try:
        result_block.search_result_blocks = lambda *a, **k: _stub_result()
        for name, q, zh_ok, want, book in cases:
            r = classify(cur, {"id": "self", "query": q, "must_hit": True,
                               "book": book}, zh_ok)
            ok = r["klass"] == want
            print(f"  [{'ok  ' if ok else 'FAIL'}] {name}：判为 {r['klass']}，"
                  f"期望 {want}")
            if not ok:
                bad.append(name)
        # ④ 繁简回退时不得硬塞归因
        zh.to_traditional = lambda s: s
        r = classify(cur, {"id": "self", "query": "齐桓公", "must_hit": True},
                     zh_works())
        ok = r["klass"] == UNKNOWN
        print(f"  [{'ok  ' if ok else 'FAIL'}] 自我检测④ 繁简回退如实报 UNKNOWN："
              f"判为 {r['klass']}，期望 {UNKNOWN}")
        if not ok:
            bad.append("自我检测④")
    finally:
        result_block.search_result_blocks = orig_search
        zh.to_traditional = orig_trad
        cur.close()
    print()
    if bad:
        print(f"自检未通过：{len(bad)} 项 —— 分类器不可信，跑分结果无意义")
        return 1
    print("自检通过：分类器能正确区分 Coverage / Search / Normalization / UNKNOWN")
    return 0


def _script_pairs(cases, results) -> list:
    """繁简对一致性：query 与繁体直输必须给出相同 hit_total。"""
    by_id = {r["id"]: r for r in results}
    rows = []
    for c in cases:
        other = c.get("expect_same_as")
        if not other:
            continue
        a, b = by_id.get(c["id"]), by_id.get(other)
        if not a or not b:
            continue
        rows.append((c["id"], c["query"], other, b["query"],
                     a["hit_total"], b["hit_total"],
                     a["hit_total"] == b["hit_total"]))
    return rows


def load_cases() -> list:
    """读 search_cases/ 下所有 *.json，按文件名排序拼接。

    每个文件形如 {version, corpus, note, cases: [...]}。**顺手查 id 唯一**：
    `by_id`（`expect_same_as` 跨文件引用）与报告里的下标都假设 id 唯一，
    今天没有这条断言时，重复 id 会静默取最后一条——拆成多文件之后更容易撞。
    """
    cases: list = []
    seen: dict = {}
    # invariant.json 是机器生成的基线（cases 是 dict 不是 list），不能当用例文件读。
    files = [p for p in sorted(CASES_DIR.glob("*.json"))
             if p.name != INVARIANT_FILE]
    if not files:
        raise SystemExit(f"没有用例文件：{CASES_DIR}")
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        for c in d.get("cases", []):
            c = dict(c)
            c["_file"] = p.name
            if c["id"] in seen:
                raise SystemExit(f"用例 id 重复：{c['id']}（{seen[c['id']]} 与 {p.name}）")
            seen[c["id"]] = p.name
            cases.append(c)
    _attach_invariant(cases)
    return cases


def _attach_invariant(cases: list) -> int:
    """把 `invariant.json` 里冻结的 expect_prov 贴回各条用例。

    **不放进 search_cases/ 里**：那些文件是人手写的用例（查询、期望、注释），
    这个文件是机器生成的基线，混在一起会被人当成手写数据去编辑。两者也按不同的
    节奏变 —— 用例是人想出来的，基线是跑出来的。
    """
    p = CASES_DIR / INVARIANT_FILE
    if not p.is_file():
        return 0
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"** {INVARIANT_FILE} 读不了（{e}）—— 本次不判 Recall Invariant")
        return 0
    frozen = d.get("cases") or {}
    n = 0
    for c in cases:
        if frozen.get(c["id"]):
            c["expect_prov"] = [tuple(x) for x in frozen[c["id"]]]
            n += 1
    return n


def run(report_path=None) -> int:
    cases = load_cases()
    zh_ok = zh_works()
    cur = db.connect()

    # 先自检：分类器要是分不出已知故障，下面的跑分结果就没有意义。
    # 输出不丢——失败时必须看得见是哪一项，否则报告里只剩一句「未通过」，
    # 连从哪改都不知道。
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        self_ok = selftest() == 0
    self_report = buf.getvalue()

    print("=" * 78)
    print("第六阶段 · 搜索召回跑分")
    print("=" * 78)
    print(f"库        {config.DB_PATH}")
    print(f"繁简转换  {'可用' if zh_ok else '!! 恒等回退 —— 简体输入会全部搜不到'}")
    print(f"用例      {len(cases)} 条")
    # 不变量加载失败时跑分照样全绿 —— 把条数印在抬头里，让「有没有判」这件事
    # 一眼可见，而不是只能从报告里推断。
    frozen_n = sum(1 for c in cases if c.get("expect_prov"))
    print(f"不变量    {frozen_n} 条带冻结基线，其余 {len(cases) - frozen_n} 条不判")
    if not self_ok:
        print("\n**分类器自检未通过 —— 下面的归因不可信**")
        print(self_report)
    print()

    results = []
    for c in cases:
        r = classify(cur, c, zh_ok)
        r["id"] = c["id"]
        r["group"] = c["group"]
        r["baseline"] = c.get("baseline_hits")
        r["baseline_blocks"] = c.get("baseline_blocks")
        r["note"] = c.get("note", "")
        # 非默认检索参数如实带进报告：同一句话在不同 mode/page_size 下块数不同，
        # 不写清楚，读者会以为两个数字该相等。
        over = [f"{k}={c[k]}" for k in ("book", "edition", "mode", "text_mode",
                                        "page", "page_size", "recall")
                if k in c and c[k] not in (None, "", "standard", "orig", 1, 20)]
        r["params"] = "、".join(over)
        results.append(r)
        mark = {"PASS": "ok  ", "WARN": "warn", "FAIL": "FAIL"}[r["status"]]
        print(f"  [{mark}] {c['id']:<12} {c['query']:<16} "
              f"段={r['hit_total']:<6} 块={r['total']:<5} {r['exec_mode']:<7} "
              f"{r['klass'] if r['klass'] != '-' else ''}"
              + (f"  {r['why']}" if r["why"] else ""))
    cur.close()

    pairs = _script_pairs(cases, results)
    text = render(results, pairs, zh_ok, self_ok, frozen_n)
    print()
    print(text)

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n"：Windows 上默认会写成 CRLF，和仓库的 eol=lf 约定不符。
        report_path.write_text(text, encoding="utf-8", newline="\n")
        print(f"\n报告已写入 {report_path}")

    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    # 自检不过必须让整次跑分失败：报告里那句「归因不可信」如果不反映到
    # 退出码上，CI 和「扫一眼全绿」的人都会把它放过——那正是本文件开头
    # 说要避免的事。
    return 1 if (n_fail or not self_ok) else 0


def render(results, pairs, zh_ok, self_ok=True, frozen_n=0) -> str:
    """生成 markdown 报告。"""
    n = len(results)
    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_warn = sum(1 for r in results if r["status"] == "WARN")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")

    L = []
    L.append("# 第六阶段 · 搜索召回基线报告")
    L.append("")
    L.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append("")
    L.append("由 `python tests/recall.py --report docs/phase6_recall.md` 生成，请勿手改。")
    L.append("")
    L.append("## 运行环境")
    L.append("")
    L.append(f"- 数据库：`{config.DB_PATH}`")
    n_file = len([p for p in CASES_DIR.glob("*.json") if p.name != INVARIANT_FILE])
    L.append(f"- 用例集：`tests/search_cases/`（{n_file} 个时代文件，{n} 条）")
    # §15 的基线有没有真的加载上，必须写进报告：加载失败时跑分照样全绿，
    # 报告长得一模一样 —— 那是「安全检查静默失效」，比不检查更糟。
    L.append(f"- Recall Invariant：**{frozen_n} 条带冻结基线**"
             + (f"，其余 {n - frozen_n} 条不判（负例 / 四态 / 超长结果，见报告末尾）"
                if frozen_n < n else "")
             + f"（`{INVARIANT_FILE}`）")
    L.append(f"- 繁简转换：**{'可用' if zh_ok else '恒等回退（简体输入会全部搜不到）'}**")
    L.append("")
    L.append("繁简转换是否可用是全局开关——脚本 `search/zh.py` 依赖 Windows 的 "
             "`LCMapStringEx`，非 Windows 或调用失败会静默返回原文。")
    L.append("")
    L.append("## 分类器自检")
    L.append("")
    if self_ok:
        L.append("**通过。** 注入已知故障后，分类器能把「繁体原形有命中却返回 0」"
                 "判为 `Search`、把「只有转换形才有命中」判为 `Normalization`、"
                 "把「语料确实没有」判为 `Coverage`，并在繁简回退时如实报 "
                 "`UNKNOWN`。")
        L.append("")
        L.append("这一步是必需的：全绿的跑分报告本身证明不了任何事——一个永远"
                 "返回 `PASS`/`Coverage` 的分类器看起来一模一样。")
    else:
        L.append("**未通过。** 分类器分不出已知故障，**本报告的归因不可信**。")
    L.append("")
    L.append("## 汇总")
    L.append("")
    L.append("| 结果 | 条数 |")
    L.append("|---|---|")
    L.append(f"| 通过 | {n_pass} |")
    L.append(f"| 注意 | {n_warn} |")
    L.append(f"| 失败 | {n_fail} |")
    L.append(f"| 合计 | {n} |")
    L.append("")
    L.append("### 八维分布")
    L.append("")
    L.append("| 维度 | 条数 | 含义 |")
    L.append("|---|---|---|")
    meaning = {
        COVERAGE: "语料里确实没有（检索列 instr 计数为 0）",
        SEARCH: "原文存在、转换正常，检索却返回 0 —— 引擎问题",
        NORMALIZATION: "繁简环节（转换恒等回退，无法判定）",
        RANKING: "有命中但期望片段不在前 N 块内",
        DISPLAY: "有命中却组装不出块 —— 展示层一块都产不出来",
        PAGINATION: "触发组装上限，或末页翻不到、has_more 与实际不符",
        PASSAGE: "块里的 text 与库中 text_orig 对不上（缺行/截断/改写）",
        PROVENANCE: "块缺溯源字段（书/文件/行号）",
        UNKNOWN: "分类不出来，需人工判定",
    }
    for d in DIMS:
        c = sum(1 for r in results if r["klass"] == d)
        if c:
            L.append(f"| `{d}` | {c} | {meaning[d]} |")
    if not any(r["klass"] in DIMS for r in results):
        L.append("| — | 0 | 没有任何 case 落入八维异常 |")
    L.append("")
    L.append("## 逐条结果")
    L.append("")
    L.append("| id | 分组 | 查询 | 转换后 | 参数 | 语料段 | 命中段 | 结果块 | 命中形态 | "
             "路径 | 机械审计 | 出处 | 维度 | 结论 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        books = "、".join(r["books"][:3]) + ("…" if len(r["books"]) > 3 else "")
        if r["baseline"] is not None and r["hit_total"] != r["baseline"]:
            books = f"⚠漂移(基线{r['baseline']}) {books}"
        if r.get("baseline_blocks") is not None and r["total"] != r["baseline_blocks"]:
            books = f"⚠块数漂移(基线{r['baseline_blocks']}) {books}"
        par = r.get("params") or "默认"
        L.append(f"| {r['id']} | {r['group']} | {r['query']} | {r['trad']} | "
                 f"{par} | "
                 f"{r['corpus_hits']} | {r['hit_total']} | {r['total']} | "
                 f"{'、'.join(r.get('match_types') or []) or '—'} | "
                 f"{r['exec_mode']} | {'、'.join(r.get('audits') or []) or '—'} | "
                 f"{books or '—'} | "
                 f"{r['klass'] if r['klass'] != '-' else ''} | "
                 f"{r['status']}{'：' + r['why'] if r['why'] else ''} |")
    L.append("")

    zero = [r for r in results if r["hit_total"] == 0]
    L.append(f"## 0 命中清单（{len(zero)} 条）")
    L.append("")
    if not zero:
        L.append("无。")
    else:
        L.append("每条都写明归因。`Coverage` 是语料问题（该扩语料），"
                 "`Search` 才是引擎问题（该改代码）——两者绝不能混。")
        L.append("")
        L.append("| id | 查询 | 语料段 | 归因 | 说明 |")
        L.append("|---|---|---|---|---|")
        for r in zero:
            L.append(f"| {r['id']} | {r['query']} | {r['corpus_hits']} | "
                     f"{r['klass']} | {r['note']} |")
    L.append("")

    L.append("## 繁简一致性")
    L.append("")
    if not pairs:
        L.append("用例集中没有声明 `expect_same_as` 的繁简对。")
    else:
        L.append("简体输入与繁体直输必须给出相同的命中数，否则说明转换环节有问题。")
        L.append("")
        L.append("| 简体 | 繁体 | 简体命中 | 繁体命中 | 一致 |")
        L.append("|---|---|---|---|---|")
        for _, qa, _, qb, na, nb, ok in pairs:
            L.append(f"| {qa} | {qb} | {na} | {nb} | {'是' if ok else '**否**'} |")
    L.append("")

    unknown = [r for r in results if r["klass"] == UNKNOWN]
    L.append(f"## UNKNOWN 清单（{len(unknown)} 条）")
    L.append("")
    if not unknown:
        L.append("无。分类器对所有 0 命中都给出了归因。")
    else:
        L.append("**这些必须人工判定**，不许默认当作通过。")
        L.append("")
        for r in unknown:
            L.append(f"- `{r['id']}` {r['query']} —— {r['why']}")
    L.append("")

    def drifted(r):
        """命中数或块数任一对不上基线。两个都报——块数漂移往往是组装逻辑变了，
        只看命中数会漏掉（第六点一阶段就出现过：命中数一个没动，块数全变）。"""
        return ((r["baseline"] is not None and r["hit_total"] != r["baseline"]) or
                (r.get("baseline_blocks") is not None and
                 r["total"] != r["baseline_blocks"]))

    drift = [r for r in results if drifted(r)]
    L.append(f"## 基线漂移（{len(drift)} 条）")
    L.append("")
    L.append("`search_cases/` 里的 `baseline_hits` / `baseline_blocks` 是 2026-09-12 "
             "在 7 部语料上的实测值（第六点二阶段扩容后重测）。漂移本身不是失败——"
             "语料扩充后必然变化——但每条都要能解释。")
    L.append("")
    if not drift:
        L.append("无漂移，与建立基线时完全一致。")
    else:
        L.append("| id | 查询 | 命中基线 | 本次 | 块数基线 | 本次 |")
        L.append("|---|---|---|---|---|---|")
        for r in drift:
            hb = r["baseline"] if r["baseline"] is not None else "—"
            bb = r.get("baseline_blocks")
            bb = bb if bb is not None else "—"
            L.append(f"| {r['id']} | {r['query']} | {hb} | {r['hit_total']} "
                     f"| {bb} | {r['total']} |")
    L.append("")
    if frozen_n < n:
        L.append(f"## Recall Invariant 的射程（{frozen_n} / {n} 条）")
        L.append("")
        L.append(f"冻结基线在 `{INVARIANT_FILE}` 里，判据是"
                 "`expected_passage_ids ⊆ actual_passage_ids`：**冻过的片段必须还在**，"
                 "防的是召回退化（语料只增不减，老结果不该消失）。剩下 "
                 f"{n - frozen_n} 条不判，三类都不是漏：")
        L.append("")
        L.append("- **负例**（`must_hit=false`）：本来就没有片段，冻一个空集等于什么都没说。")
        L.append("- **四态用例**（`cov-*`）：判的是措辞（`result_status` + 收录范围 + "
                 "不许断言「书里没有」），判据走 `diagnose()`，这条路径不取片段表。")
        L.append("- **超长结果用例**（`nb-single-01` / `pagination-01`）：在分页审计那一步"
                 "就返回了，走不到冻结那一行；它们本身就是本阶段记录的性能债。")
        L.append("")
        L.append("反方向（一条 0 命中的用例以后命中了）不在射程内 —— "
                 "那由四态用例自己的 `expect_status` 去守。一条断言管两头，两头都会松。")
        L.append("")
    return "\n".join(L)


def _invariant_payload(frozen: list) -> str:
    """基线文件的**全部**内容构造（单独一个函数，为了能先空跑一遍）。

    这里曾经把 `datetime.now(timezone.utc)` 写在 `freeze_run` 的最后一行，而
    `timezone` 没 import —— 于是跑完 373 条用例、耗掉十几分钟之后才在**最后一句**
    NameError，全部结果丢掉。写文件的那段代码在真跑之前先拿空表执行一次，
    这种错就会在毫秒内炸出来。
    """
    return json.dumps(
        {"note": "自动生成（tests/recall.py --freeze-invariant）：每条用例本次"
                 "返回的前若干段的来源坐标（书 id/文件名/起止行）。语料只增不减，"
                 "这些片段必须在以后每次跑里都还出现 —— 这是 §15 的 Recall "
                 "Invariant：expected_passage_ids ⊆ actual_passage_ids。"
                 "**不要手改**；要重冻就再跑一次 --freeze-invariant（先确认报告是绿的）。",
         "frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),   # 同 689 行：本地时间
         "count": len(frozen),
         "cases": {x["id"]: x["prov"] for x in frozen}}, ensure_ascii=False,
        indent=2)


def freeze_run(quiet: bool = False) -> int:
    """跑一遍**当前**用例集，把前 N 段的来源坐标冻成 `invariant.json`。

    先用脚跑、再看结果 —— 冻结动作本身不判断对错，它只是把「今天这套引擎给出的
    结果」记成以后的底线。所以**跑之前先看一眼报告是绿的**：把一次带 bug 的输出
    冻进去，那个 bug 就从此变成了「基线」，再也不会红。
    """
    _invariant_payload([])          # 先空跑一次写文件的代码，见上面的 docstring
    cases = load_cases()
    cur = db.connect()
    frozen: list = []
    for c in cases:
        r = classify(cur, c, zh_works())
        provs = r.get("_prov_top") or []
        if provs:
            frozen.append({"id": c["id"], "prov": [list(p) for p in provs]})
    p = CASES_DIR / INVARIANT_FILE
    p.write_text(_invariant_payload(frozen), encoding="utf-8")
    if not quiet:
        print(f"已冻结 {len(frozen)} / {len(cases)} 条用例的前 {INVARIANT_IN} 段 → {p}")
        print("（负例与篇名用例没有正文块，不入表，这是正常的。）")
    return 0


def main(argv) -> int:
    if "--selftest" in argv:
        if not config.DB_PATH.is_file():
            print(f"缺 history.db：{config.DB_PATH}")
            return 1
        print("=" * 78)
        print("分类器自检（注入已知故障，验证分得出来）")
        print("=" * 78)
        return selftest()
    report = None
    if "--report" in argv:
        i = argv.index("--report")
        report = Path(argv[i + 1]) if i + 1 < len(argv) else DEFAULT_REPORT
    elif "--no-report" not in argv:
        report = DEFAULT_REPORT
    if not config.DB_PATH.is_file():
        print(f"缺 history.db：{config.DB_PATH}\n先跑 python -m scripts.pipeline.run_all")
        return 1
    if "--freeze-invariant" in argv:
        return freeze_run(report is not None)
    # 报告要落盘时把过程输出收起来，避免和 markdown 混在一起
    if report:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(report)
        print(buf.getvalue())
    else:
        rc = run(None)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
