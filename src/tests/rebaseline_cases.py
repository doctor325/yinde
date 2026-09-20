"""重测召回用例集的基线数（§21/§23）。

用法（在 HistoryAI 目录下）：
    PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/rebaseline_cases.py [--write]

不加 `--write` 只打印漂移表，不落盘 —— 先看清楚「哪些数字动了、动了多少」，
再决定要不要写回去。加了 `--write` 才把 `baseline_hits` / `baseline_blocks`
按当前库重写进 `tests/search_cases/*.json`。

## 为什么需要它

`baseline_*` 是**语料和引擎的联合快照**，两者任一变动它就过期：

- 加书（第六点二阶段加了 前漢書/後漢書）→ 命中数必然涨；
- 改块组装（本阶段修跨篇界）→ 块数变而命中数不变。

过期本身不是错误（recall.py 只把漂移列出来，不判失败），但如果没人重测，
「⚠漂移」那栏会一直红着，真出问题时反而看不出来。所以加书/改组装之后
必须跑一次这里，并**在阶段报告里逐条解释漂移**——数字自己不会解释自己。

负例（`must_hit: false`）不写基线：它们的期望值是 0，而 0 不是「测出来的
基线」；写上去只会让人以为它和正例是一回事。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api import db                              # noqa: E402
from search import result_block                 # noqa: E402

CASES_DIR = ROOT / "tests" / "search_cases"


def measure(cur, case: dict) -> tuple:
    """按 case 自己的检索参数跑一次，返回 (命中段, 结果块)。"""
    res = result_block.search_result_blocks(
        cur, case["query"], book=case.get("book"), edition=case.get("edition"),
        page=case.get("page", 1), page_size=case.get("page_size", 20),
        mode=case.get("mode", "standard"),
        text_mode=case.get("text_mode", "orig"))
    return res["hit_total"], res["total"]


def main(argv: list[str]) -> int:
    write = "--write" in argv
    files = sorted(CASES_DIR.glob("*.json"))
    if not files:
        print(f"没有用例文件：{CASES_DIR}")
        return 2
    cur = db.connect()
    n_case, n_drift = 0, 0
    print(f"{'id':<16}{'查询':<18}{'命中(旧→新)':<20}{'块数(旧→新)':<20}文件")
    print("-" * 88)
    for p in files:
        doc = json.loads(p.read_text(encoding="utf-8"))
        for c in doc.get("cases", []):
            n_case += 1
            if not c.get("must_hit", True):
                continue
            hit, blk = measure(cur, c)
            old_hit, old_blk = c.get("baseline_hits"), c.get("baseline_blocks")
            drift = (old_hit != hit) or (old_blk is not None and old_blk != blk)
            if drift:
                n_drift += 1
                hs = f"{old_hit} → {hit}" if old_hit != hit else f"{hit}（同）"
                bs = (f"{old_blk} → {blk}" if old_blk != blk
                      else f"{blk}（同）")
                print(f"{c['id']:<16}{c['query']:<18}{hs:<20}{bs:<20}{p.name}")
            c["baseline_hits"] = hit
            c["baseline_blocks"] = blk
        if write:
            # indent=1 与本目录既有格式一致；newline="\n" 对齐仓库的 eol=lf 约定。
            p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8", newline="\n")
    cur.close()
    print("-" * 88)
    print(f"{n_case} 条用例，{n_drift} 条与基线不符")
    print("已写入所有文件的 baseline_* —— 报告里要逐条解释这些漂移"
          if write else "（只打印，未落盘；加 --write 才写回）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
