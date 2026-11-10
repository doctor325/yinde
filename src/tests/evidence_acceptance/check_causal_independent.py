"""Read-only source-integrity check and local causal audit (no model retrieval).

Capability misses are reported, not hidden as successful correctness assertions.
"""
import argparse
import hashlib
import json
from pathlib import Path
from search import evidence_pipeline as E, punctuated_search as S


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=args.out.resolve()
    if not out.is_relative_to(root/'data'):raise ValueError('Output must be in src/data')
    if out.exists():raise FileExistsError(out)
    path=Path(__file__).with_name('causal_independent6.json')
    cases=json.loads(path.read_text(encoding='utf8'))
    rows=[]
    with S.connect() as conn:
        for case in cases:
            chunk=case['chunk']
            original=json.loads(conn.execute('SELECT payload FROM chunks WHERE chunk_id=?',(chunk['chunk_id'],)).fetchone()[0])
            if original!=chunk:raise AssertionError('Source payload drift: '+case['id'])
            if case['evidence'] not in chunk['text_orig']:raise AssertionError('Evidence drift')
            plan=E.parse(case['query'])
            audit=E.audit(chunk,plan,[])
            confirmed=audit['status']=='clues_present'
            rows.append(dict(id=case['id'],query=case['query'],source_can_answer=case['source_can_answer'],
                confirmed=confirmed,miss=case['source_can_answer'] and not confirmed,
                false_confirmation=confirmed and not case['source_can_answer'],
                event_reports=audit.get('causal_event_explanations',[]),plan=plan,audit=audit))
    report=dict(scope='source-chunk audit only; no retrieval or model ranking claim',
        fixture_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_payloads_verified=len(rows),
        answerable=sum(r['source_can_answer'] for r in rows),misses=sum(r['miss'] for r in rows),
        false_confirmations=sum(r['false_confirmation'] for r in rows),
        capability_pass=all(r['confirmed']==r['source_can_answer'] for r in rows),rows=rows)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=True))

if __name__=='__main__':main()
