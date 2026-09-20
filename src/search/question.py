"""第四阶段：自然语言问题的分析层。

职责**只有分析**，不做检索、不生成答案（§2.3：不能让 AI 代替检索器）。
输入一句中文问题，输出结构化的：

    原始问题 → 繁简统一 → 实体识别 → 意图识别 → 古代表达扩展

后面的召回/排序/事件聚合都只读这里的结论，不再看自然语言。

词典独立存放（任务书 §7）：意图词表、时间词、场所词都集中在本模块顶部的
常量里，扩展成检索词由 query_expansion.py 负责，两者分开便于审查与替换。
所有词表都用**本语料实测频次**校准，不用想当然的古代汉语（见各表注释）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from search import entities, zh

# ------------------------------------------------------------------ 意图词表
#
# 每个意图给出：问句里的触发词（用户怎么问），以及语料里**真实存在**的表达
# （史料怎么写）。触发词用于判意图，真实表达用于检索，两者不混。
#
# 频次是 2026-09-11 在本库正文（175 万字体量）里数的，写在这里是为了让后来
# 改词表的人知道每个词的分量 —— 例如「殁」全库只有 2 次，「没」108 次，
# 把「殁」当主力检索词是错的。

INTENT_VOCAB: dict[str, dict] = {
    # 某人什么时候死的 / 怎么死的
    "death": {
        "ask": ["卒", "死", "去世", "逝世", "过世", "亡", "崩", "薨", "殁", "没",
                "怎么死", "如何死", "何时死", "哪年死", "死于", "被杀", "被弑",
                "结局", "下场", "最后怎么样", "怎么没的"],
        # 强词：这些字**基本只**用于「死」。问某人怎么死的，命中它们就八九不离十。
        # 歿/殁 是任务书 §31 点名的扩展词，语料里确实有（歿 7 段、殁 2 段，全是
        # 「死」的意思），按「说不清出处的词不写进结果」的标准够格，收进强词。
        "corpus": ["卒", "薨", "崩", "沒", "歿", "殁", "弑", "卒於", "卒于",
                   "死於", "死于", "卒之歲", "卒之岁"],
        # 弱词：确实能表示死，但**大量**用于别的意思，单靠它召回的全是噪声。
        # 实测（与各实体共现段数 / 全库段数）：
        #   卒  齊桓公9 管仲6 晉文公8   → 强
        #   終  管仲1                  → 弱（終=终于/最终，全库660）
        #   亡  齊桓公1 管仲1 晉文公1   → 弱（亡=灭亡/逃亡，全库1680）
        #   殺  管仲3                  → 弱（殺=杀别人，全库2352）
        #   死  管仲5                  → 中等，但「死」也用于「死罪」「致死」
        # 弱词仍参与标注与排序，但**不能**单独把段落召回来，否则召回池被灌爆，
        # 真正相关的段落排不上去（实测：管仲+殺 命中的是「殺之」「王則殺之」）。
        "corpus_weak": ["死", "終", "亡", "殺"],
        #
        # 「亡」还要排除用法（全库 1680 次里占多数的是「追亡人」23、「必亡」17、
        # 「存亡之」12 这类**灭亡/逃亡**）。不加排除的话「流亡了多少年」会被判成
        # death 意图 —— 见 _triggered 与下面的 exclude 表。
        "exclude": {"亡": ("流亡", "出亡", "亡人", "亡去", "追亡", "存亡",
                           "亡國", "亡国", "亡奔", "逃亡", "亡命", "亡歸",
                           "亡归", "亡走", "亡之", "亡也", "不亡", "未亡"),
                    "死": ("不死", "死罪", "死士", "致死", "死之", "死於")},
    },
    # 出亡 / 流亡（「重耳流亡了多少年」这类）
    "flight": {
        "ask": ["流亡", "出亡", "逃亡", "出奔", "奔亡", "避难", "避難",
                "在国外", "在外", "客居", "流落"],
        "corpus": ["出亡", "奔", "出奔", "亡奔", "出居", "居"],
        # 語料实测：重耳 198 段里有 出亡4 / 奔9 / 居多，且「重耳居狄凡十二年而去」
        # 直接写着年数。注意「奔」「居」本身很泛（奔=投奔/奔走，居=居住），
        # 但它们只在**问句已表明在问流亡**时才进意图组，不影响别的问句。
    },
    # 多少年 / 多久
    "duration": {
        "ask": ["多少年", "几年", "多久", "多长时间", "多少时候", "几年间",
                "十九年", "多少载", "几年时间"],
        "corpus": [],                          # 无固定词：靠 _RE_DURATION 提年数
    },
    # 某人何时出生 / 出身
    "birth": {
        "ask": ["出生", "生于", "何时生", "哪年生", "出身", "来历", "身世",
                "是谁的儿子", "谁之子", "家族"],
        "corpus": ["生", "生於", "生于", "立", "少", "初", "為公子", "为公子"],
        # 频次: 生 常见但多义（生死/生出），作检索词要配合实体
    },
    # 某句史料说的是谁 / 某人是哪国人
    "identity": {
        "ask": ["是谁", "什么人", "哪个国家", "哪国人", "何人", "何许人",
                "什么意思", "指谁", "说的谁"],
        # 全部是单字且极泛（曰/名/字/氏/姓 全库各上万段），**不能**用来召回：
        # 实测问「董卓是什么人」时它们把兜底池灌到 8757 条，等于用无关史料
        # 假装回答了「董卓不在语料里」这个问题。只留作排序时的弱信号。
        "corpus": [],
        "corpus_weak": ["曰", "謂", "谓", "名", "字", "諡", "谥", "氏", "姓"],
        # 身份类问句的正经答案是「史料里的记载」，靠实体或主题词召回，
        # 而不是靠这些泛词。
    },
    # 两个人什么关系
    "relation": {
        "ask": ["关系", "什么关系", "与", "和", "之间", "父子", "君臣",
                "谁的儿子", "朋友", "敌对", "辅佐", "推荐", "师父"],
        # 只用多字词召回。单字（子/父/臣/君/兄/弟/友/相/佐/事）会被「母弟」
        # 「公子」「晉君」这样的词内部命中，等于给所有段落加同样的分，
        # 结果把「秦穆公以夫人入公子夷吾為晉君」顶到「秦穆公+百里奚同段」前面，
        # 而后者才是回答问题的段落。降为弱词，只参与排序。
        "corpus": ["父子", "君臣", "兄弟", "師事", "相之", "輔佐", "薦", "事之"],
        "corpus_weak": ["子", "父", "臣", "君", "兄", "弟", "友", "師", "师",
                        "相", "佐", "事", "從", "从"],
    },
    # 某件事的经过
    "event": {
        # 触发词**不含**「怎么」「如何」：它们是疑问副词，任何问句里都可能出现，
        # 拿它们判「事件」意图会让「管仲是怎么死的」同时命中 death 与 event，
        # 于是事件强词（伐/戰于）也跟着参与打分 —— 实测「管夷吾奉公子糾奔魯」
        # 那一段因此压过了「管仲卒受下卿之禮」。判事件意图要用**实义**问法：
        # 「什么事」「怎么回事」「经过」「哪件事」，或者直接说出事件名（变法/改革）。
        "ask": ["发生了什么", "什么事", "经过", "怎么回事",
                "哪件事", "做了", "事件", "变法", "變法", "改革", "新政"],
        # 只用**多字**词做召回。单字（立/殺/取/入）看着像事件词，实际全库到处
        # 都有，召回的全是噪声，而且命中理由无法向用户交代。
        "corpus": ["伐", "戰", "盟", "會", "出奔", "圍", "敗", "變法",
                   "伐之", "戰于", "會于", "盟于", "至于", "法", "令", "政"],
        "corpus_weak": ["立", "殺", "取", "入"],
        # 「法」「令」「政」是变法类问句的正词：实测与商鞅同段的「法」6 次、
        # 「令」4 次（例「至夫秦用商鞅之法，¶」「衛鞅聞是令下，¶」）。它们放强档
        # 的前提是**事件意图不被疑问副词误触发**（见上面的 ask 表）：一旦
        # 「怎么」也能触发 event，「令」就会让「管仲因而令燕修召公之政」这种
        # 「令=命令」的段落挤进死亡问句的前列。词表与触发条件是配套的。
    },
    # 为什么
    "reason": {
        # 「怎么回事」不在这里：它问的是「这是件什么事」，属于 event；
        # 「怎么会」才是问原因。两个词只差一个字，意图完全不同，不能混。
        "ask": ["为什么", "為何", "为何", "原因", "怎么会", "凭什么", "何以",
                "所以然"],
        "corpus": ["故", "是以", "由是", "於是", "于是", "以此",
                   "所以", "為之", "为之", "故曰"],
        # 注：史书里原因常以「故」「是以」引出，但这两个词极泛，
        # 单靠它们检索噪声很大，实际使用时会与实体联合收紧（见 query_expansion）。
    },
    # 什么时候
    "time": {
        "ask": ["什么时候", "何时", "哪一年", "几年", "那年", "哪一年",
                "同时", "先后"],
        "corpus": ["元年", "二年", "三年", "四年", "五年", "六年", "七年",
                   "八年", "九年", "十年", "是歲", "是岁", "其年", "明年"],
    },
    # 在哪里
    "place": {
        "ask": ["哪里", "在哪", "何地", "什么地方", "哪国", "都城", "到哪"],
        "corpus": ["于", "於", "在", "至", "及", "之", "邑", "城", "都"],
    },
}

# 意图判定优先级：一句话里同时出现多个触发词时，按此顺序取第一个命中的。
# 死亡/出生这类「事实性」意图比「事件」「关系」更具体，优先匹配能减少误判。
# flight 排在 death 前面：问「重耳流亡多少年」时问句里既有「亡」也有年数，
# 但用户问的是流亡这件事，不是死。让它先命中，death 的「亡」还有 exclude 兜底。
INTENT_PRIORITY = ("flight", "duration", "death", "birth", "relation", "reason",
                   "time", "place", "identity", "event")


def _triggered(text: str, term: str, excludes: tuple[str, ...]) -> bool:
    """问句里是否真的命中了这个词：命中位置若落在排除用法内，不算。

    「流亡了多少年」里的「亡」不算死亡意图——排除表把它剔掉。
    判定看命中处左右各一字能否凑成排除词，而不是简单 `in`。
    """
    start = 0
    while True:
        i = text.find(term, start)
        if i < 0:
            return False
        # 命中处能否和邻近字凑成排除词？窗口取「命中前 2 字 + 词本身」，这样
        # 排除词无论从命中处往前伸（流亡 的 流 在 亡 前面）还是往后伸都能盖住。
        window = text[max(0, i - len(term)):i + len(term) * 2]
        if not any(x in window for x in excludes):
            return True
        start = i + 1

# 关系类问句里「A 和 B」的分隔写法
_RELATION_SPLIT = re.compile("[与與和及]|之间|之間|的关系|的關係")

# 年份表达：隐公元年 / 僖公三十年 / 周赧王元年…
_RE_REIGN_YEAR = re.compile("([一-鿿]{1,4}(?:公|侯|伯|王))([元一二三四五六七八九十百]+年)")
# 公元纪年：公元前 651 年 / 651 BC（春秋战国问题里用户常用公历）
_RE_BC = re.compile("(?:公元前|西元前|BC|bc)\\s*(\\d{1,4})|(\\d{1,4})\\s*(?:BC|bc|年\\s*前)")


@dataclass
class Question:
    """一句问题分析完之后的样子。"""
    raw: str                                   # 用户原话
    text: str                                  # 繁简统一后的形式
    intents: list[str] = field(default_factory=list)   # 命中的意图，按优先级
    entities: list[dict] = field(default_factory=list) # 识别出的实体
    years: list[str] = field(default_factory=list)     # 问题里提到的纪年
    # 问「多少年」时置位：表示用户在问**时长**。排序层据此奖励含年数的段落
    # （「重耳居狄凡十二年而去」正是答案），但这些年数不进检索词。
    asks_duration: bool = False
    notes: list[str] = field(default_factory=list)     # 裁决依据等，给前端/报告看

    @property
    def main_intent(self) -> str | None:
        return self.intents[0] if self.intents else None

    @property
    def main_entity(self) -> str | None:
        """权重最高的实体（排序层会用）。没有实体就只能靠意图词检索。"""
        if not self.entities:
            return None
        best = max(self.entities, key=lambda e: e["weight"])
        return best["entity"]

    def as_dict(self) -> dict:
        return {
            "raw": self.raw,
            "text": self.text,
            "intents": self.intents,
            "main_intent": self.main_intent,
            "entities": self.entities,
            "years": self.years,
            "notes": self.notes,
        }


def _detect_intents(text: str) -> tuple[list[str], list[str]]:
    """判意图，返回 (命中的意图序列, 说明)。按 INTENT_PRIORITY 排序，不是出现顺序。"""
    hits, notes = [], []
    for name in INTENT_PRIORITY:
        spec = INTENT_VOCAB[name]
        excludes = spec.get("exclude", {})
        for kw in spec["ask"]:
            if _triggered(text, kw, excludes.get(kw, ())):
                hits.append(name)
                notes.append(f"意图「{name}」由问句里的「{kw}」触发")
                break
    return hits, notes


def _find_entities(text: str, db_path: str | None) -> list[dict]:
    """找出问题里的实体，带上语料给的确定度权重。

    权重是**可解释的**，来自实体层而不是拍脑袋：
      · 全称（齊桓公/秦繆公）    → 1.0，语料里就是这么写的
      · 短称且上下文能裁决       → 0.7，语料分布支持
      · 短称但上下文裁决不了     → 0.3，只是候选，排序时不能压过确定命中
    """
    # 同一个实体的多种写法（齊桓公 与 桓公 同时出现在问句里）合并成一条，
    # 保留权重最高的那次作为主命中，其余写法记在 also 里。
    best: dict[str, dict] = {}
    for ent, word, start, end in entities.find_in_text(text, db_path):
        if ent == word:                       # 全称直接命中
            weight, why = 1.0, "全称精确命中"
        else:
            resolved, why = entities.resolve_alias(word, text, db_path)
            # 語料裁决成功 → 0.7；没裁决成功（实体层返回 None 或指到别人）→ 0.3，
            # 当候选处理，排序时压不过确定命中。
            weight = 0.7 if resolved == ent else 0.3
        cur = best.get(ent)
        if cur is None:
            best[ent] = {"entity": ent, "matched": word, "span": [start, end],
                         "weight": weight, "why": why, "also": []}
        elif weight > cur["weight"]:
            cur["also"].append(cur["matched"])
            cur.update({"matched": word, "span": [start, end],
                        "weight": weight, "why": why})
        elif word != cur["matched"]:
            cur["also"].append(word)
    return sorted(best.values(), key=lambda e: -e["weight"])


def _find_years(text: str) -> list[str]:
    """抽取纪年表达。只做识别与保留，不做公历换算（换算是史实推断，见 §八.4）。"""
    out = [m.group(0) for m in _RE_REIGN_YEAR.finditer(text)]
    for m in _RE_BC.finditer(text):
        out.append(m.group(0).strip())
    return out


def analyze(question: str, db_path: str | None = None) -> Question:
    """把一句自然语言问题分析成结构化结论。

    这是本模块唯一的入口。确定性（deterministic）——同样的问题永远得到同样的
    结论，没有随机、没有外部调用、没有 LLM（§2.3）。
    """
    raw = (question or "").strip()
    if not raw:
        return Question(raw=raw, text="", notes=["空问题"])

    # 1. 繁简统一：用户多半用简体问，语料是繁体。
    #    转换不可靠时恒等回退（保留原字），由 zh.to_traditional 负责，§12 的要求。
    text = zh.to_traditional(raw)
    q = Question(raw=raw, text=text)
    if text != raw:
        q.notes.append(f"繁简统一：「{raw}」→「{text}」")
    else:
        q.notes.append("繁简统一：未发生转换（原文已可用）")

    # 2. 实体识别
    q.entities = _find_entities(text, db_path)
    for e in q.entities:
        q.notes.append(
            f"实体 {e['entity']}（问句里写作「{e['matched']}」，权重 {e['weight']}）：{e['why']}")

    # 3. 意图识别
    q.intents, notes = _detect_intents(text)
    q.notes.extend(notes)

    # 4. 纪年 + 时长问法
    q.years = _find_years(text)
    if q.years:
        q.notes.append("识别到纪年：" + "、".join(q.years))
    if "duration" in q.intents:
        q.asks_duration = True
        q.notes.append("问的是时长：排序时优先含年数表达的史料")

    # 5. 关系类问句：两个实体都在时，明确记下配对，供事件聚合区分 A→B / B→A
    if "relation" in q.intents and len(q.entities) >= 2:
        q.notes.append("关系类问句，涉及 " +
                       " 与 ".join(e["entity"] for e in q.entities[:2]))

    if not q.intents and not q.entities:
        q.notes.append("未能识别出实体或意图，将退化为普通全文检索")
    return q


def expand_terms(q: Question) -> list[str]:
    """问题 → 检索词表（古代表达扩展）。排序：实体 > 意图词 > 年份。

    只做**词表扩展**，不做同义改写：每个词都能在语料里查到实际出现，
    这是 §2.3「不能凭空让 AI 生成史料」在检索侧的最低要求。
    """
    terms: list[str] = []
    for e in q.entities:
        if e["matched"] not in terms:
            terms.append(e["matched"])
        if e["entity"] not in terms:
            terms.append(e["entity"])
        # 别名也进检索：问「商鞅」时「衛鞅」「公孫鞅」的史料同样该召回
        for al in entities.aliases_of(e["entity"]):
            if al not in terms:
                terms.append(al)
    for name in q.intents:
        for w in INTENT_VOCAB[name]["corpus"]:
            if w not in terms:
                terms.append(w)
    for y in q.years:
        if y not in terms:
            terms.append(y)
    return terms
