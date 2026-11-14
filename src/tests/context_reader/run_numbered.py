"""Single exposed development case by default; never runs holdout questions."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from search import numbered_evidence as N
from search.local_evidence_reader import LocalReader, DEFAULT_HOME
from tests.context_reader.run import cases


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-inputs',action='store_true',help='No model: verify four source-to-wire mappings')
    parser.add_argument('--case',choices=('chenping_positive','chenping_contrast','dengyu_positive','dengyu_contrast'),default='chenping_positive')
    args=parser.parse_args()
    frozen,receipt=cases()
    previous=json.loads((DEFAULT_HOME/'abc-pilot.json').read_text(encoding='utf8'))
    if previous['frozen']['sha256'] not in (receipt['sha256'],receipt['original_windows_file_sha256']):
        raise ValueError('Baseline has different frozen gold')
    selected=[r for r in previous['rows'] if r['split']=='development']
    expected={r['id'] for r in frozen if r['group'] in ('chenping','dengyu')}
    if {r['id'] for r in selected}!=expected:raise ValueError('Development sample drift')
    gold={r['id']:r for r in frozen}
    for r in selected:
        g=gold[r['id']]
        if r['query']!=g['query'] or r['expected']!=g['expected']:raise ValueError('Question/gold drift')
        if r['context']['anchor_chunk_id']!=g['anchor_chunk_id']:raise ValueError('Candidate drift')
        if any(r['context']['source'][k]!=g['source'][k] for k in ('page_id','book_id','revision_id','source_sha256')):
            raise ValueError('Source drift')
    if args.check_inputs:
        for r in selected:
            es=N.numbered(r['context']);wire=N.evidence_payload(es)
            for e in es:
                assert wire['evidence'][e['id']]==e['text']==''.join(p['quote'] for p in e['parts'])
                assert (e['id'] in wire['opening_context'])==(e['role']=='opening_context')
                assert (e['id'] in wire['incomplete'])==(not e['complete'])
                for part in e['parts']:
                    s=next(s for s in r['context']['segments'] if s['block_index']==part['block_index'] and s['start']<=part['start']<part['end']<=s['end'])
                    assert s['text'][part['start']-s['start']:part['end']-s['start']]==part['quote']
            legacy=json.dumps(dict(evidence=[{k:e[k] for k in ('id','text','role','complete')} for e in es]),ensure_ascii=False)
            compact=N.encode(wire)
            assert len(compact)<len(legacy)
            print(json.dumps(dict(id=r['id'],units=len(es),legacy_chars=len(legacy),compact_chars=len(compact),equivalent=True)))
        return 0
    selected=[r for r in selected if r['id']==args.case]
    run_dir=DEFAULT_HOME/'numbered-v3'/uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True,exist_ok=False)
    out=run_dir/'report.json'
    report=dict(version=N.VERSION,scope='one exposed development case; no holdout',
        prompt_sha256=hashlib.sha256((N.PARSE+N.JUDGE).encode()).hexdigest(),rows=[])
    def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    log=DEFAULT_HOME/'reader-runtime.log'
    if log.exists():shutil.copyfile(log,run_dir/'previous-runtime.log')
    print('Report: '+str(out),flush=True)
    try:
        with LocalReader() as reader:
            report['startup_ms']=reader.startup_ms
            for r in selected:
                row=dict(id=r['id'],query=r['query'],expected=r['expected'],baseline=r['C'],
                    context_sha256=hashlib.sha256(json.dumps(r['context'],sort_keys=True,ensure_ascii=False).encode()).hexdigest())
                report['rows'].append(row)
                def checkpoint(progress):
                    row['v3']=progress;save()
                try:row['v3']=N.read(reader,r['query'],r['context'],on_progress=checkpoint)
                except (OSError,ValueError,KeyError) as e:row['error']=str(e)
                save()
                print(json.dumps(dict(id=row['id'],error=row.get('error'),
                    question=row['v3'].get('question'),judgement=row['v3'].get('judgement'),
                    validation=row['v3'].get('validation'),ms=row['v3']['elapsed_ms']),ensure_ascii=False),flush=True)
    finally:
        save()
        if log.exists():shutil.copyfile(log,run_dir/'runtime.log')
    return int(any('error' in r for r in report['rows']))

if __name__=='__main__':raise SystemExit(main())
