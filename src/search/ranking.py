"""第四阶段：候选史料的相关性排序。

输入是召回阶段的候选片段（引擎返回的 passage 命中），输出是按相关性降序的列表。
**纯打分排序，不做文本加工** —— 排序只读 text_orig / normalized_text，
不改写、不摘要、不生成（§2.3）。

打分项都是可解释的、逐项能说清「为什么加了这 5 分」的，便于在验收时复核
（数值与 WEIGHTS 一致，改权重时两处一起改）：

    实体精确命中      +14   问「齊桓公」，段里就写着「齊桓公」
    第二个实体        +9.5  问「秦穆公和百里奚」，第二个人的规范名也在段里
    实体别名命中      +6    写的是「重耳」；只在没有规范名命中时才计
    实体候选（短称）  +2    语料裁决不了的短称，只是个候选
    意图强词命中      +8    问「怎么死的」，段里有「卒/薨/崩/沒」
    多意图词          +2/个 词命中的越多越相关，但收益递减（封顶 +14）
    意图弱词          +1.5/个 兼用字（殺/子/君），封顶 +4.5，且不触发共现加成
    同 passage 内共现 +15   实体与**强**意图词在同一段里 —— 这是最强的信号
    多实体同段        +20   问 A 与 B，一段里同时写着 A、B（按全部写法判）
    只有实体没有意图  +3    相关，但不是问的那件事
    时长年数          +20   问「多少年」时，写着「凡十二年」的段落就是答案

相邻段落/成段拼接属于**事件聚合**那一步（Phase 3 result_block 的职责），
不在这里打分 —— 这里只给单段打分，避免同一份信息被两处重复计分。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from search import entities

# 打分权重。数值不是调参调出来的玄学，每一项都能对着一条真实命中说明白：
# 见文件头注释。改动这里要同步更新 docs/phase4_report.md。
WEIGHTS = {
    "entity_exact": 14.0,     # 命中第一个实体（全称）：最确定的信号
    "entity_more": 9.5,       # 命中第二个及以后的实体（问「A 和 B」时 B 也要算）
    "entity_alias": 6.0,      # 别名命中：确定，但隔了一层
    "entity_weak": 2.0,       # 短称且上下文裁决不了：只是个候选
    "intent_hit": 8.0,        # 第一个意图强词命中（卒/薨/崩/沒 这类专用词）
    "intent_extra": 2.0,      # 每多命中一个意图词
    "intent_cap": 14.0,       # 意图词加分上限，避免穷举词表刷分
    # 弱意图词（子/君/弟/殺 这类兼用字）：命中说明不了什么，只给很小的分。
    # 关键：弱词**不能**触发 co_occur 加成 —— 否则「公子」「晉君」里的
    # 「子」「君」会把「秦穆公以夫人入公子夷吾為晉君」顶到真正的
    # 「秦穆公与百里奚同段」之上（实测就是这么错的）。
    "intent_weak_hit": 1.5,
    "intent_weak_cap": 4.5,
    "co_occur": 15.0,         # 实体与（强）意图词同段：最强的相关性证据
    # 候选实体（裁决不了的短称/共用别名）与意图词同段：段落确实在讲这件事，
    # 但「是不是这个人」没定论，所以只给一半多一点的加成，不能让它顶掉确定命中。
    "co_occur_weak": 6.0,
    # 问「A 和 B」时，A、B 同时出现在一段里。20 分和 duration_answer 同一个道理：
    # 用户问的是两个人的关系，那么「两位都写着」就比「写着其中一位 + 意图词」
    # 更贴近所问（实测：「管仲和鲍叔牙」若不压过，左傳「有鮑叔牙、賓須無…」
    # 只写着一位却霸着第一，史記「鮑叔牙曰…君將治齊」那段正面记载反而沉下去）。
    "multi_entity": 20.0,
    "entity_only": 3.0,       # 只有实体、没有意图词
    # 没有人物实体时（「城濮之戰谁赢了」），问句里的实词就是用户唯一的约束。
    # 这一档只做**如实标注**用：这些片段能进候选池本就是因为命中了主题词，
    # 给分是为了让结果里的「命中理由」不是空的，不改变它们之间的次序。
    "topic_hit": 5.0,
    # 问「多少年」时，含年数表达的段落优先：「重耳居狄凡十二年而去」直接就是答案。
    # 20 分压过 co_occur，因为这时候年数比「有没有写卒/奔」更贴近用户所问。
    "duration_answer": 20.0,
}

# 年数表达（十二年 / 十九年 / 三年）—— 只用于判断段落里有没有年数，不改文本
# 时长年数 vs 纪年：区分靠**前一个字**，不靠年数本身。
#   「重耳居狄凡十二年而去」→ 凡十二年 = 时长，是答案
#   「魯僖之二十五年」      → 之二十五年 = 纪年，只是时间定位
# 前置字取表时长/累计的动词与副词（凡/居/立/行/後/積/共/前後/歷）才认。
_RE_YEAR_COUNT = re.compile(
    "[凡居立行後后積积共歷历]([一二三四五六七八九十百千萬万]{1,6}餘?年"
    "|[兩两三四五六七八九十]年)")


def has_form(text: str, forms: list[tuple[str, str]]) -> bool:
    """文本里有没有这个实体的任何一种写法。

    一个实体的写法分档（见 query_expansion._term_roles），比对方式也跟着分：
    规范名用字面比对；别名/短称要过 bare_hit，否则「鄭穆公」里的「穆公」会被
    当成秦穆公（判据见 entities.bare_hit）。
    """
    for role, t in forms:
        if not t:
            continue
        if t in text if role == "entity" else entities.bare_hit(text, t):
            return True
    return False


@dataclass
class Candidate:
    """一条候选片段。字段与 engine 返回的命中行对齐，额外记打分明细。"""
    passage_id: int
    file_id: int
    row_no: int
    seq: int = 0                                    # 文件内记录序，组装片段要用
    book_id: str = ""
    book_title: str = ""
    text_orig: str = ""
    score: float = 0.0
    hits: list[str] = field(default_factory=list)   # 实际命中的检索词
    detail: dict = field(default_factory=dict)      # 逐项得分，验收时可复核
    layer: str = "main"                             # Phase 1 结构层


def score_candidate(cand: Candidate, entity_terms: list[str],
                    alias_terms: list[str], weak_terms: list[str],
                    intent_terms: list[str], asks_duration: bool = False,
                    intent_weak: list[str] | None = None,
                    topic_terms: list[str] | None = None,
                    asked_forms: list[str] | None = None,
                    entity_forms: list[list[tuple[str, str]]] | None = None
                    ) -> Candidate:
    """给一条片段打分。就地写 cand.score / hits / detail 并返回它。"""
    text = cand.text_orig or ""
    detail: dict[str, float] = {}
    hits: list[str] = []

    # --- 实体侧 ---------------------------------------------------------
    # 问句里每个实体都单独计分：问「秦穆公和百里奚是什么关系」时，同时写着
    # 这两个人的段落才是回答问题的段落，只写着秦穆公的只是话题相关。
    ent_score = 0.0
    ent_n = 0
    ent_tier = ""                                   # exact / alias / weak
    for t in entity_terms:
        if not t or t not in text:
            continue
        w = WEIGHTS["entity_exact"] if ent_score == 0.0 else WEIGHTS["entity_more"]
        ent_score += w
        ent_n += 1
        ent_tier = "exact"
        hits.append(t)
        detail[f"实体:{t}"] = w
    # 问「A 和 B 是什么关系」时，一段里同时写着 A 和 B 才是回答问题的段落 ——
    # 只写着其中一位的段落，哪怕把意图词命中了，也只是回答了半个问题。
    #
    # 同段要按**全部写法**判，不能只看规范名：语料里写「昔繆公求士…東得百里奚
    # 於宛」（諫逐客書），只认「秦穆公」就会漏掉这段**正面记载两人关系**的史料。
    # 短称（穆公/繆公）仍旧要过 bare_hit，不至于把「鄭穆公」算成秦穆公。
    present = (sum(1 for forms in entity_forms if has_form(text, forms))
               if entity_forms else ent_n)
    if present >= 2:
        ent_score += WEIGHTS["multi_entity"]
        detail[f"{present}个实体同段"] = WEIGHTS["multi_entity"]
    if ent_score == 0.0:
        for t in alias_terms:
            # 别名同样要过 bare_hit：已核实的别名里既有姓名式（重耳/小白/
            # 管夷吾，前面不可能跟国名，守卫对它们是空操作），也有谥号式
            # （穆公/繆公），后者正是「鄭穆公」会冒充的那个。
            if t and entities.bare_hit(text, t):
                # 用户**自己写的那个词**命中，比规范名命中更该算数：问「重耳流亡」
                # 时，写着「重耳出奔」的段落是正面回答，只写「晉文公」的段落可能
                # 讲的是别人的事（实测 國語「敗於城濮懼出奔楚…晉文公討不伏」讲的是
                # 衛成公出奔，却凭「晉文公+奔」压过了「重耳出奔」那一句）。
                # 这不是新增同义词，只是承认用户的原词就是最精确的检索词。
                asked = t in (asked_forms or ())
                w = WEIGHTS["entity_exact"] if asked else WEIGHTS["entity_alias"]
                ent_score += w
                ent_tier = "exact" if asked else "alias"
                hits.append(t)
                detail[f"{'问句原词' if asked else '实体别名'}:{t}"] = w
                break
    if ent_score == 0.0:
        for t in weak_terms:
            # 短称必须**独立**出现才算命中：段里写「鄭穆公之子」时，其中的
            # 「穆公」是鄭穆公，不能拿去顶秦穆公（判据见 entities.bare_hit）。
            if t and entities.bare_hit(text, t):
                ent_score += WEIGHTS["entity_weak"]
                ent_tier = "weak"
                hits.append(t)
                detail[f"实体候选:{t}"] = WEIGHTS["entity_weak"]
                break

    # --- 意图侧 ---------------------------------------------------------
    # 强词与弱词分开算：强词（卒/薨/崩）命中说明段落真的在讲这件事；
    # 弱词（子/君/殺）命中只说明这个字出现过。两者对「实体+意图同段」这个
    # 最强信号的意义完全不同，所以弱词的分数独立记、且**不**触发组合加成。
    intent_hits = [t for t in intent_terms if t and t in text]
    weak_hits = [t for t in (intent_weak or []) if t and t in text]
    intent_score = 0.0
    if intent_hits:
        intent_score = WEIGHTS["intent_hit"]
        detail[f"意图:{intent_hits[0]}"] = WEIGHTS["intent_hit"]
        extra = min((len(intent_hits) - 1) * WEIGHTS["intent_extra"],
                    WEIGHTS["intent_cap"] - WEIGHTS["intent_hit"])
        if extra > 0:
            intent_score += extra
            detail[f"意图词+{len(intent_hits) - 1}"] = extra
        hits.extend(intent_hits)
    if weak_hits:
        weak_score = min(len(weak_hits) * WEIGHTS["intent_weak_hit"],
                         WEIGHTS["intent_weak_cap"])
        intent_score += weak_score
        detail[f"弱意图词{weak_hits[:3]}"] = weak_score
        hits.extend(weak_hits)

    # --- 主题词（无人物实体时的兜底约束）---------------------------------
    topic_score = 0.0
    topic_hits = [t for t in (topic_terms or []) if t and t in text]
    if topic_hits:
        topic_score = WEIGHTS["topic_hit"]
        hits.append(topic_hits[0])
        detail[f"主题:{topic_hits[0]}"] = WEIGHTS["topic_hit"]

    # --- 组合项 ---------------------------------------------------------
    # co_occur 只认**强**意图词：弱词遍地都是，拿它当「同段」证据等于没证据。
    combo = 0.0
    if ent_score > 0 and intent_hits:
        # 只有**确定**的实体命中（全称/已核实别名）才配拿满额共现加成。
        # 候选档（裁决不了的短称、多家共用的别名）与意图词同段只说明「这段在讲
        # 这件事」，不说明「讲的是这个人」—— 给它满分会让「屈羽卒，子夷吾立」
        # 这种别的国家的世系排到真正的史料前面。
        w = (WEIGHTS["co_occur"] if ent_tier in ("exact", "alias")
             else WEIGHTS["co_occur_weak"])
        combo += w
        detail["实体+意图同段" if w == WEIGHTS["co_occur"] else "候选实体+意图同段"] = w
    elif ent_score > 0 and not intent_hits:
        combo += WEIGHTS["entity_only"]
        detail["仅实体"] = WEIGHTS["entity_only"]

    # 问「多少年」时，写了**时长**年数的段落更接近答案（「居狄凡十二年而去」）。
    # 纪年（「魯僖之二十五年」）不算 —— 它只是时间定位，不是用户问的时长。
    if asks_duration:
        m = _RE_YEAR_COUNT.search(text)
        if m:
            combo += WEIGHTS["duration_answer"]
            detail[f"含时长:{m.group(0)}"] = WEIGHTS["duration_answer"]

    cand.score = round(ent_score + intent_score + topic_score + combo, 2)
    cand.hits = hits
    cand.detail = detail
    return cand


def rank(cands: list[Candidate], entity_terms: list[str],
         alias_terms: list[str], weak_terms: list[str],
         intent_terms: list[str], asks_duration: bool = False,
         intent_weak: list[str] | None = None,
         topic_terms: list[str] | None = None,
         asked_forms: list[str] | None = None,
         entity_forms: list[list[tuple[str, str]]] | None = None
         ) -> list[Candidate]:
    """打分并降序排。

    同分时的次序是**确定的**（不依赖输入顺序，避免两次请求结果不一样）：
    先按书、再按文件、最后按行号 —— 都是数据库里的稳定字段。
    """
    for c in cands:
        score_candidate(c, entity_terms, alias_terms, weak_terms, intent_terms,
                        asks_duration, intent_weak, topic_terms, asked_forms,
                        entity_forms)
    return sorted(cands, key=lambda c: (-c.score, c.book_id or "",
                                        c.file_id, c.row_no))
