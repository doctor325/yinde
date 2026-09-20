"""阶段编排入口（CLI）。

用法：
  python -m scripts.pipeline.run_all                  # 全流程：inventory→process→db→validate
  python -m scripts.pipeline.run_all --no-inventory   # 跳过某阶段

原则：所有产物（metadata/jsonl/db/logs）都在 data/ 下，可整目录删除后一键重建；
library 只读，绝不写入。
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys

from . import config
from .jsonl_io import run_stage_process
from .sqlite_store import rebuild
from .validate import run_validation

log = logging.getLogger("pipeline")


def _setup_logfile():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fh = logging.FileHandler(config.LOG_DIR / f"pipeline_{ts}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description="HistoryAI 解析管线（阶段 1：结构化）")
    ap.add_argument("--no-inventory", action="store_true", help="跳过 inventory 扫描")
    ap.add_argument("--no-process", action="store_true", help="跳过 segmentation→JSONL")
    ap.add_argument("--no-db", action="store_true", help="跳过 JSONL→SQLite")
    ap.add_argument("--no-validate", action="store_true", help="跳过 validate 对账")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    _setup_logfile()
    config.ensure_data_dirs()

    report = {}
    if not args.no_inventory:
        from .inventory import run_inventory
        report["inventory"] = run_inventory()
    if not args.no_process:
        report["process"] = [
            {k: s[k] for k in ("book", "files", "records")} for s in run_stage_process()]
    if not args.no_db:
        report["db"] = rebuild()
    if not args.no_validate:
        v = run_validation(quiet=True)
        report["validate"] = {k: v[k] for k in
                              ("files", "files_ok", "body_ok", "sha256_ok",
                               "meta_ok", "pb_bad", "kr_bad")}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 日志落盘 flusher
    for h in logging.getLogger().handlers:
        h.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
