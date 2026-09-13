"""语料目录加载器（第六点三阶段 6.3-A/B）。

一个数据文件（`corpus_catalog.json`）+ 一个加载器。**语料骨架与解析规则的唯一
人工维护处**：加一本书、给某书换一套标题模式、给某文件改层，都在 JSON 里做。

为什么要有它：扩容前「有哪些书」这件事只存在于磁盘（`HistoryLibrary/kanripo/`
下扫到什么算什么），于是「书没入库」和「书没登记」分不开——`manifest` 只能报
已入库的书，前端 `#/coverage` 也无从显示「尚未收录」。目录把**意图**（应该
有什么）与**事实**（库里有什么）分开记，两者一比就是覆盖率。

校验（`load()` 一次做完，写错立刻报，不等到跑管线）：

- 顶层结构、每书必填字段、未知字段（防拼写错误静默生效）；
- 书的键 = kanripo ID，且与 `kanripo_id` 字段、`dir`（磁盘目录名）三处对账
  —— 复制粘贴一条改一半是这类数据文件最常见的错；
- `title_patterns` / `families[].title_patterns` 引用的模式名必须在
  `title_patterns.PATTERNS` 里已定义（只按名引用，正则不放 JSON，理由见该模块）；
- `file_overrides` 的层名/状态名必须在 `records.LAYER_VALUES` / `STATUS_VALUES` 里
  —— 否则会往库里写进一个下游认不出的层。

回滚路径：`try_load()` 在文件不存在时返回 None，调用方回落到 6.2 的既有行为
（`structure.file_layer_defaults` 的内置 overrides 等）。

CLI：`python -m scripts.pipeline.catalog --check`（校验并打印统计，CI 可用）。
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config
from .records import LAYER_VALUES, STATUS_VALUES
from .title_patterns import PATTERNS, TitlePattern

CATALOG_PATH = config.PIPELINE_DIR / "corpus_catalog.json"
SCHEMA_VERSION = 1

FAMILIES = ("tls", "sbck", "wyg")
CATEGORIES = ("先秦文獻", "正史")
H2_ROLES = ("division", "section", "juan")
AB_SYSTEMS = ("zuozhuan",)
TITLE_HANDLERS = ("zhanguoce",)
# 层默认规则：命中条件（`when`）与「层名由函数算」的具名解析器。
# 解析器本体在 structure.py（它才有 part_layer_of），这里只登记名字——与
# title_patterns 引用 PATTERNS 里的模式名同理，名字是接口，实现在代码里。
LAYER_WHEN = ("always", "file_zero", "juan_part")
LAYER_RESOLVERS = ("part_layer_of",)

KR_ID_RE = re.compile(r"^KR[0-9a-z]{6}$")
DIR_RE = re.compile(r"^[a-z][a-z0-9_]*$")

REQUIRED_BOOK_KEYS = ("dir", "title", "dynasty", "era_group", "category",
                      "kanripo_id", "family_expected", "repo", "branch",
                      "recall_verified")
OPTIONAL_BOOK_KEYS = ("h2_role", "ab_system", "title_handler", "title_patterns",
                      "juan_prefixes", "juan_as_section", "file_overrides", "note")
TOP_KEYS = ("version", "note", "books", "families", "pattern_note", "layer_note")
FAMILY_KEYS = ("title_patterns", "layer_defaults", "note")
LAYER_RULE_KEYS = ("when", "layer", "resolver", "status", "note")


class CatalogError(ValueError):
    """目录文件本身写错了（不是语料的问题）。"""


@dataclass(frozen=True)
class Book:
    """目录里的一本书（书目骨架 + 解析规则选择；不含正文）。"""
    book_id: str
    dir: str
    title: str
    dynasty: str
    era_group: str
    category: str
    family_expected: str
    repo: str
    branch: str
    recall_verified: bool
    h2_role: str | None = None
    ab_system: str | None = None
    title_handler: str | None = None
    title_patterns: tuple[str, ...] = ()
    juan_prefixes: tuple[str, ...] = ()
    juan_as_section: bool = False
    file_overrides: dict = field(default_factory=dict)
    note: str = ""

    def override_for(self, file_no: int | None) -> dict | None:
        """该文件的层/状态特例（键是文件号的字符串形式），没有则 None。"""
        if file_no is None:
            return None
        return self.file_overrides.get(str(file_no))


@dataclass(frozen=True)
class Catalog:
    version: int
    books: dict[str, Book]
    families: dict[str, dict]
    path: Path
    by_dir_map: dict[str, Book] = field(default_factory=dict)

    # ---- 查询 ----
    def book(self, book_id: str) -> Book | None:
        return self.books.get(book_id)

    def by_dir(self, book_dir: str) -> Book | None:
        """按磁盘目录名找书（segmentation / structure 拿到的是 book_dir）。"""
        return self.by_dir_map.get(book_dir)

    def titles(self) -> list[str]:
        return [b.title for b in self.books.values()]

    def patterns_for(self, book: Book) -> tuple[TitlePattern, ...]:
        """本书要试的具名模式：家族默认 + 本书声明（去重、保序）。

        家族默认来自 `families[family_expected].title_patterns`；某书在
        `title_patterns` 里声明过就用**它自己那一份**——声明是**替换**不是追加，
        所以既能给某本书加模式（本书有家族默认里没有的形态），也能把家族默认里
        对本书误伤的模式摘掉（同名书形态异常时）。
        """
        fam = self.families.get(book.family_expected, {})
        names = list(book.title_patterns) or list(fam.get("title_patterns") or ())
        seen, out = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(PATTERNS[n])
        return tuple(out)

    def file_override(self, book_dir: str, file_no: int | None) -> dict | None:
        b = self.by_dir(book_dir)
        return b.override_for(file_no) if b else None

    def layer_defaults(self, family: str | None) -> tuple[dict, ...]:
        """该家族的层默认规则（保持声明顺序）；family 为 None 或未登记 → 空。

        消费方是 structure.file_layer_defaults（签名不变），见 corpus_catalog.json
        的 layer_note。
        """
        return tuple((self.families.get(family) or {}).get("layer_defaults") or ())

    # ---- 与磁盘对账 ----
    def dirs_on_disk(self) -> dict[str, bool]:
        """目录 → 磁盘上有没有这个书目录（CI 无本地语料时全 False）。"""
        root = config.LIBRARY_DIR
        out = {}
        for b in self.books.values():
            out[b.dir] = (root / b.dir).is_dir()
        return out

    def uncatalogued_dirs(self) -> list[str]:
        """磁盘上有、目录里没登记的书目录——扩容时最该先看到的告警。"""
        root = config.LIBRARY_DIR
        if not root.is_dir():
            return []
        known = {b.dir for b in self.books.values()}
        return sorted(d.name for d in root.iterdir()
                      if d.is_dir() and d.name not in known)

    def summary(self) -> dict:
        on_disk = self.dirs_on_disk()
        return {
            "path": str(self.path),
            "version": self.version,
            "books": len(self.books),
            "by_category": {c: sum(1 for b in self.books.values() if b.category == c)
                            for c in CATEGORIES},
            "by_era_group": _count(self.books.values(), "era_group"),
            "on_disk": sum(1 for v in on_disk.values() if v),
            "planned": sorted(b.title for b in self.books.values()
                              if not on_disk.get(b.dir)),
            "uncatalogued": self.uncatalogued_dirs(),
            "recall_verified": sorted(b.title for b in self.books.values()
                                      if b.recall_verified),
        }


def _count(books, attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for b in books:
        out[getattr(b, attr)] = out.get(getattr(b, attr), 0) + 1
    return dict(sorted(out.items()))


# ------------------------------------------------------------------ 校验

def _no_duplicate_keys(pairs):
    """object_pairs_hook：JSON 里重复的键会被 json 静默丢弃，这里改成报错。

    这类文件最常见的错法是「复制一条改一半」——同一个 ID 出现两次，后者悄悄
    盖掉前者，看起来一切正常。
    """
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise CatalogError(f"JSON 里有重复的键：{k!r}")
        seen[k] = v
    return seen


def _check_book(bid: str, raw: dict, problems: list[str]) -> None:
    where = f"books[{bid}]"
    if not isinstance(raw, dict):
        problems.append(f"{where}: 必须是对象")
        return
    for k in REQUIRED_BOOK_KEYS:
        if k not in raw:
            problems.append(f"{where}: 缺必填字段 {k!r}")
    for k in raw:
        if k not in REQUIRED_BOOK_KEYS + OPTIONAL_BOOK_KEYS:
            problems.append(f"{where}: 未知字段 {k!r}（拼错了？）")
    if not KR_ID_RE.match(bid):
        problems.append(f"{where}: 键不是 kanripo ID 形态（^KR[0-9a-z]{{6}}$）")
    if raw.get("kanripo_id") != bid:
        problems.append(f"{where}: kanripo_id={raw.get('kanripo_id')!r} 与键 {bid!r} 不一致")
    d = raw.get("dir") or ""
    if not DIR_RE.match(d):
        problems.append(f"{where}: dir={d!r} 不是合法目录名（小写字母/数字/下划线）")
    elif d != bid and raw.get("kanripo_id") and d == raw["kanripo_id"]:
        problems.append(f"{where}: dir 与 kanripo_id 相同，忘了改成拼音目录名？")
    if raw.get("family_expected") not in FAMILIES:
        problems.append(f"{where}: family_expected={raw.get('family_expected')!r} "
                        f"不在 {FAMILIES}")
    if raw.get("category") not in CATEGORIES:
        problems.append(f"{where}: category={raw.get('category')!r} 不在 {CATEGORIES}")
    if raw.get("h2_role") not in (None,) + H2_ROLES:
        problems.append(f"{where}: h2_role={raw.get('h2_role')!r} 不在 {H2_ROLES}")
    if raw.get("ab_system") not in (None,) + AB_SYSTEMS:
        problems.append(f"{where}: ab_system={raw.get('ab_system')!r} 不在 {AB_SYSTEMS}")
    if raw.get("title_handler") not in (None,) + TITLE_HANDLERS:
        problems.append(f"{where}: title_handler={raw.get('title_handler')!r} "
                        f"不在 {TITLE_HANDLERS}")
    for n in raw.get("title_patterns") or ():
        if n not in PATTERNS:
            problems.append(f"{where}: title_patterns 引用了未定义的模式 {n!r}"
                            f"（已定义：{sorted(PATTERNS)}）")
    if not isinstance(raw.get("recall_verified"), bool):
        problems.append(f"{where}: recall_verified 必须是 true/false（人工确认位）")
    if raw.get("juan_as_section") not in (None, True, False):
        problems.append(f"{where}: juan_as_section={raw.get('juan_as_section')!r} "
                        f"必须是 true/false（缺省=false）")
    for k in ("title", "dynasty", "era_group", "repo", "branch"):
        if not (raw.get(k) or "").strip():
            problems.append(f"{where}: {k} 不能为空")
    for fno, ov in (raw.get("file_overrides") or {}).items():
        w = f"{where}.file_overrides[{fno}]"
        if not str(fno).isdigit():
            problems.append(f"{w}: 键必须是文件号（数字字符串，如 \"59\"）")
        if not isinstance(ov, dict):
            problems.append(f"{w}: 必须是对象")
            continue
        for k in ov:
            if k not in ("layer", "status", "note"):
                problems.append(f"{w}: 未知字段 {k!r}")
        if ov.get("layer") not in LAYER_VALUES:
            problems.append(f"{w}: layer={ov.get('layer')!r} 不在 records.LAYER_VALUES")
        if ov.get("status") not in STATUS_VALUES:
            problems.append(f"{w}: status={ov.get('status')!r} 不在 records.STATUS_VALUES")


def _check_family(fname: str, raw: dict, problems: list[str]) -> None:
    """家族段校验。

    未知键一律报错：`layer_default`（单数、没人读）就是这样混进来的——声明了却
    没有执行者，比字段写错更危险，因为它看起来"已经配好了"。
    """
    where = f"families[{fname}]"
    for k in raw:
        if k not in FAMILY_KEYS:
            problems.append(f"{where}: 未知字段 {k!r}（拼错了？）")
    for n in raw.get("title_patterns") or ():
        if n not in PATTERNS:
            problems.append(f"{where}: 引用了未定义的模式 {n!r}")
    rules = raw.get("layer_defaults")
    if rules is None:
        problems.append(f"{where}: 缺 layer_defaults（家族层默认，至少要有一条 when=always）")
        return
    if not isinstance(rules, list):
        problems.append(f"{where}.layer_defaults: 必须是数组（按声明顺序取第一条命中）")
        return
    for i, r in enumerate(rules):
        w = f"{where}.layer_defaults[{i}]"
        if not isinstance(r, dict):
            problems.append(f"{w}: 必须是对象")
            continue
        for k in r:
            if k not in LAYER_RULE_KEYS:
                problems.append(f"{w}: 未知字段 {k!r}")
        when = r.get("when", "always")
        if when not in LAYER_WHEN:
            problems.append(f"{w}: when={when!r} 不是 catalog.LAYER_WHEN "
                            f"{LAYER_WHEN} 之一")
        if when == "juan_part":
            if r.get("resolver") not in LAYER_RESOLVERS:
                problems.append(f"{w}: when=juan_part 须给 resolver ∈ {LAYER_RESOLVERS}")
        elif r.get("resolver"):
            problems.append(f"{w}: 只有 when=juan_part 才用 resolver（这里 when={when!r}）")
        if not r.get("resolver") and r.get("layer") not in LAYER_VALUES:
            problems.append(f"{w}: layer={r.get('layer')!r} 不在 records.LAYER_VALUES")
        if r.get("status") not in STATUS_VALUES:
            problems.append(f"{w}: status={r.get('status')!r} 不在 records.STATUS_VALUES")
    if rules and isinstance(rules[-1], dict) and rules[-1].get("when", "always") != "always":
        problems.append(f"{where}.layer_defaults: 末条规则必须是 when=always 的兜底，"
                        f"否则漏网的文件会掉回内置默认，行为随 catalog 在不在而变")


def _validate(raw: dict, path: Path) -> Catalog:
    problems: list[str] = []
    for k in raw:
        if k not in TOP_KEYS:
            problems.append(f"顶层未知字段 {k!r}")
    if raw.get("version") != SCHEMA_VERSION:
        problems.append(f"version={raw.get('version')!r}，本加载器只认 {SCHEMA_VERSION}")
    books_raw = raw.get("books")
    if not isinstance(books_raw, dict) or not books_raw:
        problems.append("books 必须是非空对象")
        books_raw = {}
    for bid, braw in books_raw.items():
        _check_book(bid, braw, problems)

    fams = raw.get("families")
    if not isinstance(fams, dict) or not fams:
        problems.append("families 必须是非空对象")
        fams = {}
    for fname, fraw in fams.items():
        if fname not in FAMILIES:
            problems.append(f"families: 未知家族 {fname!r}")
        if not isinstance(fraw, dict):
            problems.append(f"families[{fname}]: 必须是对象")
            continue
        _check_family(fname, fraw, problems)

    # dir 唯一：两个 ID 指向同一个磁盘目录 = 同一本书登记了两次
    seen_dir: dict[str, str] = {}
    for bid, braw in books_raw.items():
        d = (braw or {}).get("dir")
        if d in seen_dir:
            problems.append(f"books[{bid}] 与 books[{seen_dir[d]}] 的 dir 都是 {d!r}")
        elif d:
            seen_dir[d] = bid

    if problems:
        raise CatalogError(f"{path} 校验未通过（{len(problems)} 处）：\n  - "
                           + "\n  - ".join(problems))

    books = {bid: Book(
        book_id=bid, dir=b["dir"], title=b["title"], dynasty=b["dynasty"],
        era_group=b["era_group"], category=b["category"],
        family_expected=b["family_expected"], repo=b["repo"], branch=b["branch"],
        recall_verified=b["recall_verified"], h2_role=b.get("h2_role"),
        ab_system=b.get("ab_system"), title_handler=b.get("title_handler"),
        title_patterns=tuple(b.get("title_patterns") or ()),
        juan_prefixes=tuple(b.get("juan_prefixes") or ()),
        juan_as_section=bool(b.get("juan_as_section")),
        file_overrides=dict(b.get("file_overrides") or {}),
        note=b.get("note", ""),
    ) for bid, b in books_raw.items()}
    return Catalog(version=raw["version"], books=books, families=fams, path=path,
                   by_dir_map={b.dir: b for b in books.values()})


# ------------------------------------------------------------------ 加载

@lru_cache(maxsize=None)
def load(path: str | None = None) -> Catalog:
    """读取 + 校验（带缓存）。文件缺失或写错都抛 CatalogError。"""
    p = Path(path) if path else CATALOG_PATH
    if not p.is_file():
        raise CatalogError(f"语料目录不存在：{p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"),
                         object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as e:
        raise CatalogError(f"{p} 不是合法 JSON：{e}") from e
    return _validate(raw, p)


def try_load(path: str | None = None) -> Catalog | None:
    """文件缺失时返回 None（回滚路径：调用方回落到 6.2 内置规则）。"""
    try:
        return load(path)
    except CatalogError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="语料目录校验（6.3-A）")
    ap.add_argument("--check", action="store_true", help="只校验并打印统计")
    ap.add_argument("--path", default=None, help="目录文件路径（默认 corpus_catalog.json）")
    args = ap.parse_args(argv)
    cat = load(args.path)
    s = cat.summary()
    print(f"语料目录 OK：{s['path']}")
    print(f"  v{s['version']}  {s['books']} 部在册"
          f"（{'、'.join(f'{k} {v}' for k, v in s['by_category'].items())}）")
    print(f"  磁盘已有目录：{s['on_disk']} 部；未下载（planned）："
          f"{len(s['planned'])} 部 {s['planned']}")
    print(f"  按时代：{s['by_era_group']}")
    print(f"  已人工确认可召回：{s['recall_verified']}")
    if s["uncatalogued"]:
        print(f"  **磁盘上有未登记的书目录**：{s['uncatalogued']} —— 补进 books 或移走")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
