"""Capture the default question results for two independent manual reviews.

Retrieval: a reviewer reads the first five hits and their source context.
Any sufficient passage can count, including a parallel source. The locator
below only tracks where retrieval and ranking placed the designated passage.

Evidence: review every displayed hit flagged by the rules and any structured
causal explanation. Check actor, action, object, state, the requested detail,
and attribution against the text. For a sufficient passage with no clue flag,
also record the missed flag. A negative case measures false flags, not recall.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path

from api import db
from search import punctuated_search as search
from search.evidence_pipeline import VERSION
from search.semantic_bridge import worker
from scripts.pipeline import config


CASES = {
    "existing17": {
        "time-han": "answerable",
        "battle-tang": "answerable",
        "office-song": "answerable",
        "battle-jin": "answerable",
        "negative-role": "reversed_roles",
    },
    "causal7": {"chao": "answerable"},
    "causal_independent6": {
        "tao_pengze": "answerable",
        "li_refusal": "answerable",
        "li_resign": "different_event",
    },
}
SOURCE_KEYS = ("page_id", "revision_id", "source_sha256", "source_url", "parts", "text_orig")


def read_cases():
    cases = []
    fixture_hashes = {}
    for fixture_name, selected in CASES.items():
        fixture = Path(__file__).with_name(fixture_name + ".json")
        raw = fixture.read_bytes()
        fixture_hashes[fixture.name] = hashlib.sha256(raw).hexdigest()
        rows = {case["id"]: case for case in json.loads(raw)}
        for case_id, policy in selected.items():
            case = rows[case_id]
            # Track the true event as a contrast locator for the reversed query.
            source = rows["place-jin"] if policy == "reversed_roles" else case
            reference = source["chunk"]
            quote = source.get("gold_quote") or source["evidence"]
            if quote not in reference["text_orig"]:
                raise ValueError(f"{case_id}: reference quote is absent")
            cases.append(dict(id=case_id, query=case["query"], policy=policy,
                              reference=reference, quote=quote))
    return cases, fixture_hashes


def verify_source(conn, chunk):
    row = conn.execute("SELECT payload FROM chunks WHERE chunk_id=?", (chunk["chunk_id"],)).fetchone()
    stored = json.loads(row[0]) if row else {}
    mismatches = [key for key in SOURCE_KEYS if chunk[key] != stored.get(key)]
    if mismatches:
        raise ValueError(f"Source mismatch for {chunk['chunk_id']}: {', '.join(mismatches)}")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    out = parser.parse_args().out.resolve()
    data = (Path(__file__).resolve().parents[2] / "data").resolve()
    if out == data or not out.is_relative_to(data):
        parser.error("--out must be a new directory under src/data")
    cases, fixture_hashes = read_cases()
    out.mkdir(parents=True, exist_ok=False)
    with search.connect() as index:
        for case in cases:
            verify_source(index, case["reference"])
    write_json(out / "inputs.json", dict(cases=cases, fixture_sha256=fixture_hashes,
                                        database=search.identity(config.DB_PATH),
                                        index=search.status()["manifest"], pipeline=VERSION))
    summary = []
    try:
        with closing(db.connect()) as conn, search.connect() as index:
            for case in cases:
                result = search.run(case["query"], mode="evidence", cur=conn.cursor())
                hits = result["hits"]
                for hit in hits:
                    verify_source(index, hit["chunk"])
                    for neighbor in hit["context_chunks"]:
                        verify_source(index, neighbor)
                pipeline = result["evidence_pipeline"]
                diagnostics = pipeline["candidate_diagnostics"]
                expected_order = [d["chunk_id"] for d in diagnostics if d["displayed"]]
                actual_order = [h["chunk"]["chunk_id"] for h in hits]
                if expected_order != actual_order:
                    raise ValueError(f"{case['id']}: candidate order differs from results")
                ref = case["reference"]["chunk_id"]
                row = dict(
                    id=case["id"], query=case["query"], policy=case["policy"],
                    reference=ref, top5=actual_order[:5], hits=len(hits),
                    reference_ranks=next((d for d in diagnostics if d["chunk_id"] == ref), None),
                    flagged=[dict(rank=i, chunk_id=h["chunk"]["chunk_id"])
                             for i, h in enumerate(hits, 1)
                             if h["evidence_audit"]["status"] == "clues_present"],
                    model_generation_calls=result["model_generation_calls"],
                    timings=result["timings"], elapsed_ms=result["elapsed_ms"],
                )
                write_json(out / (case["id"] + ".json"), result)
                summary.append(row)
                write_json(out / "summary.json", summary)
                print(case["id"], row["top5"], row["elapsed_ms"], flush=True)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
