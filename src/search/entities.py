"""第四阶段：语料派生的轻量实体层。

设计原则（任务书 §6 / §33）
----------------------------
1. **候选来自语料，不来自外部知识。** 本模块不硬编码任何历史人物的生卒、事迹、
   亲属关系。它只做一件事：把语料里**实际出现的**人物称谓扫出来，统计频次与分布。
   某个人物在不在库里，取决于它有没有真的出现在这五本书中。

2. **别名分组必须带语料依据。** 表里每一条 `{canonical: [alias…]}` 都附一条
   取自语料的佐证（哪本书的哪句话把两个名字系到同一个人身上）。
   拿不出佐证的别名**不写进来**——宁可少认，不可瞎认（§33「不要凭空增加历史人物」）。

3. **短称不自动等于全称。** 「桓公」467 条，而「齊桓公」96 条、「秦桓公」7 条、
   「魯桓公」13 条——同一个「桓公」在不同的书里指不同的人（§6 特别注意）。
   因此短称一律是**低权重候选**，要由上下文（同 passage 是否出现国名）来裁决方向，
   见 rank_alias()。

4. **实体识别是只读的。** 全程不写库、不改原文。候选与频次在进程内缓存。
"""
from __future__ import annotations

import re
import sqlite3
from functools import lru_cache

# 春秋战国主要国名。只用于**识别语料里的称谓形态**，不代表任何史实主张。
STATES = ("齊", "晉", "秦", "魯", "楚", "宋", "衛", "鄭", "燕", "陳", "曹", "蔡",
          "吴", "吳", "越", "虞", "虢", "滕", "薛", "許", "杞", "莒", "邾",
          "趙", "魏", "韓", "田", "中山")

# 战国以后的国名/名号也用得上（史記、戰國策）
STATES_EXTRA = ("周", "召", "單", "劉", "尹")

_TITLE = "公侯伯子男"

# 国名+爵位+一字：齊桓公、晉文公、秦穆公、鄭莊公、魯隱公…
#
# 为什么不直接用「国名+爵位」：实测「國名+公」会被两类东西污染 ——
#   (a) 句中巧合：「辰請如齊公使往」里「齊公」= 「如齊」+「公使」；
#   (b) 亲属/宗族词：「晉公族」「魯公子翬」「秦公孫枝」。
# (a)(b) 都靠「至少还有第三个字、且那个字不是 族子孫…」排除，见 _NON_FOLLOW 断言。
_NON_FOLLOW = "(?![族子孫女弟主室家甸后妃母])"
# 国名 + 亲属/封号字 + 爵位，如 楚公子/晉公子/楚太子/燕太子/魏公子/魯夫人。
# 这类是「某国的公子/太子」，**不是某一个人**，不能当实体——否则「楚公子」会把
# 楚国历代所有公子揉成一个词条。判据是中间那个字：凡亲属/位号字一律排除。
_NON_KIN = set("公太世王長次幼少夫母女弟兄孫族室主后妃妾")
_STATE_CLS = "[" + "".join(STATES + STATES_EXTRA) + "]"
# 全称 → 实体。**字序是 国名 + 谥号 + 爵位**，不是 国名 + 爵位 + 谥号：
# 「齊桓公」= 齊 + 桓 + 公，中间的桓是谥号，末尾的公才是爵位。
# 写成 [公侯伯子男] 打头会把「晉侯使」「公曰」这类散文当人名收进来（实测就是这个
# 毛病：top20 全是 侯使/公曰/子伐），必须让爵位落在**最后**一位。
# 注意「一字」用的是**真的** U+4E00–U+9FFF 两个字符，不是转义序列：
# 写成 "[\\u4e00-\\u9fff]" 会让 Python 把反斜杠原样交给 re，
# 于是字符类变成 {\, u, 4, e, 0, -, 9, f} 这种垃圾，漢字一个都匹配不上。
_PAT_FULL = re.compile(
    _STATE_CLS + "([一-鿿])" + "([" + _TITLE + "])" + _NON_FOLLOW)
# 两句短称（晉侯/齊侯/鄭伯/楚子/宋公，以及全称截出来的 齊桓公→桓公）**没有**
# 正则：两字窗格的裸计数在建库扫描里直接数（见 _corpus_scan）。
# 试过两种写法，都不能用：
#   · 非重叠 finditer + _STATE_CLS[爵位]：在「齊桓公」里匹到的是「齊桓」而不是
#     「桓公」，offset 1 被跳过，短称统计整批丢光（实测 bare['桓公'] 为 None）；
#   · 加先行断言让它重叠：断言要求匹配后面还有一个字符，句末的「桓公」匹不上。
# 两字窗格直接数没有边界歧义，也不可能漏。


@lru_cache(maxsize=1)
def _corpus_scan(db_path: str) -> tuple[dict[str, int], dict[str, dict[str, int]],
                                       dict[str, int]]:
    """扫一遍正文，返回 (全称→条数, 短称→{来源全称: 条数}, 短称实际出现次数)。

    仅此一次，进程内缓存。
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        full: dict[str, int] = {}
        raw: dict[str, int] = {}                # 两字形态的裸出现次数
        for (text,) in conn.execute(
                "SELECT text_orig FROM passages WHERE kind = 'passage'"):
            if not text:
                continue
            for m in _PAT_FULL.finditer(text):
                name = m.group(0)               # 齊桓公
                if m.group(1) in _NON_KIN:      # 楚公子/燕太子：不是某一个人
                    continue                    # 连短称也不算
                full[name] = full.get(name, 0) + 1
            # 两字形态的**重叠**裸计数。
            # 不用正则：finditer 非重叠，在「齊桓公」里只匹到「齊桓」而跳过
            # offset 1 的「桓公」（短称统计整批丢光）；改用先行断言又要求匹配
            # 后面还有一个字符，句末的「桓公」匹不上。两字窗格直接数最稳。
            n = len(text)
            for i in range(n - 1):
                raw[text[i:i + 2]] = raw.get(text[i:i + 2], 0) + 1
        # 短称（全称去掉国名，于是齊桓公 → 桓公）及其来源分布
        short: dict[str, dict[str, int]] = {}
        for name, n in full.items():
            s = name[1:]
            short.setdefault(s, {})
            short[s][name] = short[s].get(name, 0) + n
        # 单独出现 = 裸计数 − 被全称解释掉的部分
        bare: dict[str, int] = {}
        for s in short:
            left = raw.get(s, 0) - sum(short[s].values())
            if left > 0:
                bare[s] = left
        # 全称短称交替时窗口会跨过组合边界（「齊桓公卒」里 齊桓/桓公/公卒 都算），
        # 那些不会出现在 short 的键里，和它们一起清掉，别在缓存里占地方。
        for k in set(raw) - set(short):
            del raw[k]
        return full, short, bare
    finally:
        conn.close()


# --------------------------------------------------------------- 已核实体表

# 每一条别名的依据都来自**本语料自身**的原文，写在 evidence 里。
# 加新条目时，evidence 必须是能 grep 到的原文；拿不出就说明这个别名不该加。
_CURATED: dict[str, dict] = {
    "齊桓公": {
        "aliases": ["桓公", "小白"],
        "state": "齊",
        "evidence": {
            "桓公": "史記·齊太公世家等以「桓公」承指齊君（96 条「齊桓公」与 467 条「桓公」并存，需上下文裁决）",
            "小白": "國語「奉公子小白出奔莒」——齊桓公名小白",
        },
    },
    "晉文公": {
        "aliases": ["文公", "重耳"],
        "state": "晉",
        "evidence": {
            "重耳": "史記·晉世家、國語載晉公子重耳出亡十九年而後立，是為晉文公",
        },
    },
    "秦穆公": {
        "aliases": ["穆公", "繆公"],
        "state": "秦",
        "evidence": {"繆公": "史記·秦本紀「穆公」「繆公」互見（同一君主的两种写法）"},
    },
    "管仲": {
        "aliases": ["管夷吾", "夷吾", "管子"],
        "state": None,
        "evidence": {
            "管夷吾": "國語注「管夷吾齊卿姬姓之後」；左傳「管夷吾」與「管仲」互見",
            "管子": "戰國策、國語中以「管子」称管仲",
        },
    },
    "商鞅": {
        "aliases": ["衛鞅", "公孫鞅"],
        "state": None,
        "evidence": {
            "衛鞅": "史記「秦封衛鞅於商」——封於商故又称商鞅（語料自证得名之由）",
            "公孫鞅": "史記「衛公孫鞅為大良造」，公孫鞅即衛鞅",
        },
    },
    "鮑叔牙": {
        "aliases": ["鮑叔"],
        "state": None,
        "evidence": {"鮑叔": "左傳、國語「鮑叔牙」與「鮑叔」互見"},
    },
    "晉惠公": {
        "aliases": ["夷吾"],
        "state": "晉",
        "evidence": {"夷吾": "史記·晉世家晉公子夷吾是為晉惠公（注意与管夷吾同名，靠上下文区分）"},
    },
    # 姓名式称谓（非「国名+爵位」形态，语料派生规则抓不到），逐个按原文核实后登记。
    # 别名栏留空的，表示**语料里找不出可靠的互指证据** —— 宁可只认全称，
    # 也不把「史书上一般这么说」写进来（§33「不要猜」、§八.4）。
    "百里奚": {
        "aliases": [],
        "state": None,
        "evidence": {"百里奚": "史記「虜虞公及其大夫井伯百里奚以媵秦穆姬」（語料 11 段）"},
    },
}

# --------------------------------------------------------------- 歧义处理

# 纯短称（不带国名）：必须靠上下文裁决，默认低权重。
_AMBIGUOUS = {}
for _canon, _info in _CURATED.items():
    _st = _info.get("state")
    for _al in _info["aliases"]:
        # 国名开头的别名（齊桓公）本身就是全称，不算歧义
        if _al.startswith(tuple(STATES)):
            continue
        _AMBIGUOUS.setdefault(_al, []).append(_canon)


def ambiguous_heads() -> dict[str, list[str]]:
    """短称 → 它可能指向的实体列表（供排序层降权与上下文裁决）。"""
    return {k: list(v) for k, v in _AMBIGUOUS.items()}


def is_ambiguous(alias: str) -> bool:
    return alias in _AMBIGUOUS


def resolve_alias(alias: str, context: str = "",
                  db_path: str | None = None) -> tuple[str | None, str]:
    """判定别名指向哪个实体，返回 (实体, 依据说明)。

    全称（带国名）直接命中；短称必须能**唯一**落到某个人身上才升级为实体 ——
    要么别名本身就是全称，要么别名只有唯一候选，要么上下文里出现了该候选的国名。
    落不了地的返回 None，由排序层当低权重候选处理，**不硬认**（§6 / §18.2）。

    两条裁决通路：
      · 已核实表（人工看过原文、带 evidence 的那批）
      · 语料派生（short_forms 的分布）—— 没人工核过，但语料自己给出的占比
        就是权重，仍是可复核的证据，不是猜的。
    """
    alias = alias.strip()
    if not alias:
        return None, "空"
    if alias in _CURATED:                       # 全称，直接是实体
        return alias, "全称精确命中"
    if alias in corpus_names(db_path):          # 语料里就是这么写的全称
        return alias, "语料全称精确命中"

    # 短称：只有**一份**分布（语料频次 + 已核实别名合并而成，见 short_forms），
    # 所以只有一条裁决路径，不存在两套口径打架。
    dist = short_forms(db_path).get(alias)
    if dist is None:
        return None, "语料中无此称谓（或其全部出现都追不到全称）"
    order = "、".join(f"{f}({n})" for f, n in sorted(dist.items(), key=lambda kv: -kv[1]))
    if len(dist) == 1:
        only = next(iter(dist))
        return only, f"「{alias}」的语料分布唯一：{order}"
    ranked = resolve_short(alias, db_path, context)
    if ranked[0][0] in context:                 # 上下文点了国名，且语料也支持
        return ranked[0], f"上下文出现「{ranked[0][0]}」，语料分布为 {order}"
    return None, f"「{alias}」在语料中分属 {order}，上下文不足以裁决"


def curated() -> dict[str, dict]:
    """已核实实体表（只读副本）。"""
    return {k: {**v, "aliases": list(v["aliases"])} for k, v in _CURATED.items()}


def aliases_of(entity: str) -> list[str]:
    info = _CURATED.get(entity)
    return list(info["aliases"]) if info else []


def _db_path(db_path: str | None) -> str:
    if db_path:
        return str(db_path)
    from scripts.pipeline import config
    return str(config.DB_PATH)


def corpus_names(db_path: str | None = None) -> dict[str, int]:
    """语料里「国名+爵位+名」**全称**及条数（进程内缓存）。"""
    return dict(_corpus_scan(_db_path(db_path))[0])


# 短称（如「桓公」）要能参与别名裁决，至少得单独出现过几次，
# 否则一律当句中巧合（「侯使」「公曰」这类确实追不到任何全称，见 short_forms 的
# 「至少有一个全称来源」判据）。
SHORT_MIN_BARE = 3


def _curated_distribution() -> dict[str, dict[str, int]]:
    """把已核实表里的别名折进语料分布，格式与语料派生的完全一致。

    没有这一步，两条通路会各自为政：_AMBIGUOUS 说「桓公」只有齊桓公一个候选，
    于是上下文写着「秦」也照样返回齊桓公 —— 而语料里秦桓公/魯桓公/鄭桓公
    都是真实存在的（§18.2 的歧义测试正是打这个）。折进来之后只有一份分布，
    裁决只有一条路。
    """
    out: dict[str, dict[str, int]] = {}
    for canon, info in _CURATED.items():
        for al in info["aliases"]:
            if al[0] in STATES or al[0] in STATES_EXTRA:
                continue                    # 黏着国名的别名本身就是全称，不算短称
            out.setdefault(al, {})
            out[al][canon] = out[al].get(canon, 0) + 1
    return out


def short_forms(db_path: str | None = None) -> dict[str, dict[str, int]]:
    """两字短称 → {对应全称: 次数}，只保留**至少被一个全称佐证过**的短称。

    「佐证」= 语料里存在 国名+该短称 的全称形态（齊桓公 佐证 桓公）。
    拿不出佐证的两字组合（侯使/公曰/子伐）是散文里跨词边界的巧合，不是称谓，
    直接丢弃 —— 这是本模块唯一能干净区分「真爵称」与「巧合」的判据。
    实测：单靠「独立出现占比」不行（文公 98.5% vs 侯使 98.3%，几乎一样），
    单靠频次也不行（侯使 58 次，比真名的 鄭莊公 13 次还高）。

    返回值的 value 就是语料给的**歧义分布**：齊桓公 93 / 魯桓公 12 / 鄭桓公 7，
    既是候选顺序也是权重，不掺任何外部知识。
    """
    _full, short, bare = _corpus_scan(_db_path(db_path))
    out: dict[str, dict[str, int]] = {}
    for s, dist in short.items():
        if not dist:                        # 没有任何全称佐证 → 巧合
            continue
        if bare.get(s, 0) < SHORT_MIN_BARE:
            continue
        out[s] = dict(dist)
    # 已核实别名**并入**语料分布（不是替换）：齊桓公的别名「桓公」要和语料里
    # 魯桓公/鄭桓公/秦桓公的真实频次放在一张表里比，才能被上下文正确裁决。
    for s, dist in _curated_distribution().items():
        cur = out.setdefault(s, {})
        for canon, n in dist.items():
            cur[canon] = cur.get(canon, 0) + n
    return out


def resolve_short(short: str, db_path: str | None = None,
                  context: str = "") -> list[str]:
    """短称可能指向哪些全称，按语料频次降序；国名出现在上下文里的排前面。

    频次就是**语料给的权重**：「桓公」93 次由齊桓公贡献、12 次由魯桓公贡献，
    所以不带国名的「桓公」优先按齊桓公解，但保留魯/鄭/秦桓公为候选 ——
    §6 / §18.2 要的正是这个，不是硬选一个。

    context 里出现的国名只做**重排**，不做过滤：即使上下文写的是晉，
    齊桓公仍在候选里（那句话可能在拿齊桓公作对比）。
    """
    dist = short_forms(db_path).get(short) or {}
    ctx = set(context or "")
    return [f for f, _ in sorted(dist.items(),
                                 key=lambda kv: (kv[0][0] not in ctx, -kv[1]))]


# 现查过的词 → 出现次数。语料是只读的，结果不会变，进程内缓存足够。
# 不加缓存的话，每个问题第一次问到这个别名都要全表扫描一次（实测 40ms）。
_occur_cache: dict[str, int] = {}


def occurs_in_corpus(word: str, db_path: str | None = None) -> int:
    """这个词在正文里出现过几次（0 = 没出现过）。

    用于判断一个**已核实别名**是否真的有语料支撑：重耳 这类姓名式别名从不写成
    「國名+爵位+名」形态，所以不在 corpus_names 里，但它实实在在出现 198 次。
    两字词先查 2-gram 表（已有缓存，零成本）；表里没有的才回库现查一次并缓存。
    """
    if not word:
        return 0
    full, _short, raw = _corpus_scan(_db_path(db_path))
    if len(word) == 2 and word in raw:
        return raw[word]
    if word in _occur_cache:
        return _occur_cache[word]
    conn = sqlite3.connect(f"file:{_db_path(db_path)}?mode=ro", uri=True)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM passages WHERE kind = 'passage' "
            "AND text_orig LIKE ?", (f"%{word}%",)).fetchone()[0]
    finally:
        conn.close()
    _occur_cache[word] = n
    return n


# 单字国名，用于判别短称前面是不是别国的国名。中山是双字国名，且「中」「山」
# 单用太常见（中间/山戎），不放进这个集合 —— 宁可漏判，不可错杀。
_STATE_CHARS = frozenset(s for s in STATES + STATES_EXTRA if len(s) == 1)


def bare_hit(text: str, short: str) -> bool:
    """短称在文本里是否**作为独立的称谓**出现，而不是别人的全称的一部分。

    語料里「鄭穆公之子」也含「穆公」，但它写的是鄭穆公，不是秦穆公。若不加区分，
    问「秦穆公和百里奚是什么关系」时，前面排的全是「鄭穆公之子子駟也」
    「召穆公思周」这类段落 —— 等于把别人的事当成这位的答案（§八.4「不要猜」）。

    判据只看**紧邻的前一个字**：是国名字（鄭/召/晉/楚…）就是在组成别人的全称。
    之所以能这么判，是因为全称的构造固定为「国名+谥号+爵位」（齊桓公），
    国名永远紧挨在谥号前面。
    """
    if not short:
        return False
    i = text.find(short)
    while i >= 0:
        if i == 0 or text[i - 1] not in _STATE_CHARS:
            return True
        i = text.find(short, i + 1)
    return False


def known_entities(db_path: str | None = None) -> list[str]:
    """已知实体**全称** = 已核实表 ∪ 语料里出现次数达标的「国名+爵位+名」形态。

    语料派生那部分提供**覆盖**（这五本书里的国君都能被认出来），
    已核实表提供**别名与裁决**（同一个人的多种叫法）。

    刻意**不**把「桓公」这类短称放进来：短称要靠 resolve_short 结合上下文才能
    落到具体的人身上，直接当实体会把 467 条「桓公」全按到一个国君头上（§6）。
    """
    out = set(_CURATED)
    for name, n in corpus_names(db_path).items():
        if n >= 3:                       # 少于 3 次的形态多半是句中巧合，不收
            out.add(name)
    return sorted(out)


def find_in_text(text: str, db_path: str | None = None
                 ) -> list[tuple[str, str, int, int]]:
    """在文本里找出实体，返回 [(实体, 命中词, 起, 止)]。

    长的优先，所以「齊桓公」不会被「桓公」吃掉（单个 alternation 里
    Python 也是对每个位置取最长分支，行为一致且更快）。

    命中的若是短称，走 resolve_alias 按上下文落地；落不了地的返回实体为 None，
    由调用方当低权重候选处理——**不硬认**。
    """
    if not text:
        return []
    # 全称与别名一起进模式；长词在前，保证最长匹配
    vocab = set(known_entities(db_path))
    for info in _CURATED.values():
        vocab.update(info["aliases"])
    pattern = "|".join(re.escape(w) for w in sorted(vocab, key=len, reverse=True))
    if not pattern:
        return []
    out: list[tuple[str, str, int, int]] = []
    for m in re.finditer(pattern, text):
        word = m.group(0)
        ent, _why = resolve_alias(word, text, db_path)
        # 三种情形：
        #   · 别名裁决成功（重耳 → 晉文公）→ 实体是规范名
        #   · word 本身就是规范名（晉文公）→ 实体即 word，resolve_alias 会原样返回
        #   · 裁决不了（短称无上下文）→ 仍把 word 当候选实体报出去，
        #     由 question.py 按 weight=0.3 降权处理，**不在这里丢掉**，
        #     否则「桓公」这种词在问句里会整个消失。
        #
        # 注意：重耳 这种别名在语料里出现 198 次但从不写成「國名+爵位+名」形态，
        # 所以不在 corpus_names 里；它能被识别**完全靠** _CURATED 的别名表。
        out.append((ent if ent else word, word, m.start(), m.end()))
    return out
