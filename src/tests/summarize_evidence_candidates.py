"""Export compact reproducible metrics; keep source quotes in local data only."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from search import evidence_pipeline as E, punctuated_search as S
from check_punctuated_relevance import metrics


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    source=args.input.resolve();output=args.output.resolve()
    if not source.is_relative_to(ROOT/'src/data') or not output.is_relative_to(ROOT):raise ValueError('Outside repository')
    phases={};queries=[]
    files=sorted(source.glob('*-before.json'))
    assert len(files)==36
    for step in ('before','after','focused'):
        records=[json.loads((source/(f.name.removesuffix('-before.json')+'-'+step+'.json')).read_text(encoding='utf8')) for f in files]
        pipes=[r['result'].get('pipeline',r['result'].get('evidence_pipeline')) for r in records]
        times=[p['elapsed_ms'] for p in pipes]
        phases[step]=dict(n=len(records),admitted=sum(r['admission']['known_evidence_found'] for r in records),
            hit20=sum(r['top']['hit_at_20'] for r in records),hit5=sum(r['top']['hit_at_5'] for r in records),
            pipeline_ms=dict(min=min(times),median=round(statistics.median(times),2),max=max(times)),
            scored_candidates=dict(min=min(p['candidate_counts']['context_scored'] for p in pipes),
                                   max=max(p['candidate_counts']['context_scored'] for p in pipes)))
    with S.connect() as db:
        for file in files:
            before=json.loads(file.read_text(encoding='utf8'))
            final=json.loads((source/(file.name.removesuffix('-before.json')+'-focused.json')).read_text(encoding='utf8'))
            case=before['case']
            p=E.parse(case['query']);subject,_=E.R.candidates(db,p)
            fallback,_=E.grounded_fallback(db,p,subject)
            raw=before['pools']['dense']+sum(before['pools']['batches'],[])+subject+fallback
            raw_found=metrics(raw,case)['known_evidence_found']
            admitted=final['admission']['known_evidence_found'];shown=final['top']['hit_at_20']
            queries.append(dict(id=case['id'],query=case['query'],before_rank=before['top']['best_rank'],
                final_rank=final['top']['best_rank'],raw_pool_contains_known_evidence=raw_found,
                model_received_known_evidence=admitted,
                failure_stage=None if shown else 'ranking' if admitted else 'admission_budget' if raw_found else 'retrieval',
                rank_regressed=(before['top']['best_rank'] is not None and (final['top']['best_rank'] is None or final['top']['best_rank']>before['top']['best_rank']))))
    roles=json.loads((source/'role-contrast.json').read_text(encoding='utf8'))
    result=dict(scope='36 existing non-exhaustive source-evidence queries; paired identical semantic/lexical pools, not blind or full-reign acceptance',
        phases=phases,queries=queries,top20_regressions=[q['id'] for q in queries if q['before_rank'] is not None and q['final_rank'] is None],
        role_contrasts=dict(n=len(roles['rows']),before=sum(r['before']['preferred_relevant'] for r in roles['rows']),
                           after=sum(r['after']['preferred_relevant'] for r in roles['rows']),
                           regressions=[r['index'] for r in roles['rows'] if r['before']['preferred_relevant'] and not r['after']['preferred_relevant']],
                           remaining_failures=[r['index'] for r in roles['rows'] if not r['after']['preferred_relevant']],
                           fixture_sha256=roles['fixture_sha256']),
        fixture_hashes=before['fixture_hashes'],pipeline_code_sha256=hashlib.sha256((ROOT/'src/search/evidence_pipeline.py').read_bytes()).hexdigest(),
        source_identity={str(p.relative_to(ROOT)).replace('\\','/'):S.identity(p) for p in [ROOT/'src/data/database/history.db',ROOT/'src/data/punctuated/library.sqlite',S.folder()/'manifest.json']},
        model_generation_calls=0)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k in ('phases','role_contrasts','top20_regressions')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
