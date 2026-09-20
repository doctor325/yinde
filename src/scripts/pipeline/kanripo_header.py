"""Kanripo 文件头（org-mode 元数据）解析。

通用键 + 可选键 + 未知键三层模型：
- 通用: TITLE/DATE/ID/JUAN/BASEEDITION
- 可选: WITNESS/FILE/CAT(随家族出现)
- 未知: 其他任何 #+ 键一律保留，不丢弃

头 = 文件开头连续以 # 开头的行块（遇到第一个非 # 行结束）。
正文中段出现的 # 注释行（如 # src:）不属于文件头，由 segmentation 处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

PROPERTY_RE = re.compile(r"^#\+PROPERTY:\s*([A-Za-z0-9_]+)\s*(.*)$")
KEY_RE = re.compile(r"^#\+([A-Za-z0-9_]+):\s*(.*)$")

KNOWN_PROPERTY_KEYS = {"ID", "JUAN", "BASEEDITION", "WITNESS", "FILE", "CAT"}


@dataclass
class FileHeader:
    """一个 Kanripo txt 文件的解析结果。

    metadata:   键为规范化键名（TITLE/DATE/ID/JUAN/BASEEDITION/WITNESS/FILE/CAT，
                未知键按 #+ 后原样大写保留）
    raw_header: 头部原文整块（含 #+TITLE 等所有行），永远保留，供追溯
    mode_line:  首行 `# -*- mode: ... -*-` 原文或 None
    """

    metadata: dict = field(default_factory=dict)
    raw_header: str = ""
    mode_line: Optional[str] = None


def split_header(text: str) -> tuple[FileHeader, str]:
    """text -> (FileHeader, 剩余正文全文)。

    约定：头 = 文件开头的连续 # 行。空行/正文行出现即视为头结束。
    """
    lines = text.splitlines()
    header_lines: list[str] = []
    body_start = 0
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            header_lines.append(ln)
        else:
            body_start = i
            break
    else:
        body_start = len(lines)

    header_text = "\n".join(header_lines)
    body = "\n".join(lines[body_start:]) if body_start < len(lines) else ""

    meta: dict = {}
    mode_line = None
    for ln in header_lines:
        if ln.startswith("# -*- mode"):
            mode_line = ln
            continue
        m = PROPERTY_RE.match(ln)
        if m:
            meta[m.group(1)] = m.group(2).strip()
            continue
        m = KEY_RE.match(ln)
        if m:
            meta[m.group(1)] = m.group(2).strip()

    h = FileHeader(metadata=meta, raw_header=header_text, mode_line=mode_line)
    return h, body


def file_no_of(name: str) -> Optional[int]:
    """KRxxxx_NNN.txt -> NNN；其他（Readme.org 等）返回 None。"""
    m = re.search(r"_(\d+)\.txt$", name)
    return int(m.group(1)) if m else None


# BASEEDITION 字面值 → 家族名。WYG 是 6.3 补的（6.2 的陈旧平行实现漏了它）：新增的
# 12 部正史全是文淵閣四庫全書本，漏判会让整族文件 family=None → 层默认 unknown、
# 标题模式一条都不匹配，整本书 0 section。
FAMILY_BY_EDITION = {"tls": "tls", "SBCK": "sbck", "WYG": "wyg"}


def family_of_edition(edition: Optional[str]) -> Optional[str]:
    """BASEEDITION 字面值 → 家族名；未知一律 None（不猜）。

    只保留这一处映射。6.3 之前这段 if/else 抄了三份（inventory / segmentation /
    family_of），其中 family_of 那份漏了 WYG —— 加一本四庫全書本正史时，只要有人
    用了错的那份，整本书的层默认与标题模式就全部落空。
    """
    return FAMILY_BY_EDITION.get((edition or "").strip())


def family_of(header: FileHeader) -> Optional[str]:
    """按文件头判断文本家族：'tls' | 'sbck' | 'wyg' | None(未知，不猜)。"""
    return family_of_edition(header.metadata.get("BASEEDITION"))
