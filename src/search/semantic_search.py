"""Experiment: semantic candidates + unchanged lexical retrieval, auditable RRF."""
import json
import sqlite3
import time

from search import engine
from search import semantic_bridge as bridge


def fuse(semantic, lexical, limit=20):
    if not 1 <= limit <= 100:
        raise ValueError("invalid result limit")
    candidates = {}
    for label, hits in (("semantic", semantic), ("keyword", lexical)):
        for rank, hit in enumerate(hits, 1):
            cid = hit["chunk_id"]
            item = candidates.setdefault(cid, {**hit, "rrf": 0.0, "channels": [], "ranks": {}})
            if label in item["channels"]:
                continue
            item["rrf"] += 1 / (60 + rank)
            item["channels"].append(label)
            item["ranks"][label] = rank
            if hit.get("semantic_score") is not None:
                item["semantic_score"] = hit["semantic_score"]
            if hit.get("context_clues") is not None:
                item["context_clues"] = hit["context_clues"]
    ordered = sorted(candidates.values(), key=lambda x: (-x["rrf"], x["chunk_id"]))
    # Adjacent overlapping chunks must not occupy many display slots.
    selected, seen = [], []
    for item in ordered:
        spans = [(p["passage_id"], p.get("start", 0), p.get("end", 1)) for p in item["chunk"]["parts"]]
        def overlap(other):
            shared = sum(max(0, min(a[2], b[2]) - max(a[1], b[1])) for a in spans for b in other if a[0] == b[0])
            return shared / max(1, min(sum(e-s for _,s,e in spans), sum(e-s for _,s,e in other)))
        if any(overlap(other) >= 0.8 for other in seen):
            continue
        selected.append(item)
        seen.append(spans)
        if len(selected) == limit:
            break
    return selected


def run(cur, query, book=None, mode="hybrid"):
    if mode not in ("semantic", "hybrid", "expanded", "contextual"):
        raise ValueError("仅支持semantic、hybrid、expanded或contextual实验模式")
    if mode == 'contextual':
        from search.semantic_contextual import open_index
        with open_index():
            pass  # Reject missing/stale sidecars before loading the model.
    started = time.perf_counter()
    semantic_result = bridge.worker.query(query, book=book, top_k=60)
    semantic = semantic_result["hits"]
    lexical, keyword_passages = [], []
    plan, searches = None, []
    if mode == 'expanded':
        from search.semantic_lexical_indexed import lexical as planned_lexical
        lexical, searches, plan, keyword_passages = planned_lexical(cur, query, book)
    if mode == 'contextual':
        from search.semantic_contextual import lexical as context_lexical
        lexical, searches, plan, keyword_passages = context_lexical(cur, query, book)
    if mode == "hybrid":
        # Literal baseline stays literal; a query with no lexical hits remains visible as zero.
        keyword = engine.run_search(cur, query, book=book, page_size=100, variants=True)
        keyword_passages = [x["passage_id"] for x in keyword["items"]]
        path = bridge.state_dir() / "index/chunks.sqlite"
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            used = set()
            for pid in keyword_passages:
                for cid, payload in conn.execute("SELECT c.chunk_id,c.payload FROM chunks c JOIN chunk_passages p "
                                                "ON p.chunk_id=c.chunk_id WHERE p.passage_id=? ORDER BY c.chunk_id", (pid,)):
                    if cid not in used:
                        used.add(cid)
                        lexical.append({"chunk_id": cid, "chunk": json.loads(payload)})
        finally:
            conn.close()
    hits = fuse(semantic, lexical)
    result = {"query": query, "mode": mode, "hits": hits, "model": "BAAI/bge-small-zh-v1.5",
            "candidate_counts": {"semantic": len(semantic), "keyword_chunks": len(lexical), "keyword_passages": len(keyword_passages)},
            "semantic_candidate_ids": [x["chunk_id"] for x in semantic],
            "keyword_candidate_ids": [x["chunk_id"] for x in lexical],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2), "model_generation_calls": 0,
            "notes": ["实验结果按相似性排序，不代表史实成立或引用已核验。", "当前仅索引正文与括注候选，不含目录、附录等其他层级。",
                      "混合检索的关键词通道使用原始问题，不把规则扩展冒充语义解析。"]}
    if mode in ('expanded', 'contextual'):
        result.update(query_plan=plan, lexical_searches=searches)
        result['notes'][-1] = '规则通道提取检索线索并扩展古文表达，与语义候选融合；尚未验证每条结果中的主体和关系。'
        if mode == 'expanded':
            from search.semantic_lexical_indexed import VERSION
            result['lexical_backend'] = VERSION
        if mode == 'contextual':
            result['notes'][-1] = '按原文片段检索相邻行中的线索；共现和字距不等于主体归属、因果或否定关系已经核实。'
    import logging
    from logging.handlers import RotatingFileHandler
    import uuid
    trace_id = uuid.uuid4().hex
    logger = logging.getLogger("yinde.semantic.queries")
    if not logger.handlers:
        logger.addHandler(RotatingFileHandler(bridge.state_dir() / "queries.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8"))
        logger.setLevel(logging.INFO)
        logger.propagate = False
    logger.info(json.dumps({"trace_id": trace_id, "query": query, "book": book, "mode": mode,
                           "query_plan":plan,"lexical_searches":searches,"lexical_backend":result.get("lexical_backend"),
                           "semantic_candidates": result["semantic_candidate_ids"], "keyword_candidates": result["keyword_candidate_ids"],
                           "selected_chunks": [x["chunk_id"] for x in hits], "elapsed_ms": result["elapsed_ms"]}, ensure_ascii=False))
    result["trace_id"] = trace_id
    return result
