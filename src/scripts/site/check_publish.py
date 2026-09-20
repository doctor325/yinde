"""发布闸门 —— 公开产物里有没有真实语料（§5 / §7 / §12）。

用法：
    python -m scripts.site.check_publish [要检查的目录]     # 默认 frontend/
    python -m scripts.site.check_publish --list            # 只列出产物清单

## 为什么要有它

公开站要把 `HistoryAI/frontend/` 整个传上 GitHub Pages。这个目录里同时躺着
`data/`（真实语料导出，33.5 MB，**永不发布**）和 `data-demo/`（自撰演示数据，
MIT）。两者只差三个字符，而 `.gitignore` 只覆盖前者 —— 一个手滑就是 §5 的
最严重违规。所以在上传**之前**、在 CI 里，再挡一道。

## 判据为什么是这几条，而不是「与真实语料比 sha256」

最直觉的闸门是「拿产物去和 HistoryLibrary 比 sha256」，但 **CI 里没有
HistoryLibrary** —— 它被 .gitignore 排除，checkout 出来根本不存在。一个在
CI 里跑不了、或者因为找不到参照物而「永远通过」的闸门，比没有闸门更危险：
它给的是虚假的安心。

所以这里的判据都**不需要真实语料在场**，而是从两个方向夹：

1. **只许出现这几类文件**（白名单）。语料想进来，得先变成一个不在名单上的
   路径 —— 当场失败。
2. **凡是出现正文的地方，逐字反查是不是自撰演示文本**。这一条借的是
   `make_demo_data.py` 里的 `DEMO_BOOKS`：它在仓库里，CI 里有它，所以
   「产物里的每一句正文都必须是这几十行自撰文本的子串」这句话是可执行的。
   真实语料的任何一个字都不可能是它们的子串。

第 2 条是主力。第 1 条管的是「以后有人加了新文件」这种情况。

第 2 条的名单（`REAL_TITLES`）**从语料目录 `corpus_catalog.json` 派生**（6.3-L）：
手写名单在 6.2 就漏过一次（前漢書/後漢書），而 6.3 一次加 12 本书。名单读不出来
时闸门直接拒发 —— 名单为空 = 判据②对任何书名都判通过，那种「永远通过」的闸门
正是本模块开头警告过的虚假安心。

## 它**不是**什么

不是「产物正确」的证明。它只回答一个问题：**这里面有没有不该公开的语料**。
产物能不能跑，由 `check_engine.py` 的 `demo-site` 那一项回答。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pipeline import config  # noqa: E402
from scripts.site.make_demo_data import demo_lines  # noqa: E402

# 允许出现在公开产物里的文件。精确到文件名，不写 glob ——
# 「engine/ 下什么都行」这种宽口径会让「往 engine/ 里塞一份语料」合法化。
ALLOWED_FILES = {
    "index.html", "style.css", "app.js", "boot.js",
    "engine/zh_table.js", "engine/zh.js", "engine/dual_text.js",
    "engine/corpus.js", "engine/engine.js", "engine/entities.js",
    "engine/question.js", "engine/query_expansion.js", "engine/ranking.js",
    "engine/result_block.js", "engine/aggregate.js", "engine/retrieve.js",
    "engine/static_api.js",
    "data-demo/stats.json", "data-demo/books.json", "data-demo/files.json",
    "data-demo/book_files.json", "data-demo/corpus.json",
    "data-demo/sections.json",
    "data-demo/raw/1.json", "data-demo/raw/2.json",
    "data-demo/raw/3.json", "data-demo/raw/4.json",
}

# 单个文件的上限。演示语料 13 KB、engine 最大 100 KB、app.js 55 KB；
# 真实语料导出的**最小**单位也在几十 MB（corpus.json 33.5 MB，raw/ 更大）。
# 1 MB 离两者都很远，不会误伤，也拦得住「悄悄塞了半份语料」。
MAX_BYTES = 1 << 20

# 真实语料的书名。它们出现在 `book_title` 之类**字段**里就是泄露。
# （index.html 的页脚曾经写死过 7 个书名，6.3-I 改成了运行时从数据填 —— 产物里
# 一个真实书名都不剩，见 frontend/app.js 的 footBooks()。这里不再为它留例外。）
#
# **真源上移到语料目录**（6.3-L）：6.2 时这份名单是手写的，规定「只对账、不复制」，
# 但 6.3 一次加 12 本书 —— 靠记性同步名单正是 6.2 补过的那类 bug（那次漏了
# 前漢書/後漢書）。现在书进了 `scripts/pipeline/corpus_catalog.json` 就自动进闸门，
# 不存在「忘了加」。语义变化（已写进 6.3 报告，不是悄悄改）：`publish_gate_sync()`
# 的 missing（库里有、闸门不认识）从此结构性恒为空；仍要人看的是 stale（在册未入库）。
def _load_real_titles() -> tuple[set[str], str]:
    """读语料目录得到真实书名集合。读不出来就**拒发**，不放行。

    失败时返回空集 + 原因：一个「名单为空」的闸门对任何真实书名都判通过，比没有
    闸门更危险 —— 它给的是虚假的安心（同本模块开头对 sha256 判据的取舍）。所以
    catalog 读不出来时不静默退化成空集，而是当成一条问题报出去。catalog 是仓库内
    的 JSON，CI（无本地语料）也能读，闸门在 Actions 里照常工作。
    """
    try:
        from scripts.pipeline import catalog as corpus_catalog
        return set(corpus_catalog.load().titles()), ""
    except Exception as e:                       # noqa: BLE001 —— 任何读不出来的原因都拒发
        return set(), (f"真实书名名单取不到（语料目录 corpus_catalog.json 读不出来："
                       f"{e}）——名单为空时判据②对任何书名都判通过，故拒绝发布")


REAL_TITLES, TITLES_ERROR = _load_real_titles()


def check_file_set(root: Path) -> list[str]:
    bad = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel not in ALLOWED_FILES:
            hint = ""
            if rel.startswith("data/") or "/data/" in rel:
                hint = "　← 这是真实语料导出目录，**绝不能发布**"
            elif rel.endswith(".db") or rel.endswith(".sqlite"):
                hint = "　← 数据库不入库（§6）"
            bad.append(f"产物里出现了不在白名单的文件：{rel}{hint}")
            continue
        n = p.stat().st_size
        if n > MAX_BYTES:
            bad.append(f"文件过大：{rel}（{n:,} B > 上限 {MAX_BYTES:,} B）"
                       f"　← 语料导出的最小单位是几十 MB")
    return bad


def check_no_real_titles(root: Path) -> list[str]:
    """扫所有 JSON 的**字段值**找真实书名。页脚文案在 index.html 里，不受影响。"""
    bad = []
    hits: set[tuple[str, str]] = set()

    def walk(node, path: Path) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k in ("book_title", "title", "book") \
                        and v in REAL_TITLES:
                    hits.add((path.relative_to(root).as_posix(), v))
                walk(v, path)
        elif isinstance(node, list):
            for v in node:
                walk(v, path)
        elif isinstance(node, str) and node in REAL_TITLES:
            hits.add((path.relative_to(root).as_posix(), node))

    for p in sorted(root.rglob("*.json")):
        try:
            walk(json.loads(p.read_text(encoding="utf-8")), p)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    for rel, v in sorted(hits):
        bad.append(f"真实书名出现在数据里：{rel} → {v}")
    return bad


def check_text_is_demo(root: Path, allowed: list[str]) -> tuple[list[str], int]:
    """主力判据：产物里出现的**每一段正文**，都必须是自撰演示文本的子串。

    覆盖两类载体：打包语料（corpus.json 的 text_orig 列）与原文对照
    （raw/*.json 的 lines[].text）。前者是「检索用的」，后者是「照着看的」，
    真实语料要走漏，只会从这两个地方走。
    """
    bad = []
    n_piece = 0

    def is_demo(s: str) -> bool:
        t = s.strip()
        return (not t) or any(t in line for line in allowed)

    corpus = root / "data-demo" / "corpus.json"
    if corpus.exists():
        d = json.loads(corpus.read_text(encoding="utf-8"))
        i_text = d["columns"].index("text_orig")
        for row in d["rows"]:
            for piece in str(row[i_text]).split("\n"):
                n_piece += 1
                if not is_demo(piece):
                    bad.append(f"语料里有非演示正文：{piece.strip()[:60]}")

    for p in sorted((root / "data-demo" / "raw").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for ln in d.get("lines") or []:
            n_piece += 1
            if not is_demo(str(ln.get("text", ""))):
                bad.append(f"{p.name} 第 {ln.get('no')} 行非演示正文："
                           f"{str(ln.get('text'))[:60]}")

    # 篇名区间表是第三个载体：它进的是**检索路径**（篇名命中直接产出结果块），
    # 一个真实篇名混进来，演示站上就能搜到它。所以它必须和正文一样逐条反查。
    sections = root / "data-demo" / "sections.json"
    if sections.exists():
        for s in json.loads(sections.read_text(encoding="utf-8")):
            n_piece += 1
            if not is_demo(str(s.get("label", ""))):
                bad.append(f"篇名区间表里有非演示篇名：{str(s.get('label'))[:60]}")
    return bad, n_piece


def main(argv: list[str]) -> int:
    args = argv[1:]
    if "--list" in args:
        root = Path(args[0]) if args else config.HISTORY_AI_DIR / "frontend"
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                mark = "" if rel in ALLOWED_FILES else "   ← 不在白名单"
                print(f"{p.stat().st_size:>10,}  {rel}{mark}")
        return 0
    root = Path(args[0]).resolve() if args else config.HISTORY_AI_DIR / "frontend"
    if not root.is_dir():
        print(f"找不到目录：{root}")
        return 2

    n_files = sum(1 for p in root.rglob("*") if p.is_file())
    print(f"发布闸门：{root}")
    print(f"产物 {n_files} 个文件")

    allowed = demo_lines()
    tbad, n_piece = check_text_is_demo(root, allowed)
    # 名字列表来自语料目录，会随语料长大（28 部），全列出来会把这一行撑成一段话 ——
    # 报个数，抽查前几个，其余留在 REAL_TITLES 里可查。
    tnames = sorted(REAL_TITLES)
    tshow = "、".join(tnames[:4]) + ("…" if len(tnames) > 4 else "")
    rules = [
        (f"① 文件白名单（{len(ALLOWED_FILES)} 项）", check_file_set(root)),
        (f"② 数据里无真实书名（{len(tnames)} 个：{tshow}）",
         ([TITLES_ERROR] if TITLES_ERROR else []) + check_no_real_titles(root)),
        (f"③ 正文逐字反查自撰文本（{len(allowed)} 行自撰 / "
         f"{n_piece:,} 段产出行）", tbad),
    ]
    bad = [msg for _, msgs in rules for msg in msgs]
    for label, msgs in rules:
        print(f"  {label}：{'通过' if not msgs else f'**{len(msgs)} 处**'}")

    if bad:
        print(f"\n**发现 {len(bad)} 处问题 —— 拒绝发布**：")
        for b in bad[:20]:
            print(f"  {b}")
        if len(bad) > 20:
            print(f"  …另有 {len(bad) - 20} 处")
        return 1
    print("\n通过：产物中没有任何真实语料。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
