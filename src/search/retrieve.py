"""第四阶段：面向问题的召回与事件聚合。

召回策略（这是第四阶段最关键的一处设计）
------------------------------------------
**实体命中的段落必须被单独捞出来，不能和意图词命中的段落混在一个池子里排序。**

原因是实测出来的：`終`/`亡`/`殺` 这类词全库各处都是，如果和实体平起平坐地
OR 在一起，召回池（取前 N 条）会被纯意图词命中的噪声灌满，真正写着
「齊桓公卒。」的段落根本进不了池子。实测就是这样：问「齊桓公何时死」，
排在前面的是「終取之必以亡」这种段落，实体一次都没出现。

所以召回分两池：

  · **实体池**：任一实体词命中 → 必收。问谁就找谁，这是用户真正的约束。
  · **兜底池**：没有任何实体命中时（问句里没识别出人物，或者语料里根本没这个人），
    才用意图强词去兜。

两池分开还有一个好处：能如实回答「语料里没有这个人」—— §18.3 的董卓测试
要求系统不能凭空造答案，那就必须允许「实体池为空」这个结论存在。

事件聚合（§10 / §11）
---------------------
按**事件**把片段聚起来，而不是按关键词。聚合键来自史料本身：
同一文件、`# src:` 段号相同、或行号相邻（沿用 Phase 3 result_block 的边界判断）。

不同史书**保持独立**：齊桓公之死在《左傳》和《史記》里是两条记录，绝不合并成
一条 —— 合并等于替用户裁决史料的异同（§八.4「不要猜」）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from search import query_expansion, question as question_mod, ranking

# 召回上限：够覆盖一个问题的全部相关史料，又不至于把库全扫一遍
POOL_LIMIT = 2000


@dataclass
class Retrieved:
    """召回结果 + 过程中的可复核信息。"""
    question: object                        # question.Question
    expanded: object                        # query_expansion.Expanded
    entity_pool: list = field(default_factory=list)   # ranking.Candidate
    fallback_pool: list = field(default_factory=list)
    ranked: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _cand(r) -> ranking.Candidate:
    return ranking.Candidate(
        passage_id=r["passage_id"], file_id=r["file_id"], row_no=r["row_no"],
        seq=r["seq"] or 0,
        text_orig=r["text_orig"] or "", book_id=r["book_id"],
        book_title=r["title"] or "", layer=r["layer"] or "main")


_SELECT = """SELECT p.passage_id, p.file_id, p.row_no, p.seq, p.text_orig, p.layer,
                    b.book_id, b.title
             FROM passages p
             JOIN files f ON f.file_id = p.file_id
             JOIN books b ON b.book_id = f.book_id
             WHERE p.kind = 'passage' AND p.normalized_text LIKE ? ESCAPE '\\'"""


def _like(cur, term: str, limit: int) -> list:
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    sql = _SELECT + " LIMIT ?"
    return [_cand(r) for r in cur.execute(sql, (f"%{esc}%", limit))]


def _like_any(cur, terms: list[str], limit: int) -> list:
    """一次扫库取出命中**任一**词的段落。

    LIKE '%x%' 用不上索引，一个词就是一次全表扫描（实测 20 万行约 76ms）。
    问一句「齊桓公什么时候死的」要查三个词（齊桓公/桓公/小白），三次扫描就是
    230ms —— 合并成一次 OR 扫描，结果完全相同（都是「命中任一词」的并集，
    调用方本来就是取并集去重），耗时降到三分之一。
    """
    terms = [t for t in terms if t]
    if not terms:
        return []
    conds, args = [], []
    for t in terms:
        esc = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append("p.normalized_text LIKE ? ESCAPE '\\'")
        args.append(f"%{esc}%")
    sql = (_SELECT.replace("WHERE p.kind = 'passage' AND p.normalized_text LIKE ? ESCAPE '\\'",
                           "WHERE p.kind = 'passage' AND (" + " OR ".join(conds) + ")")
           + " LIMIT ?")
    args.append(limit)
    return [_cand(r) for r in cur.execute(sql, args)]


def retrieve(cur: sqlite3.Cursor, qtext: str) -> Retrieved:
    """一句问题 → 排序好的候选史料。全程只读，不写库、不改原文。"""
    q = question_mod.analyze(qtext)
    exp = query_expansion.expand(q)
    out = Retrieved(question=q, expanded=exp)

    # 实体词按扩展层分好的档位取用（档位依据见 query_expansion._term_roles）
    entity_terms = [t for g in exp.groups if g.role == "entity" for t in g.terms]
    alias_terms = [t for g in exp.groups if g.role == "alias" for t in g.terms]
    weak_terms = [t for g in exp.groups if g.role == "weak" for t in g.terms]
    intent_terms = [t for g in exp.groups if g.role == "intent" for t in g.terms]
    weak_intent = [t for g in exp.groups if g.role == "intent_weak"
                   for t in g.terms]

    seen: set[int] = set()
    for c in _like_any(cur, entity_terms + alias_terms + weak_terms, POOL_LIMIT):
        if c.passage_id not in seen:
            seen.add(c.passage_id)
            out.entity_pool.append(c)
    out.notes.append(f"实体池：{len(out.entity_pool)} 条（实体词 {entity_terms + alias_terms + weak_terms}）")

    topic_terms = [t for g in exp.groups if g.role == "topic" for t in g.terms]
    if not out.entity_pool:
        # 没有人物实体时，「城濮之战」这类**事件名**就是用户真正的约束，
        # 所以主题词要和意图词一起兜底 —— 只兜意图词会让这类问句空手而归
        # （实测：「城濮之战谁赢了」曾经返回 0 条，而语料里「城濮」有大把段落）。
        seen2: set[int] = set()
        for c in _like_any(cur, topic_terms + intent_terms, POOL_LIMIT):
            if c.passage_id not in seen2:
                seen2.add(c.passage_id)
                out.fallback_pool.append(c)
        out.notes.append(
            f"实体池为空，兜底池：{len(out.fallback_pool)} 条"
            f"（主题词 {topic_terms} + 意图强词 {intent_terms}）")
        if not out.fallback_pool:
            out.notes.append("语料中没有该实体，也没有可用的意图词 —— 如实返回空，不编造")
            return out

    pool = out.entity_pool or out.fallback_pool
    # 用户在问句里**实际写下的**称谓（「重耳」而不是规范名「晉文公」）——
    # 命中它比命中规范名更该算数，交给排序层当精确命中处理。
    asked_forms = [e["matched"] for e in q.entities if e.get("matched")]
    entity_forms = _entity_forms(q)
    out.ranked = ranking.rank(pool, entity_terms, alias_terms, weak_terms,
                              intent_terms, getattr(exp, "asks_duration", False),
                              weak_intent, topic_terms, asked_forms, entity_forms)

    # 问「A 和 B 是什么关系」时，若**没有任何一段**同时写着这两个人，必须如实
    # 说清楚：下面的结果只是分别提到其中一方。不说的话，用户会把「穆公相之」
    # 当成两者的关系记载 —— 那就是系统在替史料下结论（§八.4「不要猜」）。
    #
    # 判定必须用**全部写法**，不能只看规范名：语料里写的是「秦繆公」，只查
    # 「秦穆公」会得出「没有一段同时写着两人」这个**假的**结论 —— 实测語料里
    # 就有 2 段同时写着 繆公 与 百里奚（「繆公」正是秦穆公的已核实别名）。
    # 系统可以少说，不能说错。
    if len(q.entities) >= 2:
        groups = entity_forms
        both = [c for c in out.entity_pool
                if all(ranking.has_form(c.text_orig or "", g) for g in groups)]
        names = " 与 ".join(e["entity"] for e in q.entities)
        if not both:
            if len(out.entity_pool) >= POOL_LIMIT:
                # 池子被截断时不敢断言「语料里没有」，只说清检索范围
                out.notes.append(
                    f"在前 {POOL_LIMIT} 条候选里没有任何一段同时写着 {names} —— "
                    f"以下结果只是分别提到其中一方，系统不据此推断两者的关系")
            else:
                out.notes.append(
                    f"语料中没有任何一段同时写着 {names} —— 以下结果只是分别提到"
                    f"其中一方，系统不据此推断两者的关系")
        else:
            out.notes.append(
                f"语料中有 {len(both)} 段同时写着 {names}，已优先排在前面")
    return out


# 实体词按可核实的档位取用（档位依据见 query_expansion._term_roles）：
# 规范名用字面比对，别名/短称要过 bare_hit —— 否则「鄭穆公」里的「穆公」
# 会被当成秦穆公（判据见 entities.bare_hit）。
def _entity_forms(q) -> list[list[tuple[str, str]]]:
    """每个实体在语料里的全部写法 [(档位, 词), …]，用于判定「同段」。"""
    out = []
    for e in q.entities:
        out.append([(role, t) for role, t in
                    query_expansion._term_roles(e["entity"], e["matched"]) if t])
    return out




def as_dict(res: Retrieved, top: int = 20, cur=None, mode: str | None = None,
            with_events: bool = True, text_mode: str = "orig") -> dict:
    """给 API 层用的可序列化结构。文本一律原样来自 text_orig，不做任何加工。

    `cur` 传了才会做事件聚合（聚合要读库组装片段，是额外的一次取数）。
    """
    out = _base_dict(res, top)
    if cur is not None and with_events:
        from search import aggregate as agg
        out["aggregation"] = agg.aggregate(
            cur, res.ranked, mode or agg.RB.DEFAULT_MODE, text_mode=text_mode)
    return out


def _base_dict(res: Retrieved, top: int = 20) -> dict:
    return {
        "question": res.question.as_dict(),
        "expanded": res.expanded.as_dict(),
        "counts": {
            "entity_pool": len(res.entity_pool),
            "fallback_pool": len(res.fallback_pool),
        },
        "notes": res.notes,
        "results": [
            {
                "passage_id": c.passage_id,
                "file_id": c.file_id,
                "row_no": c.row_no,
                "book_id": c.book_id,
                "book_title": c.book_title,
                "layer": c.layer,
                "text_orig": c.text_orig,
                "score": c.score,
                "hits": c.hits,
                "detail": c.detail,
            }
            for c in res.ranked[:top]
        ],
    }
