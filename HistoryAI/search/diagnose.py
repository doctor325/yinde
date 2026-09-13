"""搜不到时的**唯一**归因实现（第六点三阶段 6.3-H）。

为什么要有它：「搜不到」有三种完全不同的原因 —— 语料没收（Coverage）、繁简转换
把词转丢了（Normalization）、引擎真出错了（Search）。三者在界面上都只表现为
「0 条结果」，用户（和开发者）会把它们混成一个「搜索坏了」。第六点二阶段在
`tests/recall.py` 里写过一份 `_empty_class()`，但那是**测试侧**的实现：API 与前端
拿不到，于是同一个问题在页面上的说法和测试报告里的说法各说各话。本模块把归因
收到一处 —— 测试、API 都问它，不再各写一份。

逐层回答（每层给出 `ok` 与证据，顺序就是一次检索的实际路径）：

    catalog    语料在册吗：这本书登记了没有、入库了没有（未收录的按时代列出）
    book       库里有这本书的正文吗（词表/索引之外的第一手事实）
    text       语料**文本**里有这个串吗（`instr` 地面真值，与检索无关）
    index      这个串在**索引**里查得到吗（按引擎实际选中的那条路径探）
    search     引擎**返回**了吗（hit_total）
    assembly   组装成块了吗（total）

前两层回答「该不该有」，中间两层回答「数据里有没有」，后两层回答「引擎给没给」。

分类（八维，与 6.2 §10 同一套口径，`classify_empty()` 是本模块唯一的判定实现）：
`Coverage` / `Search` / `Normalization` / `Ranking` / `Display` / `Pagination` /
`Passage` / `Provenance` / `UNKNOWN`。其中 `Coverage` 按计划书要求**细分两种原因**，
在 `class_detail` 里给出：

    not-imported  这本书还没入库（在册的 planned，或已下载未跑管线）
    not-in-text   已收录的书正文里确实没有这个串 —— 语料本身没有

**一件容易搞混的事**：`hit_total == 0` 不等于「搜不到」。篇名命中（match_type=
section，如「魏志卷三十」）会组装出块但一个正文段都不占 —— 用户界面上看得到结果。
所以「有没有搜不到的问题」的判据是 `total > 0`（有块可看），不是 hit_total。

代价：一次空结果归因要跑 1~3 次全表 `instr` 计数（每次本地实测 ~0.35s）。这是
**诊断**路径的代价，正常检索路径一行都不改（计划书 §16）。
"""
from __future__ import annotations

import re

from scripts.pipeline import catalog as catalog_mod
from search import engine, result_block, zh

_WS_RE = re.compile(r"\s+")

# 八维（与 tests/recall.py 的 DIMS 同名同义；那边已改为从这里导入）
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

# Coverage 的两种细分（class_detail）
NOT_IMPORTED = "not-imported"
NOT_IN_TEXT = "not-in-text"

LAYER_KEYS = ("catalog", "book", "text", "index", "search", "assembly")
LAYER_TITLES = {
    "catalog": "语料在册",
    "book": "库内正文",
    "text": "文本命中",
    "index": "索引可达",
    "search": "检索返回",
    "assembly": "块组装",
}

# 繁简探针：这几对在简繁下必然不同形。全对才算转换可用（与
# scripts/site/check_engine.py 的 preflight 卡的是同一件事）。
ZH_PROBE = [("齐", "齊"), ("郑", "鄭"), ("苏", "蘇"), ("张", "張"), ("晋", "晉")]


def zh_works() -> bool:
    """繁简转换是否真的在工作（而非非 Windows 下的恒等回退）。

    这是全局开关：一旦回退，整串查询不转换，简体输入会静默地全部搜不到。
    归因必须先问这一条 —— 转换回退时「繁体形 == 简体形」，根本分不出
    「语料没有」与「繁简没转」，硬塞进 Coverage 就是把故障伪装成缺书。
    """
    return all(zh.to_traditional(s) == t for s, t in ZH_PROBE)


def terms_of(trad_q: str) -> list:
    """检索词。engine.plan_query 就是按空白切的，这里保持一致。"""
    return [t for t in trad_q.split() if t]


def corpus_count(cur, terms, bid=None, edition=None) -> int:
    """语料里**同时**含全部检索词的正文段数 —— 「本该命中多少」的地面真值。

    必须用转换后的繁体词：库里的 normalized_text 是繁体，拿简体裸查会把
    「焚书」（0 段）误判成语料缺失，而实际库里有「焚書」6 段。

    `bid` / `edition` 传了就跟着过滤，让地面真值与**这次检索的范围**一致
    （带书过滤时，全库有而这本书没有，归因应当是 Coverage 而不是 Search）。
    """
    if not terms:
        return 0
    sql = ("SELECT COUNT(*) FROM passages p JOIN books b ON b.book_id = p.book_id"
           " WHERE p.kind='passage'"
           + "".join(" AND instr(p.normalized_text, ?) > 0" for _ in terms))
    args = list(terms)
    if bid:
        sql += " AND b.book_id = ?"
        args.append(bid)
    if edition:
        sql += " AND LOWER(b.family) = ?"
        args.append(edition)
    return cur.execute(sql, tuple(args)).fetchone()[0]


def classify_empty(cur, q_raw, n_corpus, zh_ok, bid=None, edition=None) -> str:
    """0 命中时归因到八维之一。判定顺序有讲究，见下面注释。

    这是**唯一**的分类实现：`tests/recall.py` 的 `_empty_class()` 与 API 的
    `/api/diagnose` 都走这里，避免两套说法。
    """
    if not zh_ok:
        # 转换回退时繁体形 == 简体形，无法区分「语料没有」与「繁简没转」。
        # 硬塞进 Coverage 会把繁简故障伪装成语料缺失，所以如实报 UNKNOWN。
        return UNKNOWN
    if n_corpus == 0:
        return COVERAGE                   # 原形与转换形都数不到 —— 语料确实没有
    # 转换后的检索词在库里有命中，那就要看是「哪一步」丢了结果：
    raw_terms = terms_of(q_raw)
    if corpus_count(cur, raw_terms, bid, edition) == 0:
        # 原形查不到、只有转换形才有 —— 繁简转换是承重环节，问题在这一步
        return NORMALIZATION
    return SEARCH                         # 原形本来就查得到，是引擎没返回


def _layer(key: str, ok, detail: str, **evidence) -> dict:
    out = {"key": key, "title": LAYER_TITLES[key], "ok": ok, "detail": detail}
    if evidence:
        out["evidence"] = evidence
    return out


def _load_catalog():
    """目录（语料骨架）。读不到就是 None —— 归因要能降级，不能因此搜不了。"""
    try:
        return catalog_mod.try_load()
    except Exception:                                         # noqa: BLE001
        return None


def _imported_dirs(cur) -> list[str]:
    return sorted(r[0] for r in cur.execute(
        "SELECT DISTINCT book_dir FROM books WHERE book_dir IS NOT NULL"))


def _frontier(cat, in_db: list[str]):
    """(未收录书名, 已下载未入库书名, 未收录按时代汇总的 dict)。

    时代顺序用目录文件里的声明顺序（＝编年顺序），不再另立一份硬编码年表；
    同一时代出现多次（书目不是严格按时代排的）按首次出现的位置合并，**不能按
    相邻段聚合** —— 否则会印出「南北朝 7 部、隋唐 1 部、南北朝 2 部」这种话。
    """
    if not cat:
        return [], [], {}
    in_db_set = set(in_db)
    on_disk = cat.dirs_on_disk()
    planned, pending, eras = [], [], {}
    for b in cat.books.values():
        if b.dir in in_db_set:
            continue
        if on_disk.get(b.dir):
            pending.append(b.title)
        else:
            planned.append(b.title)
            eras[b.era_group] = eras.get(b.era_group, 0) + 1
    return sorted(planned), sorted(pending), eras


def _era_text(eras: dict) -> str:
    return "、".join(f"{k} {n} 部" for k, n in eras.items())


def _edge_titles(cat, in_db: list[str], eras: dict) -> str:
    """缺口的两端各举一书：「最早的缺口是《宋書》，一直到《明史》」。

    只说时代不够具体，只列书名又看不出断在哪一代 —— 两端各一个正好。
    """
    if not cat or not eras:
        return ""
    in_db_set = set(in_db)
    first_era, last_era = next(iter(eras)), next(reversed(eras))
    first = [b.title for b in cat.books.values()
             if b.era_group == first_era and b.dir not in in_db_set]
    last = [b.title for b in cat.books.values()
            if b.era_group == last_era and b.dir not in in_db_set]
    if not first:
        return ""
    if first_era == last_era:
        return f"缺口都在 {first_era}"
    return (f"最早的缺口是《{first[0]}》，"
            f"一直到《{last[-1] if last else first[-1]}》")


def _titles_text(titles: list[str]) -> str:
    if len(titles) <= 6:
        return "、".join(f"《{t}》" for t in titles)
    return "、".join(f"《{t}》" for t in titles[:6]) + f" 等 {len(titles)} 部"


def _catalog_only_book(param: str):
    """`book=` 给的书在册但没入库 —— engine.resolve_book 认不出来，但这里能。

    这正是最该说清楚的一种空：「《舊唐書》里搜不到」不是这本书里没有，是这本书
    根本还没进来。
    """
    cat = _load_catalog()
    if not cat:
        return None
    key = _book_key(param)
    if not key:
        return None
    for b in cat.books.values():
        for c in (b.title, b.dir, b.book_id, zh.to_simplified(b.title)):
            if _book_key(c) == key:
                return b
    return None


def _book_key(text: str) -> str:
    """书名参数的归一化键：去空白、去书名号、转简体（与 resolve_book 同口径）。"""
    return zh.to_simplified(_WS_RE.sub("", text or "").strip().strip("《》"))


def _result(base: dict, klass, detail, summary, **extra) -> dict:
    """组装返回体。`class` 是 Python 关键字，不能写成 kwarg，只能这样塞。"""
    out = dict(base)
    out["class"] = klass
    out["class_detail"] = detail
    out["summary"] = summary
    out.update(extra)
    return out


def _fts_reachable(cur, q_trad: str, exec_mode: str, filtered: bool):
    """按引擎**实际选中**的那条路径，验证这个串在索引里查得到。

    只探引擎已经选中的那条，不另判一次路径 —— 路径选择是 engine.run_search 的
    职责，这里再判一遍就成了第二套说法。
    """
    if exec_mode == "like":
        return None, "引擎选中 LIKE 全扫路径（1 字词或索引缺席），无索引可探"
    table = "passages_fts" if exec_mode == "fts" else "passages_bg"
    long_terms, short_terms = engine.plan_query(q_trad)
    probe_terms = long_terms if exec_mode == "fts" else short_terms
    if not probe_terms:
        return None, f"{exec_mode} 路径没有可探的词"
    match = engine.fts_match_text(probe_terms)
    try:
        n = cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {table} MATCH ?",
                        (match,)).fetchone()[0]
    except Exception as e:                                    # noqa: BLE001
        return None, f"{table} 探针抛异常 {type(e).__name__}: {e}"
    scope = "（探针不按书过滤）" if filtered else ""
    return n > 0, f"{table} 命中 {n} 行{scope}（{match}）"


def diagnose(cur, q: str, book=None, edition=None, page: int = 1,
             page_size: int = 20, mode: str | None = None,
             text_mode: str = "orig", zh_ok: bool | None = None) -> dict:
    """一次搜不到（或搜得可疑）的完整归因。**只读**，不改任何检索行为。"""
    q = (q or "").strip()
    if not q:
        return {"q": q, "error": "请提供搜索关键词", "layers": [],
                "class": UNKNOWN, "class_detail": None, "summary": "查询为空。"}

    zh_ok = zh_works() if zh_ok is None else zh_ok
    q_trad = zh.to_traditional(q)
    terms = terms_of(q_trad)
    cat = _load_catalog()
    in_db = _imported_dirs(cur)
    planned, downloaded, eras = _frontier(cat, in_db)
    era_text = _era_text(eras)
    edge_text = _edge_titles(cat, in_db, eras)

    # 书过滤先解析。识别不了时，先当成「在册未入库」试一次 —— 那是最该讲清楚的
    # 一种空；确实不是目录里的书才把它当参数错误抛回给调用方。
    bid = None
    if book:
        try:
            bid = engine.resolve_book(cur, book)
        except ValueError:
            b = _catalog_only_book(book)
            if b is None:
                raise
            on_disk = "已下载未入库" if (cat and cat.dirs_on_disk().get(b.dir)) else "尚未下载"
            lay = [_layer("catalog", False,
                          f"《{b.title}》在目录里登记为 {b.dynasty}·{b.era_group}，"
                          f"但{on_disk} —— 它不在检索范围里",
                          book_dir=b.dir, era_group=b.era_group),
                   _layer("book", False, "库里没有这本书的正文",
                          book_title=b.title)]
            return _result(
                {"q": q, "q_traditional": q_trad, "terms": terms, "zh_ok": zh_ok,
                 "book": None, "book_title": b.title, "edition": None,
                 "layers": lay, "planned_books": planned,
                 "downloaded_not_imported": downloaded},
                COVERAGE, NOT_IMPORTED,
                f"《{b.title}》{on_disk}，还没进检索范围 —— "
                f"这不是「这本书里没有「{q_trad}」」，是这本书还搜不了。"
                + (f"已收录 {len(in_db)} 部，未收录 {len(planned)} 部。"
                   if in_db else ""),
                suggestions=["去掉 book 过滤，先在全库里搜一次看有没有。",
                             "把这本书加进语料（6.3-E 的导入流程）后再来搜。"])
    edi = engine.resolve_edition(edition) if edition else None

    # 先跑一次真实检索（后两层判据来自它，索引层要按它选中的路径探）
    res = result_block.search_result_blocks(
        cur, q, book=book, edition=edition, page=page, page_size=page_size,
        mode=mode or result_block.DEFAULT_MODE, text_mode=text_mode)
    hit_total, total = res["hit_total"], res["total"]
    exec_mode = res.get("exec_mode")

    # 地面真值
    n_scope = corpus_count(cur, terms, bid, edi)
    n_global = n_scope if (bid is None and edi is None) \
        else corpus_count(cur, terms)
    n_raw = corpus_count(cur, terms_of(q), bid, edi) if q_trad != q else n_scope

    # 1 语料在册 ------------------------------------------------------------
    n_reg = len(cat.books) if cat else 0
    layers = [_layer(
        "catalog", True,
        f"库里已入库 {len(in_db)} 部；目录在册 {n_reg or '?'} 部"
        + (f"，未收录 {len(planned)} 部：{era_text}" if planned
           else "，无未收录"),
        imported=len(in_db), planned_titles=planned,
        planned_count=len(planned), planned_by_era=eras,
        downloaded_not_imported=downloaded)]

    # 2 库内正文 ------------------------------------------------------------
    if bid:
        row = cur.execute("SELECT title FROM books WHERE book_id=?",
                          (bid,)).fetchone()
        n_body = cur.execute(
            "SELECT COUNT(*) FROM passages WHERE kind='passage' AND book_id=?"
            " AND normalized_text IS NOT NULL AND normalized_text <> ''",
            (bid,)).fetchone()[0]
        layers.append(_layer("book", n_body > 0,
                             f"《{row[0] if row else bid}》可检索正文 {n_body} 段",
                             book_id=bid, passages=n_body))
    else:
        n_body = cur.execute(
            "SELECT COUNT(*) FROM passages WHERE kind='passage'"
            " AND normalized_text IS NOT NULL AND normalized_text <> ''").fetchone()[0]
        layers.append(_layer("book", n_body > 0,
                             f"全部书可检索正文 {n_body} 段", passages=n_body))

    # 3 文本命中（与检索无关的地面真值）--------------------------------------
    text_detail = (f"语料文本里「{q_trad}」有 {n_scope} 段" if n_scope
                   else f"语料文本里没有「{q_trad}」")
    if n_scope and n_scope != n_global:
        text_detail += f"（全库 {n_global} 段）"
    if q_trad != q and n_raw > n_scope:
        text_detail += f"；注意按原样「{q}」另有 {n_raw} 段"
    layers.append(_layer("text", n_scope > 0, text_detail,
                         terms=terms, scope=n_scope, global_=n_global,
                         raw_form=n_raw if q_trad != q else None))

    # 4 索引可达 ------------------------------------------------------------
    idx_ok, idx_detail = _fts_reachable(cur, q_trad, exec_mode or "like",
                                        filtered=bool(bid or edi))
    layers.append(_layer("index", idx_ok, idx_detail, exec_mode=exec_mode))

    # 5 检索返回 ------------------------------------------------------------
    layers.append(_layer("search", hit_total > 0,
                         f"引擎（{exec_mode} 路径）返回 {hit_total} 段命中",
                         hit_total=hit_total, truncated=res.get("truncated")))

    # 6 块组装 --------------------------------------------------------------
    layers.append(_layer("assembly", total > 0,
                         f"组装出 {total} 个结果块"
                         + ("（篇名命中不占 hit_total）"
                            if total > 0 and hit_total == 0 else ""),
                         total=total, section_truncated=res.get("section_truncated")))

    base = {"q": q, "q_traditional": q_trad, "terms": terms, "zh_ok": zh_ok,
            "book": bid, "edition": edi, "layers": layers,
            "planned_books": planned, "downloaded_not_imported": downloaded}

    # ---- 有块可看：不是「搜不到」-------------------------------------------
    if total > 0:
        if hit_total > 0:
            summary = (f"正常命中：{hit_total} 段 / {total} 块"
                       f"（{exec_mode} 路径）—— 没有搜不到的问题。")
        else:
            summary = (f"正文命中 0 段，但篇名检索命中 {total} 个块 —— "
                       f"界面上看得到结果（篇名不占 hit_total），不是搜不到。")
        return _result(base, None, None, summary)

    # ---- hit_total > 0 却组装不出块 = Display ------------------------------
    if hit_total > 0:
        return _result(
            base, DISPLAY, None,
            f"命中 {hit_total} 段却一块都组装不出来 —— 这是展示层（Display）"
            f"的问题，不是语料问题。",
            suggestions=["看 result_block.search_result_blocks 的组装逻辑。"])

    # ---- 0 命中：归因 -----------------------------------------------------
    klass = classify_empty(cur, q, n_scope, zh_ok, bid, edi)
    detail = None
    if klass == COVERAGE:
        detail = NOT_IN_TEXT if n_body > 0 else NOT_IMPORTED
    summary, tips = _explain(klass, detail, {
        "q": q, "q_trad": q_trad, "n_scope": n_scope, "n_global": n_global,
        "n_raw": n_raw, "planned": planned, "downloaded": downloaded,
        "era_text": era_text, "edge_text": edge_text, "in_db": in_db})
    return _result(base, klass, detail, summary, suggestions=tips)


def _explain(klass, detail, ctx: dict):
    """把归因说成人话：一句话摘要 + 若干可行的下一步。"""
    q, q_trad = ctx["q"], ctx["q_trad"]
    n_scope, n_raw = ctx["n_scope"], ctx["n_raw"]
    planned, downloaded = ctx["planned"], ctx["downloaded"]
    tips: list[str] = []
    if klass == UNKNOWN:
        return ("繁简转换当前不可用（非 Windows 或系统映射失败），"
                "无法区分「语料没有」与「转换没生效」—— 先修转换。"), tips

    if klass == COVERAGE:
        if detail == NOT_IMPORTED:
            head = "包含该词的书还没有入库，不在检索范围里。"
        else:
            head = (f"语料里没有「{q_trad}」（已收录的 {len(ctx['in_db'])} 部史书"
                    f"共 0 段）—— 已收录的正文里确实没有这个串。")
        if planned:
            edge = f"；{ctx['edge_text']}" if ctx["edge_text"] else ""
            head += (f"另外，尚未收录的正史还有 {len(planned)} 部（"
                     f"{ctx['era_text']}{edge}）—— 若该词属于这些书"
                     f"的范围，搜不到是覆盖范围问题，不是检索问题。")
        if downloaded:
            tips.append(f"已下载但尚未入库：{_titles_text(downloaded)}"
                        "（跑一次管线即可入库）")
        tips.append("换个写法试试：异体字/避讳/称谓差异都可能让同一个人名在史料里"
                    "写作另一种形式（例：司馬懿在晉書里多称「宣帝」）。")
        tips.append("拆开搜：先搜其中一个词，确认语料里有没有相关的记载。")
        if n_raw > n_scope and q_trad != q:
            tips.append(f"注意：语料里按原样写的「{q}」有 {n_raw} 段，"
                        f"而查询被繁简转换成了「{q_trad}」（{n_scope} 段）—— "
                        f"转换后的写法在语料里是少数派。")
        return head, tips

    if klass == NORMALIZATION:
        return (f"繁简环节丢了结果：转换形「{q_trad}」在语料里有 {n_scope} 段，"
                f"而原样「{q}」一段都数不到 —— 检索只按转换后的词查，"
                f"问题在这一步。"), ["确认系统繁简映射是否正常"
                                     "（python -m scripts.site.check_engine）"]

    if klass == SEARCH:
        return (f"引擎问题：语料文本里「{q_trad}」确实有 {n_scope} 段"
                f"（全库 {ctx['n_global']} 段），检索却返回 0 —— "
                f"原形本来就查得到，是检索没给出来。"), [
            "看执行路径：1 字词走 LIKE 全扫，>=3 字走 trigram，纯 2 字走 bigram。",
            "确认索引在场：/api/stats 的 fts.ok / fts.bigram。",
        ]

    return f"空的归因是 {klass} —— 不是语料问题，请看对应维度的报告。", tips
