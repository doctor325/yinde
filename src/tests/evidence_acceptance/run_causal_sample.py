"""Seven frozen causal questions; real retrieval and local reranker."""
import argparse,json
from pathlib import Path
from api import db
from search import punctuated_search as S
from search.semantic_bridge import worker

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 root=Path(__file__).resolve().parents[2];out=a.out.resolve()
 if not out.is_relative_to(root/'data'):raise ValueError('Outside src/data')
 out.mkdir(parents=True,exist_ok=False)
 cases=json.loads(Path(__file__).with_name('causal7.json').read_text(encoding='utf8'));rows=[];conn=db.connect()
 try:
  for c in cases:
   r=S.run(c['query'],mode='evidence',cur=conn.cursor())
   (out/(c['id']+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
   d=next((d for d in r['evidence_pipeline']['candidate_diagnostics'] if d['chunk_id']==c['chunk']['chunk_id']),None)
   row=dict(id=c['id'],split=c['split'],query=c['query'],expected_class=c['classification'],gold=d,top5=[h['chunk']['chunk_id'] for h in r['hits'][:5]],confirmed=[h['chunk']['chunk_id'] for h in r['hits'] if h['evidence_audit']['status']=='clues_present'],ms=r['elapsed_ms'])
   rows.append(row);(out/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(row,ensure_ascii=False),flush=True)
 finally:conn.close();worker.close()
if __name__=='__main__':main()
