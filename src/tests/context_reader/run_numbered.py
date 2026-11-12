"""Four exposed development cases only; reuse identical v1 source contexts."""
import hashlib
import json
from pathlib import Path
import shutil
from search import numbered_evidence as N
from search.local_evidence_reader import LocalReader, DEFAULT_HOME
from tests.context_reader.run import cases


def main():
    frozen,receipt=cases()
    previous=json.loads((DEFAULT_HOME/'abc-pilot.json').read_text(encoding='utf8'))
    if previous['frozen']['sha256'] not in (receipt['sha256'],receipt['original_windows_file_sha256']):
        raise ValueError('Baseline has different frozen gold')
    selected=[r for r in previous['rows'] if r['split']=='development']
    expected={r['id'] for r in frozen if r['group'] in ('chenping','dengyu')}
    if {r['id'] for r in selected}!=expected:raise ValueError('Development sample drift')
    out=DEFAULT_HOME/'numbered-v2.json'
    report=dict(version=N.VERSION,scope='four exposed development cases; no holdout',
        prompt_sha256=hashlib.sha256((N.PARSE+N.JUDGE).encode()).hexdigest(),rows=[])
    log=DEFAULT_HOME/'reader-runtime.log'
    backup=DEFAULT_HOME/'reader-runtime-before-v2.log'
    if log.exists() and not backup.exists():shutil.copyfile(log,backup)
    try:
        with LocalReader() as reader:
            report['startup_ms']=reader.startup_ms
            for r in selected:
                row=dict(id=r['id'],query=r['query'],expected=r['expected'],baseline=r['C'],
                    context_sha256=hashlib.sha256(json.dumps(r['context'],sort_keys=True,ensure_ascii=False).encode()).hexdigest())
                try:row['v2']=N.read(reader,r['query'],r['context'])
                except (OSError,ValueError,KeyError) as e:row['error']=str(e)
                report['rows'].append(row)
                out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
                print(json.dumps(row,ensure_ascii=False) if 'error' in row else json.dumps(dict(
                    id=row['id'],question=row['v2']['question']['output'],
                    judgement=row['v2']['judgement']['output'],validation=row['v2']['validation']['errors'],
                    ms=row['v2']['elapsed_ms']),ensure_ascii=False),flush=True)
    finally:
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return int(any('error' in r for r in report['rows']))

if __name__=='__main__':raise SystemExit(main())
