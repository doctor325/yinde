"""Frozen-source judgement ablation. No gold is injected into live search.

This is a conditional evidence test, NOT an end-to-end recall/ranking test.
Candidate ID/order is identical for A/B/C; only context and reader differ.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import statistics
import time

from search import evidence_context as C, evidence_pipeline as E, evidence_reader as R
from search import punctuated as P, punctuated_search as S

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
# Fixed before inference; two development and two held-out source groups.
PILOT_GROUPS=('chenping','dengyu','wangwei','songlian')


def cases():
    raw=(HERE/'cases40.json').read_bytes()
    receipt=json.loads((HERE/'frozen.json').read_text(encoding='utf8'))
    if hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()!=receipt['sha256']:
        raise ValueError('Frozen cases changed; do not overwrite acceptance gold')
    data=json.loads(raw)
    if len(data)!=40 or len({x['id'] for x in data})!=40:raise ValueError('Case count/IDs changed')
    splits={}
    for case in data:
        for key in ('person','group'):
            value=(key,case[key])
            if value in splits and splits[value]!=case['split']:raise ValueError('Person/group split leakage')
            splits[value]=case['split']
        source=('source',case['source']['page_id'])
        if source in splits and splits[source]!=case['split']:raise ValueError('Chapter split leakage')
        splits[source]=case['split']
    return data,receipt


def source_check(lib,case):
    source=case['source']
    row=lib.execute('SELECT * FROM chapters WHERE page_id=?',(source['page_id'],)).fetchone()
    if not row or any(row[k]!=source[k] for k in C.IDENTITY+('source_url','title')):
        raise ValueError('Frozen source identity changed: '+case['id'])
    text=json.loads(row['blocks_json'])[source['block_index']]['text']
    if text[source['start']:source['end']]!=source['quote']:
        raise ValueError('Frozen source quote changed: '+case['id'])


def summarize(rows):
    result={}
    for split in ('development','holdout'):
        subset=[x for x in rows if x['split']==split]
        result[split]={}
        for strategy in ('A','B','C'):
            measured=[x for x in subset if strategy in x and 'error' not in x[strategy]]
            positives=[x for x in measured if x['expected']=='supported']
            negatives=[x for x in measured if x['expected']!='supported']
            key='eligible' if strategy=='C' else 'rule_positive'
            result[split][strategy]=dict(measured=len(measured),errors=sum('error' in x.get(strategy,{}) for x in subset),
                positives=len(positives),positive_flags=sum(x[strategy][key] for x in positives),
                negatives=len(negatives),false_confirmation_flags=sum(x[strategy][key] for x in negatives),
                median_ms=statistics.median([x[strategy]['elapsed_ms'] for x in measured]) if measured else None,
                warning='Flags require human semantic review; quote checks do not grade entailment')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-pilot',action='store_true',help='Only the frozen 8-case local-reader pilot; other 32 C cases remain unmeasured')
    parser.add_argument('--output',default='data/evidence-reader/ab-rules.json')
    args=parser.parse_args()
    target=(Path.cwd()/args.output).resolve()
    if not target.is_relative_to(ROOT/'src/data'):raise ValueError('Reports must remain under repository src/data')
    data,receipt=cases()
    if args.model_pilot:data=[c for c in data if c['group'] in PILOT_GROUPS]
    if args.model_pilot:
        from search.local_evidence_reader import LocalReader
        reader_context=LocalReader()
    else:reader_context=nullcontext(None)
    report=dict(scope='source-conditional judgement only',frozen=receipt,rows=[],
        retrieval_recall=None,top5=None,candidate_selection='frozen source anchor, not live retrieval',
        default_search_changed=False,prompt_sha256=hashlib.sha256(R.SYSTEM.encode()).hexdigest())
    def save():
        target.parent.mkdir(parents=True,exist_ok=True)
        report['summary']=summarize(report['rows'])
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    try:
        with P.connect() as lib,S.connect() as idx,reader_context as reader:
            if reader:report['model']=dict(receipt=reader.receipt,startup_ms=reader.startup_ms)
            for case in data:
                source_check(lib,case)
                chunk=json.loads(idx.execute('SELECT payload FROM chunks WHERE chunk_id=?',(case['anchor_chunk_id'],)).fetchone()[0])
                if any(chunk[k]!=case['source'][k] for k in C.IDENTITY):raise ValueError('Candidate moved to another source')
                plan=E.parse(case['query']);contract=R.query_contract(case['query'],plan)
                started=time.perf_counter();context=C.assemble(lib,chunk)
                assembly_ms=(time.perf_counter()-started)*1000
                row=dict(id=case['id'],split=case['split'],expected=case['expected'],query=case['query'],
                    candidate_ids=[chunk['chunk_id']],context=context,assembly_ms=assembly_ms)
                for strategy,view,neighbors in (('A',chunk,E.context(idx,chunk)),('B',C.rule_view(chunk,context),None)):
                    started=time.perf_counter();audit=E.audit(view,plan,neighbors or [view])
                    row[strategy]=dict(rule_positive=audit['status']=='clues_present',
                        elapsed_ms=(time.perf_counter()-started)*1000,contract=R.rule_contract(audit,contract,view),
                        audit=audit,view_parts=view['parts'])
                if reader:
                    try:
                        reading=reader.read(contract,context)
                        row['C']=dict(**reading,eligible=reading['validation']['eligible'])
                    except (OSError,ValueError,KeyError) as exc:
                        row['C']=dict(error=type(exc).__name__+': '+str(exc),eligible=False)
                report['rows'].append(row);save()
                print(json.dumps(dict(id=case['id'],A=row['A']['rule_positive'],B=row['B']['rule_positive'],
                    C=row.get('C',{}).get('eligible'),error=row.get('C',{}).get('error')),ensure_ascii=False),flush=True)
    finally:save()
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)
    # Semantic failures are findings, never hidden by changing gold.
    return int(any('error' in row.get('C',{}) for row in report['rows']))


if __name__=='__main__':raise SystemExit(main())
