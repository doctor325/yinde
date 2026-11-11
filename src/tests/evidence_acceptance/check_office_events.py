"""Small live regression for office evidence, using the local search entry."""
import json
import sys
from contextlib import closing
from pathlib import Path

from api import db
from search import evidence_pipeline as E, punctuated_search as S
from search.semantic_bridge import worker


def main():
    fixtures = Path(__file__).parent
    cases = [
        case
        for filename in ('office_holdout6.json', 'office_independent4.json')
        for case in json.loads((fixtures / filename).read_text(encoding='utf-8'))
    ]
    requested = set(sys.argv[1:])
    if requested:
        if requested - {case['id'] for case in cases}:
            raise SystemExit('Unknown case ID')
        cases = [case for case in cases if case['id'] in requested]
    failures = []
    try:
        with S.connect() as index, closing(db.connect()) as corpus:
            for case in cases:
                plan = E.parse(case['query'])
                local = {}
                for source in case['sources']:
                    cid = source['chunk_id']
                    chunk = json.loads(index.execute(
                        'SELECT payload FROM chunks WHERE chunk_id=?', (cid,)
                    ).fetchone()[0])
                    start = source['offset']
                    assert chunk['text_orig'][start:start+len(source['quote'])] == source['quote']
                    assert all(chunk[key] == source[key] for key in (
                        'page_id', 'revision_id', 'source_sha256', 'source_url', 'heading'))
                    local[cid] = E.audit(chunk, plan, E.context(index, chunk))['status']
                result = S.run(case['query'], mode='evidence', cur=corpus.cursor())
                diagnostics = result['evidence_pipeline']['candidate_diagnostics']
                targets = [d for d in diagnostics if d['chunk_id'] in local]
                confirmed = [d['chunk_id'] for d in diagnostics if d['audit_status'] == 'clues_present']
                passed = (any(d['final_rank'] <= 5 and d['audit_status'] == 'clues_present'
                              and local[d['chunk_id']] == 'clues_present' for d in targets)
                          if case['answerable'] else
                          not confirmed and 'clues_present' not in local.values())
                print(json.dumps(dict(id=case['id'], passed=passed, direct=local, targets=targets,
                                      confirmed=confirmed, ms=result['elapsed_ms'])), flush=True)
                if not passed:
                    failures.append(case['id'])
    finally:
        worker.close()
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
