"""Step 7 — 数据验证：数据库 vs library 原文逐文件对账。

核心手段是"字符守恒"：把某文件入库的全部记录按 (row_no, char_start) 次序拼回，
应与 library 原文去掉（头部行 + 空行）后逐字符相等。该检查一次涵盖：
¶、<pb:...>、&KR...; 等全部行内字符的保留（任一丢失/改序即失败）。

另含独立检查：
- sha256 对账（files.origin_path 真实存在且哈希一致 → 追溯性）
- 头部 metadata 对账（files.meta_json vs 重新解析的头）
- pb 标记语法（入库文本中 <pb:...> 是否全部符合严格格式）
- KR 码语法（入库文本中 &KR...; 是否全部符合格式）

已知的、设计上允许的差异（差异类别，报告中会给出 原因/影响/下一步）：
  skip_header  —— 头部 # 行不入 passages（原件在 files.meta_json + library 原文）
  skip_blank   —— 空行不入库（原文仍可在 raw 视图 / library 原文看到）
其余任何不一致都会在 mismatch 中给出首个差异位置与上下文，便于定位。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from . import config
from .kanripo_header import split_header
from .sqlite_store import connect

log = logging.getLogger("pipeline")

PB_SPLIT_RE = config.PB_SPLIT_RE          # 严格格式
PB_ANY_RE = config.PB_ANY_RE
KR_RE = config.KR_CODE_RE


def _decode(raw: bytes) -> tuple[str, bool]:
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), True


def _is_blank(line: str) -> bool:
    # 与 segmentation.empty_line 同一判定：无内容且无 ¶
    return not line.strip() and config.PARA_CHAR not in line


def validate_file(cur, frow, library_root: Path) -> dict:
    """验证单个文件。frow: sqlite Row（files 表行）。返回该文件结果 dict。"""
    out = {
        "file": frow["file_name"], "book": frow["book_id"],
        "path": frow["origin_path"],
        "sha256_ok": False, "meta_ok": False, "rows": 0,
        "body_ok": False, "diff_n": 0, "diff_pos": None, "diff_ctx": None,
        "skip_blank": 0, "pb_bad": 0, "kr_bad": 0, "notes": [],
    }
    path = library_root / frow["origin_path"]
    if not path.is_file():
        out["notes"].append("origin 文件缺失（library 被移动/改名？）")
        return out
    raw = path.read_bytes()
    out["sha256_ok"] = (hashlib.sha256(raw).hexdigest() == frow["sha256"])
    text, dec_err = _decode(raw)
    if dec_err:
        out["notes"].append("decode_error(用 U+FFFD 替换解码，与解析一致)")
    header, body = split_header(text)

    # --- 头部 metadata 对账 ---
    db_meta = json.loads(frow["meta_json"] or "{}") or {}
    diff_keys = {k for k in set(db_meta) | set(header.metadata)
                 if db_meta.get(k) != header.metadata.get(k)}
    out["meta_ok"] = not diff_keys
    if diff_keys:
        out["notes"].append("头部 metadata 与 library 重解析不一致: " + ",".join(sorted(diff_keys)))

    # --- 取本文件记录（行号 + 字符偏移次序）---
    rows = cur.execute(
        "SELECT row_no, char_start, char_end, kind, layer, status, text_orig "
        "FROM passages WHERE file_id=? ORDER BY row_no, COALESCE(char_start,0), seq",
        (frow["file_id"],)).fetchall()
    out["rows"] = len(rows)

    # --- 语法抽查：pb / KR ---
    for r in rows:
        t = r["text_orig"]
        for m in PB_ANY_RE.finditer(t):
            if not PB_SPLIT_RE.match(m.group(0)):
                out["pb_bad"] += 1
        for m in KR_RE.finditer(t):
            if not re.fullmatch(r"&KR[A-Za-z0-9]+;", m.group(0)):
                out["kr_bad"] += 1

    # --- 字符守恒：重建 vs 预期 ---
    body_lines = body.splitlines()
    expected = [ln for ln in body_lines if not _is_blank(ln)]
    out["skip_blank"] = len(body_lines) - len(expected)
    # 逐行拼接：同一 row_no 的多条记录（SBCK 括号切分）连续拼接不插 \n；
    # 换 row_no 之间插 \n（跨行块记录 text_orig 自带 \n，行号已跳变，天然对齐）
    recon_parts: list[str] = []
    prev_rno = None
    for r in rows:
        if prev_rno is not None and r["row_no"] != prev_rno:
            recon_parts.append("\n")
        recon_parts.append(r["text_orig"])
        prev_rno = r["row_no"]
    recon = "".join(recon_parts)
    exp = "\n".join(expected)
    if recon == exp:
        out["body_ok"] = True
    else:
        out["diff_n"] = 1
        # 定位首个差异（含前缀情形：一方先结束）
        mlen = 0
        n = min(len(recon), len(exp))
        while mlen < n and recon[mlen] == exp[mlen]:
            mlen += 1
        out["diff_pos"] = mlen
        out["diff_ctx"] = ("recon:", repr(recon[max(0, n - 30):n + 40]),
                           "expect:", repr(exp[max(0, n - 30):n + 40]))
    return out


def run_validation(db_path: Path | None = None, quiet: bool = False) -> dict:
    """全量验证：每文件 sha256/meta/字符守恒/语法。返回汇总；写 validation.json。"""
    conn = connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    lib = config.LIBRARY_DIR

    files = cur.execute(
        "SELECT f.file_id, f.file_name, f.book_id, f.origin_path, f.sha256, f.meta_json "
        "FROM files f ORDER BY f.book_id, f.file_no").fetchall()

    per_file, bad = [], []
    for f in files:
        r = validate_file(cur, f, lib)
        per_file.append(r)
        if not (r["sha256_ok"] and r["meta_ok"] and r["body_ok"]
                and r["pb_bad"] == 0 and r["kr_bad"] == 0):
            bad.append(r)

    summary = {
        "files": len(files),
        "files_ok": len(files) - len(bad),
        "sha256_ok": sum(1 for r in per_file if r["sha256_ok"]),
        "meta_ok": sum(1 for r in per_file if r["meta_ok"]),
        "body_ok": sum(1 for r in per_file if r["body_ok"]),
        "pb_bad": sum(r["pb_bad"] for r in per_file),
        "kr_bad": sum(r["kr_bad"] for r in per_file),
        "skip_blank_total": sum(r["skip_blank"] for r in per_file),
        "bad_files": [{
            "book": r["book"], "file": r["file"],
            "sha256_ok": r["sha256_ok"], "meta_ok": r["meta_ok"],
            "body_ok": r["body_ok"], "pb_bad": r["pb_bad"],
            "kr_bad": r["kr_bad"], "notes": r["notes"],
            "diff_ctx": r["diff_ctx"],
        } for r in bad],
    }
    if not quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    config.METADATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.METADATA_DIR / "validation.json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "per_file": [
            {k: v for k, v in r.items() if k != "diff_ctx"} for r in per_file]},
            fh, ensure_ascii=False, indent=2)
    conn.close()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_validation()
