"""Step 3/4 — 分段与结构解析：一个 txt 文件 -> Record 列表。

容错要点（对应实测差异）：
- tls 系（尚書/左傳/史記）：句行 + ¶；org 标题（** / *** 形态不一）；左传 A/B 条目；
- SBCK 系（國語/戰國策）：整行连排 + 行内圆括号注候选（/ 为原刊行分隔）；
- 序/附录由文件层默认决定（structure.file_layer_defaults）；
- 一切拿不准 → pending_* / unknown，绝不猜测。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import catalog, config
from .kanripo_header import FileHeader, family_of, file_no_of, split_header
from .records import Record
from .title_patterns import (
    GUOYU_HEAD_RE,
    GUOYU_SECTION_RE,
    PATTERNS,
    WYG_JUAN_CONFIDENCE,
    WYG_SIKU_RE,
    ZHANGUOCE_CHAPTERS_RE,
    ZHANGUOCE_JUAN_RE,
    ZHANGUOCE_SECTION_RE,
    ZHANGUOCE_STATES,
    build_wyg_juan_re,
    normalize_label,
)
from .structure import (
    classify_comment_line,
    clean_title,
    file_layer_defaults,
    find_pb,
    is_org_heading,
    make_normalized,
    part_layer_of,
    parse_pb,
    pending_split_parens,
    strip_pb,
    title_candidates,
    zuozhuan_classify,
)

# 6.2 的**冻结快照**，只在 catalog 不可用时兜底（回滚路径，同 structure 那份）。
# 真源是 corpus_catalog.json 各书的 h2_role / ab_system / title_handler —— 加新书
# 请改 JSON，别在这里加行。
BUILTIN_DISPATCH = {
    # “** N X” 的语义角色（经实际扫描确认）：shiji X=类目、shangshu X=篇题、
    # zuozhuan X=公名
    "shiji": {"h2_role": "division"},
    "shangshu": {"h2_role": "section"},
    "zuozhuan": {"h2_role": "juan", "ab_system": "zuozhuan"},
    "zhanguoce": {"title_handler": "zhanguoce"},
}
BUILTIN_TITLE_PATTERNS = {
    "wyg": (PATTERNS["wyg_di_n"],),
    "sbck": (PATTERNS["guoyu_head"], PATTERNS["guoyu_volume"],
             PATTERNS["zhanguoce_juan"], PATTERNS["zhanguoce_state"]),
    "tls": (PATTERNS["shiji_plain"],),
}

# section 溯源：只在**建立起一条新 section 的记录**上标注（后续继承上下文的记录
# 留空，由 sqlite_store 首现时走兜底档）。取值与词表见 records.SECTION_METHODS；
# 正则形态的置信度取自 title_patterns 里那条模式自己的声明，不在调用点另写一个数。
def mark_section(rec: Record, method: str, confidence: float) -> Record:
    rec.section_method = method
    rec.section_confidence = confidence
    return rec


# 文件内分段（部分）段名 → 语义层。规则表在 structure.part_layer_of（那一层
# 要给 file_layer_defaults 用，放这儿会绕成循环 import），此处只管调用。

# 标题形态与判据集中在 title_patterns.py（唯一注册处，见该模块头注释）：
# 原先内联在这里的 GUOYU_* / ZHANGUOCE_* / WYG_* 正则与 CJK_CHAR 已原样搬过去，
# 本文件与语料目录 corpus_catalog.json 引用的都是同一批模式名。


def empty_line(line: str) -> bool:
    return not line.strip() and config.PARA_CHAR not in line


def only_pb_line(line: str) -> bool:
    t = strip_pb(line).replace(config.PARA_CHAR, "")
    return "<pb:" in line and not t.strip()


class FileSegmenter:
    """单个 txt 的状态机。逐行：页码继承 → 注释块合并 → 标题/条目/正文分流。"""

    def __init__(self, header: FileHeader, book_dir: str, file_no: int | None, text: str):
        self.book_dir = book_dir
        self.file_no = file_no
        self.meta = header.metadata
        self.family = family_of(header)   # 唯一判据在 kanripo_header（6.3 起）
        # ---- 解析规则按书查表（6.3-C①）----
        # 书在 catalog 里就照它的声明走；不在（或 catalog 不可用）→ 6.2 的冻结
        # 快照，行为与扩容前一致。加一本新书＝加一条 JSON，不改这里的代码。
        cat = catalog.try_load()
        book = cat.by_dir(book_dir) if cat is not None else None
        builtin = BUILTIN_DISPATCH.get(book_dir, {}) if book is None else {}
        self.h2_role = (book.h2_role if book is not None else builtin.get("h2_role")) \
            or "section"
        self.ab_system = book.ab_system if book is not None else builtin.get("ab_system")
        self.title_handler = (book.title_handler if book is not None
                              else builtin.get("title_handler"))
        self.title_res = (cat.patterns_for(book) if book is not None
                          else BUILTIN_TITLE_PATTERNS.get(self.family, ()))
        # 卷题是不是**篇名粒度**由书声明：三國志正文里没有篇题行（傳名只出现在卷首
        # 的卷目行里），卷题就是它最细的篇名，不声明就整卷无 section。前漢書/後漢書
        # 每卷跟着若干 `第N` 篇题，卷题只用来推进卷次，维持原行为（默认 False）。
        self.juan_as_section = bool(book.juan_as_section) if book is not None else False
        # WYG 卷题用 TITLE 属性拼（前漢書/後漢書各一条，不硬编码书名）；形态不同的
        # 书在 catalog 的 juan_prefixes 里声明前缀（三國志写 `魏志卷一`/`蜀志卷一`）
        self.book_title = (self.meta.get("TITLE") or "").strip()
        self.wyg_juan_re = build_wyg_juan_re(
            self.book_title, list(book.juan_prefixes) if book is not None else None)
        self.lines = text.splitlines()
        self.records: list[Record] = []
        self.cur_pb_raw = ""
        self.cur_pb = None
        self._reset_context()

    # ---- 语义上下文（文件内随标题推进）----
    def _reset_context(self) -> None:
        layer, status, note = file_layer_defaults(
            self.book_dir, self.file_no, self.family, self.meta)
        self.def_layer, self.def_status, self.layer_note = layer, status, note
        self.part_layer: str | None = None   # 文件内分段（FILE 段名）的层
        self.part_label: str | None = None
        self.juan: str | None = None
        self.section: str | None = None
        # section 标签 → (method, confidence)：由首个携带该标签的记录认领。
        # 只有 org 标题（h2/h3）走这条，见 _mark_for_section。
        self.section_marks: dict[str, tuple[str, float]] = {}
        self.division: str | None = None
        self.zb_year: str | None = None      # 左传：当前公年份（如 '1.1'）
        self.ab: str | None = None           # 左传：A(經)/B(傳)
        # 戰國策：本卷卷首题声明过的国名，以及「现在可以认一个国别题」的门
        # （卷首开一次，此后每见一行 `凡N章` 再开一次，认过就关）。
        self.zc_states: tuple[str, ...] = ()
        self.zc_open = True

    def _mark_for_section(self, label: str, method: str, confidence: float) -> None:
        """登记**这条 section** 的检测来源，交给首个携带它的记录（见 _base）。

        org 标题是先改 `self.section`、再让下一条记录带上新标签，所以标注打不到
        标题行自己身上——直接 `mark_section(标题行)` 会静默失效（6.3-C 实测：
        尚書/史記 的 header 标注一条都没进库，全被记成 first-occurrence 0.5）。
        """
        self.section_marks[label] = (method, confidence)

    # ---- 通用记录构造 ----
    def _base(self, row_no: int, text_orig: str, kind: str, layer: str | None = None,
              status: str | None = None) -> Record:
        layer = layer if layer is not None else (self.part_layer or self.def_layer)
        status = status if status is not None else self.def_status
        rec = Record(kind=kind, layer=layer, status=status, row_no=row_no,
                     text_orig=text_orig)
        rec.juan = self.juan
        rec.section = self.section
        rec.division = self.division
        rec.ab = self.ab
        # section 溯源认领：首个携带该标签的记录就是 sqlite_store 建 sections 行时
        # 那一条（判据同为 rs != cur_section），两处口径一致。认领即出队——只有**声明
        # 该 section 的那一条**带标注，与其余分支 mark_section(标题行) 的粒度相同，
        # 不给后续同 section 的正文行重复挂标（那会让 JSONL 白胖一圈）。
        # 直接 mark_section 的调用点在本函数返回后覆盖这里，显式标注优先。
        if rec.section:
            mark = self.section_marks.pop(rec.section, None)
            if mark:
                rec.section_method, rec.section_confidence = mark
        # 页码：行内 pb 优先，否则继承前文最近 pb
        pbs = find_pb(text_orig)
        if pbs:
            raw = pbs[0][0]
            if rec.pb_raw == "" or pbs[0][1] == 0:
                rec.pb_raw = raw
                rec.pb = parse_pb(raw)
            if len(pbs) > 1:
                rec.pb_last_raw = pbs[-1][0]
        if not rec.pb_raw:
            rec.pb_raw = self.cur_pb_raw
            rec.pb = parse_pb(self.cur_pb_raw) if self.cur_pb_raw else None
        # 更新“最近页码”游标（整行扫描）
        for raw, _ in pbs:
            self.cur_pb_raw = raw
            self.cur_pb = parse_pb(raw)
        rec.special_chars = config.KR_CODE_RE.findall(text_orig)
        rec.normalized_text = make_normalized(text_orig, kind, layer)
        return rec

    # ---- 主循环 ----
    def run(self) -> list[Record]:
        lines = self.lines
        n = len(lines)
        # 绝对行号 = 1 起；头部行跳过（本类从 body 开始，行号信息由调用方传入 first_body_line）
        i = self.first_line_index - 1
        while i < n:
            line = lines[i]
            row_no = i + 1
            stripped = line.strip()

            # 1) 文件内分段块（#+PROPERTY: ... FILE SB..-後跋./-目録. 等，
            #    只出现在 SBCK 系首文件中，标志序/跋/目録等部分的开始）
            if line.startswith("#+"):
                block = [line]
                j = i + 1
                while j < n and lines[j].startswith("#+"):
                    block.append(lines[j])
                    j += 1
                self._emit_part_block(row_no, block)
                i = j
                continue

            # 1b) 页码行（只有 <pb:...> 及可能跟的 ¶）
            if "<pb:" in line and only_pb_line(line):
                rec = self._base(row_no, line, "page")
                rec.layer = "structure"
                rec.status = "ok"
                self.records.append(rec)
                i += 1
                continue

            # 1c) 空白残行（内容仅为空格与 ¶，如 shiji '　　¶' 页底残行）
            if line.strip().replace(" ", "").replace("　", "") == config.PARA_CHAR:
                rec = self._base(row_no, line, "noise")
                rec.layer = "structure"
                rec.status = "ok"
                self.records.append(rec)
                i += 1
                continue

            # 2) 注释块（# 开头连续行合并；src/dating 等键原样收集）
            if line.startswith("#"):
                block = [line]
                j = i + 1
                while j < n and lines[j].startswith("#"):
                    block.append(lines[j])
                    j += 1
                self._emit_comment_block(row_no, block)
                i = j
                continue

            # 3) 空行（保留为 passage？空行不入记录，避免噪声；原文在 raw 视图仍可见）
            if empty_line(line):
                i += 1
                continue

            # 4) 正文/标题分流
            self._emit_content(line, row_no)
            i += 1
        return self.records

    # ---- 文件内分段块 ----
    def _emit_part_block(self, row_no: int, block: list[str]) -> None:
        text_orig = "\n".join(block)
        rec = self._base(row_no, text_orig, "part")
        rec.layer = "structure"
        rec.status = "ok"
        rec.normalized_text = None
        # 段名：FILE 属性末段（SBCK 系）优先，其次是 JUAN 整值（WYG 系）。
        # 两者都只在属性块里、由 kanripo 自己写的，取谁都算有据可依。
        label = None
        for ln in block:
            m = re.match(r"^#\+PROPERTY:\s*FILE\s+(.+)$", ln)
            if m:
                tail = m.group(1).strip()
                if "-" in tail:
                    label = tail.rsplit("-", 1)[1].rstrip(".").strip()
        if label is None:
            for ln in block:
                m = re.match(r"^#\+PROPERTY:\s*JUAN\s+(.+)$", ln)
                if m:
                    label = m.group(1).strip()
        # 认得出层就换段；认不出也**照样把段层还原成文件默认**——WYG 一卷里
        # `卷一上考證`(appendix) 之后紧接 `卷一下`(无层)，不还原的话下卷正文
        # 会一路带着 appendix 走到底（实测会多出 20 万字的「附录」）。
        self.part_label = label
        self.part_layer = part_layer_of(label)
        if label and self.part_layer:
            rec.notes.append(f"文件内分段开始: {label} → {self.part_layer}")
        elif label:
            rec.notes.append(f"文件内分段: {label}（认不出层，还原为文件默认）")
        else:
            rec.notes.append("未识别段名，按普通元数据块保留")
        self.records.append(rec)

    # ---- 注释块 ----
    def _emit_comment_block(self, row_no: int, block: list[str]) -> None:
        text_orig = "\n".join(block)
        rec = self._base(row_no, text_orig, "comment")
        keys = []
        src_refs = []
        for ln in block:
            k, res = classify_comment_line(ln)
            keys.append(k)
            if res:
                src_refs.append(res)
        rec.notes.append("comment_keys:" + ",".join(sorted(set(keys))))
        if src_refs:
            first = src_refs[0]
            rec.source_reference = {
                "raw": first["raw"],
                "src_text": first["src_text"],
                "prefix": first["prefix"],
                "section_ref": first["section_ref"],
                "src_count": len(src_refs),
            }
        rec.layer = "structure"
        rec.status = "ok"
        rec.normalized_text = None
        self.records.append(rec)

    # ---- 正文行/标题行 ----
    def _emit_content(self, line: str, row_no: int) -> None:
        stripped = line.strip()
        # 4a) org 标题（tls 系为主；SBCK 异常出现也保留为结构行，不改正文）
        lvl, _code, _title = is_org_heading(line)
        if lvl:
            self._handle_org_heading(lvl, _code, line, row_no)
            return

        # 4b) 左传 A/B 体系（先于明文标题，避免 'A1.1《…》' 误入普通标题）
        # 是否 A/B 体系由目录声明（ab_system），不是书名分支
        if self.ab_system == "zuozhuan":
            zk, zextra = zuozhuan_classify(line)
            if zk == "heading":
                ab = zextra["ab"]
                code = zextra.get("code")
                if code is None:
                    rec = self._mk_heading(line, row_no)
                    rec.ab = ab
                    rec.notes.append(f"左传 {ab} 类卷题")
                    self._append(rec)
                    return
                # A1.1《隱公元年經》 式卷题
                title = zextra.get("title", "")
                rec = self._mk_heading(line, row_no)
                rec.ab = ab
                rec.section = clean_title(title) if title else rec.section
                self.zb_year = code
                self.ab = ab
                if title:
                    # 年份卷题下推为上下文：其下条目 sec=隱公元年經（便于核对归属）
                    self.section = rec.section
                    mark_section(rec, "title", 0.9)
                if ab == "A":
                    rec.notes.append("春秋經卷题")
                else:
                    rec.notes.append("左傳卷题")
                self._append(rec)
                return
            if zk == "passage":
                rec = self._mk_passage(line, row_no)
                rec.ab = zextra["ab"]
                rec.subsection = zextra["code"]
                rec.section = self.section or self.juan
                self.ab = zextra["ab"]
                self._append(rec)
                return

        # 4c) 明文篇题候选（史记 1.1《五帝本紀》 / *** 2.1　《三代世表》已走 org 分支；此处兼容无星标行）
        # 只对**目录里声明了该模式的书**生效：title_patterns 是「本书会出现哪些标题
        # 形态」的声明；没声明却去匹配，认错的代价是那行不再进正文索引。
        for pat in title_candidates(self.title_res):
            m = pat.match(line)
            if m:
                rec = self._mk_heading(line, row_no)
                rec.section = clean_title(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
                if rec.section:
                    mark_section(rec, "title", 0.9)
                rec.notes.append("明文篇题")
                self._append(rec)
                return

        # 4d) SBCK 明文卷首题（guoyu / zhanguoce 特有形态）
        #
        # 國語一文件一卷，卷名在两处出现，**只有卷首那处带韦昭解题署**：
        #   卷首题   `周語上第一　國語　韋氏解¶`（文件第 2 行，21 个文件里 19 个有）
        #   版心题   `周語上第一　國語¶`       （文件末行，21 个文件里 20 个有）
        # 两者都被 GUOYU_SECTION_RE 命中。旧写法不区分，于是版心题又记一条 section，
        # 标签还是拼接串（`晉語第十三國語`、`越語上第二十&KR0680;國語`），把该文件
        # 后半段的行全部标错篇名。判据就用现成的 GUOYU_HEAD_RE（`…韋氏解`）：
        # 有题署＝卷首题，声明 section；无题署＝版心题，只当结构行。
        # 003 / 007 两文件的卷首题在源转录里就没有（第 2 行直接是正文），于是只剩
        # 版心题可认——如实留空，交给 Section Coverage Audit 点名，不硬凑。
        if self.family == "sbck" and self.def_layer in ("main", "preface"):
            s = stripped
            if GUOYU_HEAD_RE.search(s):
                rec = self._mk_heading(line, row_no)
                rec.section = rec.section or self._sbck_section_label(s)
                rec.status = "ok"
                if rec.section:
                    mark_section(rec, "title", PATTERNS["guoyu_head"].confidence)
                rec.notes.append("國語卷首题（韦昭解题署）")
                self._append(rec)
                return
            if GUOYU_SECTION_RE.match(s):
                rec = self._mk_heading(line, row_no)
                rec.notes.append("國語卷末版心题（不声明 section）")
                self._append(rec)
                return
            # 戰國策专属形态由目录声明（title_handler），不是书名分支
            if self.title_handler == "zhanguoce" and self._zhanguoce_title(s, line, row_no):
                return

        # 4d-WYG) 四庫系明文标题。**只在正文层认**：_000 是御製詩 + 敘例 + 目録 + 提要，
        # 目録里的 `　傳第七十上¶` 与正文篇题长得一模一样，光看长相分不开；一卷末尾的
        # 考證段（part_layer=appendix）里也有引篇名的写法。用「当前层是不是正文」把它们
        # 一起挡掉，比再加几条正则可靠。
        #
        # 顺序约束（6.3 加 cjk_ming_paren 形态时必须守住）：标题分流在 4e 行内括号注
        # 切分**之前**。三國志的傳名 `　武帝(操)` 若过了 4e，`(操)` 会被切成注候选、
        # `武帝` 降级成正文，篇题永久丢失且事后无法还原。
        if (self.family == "wyg" and (self.part_layer or self.def_layer) == "main"
                and self._wyg_title(line, row_no)):
            return

        # 4e) SBCK / WYG 正文行内括号注候选拆分
        # WYG 的颜师古注、章怀太子注都在行内圆括号里（实测占 36% 字符），与 SBCK
        # 同一形态同一处理：切成 commentary_candidate，正文一个字不动（§16）。
        if (self.family in ("sbck", "wyg") and (self.part_layer or self.def_layer) == "main"
                and ("(" in line or "（" in line)):
            self._emit_sbck_row(line, row_no)
            return

        # 4f) 普通正文行
        rec = self._mk_passage(line, row_no)
        self._append(rec)

    def _zhanguoce_title(self, s: str, line: str, row_no: int) -> bool:
        """戰國策明文标题分流；认下就记账并返回 True（调用方直接 return）。

        三种形态与判据见文件头 ZHANGUOCE_* 常量的注释。这里只做**判定顺序**：
        卷首题 → 章數行 → 国别题。顺序不能换：卷首题里含国名，先判它才不会
        被国别题的正则截走；章數行必须先于国别题开好门，下一行的国别题才认得上。

        `_sbck_section_label` 那种「截断前 16 字」的写法在这里用不得——国别题
        的括号里跟着上百字地理沿革注，截出来是 `東周(漢志河南鞏東周君所居正`。
        国名直接从正则的 state 组取，一个字不多。
        """
        m = ZHANGUOCE_JUAN_RE.match(s)
        if m:
            states = tuple(re.findall(ZHANGUOCE_STATES, m.group("states")))
            # 卷首题会在**卷末再抄一遍**（版心题，实测 002–009 每卷都有，file 001
            # 作 `戰國策西周卷第一`(策) 与卷首的 䇿 写法不同）。同国名重复出现＝
            # 卷末题，不是换卷——否则会多记一条 juan，还会把 section 清掉。
            colophon = m.group("end") or (states == self.zc_states and self.juan)
            if not colophon:
                # 卷首题：换卷——声明本卷有哪几国，并把门打开（卷首的这一国）。
                # 上下文先设好再造记录：rec.juan/section 由 _base 从 self 上取。
                self.zc_states = states
                self.zc_open = True
                self.section = None          # 上一卷的国名不许漏到这一卷
                self.juan = s.rstrip(config.PARA_CHAR).strip()
            rec = self._mk_heading(line, row_no)
            rec.notes.append("戰國策卷末题（版心题）" if colophon
                             else f"戰國策卷首题（声明国名：{'、'.join(states)}）")
            self._append(rec)
            return True

        if ZHANGUOCE_CHAPTERS_RE.match(s):
            # 一国策到此为止：开一次门，等下一个国别题
            self.zc_open = True
            rec = self._mk_heading(line, row_no)
            rec.notes.append("戰國策章數行（一国策结束）")
            self._append(rec)
            return True

        m = ZHANGUOCE_SECTION_RE.match(s)
        if m and self.zc_open and m.group("state") in self.zc_states:
            self.zc_open = False
            self.section = m.group("state")
            rec = self._mk_heading(line, row_no)
            rec.section = self.section
            rec.status = "ok"
            mark_section(rec, "title", PATTERNS["zhanguoce_state"].confidence)
            rec.notes.append("戰國策国别题（国名取自卷首题声明）")
            self._append(rec)
            return True
        return False

    def _wyg_title(self, line: str, row_no: int) -> bool:
        r"""WYG 明文标题分流；认下就记账并返回 True（调用方直接 return）。

        注意**传整行、不传 strip 过的**：篇题形态的判据之一就是行首缩进
        （`　　高帝紀第一上`），strip 掉缩进后 `^[　\s]{1,8}` 永远不成立——
        实测漏判全部 209 条篇题。

        顺序：卷题 → 篇题。卷题里不含 `第N`，两者不会互相截走，但卷题要先认，
        否则 `　前漢書卷一上` 会被当成正文（它确实不含第 N）。

        篇题按**本书声明的 title_patterns 顺序**试（catalog.patterns_for）：没声明的
        书用家族默认（WYG 族＝`wyg_di_n`，实测覆盖前漢書/後漢書/晉書）。顺序即优先级，
        新形态必须排在末尾，免得把 `第N` 也有的书截走。
        """
        m_juan = self.wyg_juan_re.match(line) if self.wyg_juan_re else None
        if m_juan:
            rec = self._mk_heading(line, row_no)
            # 卷次由正则的 juan 组给（`卷一上`），与 kanripo 自己的 JUAN 属性值一致，
            # 便于两处对账。前缀只在**不是本书书名**时保留：前漢書的卷题前缀就是书名
            # （`前漢書卷一上`），按库内既有惯例记 `卷一上`（115 条 juan 全是这个形状）；
            # 三國志写 `魏志卷一`，魏/蜀/吳 三志各自从卷一数起，去掉前缀就分不出哪个志。
            juan = m_juan.group("juan")
            prefix = m_juan.group("prefix")
            if normalize_label(prefix) != normalize_label(self.book_title):
                juan = prefix + juan
            self.juan = normalize_label(juan)
            rec.juan = self.juan
            if self.juan_as_section:
                # 该书以卷为篇名粒度：卷题本身声明 section（标签留整行卷题的写法，
                # `魏志卷九` 比 `卷九` 更能说明是哪个志——三國志三国各志各自起卷）
                self.section = normalize_label(line.rstrip(config.PARA_CHAR).strip())
                rec.section = self.section
                mark_section(rec, "title", WYG_JUAN_CONFIDENCE)
            else:
                self.section = None      # 上一卷的篇名不许漏到这一卷
            rec.notes.append("WYG 卷题" + ("（篇名粒度）" if self.juan_as_section else ""))
            self._append(rec)
            return True

        for pat in self.title_res:
            if pat.method != "title":
                continue     # 结构模式（四庫叢書題）由各自分支处理，不声明 section
            m = pat.regex.match(line)
            if not m:
                continue
            # 篇名只取 name 组：行尾可能跟着一段行内注（见 title_patterns.wyg_di_n
            # 的注释），整行当标签会把 `師古曰…` 一起拖进 section 表。
            self.section = m.group("name")
            rec = self._mk_heading(line, row_no)
            rec.section = self.section
            mark_section(rec, pat.method, pat.confidence)
            rec.notes.append(f"WYG 篇题（{pat.name}）")
            self._append(rec)
            return True

        if WYG_SIKU_RE.match(line):
            # `欽定四庫全書` / `欽定四庫全書　　　史部一`——丛书题，不是史料
            rec = self._mk_heading(line, row_no)
            rec.notes.append("WYG 四庫叢書題")
            self._append(rec)
            return True
        return False

    def _sbck_section_label(self, s: str) -> str:
        t = re.sub(r"^[　\s]+", "", s)
        t = t.split("　")[0]
        return t[:16] or None

    def _mk_heading(self, line: str, row_no: int) -> Record:
        rec = self._base(row_no, line, "heading")
        rec.layer = "structure"
        rec.status = "ok"
        return rec

    def _mk_passage(self, line: str, row_no: int) -> Record:
        rec = self._base(row_no, line, "passage")
        if rec.status != "ok":
            pass  # 文件层默认已带（如 pending_section 的 preface）
        return rec

    def _append(self, rec: Record) -> None:
        self.records.append(rec)

    # ---- org 标题按角色处理 ----
    def _handle_org_heading(self, lvl: str, code: str, line: str, row_no: int) -> None:
        rec = self._mk_heading(line, row_no)
        # 由 structure.is_org_heading 传回标题文字（去掉《》与空白）
        if lvl == "h2":
            role = self.h2_role
            m = re.match(r"^\*\*\s+(\d+)\s*(.*)$", line)
            text = clean_title(m.group(2)) if m else None
            if role == "division":
                self.division = text or None
            elif role == "juan":
                self.juan = text or None
                # 进入新公：重置 A/B 年份与年份篇题上下文
                self.zb_year = None
                self.ab = None
                self.section = None
            else:
                self.section = text or None
                if self.section:
                    self._mark_for_section(self.section, "header", 0.8)
        elif lvl == "h3":
            # *** 2.1　《三代世表》：code=2.1，标题在行内后续《…》里
            m = re.match(r"^\*\*\*\s*[0-9.]+\s*(.*)$", line)
            text = clean_title(m.group(1)) if m else None
            if text:
                self.section = text
                self._mark_for_section(self.section, "header", 0.8)
        rec.notes.append(f"org_{lvl}")
        self._append(rec)

    # ---- SBCK 行内括号注 ----
    def _emit_sbck_row(self, line: str, row_no: int) -> None:
        pieces = pending_split_parens(line)
        is_multi = len(pieces) > 1
        if not is_multi and pieces[0][3] == "main":
            # 无有效括号 → 普通行
            rec = self._mk_passage(line, row_no)
            rec.char_start, rec.char_end = 0, len(line)
            self._append(rec)
            return
        # 行尾的 ¶/空白归入上一片段（行重建仍连续；¶ 自身也要并回去，
        # 否则产生只含 "¶" 的孤立记录——strip() 对 ¶ 恒非空，须显式剥除）
        while len(pieces) > 1 and pieces[-1][3] == "main":
            tail_txt, _, _, _ = pieces[-1]
            residue = tail_txt.replace(config.PARA_CHAR, "").replace(" ", "").replace("　", "")
            if residue:
                break
            pieces.pop()
            pt, ps, pe, ptag = pieces[-1]
            pieces[-1] = (pt + tail_txt, ps, pe + len(tail_txt), ptag)
        # 拆成 main / paren 片段记录；行号相同、偏移不同
        # 重建校验按 (row_no, char_start) 顺序即可还原整行
        for seg_text, s, e, tag in pieces:
            rec = self._base(row_no, seg_text, "passage")
            rec.char_start, rec.char_end = s, e
            if tag == "paren":
                rec.layer = "commentary_candidate"
                rec.status = "pending_commentary"
                rec.notes.append("行内圆括号注候选（注者不猜）")
            else:
                if rec.status == "ok":
                    pass
            # 特殊字符在全行范围已有 _base 计算；空主段（行首即括号）仍保留
            if seg_text:
                self._append(rec)
        # 该行若因括号异常退回整行 main（pending_split_parens 已处理）

    # 供 run() 使用：记录正文从第几行开始（由 segment_file 传入）
    first_line_index: int = 1


def segment_file(path: Path) -> tuple[FileHeader, list[Record], str]:
    """txt 文件 -> (header, records, body)。body 供重建校验。"""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        decode_error = False
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        decode_error = True
    header, body = split_header(text)
    file_no = file_no_of(path.name)

    # body 行相对文件的行号 = header 行数 + 1 起
    header_line_count = len(header.raw_header.splitlines()) if header.raw_header else 0
    seg = FileSegmenter(header, path.parent.name, file_no, text)
    seg.first_line_index = header_line_count + 1  # 1-based 绝对行号
    # body 从 header 结束后立即开始：seg 需要从 header_line_count 行开始遍历
    # FileSegmenter 内部以 lines[i] 循环；此处换算成绝对行号偏移：
    seg.lines = text.splitlines()
    seg.run()
    for r in seg.records:
        if decode_error:
            r.notes.append("decode_error")
    return header, seg.records, body
