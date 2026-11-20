"""Independent reign inventory + strict five-item sampling gate (not a gold generator).

Periods with no independently verified pool fail coverage. Arbitrary text slices
are never promoted to people/events or assigned to a reign by a substring match.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "src/data/reign_sources"


class Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and attrs.get("id", "").startswith("av"):
            self.row = {"source_row_id": attrs["id"], "cells": []}
        if tag == "td" and self.row is not None:
            self.cell = []
    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
    def handle_endtag(self, tag):
        if tag == "div" and self.cell is not None:
            self.cell.append(" ")
        if tag == "td" and self.cell is not None:
            self.row["cells"].append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def inventory():
    periods, sources = [], []
    for page in (1, 2, 3):
        path = DATA / f"moe_emperors_{page}.html"
        raw = path.read_bytes()
        parser = Table()
        parser.feed(raw.decode("utf-8"))
        source = f"https://dict.revised.moe.edu.tw/appendix.jsp?ID=22&la=0&page={page}&powerMode=0&ver=4"
        sources.append({"url": source, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(parser.rows)})
        for row in parser.rows:
            if len(row["cells"]) != 3:
                raise ValueError("unexpected reign table structure")
            dynasty, emperor, years = row["cells"]
            if dynasty in ("周", "清"):
                continue
            periods.append({"id": "moe-" + row["source_row_id"], "dynasty": dynasty,
                            "emperor": emperor, "reign_years_as_source": years,
                            "source": source + "#" + row["source_row_id"],
                            "required_samples": 5, "source_review": "pending_crosscheck"})
    if not periods or len({p["id"] for p in periods}) != len(periods):
        raise ValueError("empty or duplicated reign inventory")
    return {"scope": "秦至明（不含清）", "registry_status": "provisional_not_exhaustive",
            "limitations": ["此独立年表没有完整覆盖十六国、十国及争议短期君主，必须补录，不能作为全覆盖完成证明。",
                            "源表年份与人物身份需要交叉核对；重登位条目按来源行分别保留。"],
            "sources": sources, "periods": periods}


def sample(registry, pools, seed):
    report, cases = [], []
    for period in registry["periods"]:
        pool = pools.get(period["id"], [])
        identities = set()
        valid = []
        for item in pool:
            # A pool item needs independently checked period membership and
            # stable provenance. Presence somewhere in a book is insufficient.
            key = (item.get("kind"), item.get("content"))
            if (item.get("kind") not in ("person", "event") or not item.get("query")
                    or not item.get("content") or item.get("period_verified") is not True
                    or not item.get("period_evidence") or not item.get("gold_sources") or key in identities):
                continue
            if not all(all(k in g for k in ("origin_path", "sha256", "row_no", "quote"))
                       for g in item["gold_sources"]):
                continue
            identities.add(key)
            valid.append(item)
        state = "ready" if len(valid) >= 5 else "insufficient_verified_pool"
        picked = []
        if state == "ready":
            rng = random.Random(str(seed) + ":" + period["id"])
            picked = rng.sample(sorted(valid, key=lambda x: (x["kind"], x["content"])), 5)
            cases.extend({"period_id": period["id"], "seed": seed, **x} for x in picked)
        report.append({**period, "verified_pool_size": len(valid), "selected": len(picked), "status": state})
    return {"seed": seed, "registry_status": registry["registry_status"], "periods": report,
            "cases": cases, "coverage_pass": registry["registry_status"] == "verified_complete" and
            all(x["status"] == "ready" for x in report)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pools", type=Path)
    ap.add_argument("--seed", type=int, default=20261021)
    args = ap.parse_args()
    registry = inventory()
    pools = json.loads(args.pools.read_text(encoding="utf-8")) if args.pools else {}
    result = sample(registry, pools, args.seed)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "reign_inventory.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "sampling_gate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"periods": len(registry["periods"]), "required_cases": len(registry["periods"]) * 5,
                      "ready_periods": sum(x["status"] == "ready" for x in result["periods"]),
                      "coverage_pass": result["coverage_pass"], "registry_status": registry["registry_status"]}, ensure_ascii=False))
    return 0 if result["coverage_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
