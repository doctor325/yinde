"""JSONL 输出与全量管线编排。

设计说明（对任务书 §16 的一处调整及原因）：
任务书建议 segmented_*.jsonl 与 parsed_*.jsonl 两层都输出。实测两者在逐行粒度上
几乎完全重叠（每行记录都携带原文），同时写两份会造成约 2 倍体积且无信息增量。
因此：
- parsed_<book>.jsonl      —— 权威中间产物：全部记录 + 完整 text_orig（SQLite 由它载入）
- 逐行的"纯切分骨架"不再单列；files.json 已含每文件行级统计，重建校验由 validate 完成
若后续确需 segmented 全量层，可随时从同一 pipeline 生成，无需改动解析逻辑。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from . import config
from .inventory import scan_library
from .segmentation import segment_file

log = logging.getLogger("pipeline")


def enrich(book: dict, finfo_name: str, header_meta: dict) -> dict:
    return {
        "book_id": book.get("book_id", ""),  # BookInfo.to_dict() 键名即 book_id
        "book_dir": book.get("dir_name", ""),
        "book_title": book.get("title", ""),
        "edition": book.get("edition", ""),
        "family": book.get("family", ""),
        # 文件名以磁盘为准（KRxxxx_NNN.txt；头 ID 键不作文件名依据）
        "original_file": finfo_name,
        "file_juan": header_meta.get("JUAN"),
        "file_header_meta": header_meta,
    }


def process_book(info) -> dict:
    """单书：逐 txt 分段 → 按记录写 parsed_<book>.jsonl。返回统计。"""
    import collections

    stat = collections.Counter()
    stat["files"] = 0
    book_name = info.dir_name
    out_path = config.PROCESSED_DIR / f"parsed_{book_name}.jsonl"

    with open(out_path, "w", encoding="utf-8") as fh:
        txts = sorted([f for f in info.files if f.kind == "txt"],
                      key=lambda f: f.file_no or -1)
        for finfo in txts:
            header, recs, _body = segment_file(finfo.path)
            env = enrich(info.to_dict(), finfo.file_name, header.metadata)
            env["sha256"] = finfo.sha256
            for rec in recs:
                row = dict(env)
                row["rec"] = rec.to_dict()
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            stat["files"] += 1
            stat["records"] += len(recs)
            for r in recs:
                stat[f"kind:{r.kind}"] += 1
                stat[f"layer:{r.layer}"] += 1
                stat[f"status:{r.status}"] += 1
                stat["kr"] += len(r.special_chars)
                if r.source_reference:
                    stat["src_refs"] += 1
                if r.kind == "passage" and r.layer == "main":
                    stat["main_passages"] += 1
    log.info("processed %s -> %s (%s 记录)", book_name, out_path.name, stat["records"])
    return dict(stat)


def run_stage_process(write_log: bool = True) -> list[dict]:
    """Step 3-5：全量跑五书分段 → JSONL。返回每书统计。"""
    config.ensure_data_dirs()
    if write_log:
        _setup_logfile("process")
    results = []
    for info in scan_library():
        stat = process_book(info)
        stat["book"] = info.dir_name
        results.append(stat)
    return results


def _setup_logfile(tag: str) -> None:
    import datetime

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fh = logging.FileHandler(config.LOG_DIR / f"{tag}_{ts}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)
