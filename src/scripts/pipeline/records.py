"""解析记录模型（JSONL 一行 = 一条记录）。

kind:
  page     只有 <pb:...> 的页码行（正文中的页码标记保留在文本内，不单独成记录）
  heading  结构标题（org ** / 明文篇题 / 左传 A/B 卷题等）
  comment  # 开头的注释块（# src: / # dating: 等，整块合并成一条）
  passage  正文单元（tls 一句一行 / SBCK 一行或行内切出片段）

layer（语义层，宁可 unknown/pending 不猜）:
  main / preface / appendix / structure / commentary_candidate / unknown

status:
  ok / pending_commentary / pending_section / pending_line

section_method（第六点三阶段，sections 的溯源字段）:
  header           语料自带的显式结构标记（org `** N X`、`#+PROPERTY` 段名）
  title            明文标题正则命中（WYG 篇题 / 國語卷首题 / 戰國策卷题 / 左传卷题）
  first-occurrence 没有标题证据，靠「section 字段首次出现」记下来的（兜底）
  interval         由区间模型补出（当前不产生，留给将来按区间切卷）
  metadata         由目录/文件头元数据声明
  override         由语料目录 file_overrides 人工点名
  置信度阶梯：1.0 人工/属性，0.9 结构正则，0.8 显式结构标记，0.6 弱形态，0.5 兜底。
  审计表按 method 出直方图——整本书若全是 first-occurrence，等于篇名是靠猜的，
  这比一个覆盖率百分比更早暴露问题。
"""
from __future__ import annotations

from dataclasses import dataclass, field

LAYER_VALUES = {"main", "preface", "appendix", "backmatter", "toc",
                "structure", "commentary_candidate", "unknown"}
STATUS_VALUES = {"ok", "pending_commentary", "pending_section", "pending_line"}
SECTION_METHODS = {"header", "title", "first-occurrence", "interval",
                   "metadata", "override"}
# 建库时的兜底档（首现即记，无标题证据）：sqlite_store 与审计共用同一个数
DEFAULT_SECTION_METHOD = "first-occurrence"
DEFAULT_SECTION_CONFIDENCE = 0.5
KIND_VALUES = {"page", "heading", "comment", "part", "noise", "passage"}


@dataclass
class Record:
    kind: str = "passage"
    layer: str = "unknown"
    status: str = "pending_line"
    row_no: int = 0
    juan: str | None = None        # 语义卷（如左传：隱公；有把握才填）
    section: str | None = None     # 当前节/篇题（如 堯典 / 五帝本紀 / 周語上第一候选）
    subsection: str | None = None  # 左传 A/B 条目号等
    division: str | None = None    # 史记类目 紀/表/書/世家/傳
    ab: str | None = None          # 左传 A(經)/B(傳)
    text_orig: str = ""
    normalized_text: str | None = None
    char_start: int | None = None  # 在原始行内的字符偏移（SBCK 行内切分才有意义）
    char_end: int | None = None
    pb: dict | None = None          # 本单元首个 <pb:...> 结构化 {raw,book_id,edition,block,page,side}
    pb_raw: str = ""
    pb_last_raw: str = ""
    special_chars: list = field(default_factory=list)
    source_reference: dict | None = None  # {raw, src_text, prefix, section_ref, keys:[...]}
    notes: list = field(default_factory=list)
    # 这篇题是**怎么认出来的**（只在「本记录建立起一个新 section」时有值；
    # 后续继承上下文的记录留空，由 sqlite_store 在首现时走兜底档）
    section_method: str | None = None
    section_confidence: float | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "layer": self.layer,
            "status": self.status,
            "row_no": self.row_no,
            "juan": self.juan,
            "section": self.section,
            "subsection": self.subsection,
            "division": self.division,
            "ab": self.ab,
            "text_orig": self.text_orig,
            "normalized_text": self.normalized_text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "pb": self.pb,
            "pb_raw": self.pb_raw,
            "pb_last_raw": self.pb_last_raw,
            "special_chars": self.special_chars,
            "source_reference": self.source_reference,
            "notes": self.notes,
            "section_method": self.section_method,
            "section_confidence": self.section_confidence,
        }
