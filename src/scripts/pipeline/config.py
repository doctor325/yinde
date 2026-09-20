"""路径与目录发现规则。

原则：
- 不硬编码五本书名/文件名；程序自动扫描 library 目录。
- library 只读；所有产物写入 HistoryAI/data 下。
- 环境变量可覆盖默认路径（HISTORY_LIBRARY / HISTORY_DATA）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 包位置: HistoryProject/HistoryAI/scripts/pipeline/
PIPELINE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = PIPELINE_DIR.parent
HISTORY_AI_DIR = SCRIPTS_DIR.parent          # HistoryProject/HistoryAI
PROJECT_ROOT = HISTORY_AI_DIR.parent         # HistoryProject

LIBRARY_DIR = Path(os.environ.get("HISTORY_LIBRARY", PROJECT_ROOT / "corpus" / "kanripo")).resolve()
DATA_DIR = Path(os.environ.get("HISTORY_DATA", HISTORY_AI_DIR / "data")).resolve()

METADATA_DIR = DATA_DIR / "metadata"
PROCESSED_DIR = DATA_DIR / "processed"
DATABASE_DIR = DATA_DIR / "database"
LOG_DIR = DATA_DIR / "logs"

DB_PATH = DATABASE_DIR / "history.db"


def ensure_data_dirs() -> None:
    for d in (METADATA_DIR, PROCESSED_DIR, DATABASE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 通用正则

# <pb:KR2e0001_SBCK_000-1b>  /  <pb:KR2a0001_tls_100-2a>
PB_SPLIT_RE = re.compile(r"^<pb:([A-Za-z0-9]+)_([A-Za-z0-9]+)_([0-9]+)-([0-9]+)([ab])>$")
PB_ANY_RE = re.compile(r"<pb:([^>]*)>")

# 断句/结构字符（tls 系句末符号；SBCK 系稀疏出现）
PARA_CHAR = "¶"  # ¶

# Kanripo 缺字码，如 &KR0632;
KR_CODE_RE = re.compile(r"&KR[A-Za-z0-9]+;")

# tls 系出处注释行
SRC_LINE_RE = re.compile(r"^#\s*src:\s*(.*)$")

# org 结构标题
ORG_H2_RE = re.compile(r"^\*\*\s+(\d+)\s*(.*)$")        # ** 1 紀 / ** 1 《堯典》 / ** 1 隱公
ORG_H3_RE = re.compile(r"^\*\*\*\s*([0-9.]+)\s*(.*)$")  # *** 2.1　《三代世表》


def natural_sort_key(path: Path) -> tuple:
    """按 Kanripo 文件名 KRxxxx_NNN.txt 的数字序排序；非 txt 排最后。"""
    m = re.search(r"_(\d+)\.txt$", path.name)
    if m:
        return (0, int(m.group(1)))
    return (1, path.name)
