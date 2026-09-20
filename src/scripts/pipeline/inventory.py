"""Step 1 — Inventory：自动发现 library 下的书与文件，只读扫描。

产出（写入 data/metadata/，由 run_all 编排写盘）：
- books.json       每书一行汇总（id/目录名/标题/家族/文件数/总大小）
- files.json       逐文件：sha256、字节、行数、标记计数、头部 metadata
- import_runs.json 本次导入流水（library 路径、时间、文件数）
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .config import (
    KR_CODE_RE,
    PB_ANY_RE,
    SRC_LINE_RE,
    natural_sort_key,
)
from .kanripo_header import FileHeader, family_of_edition, file_no_of, split_header

# 正文统计用的“页标记”计数：<pb:...> 出现次数（与行位置无关）
PB_COUNT_RE = PB_ANY_RE


@dataclass
class BookInfo:
    dir_name: str
    path: Path
    files: list = field(default_factory=list)  # FileInfo
    id_hint: str = ""
    title: str = ""
    family: str = ""  # tls | sbck | wyg | ""
    edition: str = ""

    def to_dict(self) -> dict:
        n_txt = sum(1 for f in self.files if f.kind == "txt")
        return {
            "dir_name": self.dir_name,
            "book_id": self.id_hint,
            "title": self.title,
            "family": self.family or None,
            "edition": self.edition or None,
            "txt_files": n_txt,
            "readme_files": sum(1 for f in self.files if f.kind == "readme"),
            "total_bytes": sum(f.byte_size for f in self.files),
        }


@dataclass
class FileInfo:
    book_dir: str
    path: Path           # library 内绝对路径（原始位置，供追溯）
    rel_path: str        # 相对 library 的路径，如 kanripo/guoyu/KR2e0001_001.txt
    file_name: str
    kind: str            # "txt" | "readme"
    file_no: int | None  # KRxxxx_NNN.txt 的 NNN；Readme 为 None
    sha256: str = ""
    byte_size: int = 0
    line_count: int = 0
    counts: dict = field(default_factory=dict)          # para/pb/src/kr 等
    header: dict = field(default_factory=dict)          # metadata 键值
    raw_header: str = ""
    decode_error: bool = False

    def to_dict(self) -> dict:
        return {
            "book_dir": self.book_dir,
            "rel_path": self.rel_path.replace("\\", "/"),
            "path": str(self.path),
            "file_name": self.file_name,
            "kind": self.kind,
            "file_no": self.file_no,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "line_count": self.line_count,
            "counts": self.counts,
            "metadata": self.header,
            "raw_header": self.raw_header,
            "decode_error": self.decode_error,
        }


def discover_books(library: Path) -> list[Path]:
    """自动发现书目录：library 下含 .txt 的直接子目录（不硬编码书名）。"""
    books = []
    if not library.is_dir():
        raise FileNotFoundError(f"library 不存在: {library}")
    for child in sorted(library.iterdir()):
        if child.is_dir() and list(child.glob("*.txt")):
            books.append(child)
    return books


def scan_file(path: Path) -> FileInfo:
    """只读扫描单个文件：sha256、头部、标记计数。"""
    book = path.parent.name
    rel = str(path.relative_to(config.LIBRARY_DIR)).replace("\\", "/")
    finfo = FileInfo(
        book_dir=book,
        path=path,
        rel_path=rel,
        file_name=path.name,
        kind="txt" if path.suffix.lower() == ".txt" else "readme",
        file_no=file_no_of(path.name),
        byte_size=path.stat().st_size,
    )
    if finfo.kind != "txt":
        finfo.sha256 = sha256_of(path)
        finfo.line_count = 0
        return finfo

    raw = path.read_bytes()
    finfo.sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        finfo.decode_error = True
    finfo.line_count = text.count("\n") + (1 if text else 0)

    header, body = split_header(text)
    finfo.header = header.metadata
    finfo.raw_header = header.raw_header

    all_text = text  # 计数用全文（头部行中的标记几乎不存在，但计数以全文件为准）
    finfo.counts = {
        "para": all_text.count(config.PARA_CHAR),
        "pb": len(PB_COUNT_RE.findall(all_text)),
        "src": len(re.findall(r"^#\s*src:", all_text, re.M)),
        "kr": len(KR_CODE_RE.findall(all_text)),
    }
    return finfo


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_library(library: Path | None = None) -> list[BookInfo]:
    """扫描 library → BookInfo 列表（不写盘）。"""
    lib = Path(library) if library else config.LIBRARY_DIR
    books: list[BookInfo] = []
    for bdir in discover_books(lib):
        files = []
        for p in sorted(bdir.iterdir(), key=natural_sort_key):
            if p.is_file() and (p.suffix.lower() == ".txt" or p.name.lower() == "readme.org"):
                files.append(scan_file(p))
        texts = [f for f in files if f.kind == "txt"]
        bi = BookInfo(dir_name=bdir.name, path=bdir, files=files)
        if texts:
            h = texts[0].header
            bi.id_hint = h.get("ID", "")
            bi.title = h.get("TITLE", "")
            ed = (h.get("BASEEDITION") or "").strip()
            bi.edition = ed
            # 家族 = BASEEDITION（kanripo 自己的属性，不是我们的猜测）：wyg = 文淵閣
            # 四庫全書，形态与 tls/SBCK 都不同。取**首个 txt 文件**的头（同一本书的
            # 文件同族）；映射只有一份，见 kanripo_header.family_of_edition。
            bi.family = family_of_edition(ed)
        books.append(bi)
    return books


def run_inventory(library: Path | None = None, write: bool = True) -> dict:
    """Step 1 执行：扫描并（默认）写 metadata 三件套，返回汇总统计。"""
    config.ensure_data_dirs()
    books = scan_library(library)

    run = {
        "run_id": uuid.uuid4().hex,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "library_path": str(config.LIBRARY_DIR),
        "books": [b.to_dict() for b in books],
        "txt_file_count": sum(1 for b in books for f in b.files if f.kind == "txt"),
    }

    if write:
        with open(config.METADATA_DIR / "books.json", "w", encoding="utf-8") as fh:
            json.dump([b.to_dict() for b in books], fh, ensure_ascii=False, indent=2)
        with open(config.METADATA_DIR / "files.json", "w", encoding="utf-8") as fh:
            json.dump([f.to_dict() for b in books for f in b.files],
                      fh, ensure_ascii=False, indent=2)
        run["completed_at"] = datetime.now(timezone.utc).isoformat()
        run["status"] = "ok"
        with open(config.METADATA_DIR / "import_runs.json", "w", encoding="utf-8") as fh:
            json.dump([run], fh, ensure_ascii=False, indent=2)

    summary = {
        "library": str(config.LIBRARY_DIR),
        "books": len(books),
        "txt_files": run["txt_file_count"],
        "by_book": {b.dir_name: len([f for f in b.files if f.kind == "txt"]) for b in books},
    }
    return summary
