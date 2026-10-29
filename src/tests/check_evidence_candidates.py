"""Paired real-model replay of frozen source evidence; no fixture editing."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from search import evidence_pipeline as E, punctuated_search as S, semantic_bridge as B
from check_punctuated_relevance import matches, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('step', choices=('before', 'after', 'focused'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'src/data'):
        raise ValueError('Results must stay within repository data')
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    digests = {}
    for name in ('punctuated_relevance_queries.json', 'punctuated_relevance_holdout.json', 'evidence_pipeline_holdout.json'):
        path = ROOT / 'src/tests' / name
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        cases.extend(json.loads(path.read_text(encoding='utf8'))['cases'])
    evaluate = E.evaluate
    try:
        with sqlite3.connect((ROOT / 'src/data/database/history.db').as_uri() + '?mode=ro', uri=True) as main_db, S.connect() as corpus:
            for case in cases:
                path = output / (case['id'] + '-' + args.step + '.json')
                if path.exists():
                    assert json.loads(path.read_text(encoding='utf8'))['fixture_hashes'] == digests
                    print('retained', path.name, flush=True)
                    continue
                for g in case['gold']:
                    c = json.loads(corpus.execute('SELECT payload FROM chunks WHERE chunk_id=?', (g['chunk_id'],)).fetchone()[0])
                    assert matches({'chunk': c}, [g]), case['id']
                    assert c['revision_id'] == g['revision_id']
                if args.step == 'before':
                    pools = {}
                    def capture(query, conn, dense, batches, book=None, **kwargs):
                        pools.update(dense=dense, batches=batches)
                        return evaluate(query, conn, dense, batches, book, **kwargs)
                    with patch.object(E, 'evaluate', side_effect=capture):
                        result = S.run(case['query'], mode='evidence', cur=main_db.cursor())
                    record = dict(case=case, pools=pools, result=result)
                else:
                    baseline = json.loads((output / (case['id'] + '-before.json')).read_text(encoding='utf8'))
                    assert baseline['fixture_hashes'] == digests
                    pools = baseline['pools']
                    result = evaluate(case['query'], corpus, pools['dense'], pools['batches'])
                    record = dict(case=case, result=result)
                for h in result['hits']:
                    for c in [h['chunk']] + h['context_chunks']:
                        stored = json.loads(corpus.execute('SELECT payload FROM chunks WHERE chunk_id=?', (c['chunk_id'],)).fetchone()[0])
                        assert c == stored
                    assert not h['evidence_audit']['relation_verified']
                    if 'evidence_focus' in h:
                        f=h['evidence_focus']
                        assert f['text']==h['chunk']['text_orig'][f['start']:f['end']]
                ids = result.get('candidate_ids', result.get('audited_candidate_ids', []))
                admitted = [dict(chunk=json.loads(corpus.execute('SELECT payload FROM chunks WHERE chunk_id=?', (cid,)).fetchone()[0])) for cid in ids]
                record.update(fixture_hashes=digests, top=metrics(result['hits'], case), admission=metrics(admitted, case))
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
                print(case['id'], 'admitted', record['admission']['best_rank'], 'top', record['top']['best_rank'], flush=True)
    finally:
        B.worker.close()


if __name__ == '__main__':
    main()
