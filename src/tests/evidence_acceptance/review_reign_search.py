"""Replay a frozen provisional reign sample through the unified desktop form."""
import argparse
import hashlib
import json
from pathlib import Path

from desktop.runtime import DesktopServer
from search import punctuated_search as search
from search.semantic_bridge import worker
from tests.evidence_acceptance.run_search_evidence_review import submit_form, verify_source, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    data = (root / 'src/data').resolve()
    source, out = args.frozen.resolve(), args.out.resolve()
    if not source.is_relative_to(data) or not out.is_relative_to(data) or out.exists():
        parser.error('Use frozen input and a new output directory under src/data')
    raw = source.read_bytes()
    entries = json.loads(raw)['cases']
    if not entries:
        parser.error('Empty sample')
    with search.connect() as index:
        for entry in entries:
            quote = search.normalize(entry['case']['gold_sources'][0]['quote'])
            for pin in entry['edition_pins']:
                row = index.execute('SELECT payload FROM chunks WHERE chunk_id=?',(pin['chunk_id'],)).fetchone()
                chunk = json.loads(row[0]) if row else {}
                assert all(chunk.get(key) == value for key,value in pin.items()), pin
                assert quote and quote in search.normalize(chunk['text_orig']), pin
    out.mkdir()
    report = dict(frozen_sha256=hashlib.sha256(raw).hexdigest(), cases=[])
    try:
        with DesktopServer(root) as server, search.connect() as index:
            for i,entry in enumerate(entries,1):
                case = entry['case']
                pins = {item['chunk_id'] for item in entry['edition_pins']}
                query = case['query']
                result, form = submit_form(server.entry,query,accept_keyword=True)
                write_json(out / f'case-{i:02d}.json',dict(result=result,form=form))
                for hit in result['hits']:
                    verify_source(index,hit['chunk'])
                baseline_rank = next((n for n,cid in enumerate(entry['original_ids'],1) if cid in pins),None)
                rank = next((n for n,cid in enumerate(form['rendered'],1) if cid in pins),None)
                diagnostics = result.get('evidence_pipeline',{}).get('candidate_diagnostics',[])
                row = dict(query=query,period_id=case['period_id'],mode=result['mode'],
                           requests=form['requestPaths'],original_total=entry['original_total'],
                           baseline_rank=baseline_rank,rank=rank,
                           ranked_evidence=[item for item in diagnostics if item['chunk_id'] in pins],
                           form_ms=round(form['form_ms'],2))
                report['cases'].append(row)
                write_json(out/'summary.json',report)
                print(query,result['mode'],baseline_rank,rank,row['form_ms'],flush=True)
    finally:
        worker.close()


if __name__ == '__main__':
    main()
