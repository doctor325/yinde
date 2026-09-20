"""语料仓库体检（第六点三阶段 6.3-E）—— **导入前**回答「上游给全了吗」。

用法：
    python -m scripts.pipeline.check_repos                # 在册且已下载的书
    python -m scripts.pipeline.check_repos --dir songshu  # 只查一本
    python -m scripts.pipeline.check_repos --gaps         # 只列有问题的

为什么要有它：第一批（三國志/晉書）导入后才发现 **Kanripo 上游转录不全** ——
三國志只到魏書卷三十（31 个 txt），晉書只到卷三十三（34 个）。这不是解析问题，
但也绝不能等用户搜「諸葛亮」搜到一半才发现。缺卷是**上游事实**，本工具把它在
导入前就摆出来，写进检查点报告；它不做判断、不改语料、连库都不碰。只读。

看三件事（都从 txt 文件头读）：

1. **BASEEDITION** —— 与 `corpus_catalog.json` 的 `family_expected` 对账（大小写
   无关：文件头写 `WYG`，catalog 写 `wyg`）。不一致说明克隆分支搞错了（每个
   Kanripo repo 都有 master/WYG/_data 三个分支），要停下上报，不擅自换分支。
   同书内混着两种底本也报 —— 那是语料本身有问题。
2. **卷号** —— 文件头 `#+PROPERTY: JUAN`（写法有 `卷一`、`卷一上`、`1` 三种）。
   与通行本卷数（EXPECTED_JUANS）比对，列出缺卷。**只用于报告，不是导入闸门**：
   上游缺多少我们改不了，能做的是别假装完整。
3. **文件号** —— `_NNN` 有没有重号。**不做连续号检查**：史記就是 100/201…/300/
   400/500 这种成块编号，按连续号查会报一百多条假缺口。

JUAN 不是全局卷序时（史記每个区块各自从 1 数起）**跳过卷号缺口比对**并在报告里
写明原因 —— 宁可说「这本查不了」，也不给一个看着像缺卷的假结论。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from . import catalog
from . import config

# 通行本卷数（中华书局点校本体系）。**只用来算缺口**。先秦四书不在表里：它们不是
# 一卷一题的体例（尚書按篇、左傳按年、國語/戰國策按国别），卷号对不上不代表缺。
EXPECTED_JUANS = {
    "shiji": 130, "qianhanshu": 100, "houhanshu": 120, "sanguozhi": 65,
    "jinshu": 130, "songshu": 100, "nanqishu": 59, "liangshu": 56, "chenshu": 36,
    "weishu": 114, "beiqishu": 50, "zhoushu": 50, "suishu": 85,
    "nanshi": 80, "beishi": 100, "jiutangshu": 200, "xintangshu": 225,
    "jiuwudaishi": 150, "xinwudaishi": 74, "songshi": 496, "liaoshi": 116,
    "jinshi": 135, "yuanshi": 210, "mingshi": 332,
}

JUAN_RE = re.compile(r"^#\+PROPERTY:\s*JUAN\s+(\S+)\s*$", re.M)
BASED_RE = re.compile(r"^#\+PROPERTY:\s*BASEEDITION\s+(\S+)\s*$", re.M)

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100}


def cn_to_int(s: str) -> int | None:
    """中文数字转整数（`二十三`→23、`一百二十`→120）。转不了返回 None。

    只为读卷号而写（最大到 496）：不做「萬/億」这类本题用不到的量级。
    """
    total = cur = 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (cur or 1) * _CN_UNITS[ch]
            cur = 0
        else:
            return None
    return total + cur


def parse_juan(raw: str) -> int | None:
    """`卷一` / `巻十二` / `卷一上` / `卷七考證` / `卷一百八之一` / `1` → 整数。

    后两种不是「认不出的写法」：`卷七考證` 是晉書那一卷（卷末附考證），
    `卷一百八之一` 是魏書按子卷分文件（卷 108 的第 1 部分）—— 都归到本卷。
    """
    s = raw.strip().lstrip("卷巻")
    s = re.sub(r"(考證|考証|考异)$", "", s)
    s = s.rstrip("上下")
    s = re.sub(r"之[一二三四五六七八九十]+$", "", s)   # 卷一百八之一 → 卷一百八
    if s.isdigit():
        return int(s)
    return cn_to_int(s)


def header_of(path: Path) -> tuple[str | None, str | None]:
    """文件头的 JUAN / BASEEDITION —— 只读开头 4 KB（正文几十 MB 的不在少数）。"""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        head = f.read(4096)
    j = JUAN_RE.search(head)
    b = BASED_RE.search(head)
    return (j.group(1) if j else None, b.group(1) if b else None)


def check_book(b) -> dict:
    """一本书的体检结果（b 是 catalog.Book）。"""
    root = config.LIBRARY_DIR / b.dir
    out = {"dir": b.dir, "title": b.title, "kanripo_id": b.book_id,
           "family_expected": b.family_expected, "exists": root.is_dir(),
           "files": 0, "juan_nos": [], "dup_files": [], "unreadable": [],
           "baseditions": {}, "problems": [], "notes": []}
    if not out["exists"]:
        out["problems"].append("目录不在磁盘上（未下载）")
        return out
    txts = sorted(root.glob("*.txt"))
    out["files"] = len(txts)
    if not txts:
        out["problems"].append("目录里没有 txt")
        return out

    nums, bases, entries = [], Counter(), []       # entries: (文件名, 文件号, JUAN 原文)
    for p in txts:
        m = re.search(r"_(\d+)\.txt$", p.name)
        fno = int(m.group(1)) if m else None
        if fno is not None:
            nums.append(fno)
        juan, base = header_of(p)
        if base:
            bases[base] += 1
        entries.append((p.name, fno, juan))
    out["baseditions"] = dict(bases)
    out["dup_files"] = sorted(n for n, c in Counter(nums).items() if c > 1)

    # ① 家族对账（大小写无关：文件头 WYG，catalog wyg）
    got = {v.lower() for v in bases}
    if len(got) > 1:
        out["problems"].append(f"同书混着两种底本 {sorted(bases)} —— 语料本身有问题")
    elif got and b.family_expected.lower() not in got:
        out["problems"].append(
            f"BASEEDITION={sorted(bases)} 与 catalog 的 "
            f"family_expected={b.family_expected!r} 不符 —— 分支搞错了？先别导入")

    # ② 文件号重号
    if out["dup_files"]:
        out["problems"].append(f"文件号重号：{out['dup_files']}")

    # ③ 卷号缺口：只在 JUAN 确实是「全局卷序」时比对
    parsed = {name: parse_juan(j) for name, _, j in entries if j}
    pairs = [(fno, parsed[name]) for name, fno, j in entries
             if j and fno is not None and parsed.get(name) is not None]
    # 卷号是不是「全局卷序」：看它跟文件号对不对得上。WYG 正史是一卷一文件
    # （_001 = 卷一），史記 这类按区块编号的书则对不上（_201 里是卷一）。对不上
    # 就跳过缺卷比对 —— 否则会报出「史記缺 117 卷」这种看着像缺卷的假结论。
    agree = sum(1 for f, n in pairs if f == n)
    aligned = bool(pairs) and agree >= 0.8 * len(pairs)
    exp = EXPECTED_JUANS.get(b.dir)
    odd = sorted({j for name, _, j in entries if j and parsed.get(name) is None})
    if odd and not aligned:
        out["notes"].append(f"卷号认不出的写法：{odd[:3]}")
    # 对得上的书里，个别文件头没写卷号（魏書的 _105 写的是「前上十志啓」）——
    # 既然整本都是「文件号 = 卷号」，就按文件号认这一卷，而不是把它算成缺卷。
    filled = []
    if aligned:
        for name, fno, j in entries:
            if fno is not None and j and parsed.get(name) is None and 0 < fno <= (exp or 0):
                parsed[name] = fno
                filled.append(f"{name} → 卷{fno}")
            elif j and parsed.get(name) is None:
                out["notes"].append(f"{name} 的头里没有卷号（JUAN={j}），不计入卷号")
    if filled:
        out["notes"].append(f"{len(filled)} 个文件头没写卷号、按文件号认卷："
                            f"{'；'.join(filled[:3])}")
    juans = sorted({n for n in parsed.values() if n is not None})
    out["juan_nos"] = juans
    dup = sorted(n for n, c in Counter(juans).items() if c > 1)
    if dup:
        out["notes"].append(f"{len(dup)} 个卷号有子卷（同卷分文件），如 {dup[:3]}")
    if not exp or not aligned:
        if exp and not aligned:
            out["notes"].append(
                f"卷号与文件号不对应（{len(pairs) - agree}/{len(pairs)} 不符，"
                f"按区块编号的书）—— 跳过缺卷比对")
    elif juans:
        have = set(juans)
        missing = [n for n in range(1, exp + 1) if n not in have]
        out["expected_juan"] = exp
        out["missing_juan"] = missing
        if missing:
            tail = (f"（缺 {missing[0]}…{missing[-1]}）" if len(missing) > 1
                    else f"（缺 {missing[0]}）")
            out["problems"].append(
                f"上游缺卷 {len(missing)}/{exp}：到卷 {max(juans)} 为止{tail}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="语料仓库体检（6.3-E）")
    ap.add_argument("--dir", default=None, help="只查这个目录名的书")
    ap.add_argument("--gaps", action="store_true", help="只列有问题的")
    args = ap.parse_args(argv)

    cat = catalog.load()
    books = list(cat.books.values())
    if args.dir:
        books = [b for b in books if b.dir == args.dir]
        if not books:
            print(f"catalog 里没有目录名 {args.dir!r} 的书")
            return 2

    rows = [check_book(b) for b in books]
    n_ok = 0
    for r in rows:
        if not r["exists"]:
            if not args.gaps:
                print(f'—— {r["title"]}（{r["dir"]}）未下载')
            continue
        if args.gaps and not r["problems"]:
            continue
        head = (f'—— {r["title"]:<6}{r["dir"]:<11}{r["files"]:>4} 文件'
                f'  底本 {"/".join(sorted(r["baseditions"])) or "?"}'
                f'  卷号 {len(set(r["juan_nos"]))} 个')
        if r["juan_nos"]:
            head += f'（{min(r["juan_nos"])}~{max(r["juan_nos"])}）'
        if not r["problems"]:
            n_ok += 1
        print(head)
        for p in r["problems"]:
            print(f'     ** {p}')
        for n in r["notes"]:
            print(f'     ·  {n}')
    print(f'\n{len([r for r in rows if r["exists"]])} 部已下载，{n_ok} 部无疑问'
          + ("（--gaps 只列有问题的）" if args.gaps else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
