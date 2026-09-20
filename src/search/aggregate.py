"""第四阶段：事件级聚合与不同史书的对照。

## 这一层不重新发明「事件边界」

任务书要求「片段 → 事件级聚合 → 不同史书对照」。事件边界**不由本模块决定**，
而是复用第三阶段已经定好的 Result Block（`search/result_block.py`）：
一个 block 就是一段可连续阅读的史料，它的边界由语料自身的证据收口 ——
同一文件、同一结构键（section/subsection/ab）、同一个 `# src:` 段号、
同一 layer。本模块只做两件事：

  1. 把第四阶段的**相关性得分与命中理由**贴回每个 block（Phase 3 只有 bm25
     或 None，看不出「为什么这段和问题有关」）；
  2. 把 block 按**史书**分开摆好，供前端做对照展示。

之所以不另写一套事件切分：`result_block._expand` 的停止条件就是「同一件事的
范围」的判据，重写一遍必然出现两种口径，用户会看到同一段史料在两个页面上
边界不同。文本也一律沿用 block 的 `text`（由 `text_orig` 直接相接），
**本模块不截取、不拼接、不改写一个字**（§2.2）。

## 不同史书绝不合并

齊桓公之死在《春秋左傳》和《史記》里是两条各自独立的记载。把它们合成一条
「事件」等于替用户裁决两部史书的异同 —— 那是史学研究，不是检索系统该做的
（§八.4「不要猜」）。所以本模块只做**并列**（`sources`），不做合并：
每部史书的记载各自成条，各自带自己的书名、篇卷、原文。

## 为什么事件里可能只有一个 block

因为没有证据说更多。`aggregate` 只在**同文件 + 结构键完全相同**时才把多个
block 并成一个事件（这种情形出现在命中相距很远、Phase 3 的区间合并不上、
但结构键证明它们同属一段的时候）。拿不出这种证据时，一段就是一件事 ——
宁可让用户多点开几条，也不把两件事说成一件事（§十：宁可短，不可臆造）。
"""
from __future__ import annotations

from search import dual_text
from search import result_block as RB

# 送进 block 组装的命中条数。40 条足够覆盖一个问题的相关史料，又不会让
# 组装过程（每文件都要取行窗口）变慢 —— 实测 40 条在 100ms 量级。
TOP_FOR_BLOCKS = 40


def _struct_key(block: dict) -> tuple | None:
    """block 的结构键：完全相同才算同一段。字段缺失时返回 None（不猜）。"""
    parts = (block.get("juan"), block.get("section"), block.get("subsection"),
             block.get("division"), block.get("ab"))
    if not any(parts):
        return None
    return parts


# 分簇间距：同一文件里的两条命中相隔超过这个行数就分两批组装。
# 取 Phase 3 行窗口上限的一半 —— 窗口宽 600 行、两侧还各留 20 行余量，
# 半窗一组能保证每组的命中都落在自己那次的窗口内。
CLUSTER_GAP = RB.WINDOW_HARD_CAP // 2


def _cluster(hits: list) -> list[list]:
    """把命中按「同文件 + 行号相近」分批。

    为什么必须分批：Phase 3 的 `_FileCache.window` 一次最多取 600 行（任务书
    §二十一禁止整文件入内存）。若把同一文件里相距几千行的命中塞进同一次调用，
    窗口只会盖住靠前的那几条，其余的在 `pos.get(pid) is None` 处被静默跳过 ——
    实测「管仲是怎么死的」40 条命中只装配出 17 个片段，《左傳》里真正的答案
    「管仲卒受下卿之禮」因为行号离别的命中太远，一个片段都没生成。
    分批之后每批各自的窗口都盖得住，组装结果再按下文去重。
    """
    groups: dict[int, list] = {}
    for h in hits:
        groups.setdefault(h[1], []).append(h)       # 按 file_id 分组
    out = []
    for _fid, hs in groups.items():
        hs.sort(key=lambda h: (h[2], h[3]))         # (row_no, seq)
        cur_batch = [hs[0]]
        for h in hs[1:]:
            if h[2] - cur_batch[0][2] > CLUSTER_GAP:
                out.append(cur_batch)
                cur_batch = [h]
            else:
                cur_batch.append(h)
        out.append(cur_batch)
    return out


def _dedupe(blocks: list) -> list:
    """分批组装会产生重叠片段：区间被更大片段包住的丢掉（保宽的）。

    只做包含判断，不做并集 —— 并集需要两侧行号都在同一个窗口里，跨批做不到；
    硬并会拼出中间缺行的正文，那是伪造原文（§2.2）。
    """
    kept: list[dict] = []
    for b in sorted(blocks, key=lambda b: (b["file_id"], b["row_first"],
                                           -b["n_chars"])):
        for k in kept:
            if (k["file_id"] == b["file_id"]
                    and k["row_first"] <= b["row_first"]
                    and k["row_last"] >= b["row_last"]):
                k["match_count"] += b["match_count"]
                break
        else:
            kept.append(b)
    return kept


def aggregate(cur, ranked: list, mode: str = RB.DEFAULT_MODE,
              top: int = TOP_FOR_BLOCKS,
              text_mode: str = dual_text.DEFAULT_MODE) -> dict:
    """排序好的候选 → 事件（含跨史书对照）。

    `ranked` 是 ranking.rank() 的输出（ranking.Candidate 列表，已按相关度降序）。
    返回的每个事件都带 `text`（原文，来自 text_orig）、`relevance`（第四阶段的
    相关度）与 `why`（逐项得分，可复核）。
    """
    picked = [c for c in ranked[:top] if getattr(c, "seq", None) is not None]
    out = {"events": [], "sources": [], "limits": dict(RB.MODE_LIMITS[mode]),
           "notes": [], "n_blocks": 0}
    if not picked:
        out["notes"].append("没有候选片段，未组装事件")
        return out

    hits = [(c.passage_id, c.file_id, c.row_no, c.seq, c.score) for c in picked]
    # 篇名区间表要走同一条路：问答路径不传的话，同一个查询在搜索页与问答页会
    # 得到**不同的块边界**（史記/國語的正文行没有行级 section，只有区间表能判篇界）。
    sec_idx = RB._section_index(cur)
    blocks_raw: list[dict] = []
    for batch in _cluster(hits):
        blocks_raw.extend(RB.build_result_blocks(cur, batch, mode, sec_idx)["blocks"])

    # 命中 → 打分结果。一个 block 里可能有多个命中（Phase 3 已把区间合并），
    # 取其中**相关度最高**的那条作为这个 block 的「为什么相关」，其余记进
    # also_hits —— 不丢弃，用户可以复核。
    by_pid = {c.passage_id: c for c in picked}
    # 每条候选在排序里的名次：事件排序的第二关键字（见文件末尾 _key）。
    # 这样「排在最前面的事件」就是「排序第一的那条史料所在的段落」，
    # 用户看到的第一个片段必然包含排名最高的命中。
    rank_of = {c.passage_id: i for i, c in enumerate(picked)}
    blocks = []
    for b in _dedupe(blocks_raw):
        inside = [by_pid[p] for p in b["passage_ids"] if p in by_pid]
        if not inside:
            # 结构行（# src: / <pb:>）也被圈进 block 时可能出现：这些行不是命中，
            # 但它们的 text_orig 已被 Phase 3 排除在正文之外，这里照常保留 block。
            inside = [by_pid[b["hit_passage_id"]]] if b["hit_passage_id"] in by_pid else []
        if not inside:
            continue
        best = max(inside, key=lambda c: c.score)
        b = dict(b)
        b["relevance"] = best.score
        b["why"] = {"hits": list(best.hits), "detail": dict(best.detail),
                    "passage_id": best.passage_id}
        b["also_hits"] = [{"passage_id": c.passage_id, "score": c.score,
                           "hits": list(c.hits)} for c in inside if c is not best]
        b["book_id"] = best.book_id
        b["book_title"] = best.book_title
        b["best_rank"] = rank_of[best.passage_id]
        # Phase 3 的 score 槽位在第四阶段装的是**第四阶段相关度**（上面 hits 的第
        # 5 个元素传的是 c.score），不是 bm25；而且 Phase 3 的合并取的是块内最小值，
        # 与这里的 relevance（取最大）口径不同，所以改名保留，免得被当成 bm25 误读。
        b["hit_score_min"] = b.pop("score", None)
        # 繁简双轨：原文留在 text 里不动，简体另存一个字段（见 dual_text）。
        blocks.append(dual_text.attach(b, text_mode))

    # 同文件 + 结构键完全相同 → 并成一个事件（证据不足则各自成事件）。
    merged: list[dict] = []
    for b in blocks:
        key = _struct_key(b)
        for m in merged:
            if (key is not None and m["_key"] == key
                    and m["file_id"] == b["file_id"]):
                m["blocks"].append(b)
                m["match_count"] += b["match_count"]
                m["relevance"] = max(m["relevance"], b["relevance"])
                m["best_rank"] = min(m["best_rank"], b["best_rank"])
                break
        else:
            merged.append({"_key": key, "file_id": b["file_id"],
                           "book_id": b["book_id"], "book_title": b["book_title"],
                           "blocks": [b], "match_count": b["match_count"],
                           "relevance": b["relevance"],
                           "best_rank": b["best_rank"]})

    # 排序：相关度 → 最好那条命中的名次 → 命中条数 → 书/文件/行号。
    # 第二级用「名次」而不是「命中条数」是有原因的：命中多的块往往是**大块**
    # （史記世家一次收十几条），把「齐桓公卒」那句话埋在中间，反而把真正写着他
    # 死亡年月的那一小段挤到后面。按名次排，「排在最前面的事件」就是排序第一的
    # 那条史料所在的段落，用户点开第一件事看到的必然是排名最高的命中。
    # 末三级是数据库里的稳定字段，保证同分时两次请求结果一致。
    def _key(e):
        return (-e["relevance"], e["best_rank"], -e["match_count"],
                e["book_id"] or "", e["file_id"],
                min(x["row_first"] for x in e["blocks"]))

    merged.sort(key=_key)

    events = []
    for i, m in enumerate(merged):
        first = m["blocks"][0]
        ev = {
            "event_id": f"E{i + 1}",
            "book_id": m["book_id"],
            "book_title": m["book_title"],
            "file_id": m["file_id"],
            # 篇卷标签：只如实带出数据库里有的，缺的写 None，不编「第几篇」
            "juan": first.get("juan"),
            "section": first.get("section"),
            "subsection": first.get("subsection"),
            "division": first.get("division"),
            "relevance": round(m["relevance"], 2),
            "best_rank": m["best_rank"],
            "match_count": m["match_count"],
            "n_blocks": len(m["blocks"]),
            "blocks": m["blocks"],
        }
        events.append(ev)

    # 跨史书对照：按史书分开摆，**不合并**。顺序按各书最好的一条的相关度排，
    # 让记载更切题的史书排在前面。
    sources: dict[str, dict] = {}
    for ev in events:
        s = sources.setdefault(ev["book_id"], {
            "book_id": ev["book_id"], "book_title": ev["book_title"],
            "n_events": 0, "best_relevance": ev["relevance"], "event_ids": []})
        s["n_events"] += 1
        s["best_relevance"] = max(s["best_relevance"], ev["relevance"])
        s["event_ids"].append(ev["event_id"])
    src_list = sorted(sources.values(), key=lambda s: (-s["best_relevance"],
                                                       s["book_id"] or ""))

    out["events"] = events
    out["sources"] = src_list
    out["n_blocks"] = len(blocks)
    out["notes"].append(
        f"事件聚合：{len(hits)} 条命中 → {len(blocks)} 个史料片段 → "
        f"{len(events)} 件事，分属 {len(src_list)} 部史书（不同史书各自成条，不合并）")
    if len(picked) < len(ranked):
        out["notes"].append(
            f"只把相关度最高的 {len(picked)} 条命中送去组装片段，"
            f"其余 {len(ranked) - len(picked)} 条未展开（避免组装耗时失控）")
    return out
