"""Replay evidence only. Saved retrieval/model ranks are historical, not rerun."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from search import evidence_pipeline as E

def main():
 rows=[]
 for c in json.loads(Path(__file__).with_name('existing17.json').read_text(encoding='utf8')):
  p=E.parse(c['query']);b=c['baseline'];matches=b['gold_matches']
  a=E.audit(c['chunk'],p,[],c['scoped_observations']) if c['chunk'] else None
  row=dict(id=c['id'],query=c['query'],identity='not independently verified',
   parsing=dict(subject=p['subject'],constraints=p['constraints'],slots=p['answer_slots'],unsupported=p['unsupported']),
   retrieval='admitted_historical' if matches else 'missing_designated_evidence' if c['gold_chunk'] else 'no_unique_gold',
   historical_ranks=matches,current_ranks='not measured',
   before=matches[0]['audit_status'] if matches else None,
   after=a['status'] if a else 'not_replayed_no_saved_gold',missing=a['missing'] if a else [],
   source={k:c['chunk'].get(k) for k in ('page_id','revision_id','source_sha256')} if c['chunk'] else None)
  rows.append(row)
 output=Path(sys.argv[1]);output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
 for r in rows:print(r['id'],r['before'],'->',r['after'])
if __name__=='__main__':main()
