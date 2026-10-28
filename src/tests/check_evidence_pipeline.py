"""Real local-model validation; preserve all failures and source identities."""
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from search import punctuated_search as S,semantic_bridge as B
from check_punctuated_relevance import metrics


def main():
    output=ROOT/'src/data/punctuated/2026-10-28/evidence-pipeline'
    output.mkdir(exist_ok=True)
    cases=[]
    for filename,limit in [('punctuated_relevance_queries.json',6),('evidence_pipeline_holdout.json',6)]:
        cases+=json.loads((ROOT/'src/tests'/filename).read_text(encoding='utf8'))['cases'][:limit]
    try:
        with sqlite3.connect((ROOT/'src/data/database/history.db').as_uri()+'?mode=ro',uri=True) as db:
            for case in cases:
                path=output/(case['id']+'.json')
                if path.exists():raise ValueError('Preserve previous outputs')
                base=S.run(case['query'],mode='expanded',cur=db.cursor())
                result=S.run(case['query'],mode='evidence',cur=db.cursor())
                with S.connect() as corpus:
                    for h in result['hits']:
                        for c in [h['chunk']]+h['context_chunks']:
                            stored=json.loads(corpus.execute('SELECT payload FROM chunks WHERE chunk_id=?',(c['chunk_id'],)).fetchone()[0])
                            assert c==stored
                        assert not h['evidence_audit']['relation_verified']
                row=dict(case=case,baseline=metrics(base['hits'],case),top=metrics(result['hits'],case),result=result)
                path.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
                print(case['id'],row['baseline']['best_rank'],'->',row['top']['best_rank'],result['elapsed_ms'],flush=True)
    finally:B.worker.close()


if __name__=='__main__':main()
