"""Frozen three-claim development probe, not general semantic acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from search import event_claim_review as V, numbered_evidence as N
from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import run_candidate_pilot as C
from tests.context_reader.run_source_batches import TEMPLATE

HERE = Path(__file__).resolve().parent
BAD = DEFAULT_HOME / 'source-first-batches/runs/048dbf6aa590/report.json'
BAD_SHA = 'ecf035ca6ff1914f4d194112b1900816585fbe552a34053986b6c0da946461dd'
FIXTURE_SHA = 'bab290e30f81052ae75af204016d6764c32632b8f951d5a90efd0f69bc0e68c2'


def prepare():
    _, reference = C.prepare()
    raw = BAD.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BAD_SHA:
        raise ValueError('Saved extraction changed')
    bad = json.loads(raw)
    evidence = bad['tasks'][0]['evidence']
    events = [e for e in bad['result']['output']['events'] if e['reason'].strip()]
    if len(events) != 1:
        raise ValueError('Expected one recorded reason proposal')
    rows = [dict(id='recorded_reason_error', event=events[0], field='reason', evidence=evidence,
                 source=bad['tasks'][0]['source'], expected='not_supported')]
    raw = (HERE.parent / 'evidence_acceptance/causal7.json').read_bytes()
    if hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() != FIXTURE_SHA:
        raise ValueError('Existing causal fixture changed')
    fixture = next(c for c in json.loads(raw) if c['id'] == 'fu')
    if fixture['split'] != 'development':
        raise ValueError('Do not use held-out source')
    chunk = fixture['chunk']
    part, = chunk['parts']
    if part['end'] - part['start'] != len(chunk['text_orig']):
        raise ValueError('Source fixture position mismatch')
    evidence = N.numbered(dict(boundary_cut=False, segments=[dict(part, text=chunk['text_orig'], role='event_context')]))
    source = {k:chunk[k] for k in ('book_id','page_id','revision_id','source_sha256')}
    for name, action, expected in [('富弼', '去位', 'supported'), ('陈升之', '罢', 'not_supported')]:
        event = dict(actor=name, action=action, object='', reason='母丧', state='occurred',
                     attribution='narrator', references=[])
        rows.append(dict(id='explicit_reason' if expected == 'supported' else 'other_actor_reason',
                         event=event, field='reason', evidence=evidence, source=source, expected=expected))
    for row in rows:
        row['request_sha256'] = V.digest(V.request(row['event'], row['field'], row['evidence']))
    return dict(version=V.VERSION, state='prepared', model_calls=0, held_out_calls=0,
        semantic_acceptance=None, rows=rows, max_calls=3, model=C.MODEL,
        scope='known development sources; no original question or gold in model requests'), reference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    report, reference = prepare()
    folder = DEFAULT_HOME / 'claim-review' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    if not args.run:
        print('Prepared 3 development claims; model calls 0.')
        return 0
    reader = C.CandidateReader()
    try:
        with reader:
            if C.verify_runtime(reader, reference) != TEMPLATE:
                raise ValueError('Template drift')
            report.update(state='running', startup_ms=reader.startup_ms,
                          model_receipt=reader.receipt, runtime_receipt=reader.runtime_receipt)
            for row in report['rows']:
                if V.digest(V.request(row['event'], row['field'], row['evidence'])) != row['request_sha256']:
                    raise ValueError('Frozen claim changed')
                report['model_calls'] += 1
                save()
                row['review'] = V.review(reader, row['event'], row['field'], row['evidence'])
                row['assessment'] = V.assess(row['event'], row['field'], row['evidence'], row['review'])
                save()
                print(json.dumps(dict(id=row['id'], status=row['assessment']['status'], ms=row['review']['ms'])), flush=True)
                if not row['review']['completed']:
                    break
            report['state'] = 'attempted'
    except BaseException as exc:
        report.update(state='interrupted', error=dict(type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save()
        if hasattr(reader, 'log_path') and reader.log_path.exists():
            shutil.copyfile(reader.log_path, folder / 'runtime.log')
    return int(any(not r.get('review', {}).get('completed') for r in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
