"""Ten-case real-model acceptance via the unified search backend; no score mocks."""
import argparse,json,time
from pathlib import Path
from api import db
from search import punctuated_search as S

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);a=parser.parse_args()
 root=Path(__file__).resolve().parents[2]
 out=a.out.resolve()
 if not out.is_relative_to(root/'data'):raise ValueError('Output must stay under src/data')
 out.mkdir(parents=True,exist_ok=False)
 fixtures=json.loads(Path(__file__).with_name('existing17.json').read_text(encoding='utf8'))
 ids=('time-han','battle-jin','battle-tang','cause-han','cause-song','negative-role','negative-era','annal-plan')
 cases=[dict(c,policy='no_confirmation' if c['id'].startswith('negative') or c['id']=='annal-plan' else 'candidate' if c['id'].startswith('cause') else 'top5_confirmed') for c in fixtures if c['id'] in ids]
 independent=json.loads(Path(__file__).with_name('patient_sources.json').read_text(encoding='utf8'))
 for c in independent[1:3]:cases.append(dict(id='independent-'+str(c['chunk']['chunk_id']),query=c['patient']+'被杀？',gold_chunk=c['chunk']['chunk_id'],policy='top5_confirmed'))
 (out/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf8')
 summary=[]
 conn=db.connect()
 try:
  for c in cases:
   started=time.perf_counter();result=S.run(c['query'],mode='evidence',cur=conn.cursor())
   (out/(c['id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
   diag=next((d for d in result['evidence_pipeline']['candidate_diagnostics'] if d['chunk_id']==c.get('gold_chunk')),None)
   confirmed=[h['chunk']['chunk_id'] for h in result['hits'] if h['evidence_audit']['status']=='clues_present']
   passed=not confirmed if c['policy']=='no_confirmation' else bool(diag) if c['policy']=='candidate' else bool(diag and diag['final_rank']<=5 and diag['audit_status']=='clues_present')
   row=dict(id=c['id'],query=c['query'],policy=c['policy'],gold=diag,confirmed=confirmed,top5=[h['chunk']['chunk_id'] for h in result['hits'][:5]],passed=passed,seconds=round(time.perf_counter()-started,3))
   summary.append(row);(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(row,ensure_ascii=False),flush=True)
 finally:
  from search.semantic_bridge import worker
  worker.close()
  conn.close()
 return 0 if all(r['passed'] for r in summary) else 1
if __name__=='__main__':raise SystemExit(main())

