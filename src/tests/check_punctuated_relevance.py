"""Frozen punctuated-witness gold; offline candidate/rank audit, never answers."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from search import punctuated_search as S, semantic_bridge as B

DIRECTORY = ROOT/'src/data/punctuated/2026-10-27/relevance'
FIXTURE = ROOT/'src/tests/punctuated_relevance_queries.json'


def matches(hit, gold):
    c=hit['chunk']
    return any(c['page_id']==g['page_id'] and c['source_sha256']==g['source_sha256']
               and g['evidence'] in c['text_orig'] for g in gold)


def metrics(hits, case):
    ranks=[i for i,h in enumerate(hits,1) if matches(h,case['gold'])]
    return dict(known_evidence_found=bool(ranks),best_rank=min(ranks,default=None),
                hit_at_5=any(i<=5 for i in ranks),hit_at_20=any(i<=20 for i in ranks))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('step',choices=('capture','rerank'))
    parser.add_argument('--limit',type=int,default=18)
    parser.add_argument('--fixture',type=Path,default=FIXTURE)
    args=parser.parse_args()
    fixture=args.fixture.resolve()
    if not fixture.is_relative_to(ROOT/'src/tests'):raise ValueError('Fixture must be in repository tests')
    cases=json.loads(fixture.read_text(encoding='utf8'))['cases'][:args.limit]
    digest=hashlib.sha256(fixture.read_bytes()).hexdigest()
    baseline_fuse=S.fused
    try:
        with sqlite3.connect((ROOT/'src/data/database/history.db').as_uri()+'?mode=ro',uri=True) as main_db:
            for case in cases:
                path=DIRECTORY/(case['id']+'-'+args.step+'.json')
                if path.exists():
                    assert json.loads(path.read_text(encoding='utf8'))['fixture_sha256']==digest
                    print('retained',path.name,flush=True);continue
                if args.step=='capture':
                    with S.connect() as db:
                        for gold in case['gold']:
                            c=json.loads(db.execute('SELECT payload FROM chunks WHERE chunk_id=?',(gold['chunk_id'],)).fetchone()[0])
                            assert matches(dict(chunk=c),[gold]),case['id']
                    pools={}
                    def capture(dense,batches,limit=20):
                        pools.update(dense=dense,batches=batches,all_fused=baseline_fuse(dense,batches,limit=120))
                        return baseline_fuse(dense,batches,limit)
                    with patch.object(S,'fused',side_effect=capture):
                        result=S.run(case['query'],mode='expanded',cur=main_db.cursor())
                    record=dict(case=case,result=result,pools=pools,baseline=metrics(result['hits'],case),
                                candidates=metrics(pools['all_fused'],case))
                else:
                    captured=json.loads((DIRECTORY/(case['id']+'-capture.json')).read_text(encoding='utf8'))
                    assert captured['fixture_sha256']==digest
                    hits=captured['pools']['all_fused'][:100]
                    texts=['《'+h['chunk']['book_title']+'》 '+h['chunk']['title']+' '+h['chunk']['heading']+'\n'+h['chunk']['text_orig'] for h in hits]
                    started=time.perf_counter();scored=B.worker.rerank(case['query'],texts)
                    ranked=[dict(hits[i],rerank_score=scored['scores'][i]) for i in sorted(range(len(hits)),key=lambda i:(-scored['scores'][i],i))]
                    record=dict(case=case,reranked=metrics(ranked,case),baseline=captured['baseline'],
                                rerank_pool=metrics(hits,case),hits=ranked,ms=(time.perf_counter()-started)*1000,
                                model_report=scored)
                record['fixture_sha256']=digest
                path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
                print(case['id'],json.dumps({k:v for k,v in record.items() if k in ('baseline','candidates','reranked','ms')},ensure_ascii=True),flush=True)
    finally:B.worker.close()


if __name__=='__main__':main()
