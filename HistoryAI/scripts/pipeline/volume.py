"""卷级覆盖（Volume Coverage）—— 第六点四阶段的**唯一**卷数实现。

## 它回答的问题

「这本书我们到底收了哪几卷、还差哪几卷」。没有它，搜索系统只能说「正文里没有」，
而有 5 部正史上游本身就只数字化到某一卷为止 —— 那时「正文里没有」会被读成
「史书里没有」。这是本模块存在的全部理由（计划书 §4、§13、§28）。

## 为什么不能只数文件、也不能只数 juans

第六点三阶段踩过两次，教训写在这里免得重犯（详见 `docs/phase6_3_upstream_gaps.md`）：

- **文件数 ≠ 卷数**：`_000.txt` 是卷首附錄（目録/序/御製詩），考證跋語文件也不是卷。
  北齊書 36 个文件是 35 卷，後漢書 125 个文件是 120 卷。
- **juans 表 ≠ 卷数**：北齊書的卷题同时出现在目録文件与正文文件里，`juans` 有 94 条；
  反过来魏書 卷一百五在源文件里被写成「之一…之四」（卷题带书名前缀），
  卷题正则认不出，`juans` 里**一条都没有**。

所以本模块取**两路独立证据的并集**，再与人工登记的卷数对账：

    证据 A（文件头）  files.juan_prop   —— `#+PROPERTY: JUAN` 原值，如 `卷三十四`
    证据 B（正文卷题）juans.label       —— 正文里出现的卷题行，如 `晉書卷七`

两路各有所长：晉書 卷七的正文在 `_006.txt` 里（文件头写的是卷六），只有证据 B 认得出；
魏書 `_105.txt` 的文件头写的是「前上十志啓」，只有证据 A 认得出别的卷。

## 三种 witness

    corpus    语料里有卷级证据，机器能数（14 部 WYG 正史）
    declared  语料里**没有**卷级证据（tls/sbck 底本以篇为单位，`JUAN` 属性是文件序号），
              卷数由人在语料目录里登记，页面必须写明「人工登记，不是机器数出来的」
    none      这本书没有卷的概念（`expected_volumes` 为 null），卷级覆盖不适用

## 判据（`audit()`）

`declared_volumes` 是**上游实际数字化到哪一卷**（人登记，来自上游目次 + 文件集核查），
`expected_volumes` 是通行本卷数。两者的比就是覆盖率。机器负责证明「登记的数与语料
对得上」，对不上就报 problem —— 登记一个数而不核，等于没登记：

    E1 声明的卷数与实测差一截（且差额没被 volume_notes 逐卷说明）  error
    E2 实测卷号超出声明范围（收进来的比声明的还多）                 error
    E3 同一个卷号被两个文件**归属**（文件头都是它）——真重复入库      error
    E4 [1, declared] 里有卷号缺失（中间缺卷）                       error
    E5 正文文件里既没有卷题、也不在任何一卷的声明范围内             warn
    E6 声明卷数 > 通行本卷数（多收了不存在的卷）                    error

E1/E4 允许用 `volume_notes` 逐卷豁免：**人能解释的缺口不算故障，人解释不了才算**。
魏書 卷一百五就是这样 —— 源文件把卷题拆成「之一…之四」并写进了文件头不认识的形态，
登记一条说明即可，不改源、也不假装它不存在。

还没有入库的书走 `audit_planned()`：只有登记、没有实测，`status` 固定 `planned`，
并附一条 info 说明卷数是人工登记的 —— 那个数带着说明一起走，才不会被读成
「我们已经有 75 卷」。

## 输出里的三个数（页面上的「已收 / 应有」就是它们）

    expected     通行本卷数（分母）
    declared     这部底本/来源有多少卷（人工登记；已入库时经实测核对）
    available    库内实测卷数（witness=none 或未入库时为 None —— **没有就是没有**）

`coverage_ratio` = available/expected（我们手上有多少），`declared_ratio` =
declared/expected（这部底本本身有多少）。两者不等只可能是「底本残缺」——
那时页面必须写明缺的是哪几卷（`gaps`），不能只说一个百分比。

零写入：本模块只读库与磁盘，不碰 `HistoryLibrary/`。
"""
from __future__ import annotations

import re

# 正文层的判定：与 manifest.py / api/db.py 同一口径（layer 为空的老数据或 main）。
BODY_LAYER_SQL = "(p.layer IS NULL OR p.layer = 'main')"

# 卷首附錄的文件号。Kanripo 的 `_000.txt` 固定是目録/序/御製詩/提要，不是卷。
# **必须按文件号判，不能按 layer 判**：北齊書/隋書/北史的目録被解析成了 main 层
# （实测 197/314/644 条 main passage），拿 layer 筛会把 50/85/100 卷的目次
# 当成已收卷数——三本书的「上游残缺」结论会当场变成「完整」。
FRONT_MATTER_FILE_NO = 0

# 汉字数字与阿拉伯数字都要认：明史的文件头 JUAN 实测是阿拉伯数字（`JUAN 1`）。
_CN = "零一二三四五六七八九"
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_NUM = rf"[{_CN}十百千]+|\d+"

# `魏書卷一百五之一` / `晉書卷七` / `卷三十四` / `前漢書卷一上` / `魏志卷一`
# 前缀（书名/志名）单独成组、不参与判卷；`之一` 与 `上/下` 是**卷内分片**，
# 同一个卷号的两个文件不是两卷（前漢書 卷一上/卷一下 = 卷一）。
VOLUME_RE = re.compile(
    rf"^(?P<prefix>[^卷巻]*)[卷巻](?P<num>{_NUM})"
    rf"(?:之(?P<sub>{_NUM}))?(?P<part>[上下])?$")


def cn_to_int(s: str) -> int | None:
    """`一百五` → 105、`七` → 7、`12` → 12；认不出返回 None。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total = cur = 0
    for ch in s:
        if ch in _CN_UNITS:
            u = _CN_UNITS[ch]
            if cur == 0:
                cur = 1
            total += cur * u
            cur = 0
        elif ch in _CN:
            cur = _CN.index(ch)
        else:
            return None
    return total + cur


def parse_volume_label(text: str | None) -> tuple[int, str] | None:
    """卷题 → `(卷号, 卷内分片)`；不是卷题返回 None。

    分片是 `''` / `'上'` / `'下'` / `'之一'` / `'之二'` —— 同一个卷号允许有多个
    分片文件。认不出的（`卷七考證`、`前上十志啓`、`目錄`、`0`、`卷一百八` 之外
    的任何写法）一律返回 None，**不猜**：猜错的代价是把缺口判成完整。
    """
    m = VOLUME_RE.match((text or "").strip())
    if not m:
        return None
    n = cn_to_int(m.group("num"))
    if n is None:
        return None
    sub = m.group("sub") or ""
    part = m.group("part") or ""
    if sub:
        return n, f"之{sub}"
    return n, part


# ------------------------------------------------------------------ 测量

def _body_files(cur, book_id: str) -> set[int]:
    """正文文件：**非**卷首附錄、且有正文层 passage 的文件。"""
    return {r[0] for r in cur.execute(
        "SELECT DISTINCT p.file_id FROM passages p WHERE p.book_id=? "
        f"AND p.kind='passage' AND {BODY_LAYER_SQL}", (book_id,))}


def measure(cur, book_id: str) -> dict:
    """两路证据的并集。**只读**，不改任何检索行为。

    返回（全部是**事实**，判据在 `audit()` 里）：

        numbers      实测卷号（升序）
        count        实测卷数
        span         [最小卷号, 最大卷号]
        gaps         span 内缺失的卷号（中间缺卷）
        splits       {卷号: [分片…]}——一个卷号有多个文件时才出现
        true_dups    同一 (卷号, 分片) 被两个文件**归属**的卷号（真重复入库）
        sources      {"header": n, "juan": n} 两路各认出多少个卷号
        by_number    {卷号: [{"source": "header"|"juan", "file_no": n, "raw": 原值}…]}
        owners       {卷号: [承载该卷的文件号…]}——归属看文件头，见下
        unwitnessed 正文文件里一条卷级证据都没有的（文件号升序）
        non_volume_headers  文件头不是卷题的原值（文件号, 原值）—— 供人工核对
    """
    body = _body_files(cur, book_id)
    by_number: dict[int, list[dict]] = {}
    sources = {"header": 0, "juan": 0}
    non_volume_headers: list[tuple[int, str]] = []
    header_files: set[int] = set()

    rows = [dict(r) for r in cur.execute(
        "SELECT file_id, file_no, juan_prop FROM files WHERE book_id=? ORDER BY file_no",
        (book_id,))]
    for r in rows:
        if r["file_no"] == FRONT_MATTER_FILE_NO or r["file_id"] not in body:
            continue
        raw = (r["juan_prop"] or "").strip()
        got = parse_volume_label(raw)
        if got is None:
            non_volume_headers.append((r["file_no"], raw))
            continue
        header_files.add(r["file_id"])
        n, part = got
        by_number.setdefault(n, []).append(
            {"source": "header", "file_no": r["file_no"], "part": part, "raw": raw})

    juan_files: set[int] = set()
    for r in [dict(x) for x in cur.execute(
            "SELECT j.label, f.file_id, f.file_no FROM juans j "
            "JOIN files f ON f.file_id = j.file_id WHERE j.book_id=? ORDER BY f.file_no",
            (book_id,))]:
        if r["file_no"] == FRONT_MATTER_FILE_NO or r["file_id"] not in body:
            continue
        got = parse_volume_label(r["label"])
        if got is None:
            continue
        juan_files.add(r["file_id"])
        n, part = got
        by_number.setdefault(n, []).append(
            {"source": "juan", "file_no": r["file_no"], "part": part,
             "raw": r["label"]})

    for n, evs in by_number.items():
        kinds = {e["source"] for e in evs}
        for k in kinds:
            sources[k] += 1

    numbers = sorted(by_number)
    span = [numbers[0], numbers[-1]] if numbers else None
    gaps = ([x for x in range(numbers[0], numbers[-1] + 1) if x not in by_number]
            if numbers else [])
    # 分片：同一个卷号有多个**不同**分片（前漢書 卷一上/卷一下、魏書 卷一百五之一…）。
    # 判据只看分片名，不看证据条数 —— 同一卷被文件头和正文卷题各认了一次，那是
    # 两路证据互相印证，不是两个分片。
    splits = {n: sorted({e["part"] for e in evs if e["part"]})
              for n, evs in by_number.items()
              if len({e["part"] for e in evs if e["part"]}) > 1}
    # 卷「归属」：这一卷是哪个文件的卷。**按文件头判**，不按正文卷题判 ——
    # 卷题可能只是别人在引述它：北齊書 `_018.txt` 是卷十八 + 卷十五~十八的考證，
    # 三國志 `_030.txt` 同理（考證里逐卷回引卷题）。按卷题判会把 5 部书全判成
    # 「重复入库」，而它们只是 WYG 把考證攒到一卷末尾。
    # 文件头认不出的卷号（晉書 卷七：正文在 `_006.txt` 里、`_007.txt` 只有考證）
    # 才退回用卷题判归属。分片（上/下、之一/之二）各占一个文件是正常拆分。
    owners: dict[int, dict[str, set[int]]] = {}
    for n, evs in by_number.items():
        hdr = [e for e in evs if e["source"] == "header"]
        for e in (hdr or evs):
            owners.setdefault(n, {}).setdefault(e["part"], set()).add(e["file_no"])
    true_dups = sorted(
        n for n, parts in owners.items() if any(len(fs) > 1 for fs in parts.values()))
    unwitnessed = sorted(
        r["file_no"] for r in rows
        if r["file_no"] != FRONT_MATTER_FILE_NO and r["file_id"] in body
        and r["file_id"] not in header_files and r["file_id"] not in juan_files)

    return {
        "book_id": book_id,
        "numbers": numbers,
        "count": len(numbers),
        "span": span,
        "gaps": gaps,
        "splits": splits,
        "true_dups": true_dups,
        "sources": sources,
        "by_number": by_number,
        "owners": {n: sorted({f for fs in parts.values() for f in fs})
                   for n, parts in owners.items()},
        "unwitnessed": unwitnessed,
        "non_volume_headers": non_volume_headers,
        "body_files": len(body),
    }


# ------------------------------------------------------------------ 判据

WITNESSES = ("corpus", "declared", "none")
VOLUME_STATUS = ("complete", "partial", "unknown")


def _problem(code: str, level: str, detail: str) -> dict:
    return {"code": code, "level": level, "detail": detail}


def audit(m: dict, expected: int | None, declared: int | None,
          witness: str, volume_notes: dict | None = None) -> dict:
    """实测 × 登记 → 每本书一个卷级结论。**纯函数**，不查库。

    `volume_notes` 是 {卷号(字符串): 说明}：人能解释的缺口不算故障。
    """
    notes = {int(k): v for k, v in (volume_notes or {}).items()}
    problems: list[dict] = []

    if witness == "none" or expected is None:
        return {
            "witness": witness, "expected": expected, "declared": declared,
            "available": None, "coverage_ratio": None, "declared_ratio": None,
            "status": "unknown",
            "numbers": m["numbers"], "span": m["span"], "gaps": m["gaps"],
            "splits": m["splits"], "true_dups": m["true_dups"],
            "sources": m["sources"], "unwitnessed": m["unwitnessed"],
            "explained_gaps": [], "problems": problems,
            "note": ("该书没有卷级模型（tls/sbck 底本以篇为单位，文件头 JUAN 属性是"
                     "文件序号不是卷次），卷级覆盖不适用" if witness == "none"
                     else "登记里没有 expected_volumes，卷级覆盖无从判定"),
        }

    if witness == "declared":
        # 语料里没有卷级证据：登记的数就是结论，但必须**标明是登记的**。
        problems.append(_problem(
            "no-witness", "info",
            "语料里没有卷级证据，卷数是人工登记的，机器核对不了"))
        status = ("complete" if declared == expected
                  else "partial" if (declared or 0) < expected else "unknown")
        return {
            "witness": witness, "expected": expected, "declared": declared,
            "available": declared, "coverage_ratio": _ratio(declared, expected),
            "declared_ratio": _ratio(declared, expected),
            "status": status, "numbers": m["numbers"], "span": m["span"],
            "gaps": m["gaps"], "splits": m["splits"], "true_dups": m["true_dups"],
            "sources": m["sources"], "unwitnessed": m["unwitnessed"],
            "explained_gaps": [], "problems": problems,
            "note": "卷数由人工登记（该底本无卷题可机械核对）",
        }

    # witness == "corpus"
    if declared is None:
        problems.append(_problem(
            "no-declared", "error",
            "声明有卷级证据（volume_witness=corpus）却登记了 declared_volumes=null"))
        declared = 0

    measured = m["count"]
    gaps_in_declared = [x for x in m["gaps"] if x <= declared]
    explained = [x for x in gaps_in_declared if x in notes]
    unexplained = [x for x in gaps_in_declared if x not in notes]

    if m["span"] and m["span"][1] > declared:
        problems.append(_problem(
            "out-of-range", "error",
            f"实测卷号到 卷{m['span'][1]}，超出声明的 {declared} 卷："
            f"{[x for x in m['numbers'] if x > declared][:8]}"))
    if measured + len(explained) != declared:
        problems.append(_problem(
            "count-mismatch", "error",
            f"声明 {declared} 卷，实测 {measured} 卷"
            + (f"，另有 {len(explained)} 卷有说明（{explained}）" if explained else "")
            + f" —— 差 {declared - measured - len(explained)} 卷"))
    if unexplained:
        problems.append(_problem(
            "gap-unexplained", "error",
            f"[1, {declared}] 里缺 {len(unexplained)} 卷没有任何说明：{unexplained[:12]}"
            " —— 要么补进 volume_notes 说明为什么，要么就是真的漏了"))
    if m["true_dups"]:
        problems.append(_problem(
            "true-duplicate", "error",
            f"同一卷号被两个文件同时归为己有（重复入库）：{m['true_dups'][:8]}"))
    if declared > expected:
        problems.append(_problem(
            "over-expected", "error",
            f"声明 {declared} 卷 > 通行本 {expected} 卷 —— 多收了不存在的卷"))

    # 正文文件没有卷级证据：只在它**没有**被缺口说明覆盖时才提（魏書 _105 就是
    # 「卷题认不出」→ 同时是缺口、也是无证据文件，一条 volume_notes 两者都解决）。
    stray = [f for f in m["unwitnessed"] if f not in notes and f not in m["gaps"]]
    if stray:
        problems.append(_problem(
            "unwitnessed-file", "warn",
            f"这些正文文件既没有卷题、也不在任何一卷的证据里：{stray[:8]}"
            " —— 机器说不出它们是哪一卷，要人看一眼"))

    status = ("complete" if declared == expected
              else "partial" if declared < expected else "unknown")
    return {
        "witness": witness, "expected": expected, "declared": declared,
        "available": measured, "coverage_ratio": _ratio(measured, expected),
        "declared_ratio": _ratio(declared, expected),
        "status": status, "numbers": m["numbers"], "span": m["span"],
        "gaps": m["gaps"], "splits": m["splits"], "true_dups": m["true_dups"],
        "sources": m["sources"], "unwitnessed": m["unwitnessed"],
        "non_volume_headers": m["non_volume_headers"][:8],
        "explained_gaps": explained, "problems": problems,
        "note": (f"实测 {measured} 卷（文件头 {m['sources']['header']} / "
                 f"正文卷题 {m['sources']['juan']}），登记 {declared} 卷"),
    }


def audit_planned(book_id: str, expected: int | None, declared: int | None,
                  volume_note: str = "") -> dict:
    """**未入库**的书：只有登记，没有语料 —— 不许冒充实测。

    输出与 `audit()` 同形，但 `numbers`/`gaps` 一律为空（没量过就是没量过），
    `status` 固定 `planned`，并附一条 info 级 problem 说明「卷数是人工登记的、
    机器核对不了」—— 这句话必须跟着数字一起走，否则页面上的 75/225 会被读成
    「我们已经有 75 卷」。
    """
    ratio = _ratio(declared, expected)
    problems = [_problem(
        "not-imported", "info",
        "这本书还没入库：卷数来自上游 Readme 目次与文件集的逐条核对，"
        "是人工登记的，机器核对不了")]
    if declared is not None and expected is not None and declared < expected:
        problems.append(_problem(
            "upstream-partial", "warn",
            f"上游数字化只到 {declared} 卷（通行本 {expected} 卷）—— "
            f"将来导入也是残缺版本，缺 {expected - declared} 卷"))
    note = volume_note or ("未导入，卷数按上游登记" if declared is not None
                           else "未导入，也没有登记卷数")
    return {
        # witness 记 "declared" 而不是 None：这本书的卷数**只有一个来源**——登记。
        # 写 None 会让下游把「没导入」和「没有卷级模型」混成一种，而它们下一步的
        # 处置完全相反（前者等导入，后者永远不用导入）。
        "book_id": book_id, "witness": "declared", "expected": expected,
        "declared": declared, "available": None, "coverage_ratio": None,
        "declared_ratio": ratio, "status": "planned",
        "numbers": [], "span": None, "gaps": [], "splits": {}, "true_dups": [],
        "sources": {"header": 0, "juan": 0}, "unwitnessed": [],
        "explained_gaps": [], "problems": problems, "note": note,
    }


def _ratio(declared: int | None, expected: int | None) -> float | None:
    if not expected or declared is None:
        return None
    return round(declared / expected, 4)


def worst_level(problems: list[dict]) -> str | None:
    for lv in ("error", "warn", "info"):
        if any(p["level"] == lv for p in problems):
            return lv
    return None


# ---------------------------------------------------------------- 快照读取
# manifest 把卷级结论写进 data/metadata/volume_coverage.json；页面（api/main.py）
# 与同义词诊断（search/diagnose.py）都从这里读，**只有一份口径**。
# 规矩：读不到 / 读坏了 / 这本书不在快照里 —— 一律回 "unknown"，
# **永远不许猜 "complete"**。宁可说「不知道全不全」，不能暗示「是全的」。

SNAPSHOT_NAME = "volume_coverage.json"


def snapshot_path():
    from scripts.pipeline import config
    return config.METADATA_DIR / SNAPSHOT_NAME


def read_snapshot() -> dict:
    """→ {"books": {book_id: row}, "planned": {title: row}, "totals": …, "
    "generated_at": …}；文件不在或坏掉时 available=False。

    坏掉的快照**不静默吞**：available=False + reason 带着走，调用方有义务把
    「卷级信息不可用」显示出来，而不是让页面看起来一切正常。
    """
    import json
    p = snapshot_path()
    if not p.exists():
        return {"available": False, "reason": f"没有 {SNAPSHOT_NAME}"
                "（跑一次 python -m scripts.pipeline.manifest）",
                "books": {}, "planned": {}, "totals": {}, "generated_at": None}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"available": False, "reason": f"{SNAPSHOT_NAME} 读不了：{e}",
                "books": {}, "planned": {}, "totals": {}, "generated_at": None}
    books = {b["book_id"]: b for b in raw.get("books", []) if b.get("book_id")}
    planned = {b.get("title"): b for b in raw.get("planned", []) if b.get("title")}
    return {"available": True, "reason": None, "books": books,
            "planned": planned, "totals": raw.get("totals") or {},
            "generated_at": raw.get("generated_at")}


def scope_of(snap: dict, book_id: str | None = None, title: str | None = None) -> dict:
    """页面与诊断要的那几个数：这部书**在卷这个维度上**到底是什么状态。

    → {"status", "expected", "declared", "available", "coverage_ratio",
       "missing", "edition", "title", "known": bool}

    `known=False` 表示我们**说得出这部书收了多少卷**；False 时所有数字为 None，
    调用方只能照实说「不清楚全不全」。三种情形回 False：快照整个不可用、
    这本书不在快照里（新导入还没重建 manifest）、这本书没有卷级模型（先秦四书、
    史記的 tls 底本）—— 第三种 `status` 回 "unknown"，不是 "partial"。
    """
    empty = {"status": "unknown", "expected": None, "declared": None,
             "available": None, "coverage_ratio": None, "declared_ratio": None,
             "missing": [], "expected_missing": [], "witness": None, "note": None,
             "edition": None, "title": title, "known": False}
    if not snap.get("available"):
        return dict(empty, reason=snap.get("reason"))
    row = None
    if book_id:
        row = snap["books"].get(book_id)
    if row is None and title:
        row = snap["planned"].get(title) or next(
            (b for b in snap["books"].values() if b.get("title") == title), None)
    if row is None:
        return dict(empty, reason="卷级快照里没有这本书（快照可能过期，"
                                  "跑一次 manifest 重建）")
    return {
        "status": row.get("status"),
        "expected": row.get("expected"),
        "declared": row.get("declared"),
        "available": row.get("available"),
        "coverage_ratio": row.get("coverage_ratio"),
        "declared_ratio": row.get("declared_ratio"),
        # 缺的卷号：以 declared 为界（底本本身的缺口），不是以 expected 为界 ——
        # expected 缺的那些卷下游还没数字化，逐个列出来会淹没真正的信息。
        "missing": [n for n in (row.get("gaps") or [])],
        "expected_missing": _expected_missing(row),
        "edition": row.get("edition"),
        "witness": row.get("witness"),
        "note": row.get("note"),
        "title": row.get("title") or title,
        "known": True,
    }


def _expected_missing(row: dict) -> list[int]:
    """通行本有、库里没有的卷号。只在 witness=corpus（真数过）时给。

    未入库的书（planned）`numbers` 是空的，那不代表「一卷都没有」，
    只代表「我们数不了」—— 列出来就是造谣。
    """
    exp, nums = row.get("expected"), row.get("numbers")
    if not exp or row.get("witness") != "corpus" or nums is None:
        return []
    have = set(nums)
    return [n for n in range(1, exp + 1) if n not in have]
