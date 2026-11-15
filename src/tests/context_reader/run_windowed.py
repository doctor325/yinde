"""Prepare, inspect, then run exactly one exposed development positive/contrast.

Selection consumes source and candidate offsets only. Gold is evaluated after
selection and is never sent to the window selector or the reader.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid

from search import event_context_window as W, numbered_evidence as N
from search import punctuated as P, punctuated_search as S
from search.local_evidence_reader import DEFAULT_HOME, LocalReader
from tests.context_reader.run import cases, source_check

PAIR = ('chenping_positive', 'chenping_contrast')
RUN_HOME = DEFAULT_HOME / 'numbered-window'


def digest(value):
    return hashlib.sha256(N.encode(value).encode('utf8')).hexdigest()


def save(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def pair():
    data, receipt = cases()
    selected = [next(c for c in data if c['id'] == key) for key in PAIR]
    if any(c['split'] != 'development' for c in selected):
        raise ValueError('Only exposed development questions are allowed')
    if selected[0]['anchor_chunk_id'] != selected[1]['anchor_chunk_id']:
        raise ValueError('The contrast must use the identical candidate')
    return selected, receipt


def source_mapping_check(context, blocks):
    """Check actual selected originals, including cross-block numbered units."""
    for segment in context['segments']:
        if blocks[segment['block_index']]['text'][segment['start']:segment['end']] != segment['text']:
            raise ValueError('Window does not reproduce original source')
    evidence = N.numbered(context)
    for unit in evidence:
        restored = ''.join(blocks[p['block_index']]['text'][p['start']:p['end']] for p in unit['parts'])
        if restored != unit['text'] or restored != ''.join(p['quote'] for p in unit['parts']):
            raise ValueError('Numbered citation mapping differs from source')
    if not evidence or any(not e['complete'] for e in evidence):
        raise ValueError('Incomplete evidence window; do not start inference')
    return evidence


def prepare():
    selected, receipt = pair()
    prior = json.loads((DEFAULT_HOME / 'abc-pilot.json').read_text(encoding='utf8'))
    if prior['frozen']['sha256'] not in (receipt['sha256'], receipt['original_windows_file_sha256']):
        raise ValueError('Prior baseline has different gold')
    with P.connect() as lib, S.connect() as index:
        for case in selected:
            source_check(lib, case)
        found = index.execute('SELECT payload FROM chunks WHERE chunk_id=?',
                              (selected[0]['anchor_chunk_id'],)).fetchone()
        if not found:
            raise ValueError('Frozen candidate missing')
        chunk = json.loads(found[0])
        # No query, gold, expected answer or user name participates in selection.
        started = time.perf_counter()
        context = W.assemble(lib, chunk)
        assembly_ms = (time.perf_counter() - started) * 1000
        page = lib.execute('SELECT blocks_json FROM chapters WHERE page_id=?', (chunk['page_id'],)).fetchone()
        evidence = source_mapping_check(context, json.loads(page[0]))
    baseline = next(r for r in prior['rows'] if r['id'] == PAIR[0])['context']
    if any(context['source'][key] != selected[0]['source'][key]
           for key in ('page_id', 'book_id', 'revision_id', 'source_sha256')):
        raise ValueError('Candidate source changed')
    report = dict(version='anchor-window-dev-pair-v1', state='prepared', frozen=receipt,
                  scope='source-conditional exposed pair; not live recall/ranking or generalization',
                  prompt_sha256=digest(N.PARSE + N.JUDGE), selector_version=W.VERSION,
                  reader_version=N.VERSION, context=context, context_sha256=digest(context),
                  assembly_ms=assembly_ms, request_timeout_seconds=120, rows=[],
                  baseline=dict(source_chars=baseline['source_chars'], units=len(N.numbered(baseline)),
                                payload_chars=len(N.encode(N.evidence_payload(N.numbered(baseline))))),
                  window=dict(source_chars=context['source_chars'], units=len(evidence),
                              payload_chars=len(N.encode(N.evidence_payload(evidence)))))
    for case in selected:
        gold = case['source']
        # This is a post-selection coverage diagnostic, not a semantic grade.
        containing = [e['id'] for e in evidence if any(
            p['block_index'] == gold['block_index'] and p['start'] <= gold['start']
            and p['end'] >= gold['end'] for p in e['parts'])]
        quoted = ''.join(s['text'] for s in context['segments'] if s['role'] == 'event_context')
        report['rows'].append(dict(id=case['id'], query=case['query'], expected=case['expected'],
                                  expected_answer=case['expected_answer'],
                                  gold_quote_retained=gold['quote'] in quoted,
                                  gold_enclosing_units=containing, semantic_review='pending'))
    folder = RUN_HOME / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    output = folder / 'report.json'
    save(output, report)
    print('Prepared report: ' + str(output), flush=True)
    print(json.dumps(dict(baseline=report['baseline'], window=report['window'],
                          selection=context.get('selection'), input_ready=context.get('input_ready'),
                          omissions=context.get('omissions'), rows=report['rows']), ensure_ascii=False), flush=True)
    for unit in evidence:
        print(json.dumps(unit, ensure_ascii=False), flush=True)
    return 0


def run(path, reader_module=N):
    path = Path(path).resolve()
    if not path.is_relative_to(RUN_HOME.resolve()) or path.name != 'report.json':
        raise ValueError('Use a prepared report inside the repository experiment directory')
    report = json.loads(path.read_text(encoding='utf8'))
    selected, receipt = pair()
    if report['state'] != 'prepared':
        raise ValueError('This pair has already been attempted; do not rerun it')
    if report['frozen'] != receipt or report['context_sha256'] != digest(report['context']):
        raise ValueError('Frozen inputs changed')
    if report['prompt_sha256'] != digest(reader_module.PARSE + reader_module.JUDGE) or report['reader_version'] != reader_module.VERSION:
        raise ValueError('Prompt/reader changed after preparation')
    if [r['id'] for r in report['rows']] != list(PAIR):
        raise ValueError('Only the prepared pair may run')
    for row, case in zip(report['rows'], selected):
        if any(row[key] != case[key] for key in ('id', 'query', 'expected', 'expected_answer')):
            raise ValueError('Question/gold changed after preparation')
    if not report['context']['input_ready']:
        raise ValueError('Source window is incomplete or over budget; do not start the model')
    log = DEFAULT_HOME / 'reader-runtime.log'
    if log.exists():
        shutil.copyfile(log, path.parent / 'previous-runtime.log')
    report['state'] = 'running'
    save(path, report)
    print('Report: ' + str(path), flush=True)
    phase = 'startup'
    started = time.perf_counter()
    try:
        with LocalReader(timeout=120) as reader:
            report['model'] = dict(receipt=reader.receipt, startup_ms=reader.startup_ms,
                                  startup_diagnostics=reader.startup_diagnostics)
            phase = 'inference'
            for row in report['rows']:
                def checkpoint(progress):
                    row['reading'] = progress
                    save(path, report)
                try:
                    reader_module.read(reader, row['query'], report['context'], on_progress=checkpoint)
                except (OSError, ValueError, KeyError) as exc:
                    row['error'] = type(exc).__name__ + ': ' + str(exc)
                reading = row.get('reading', {})
                validation = {k: v for k, v in reading.get('validation', {}).items() if k != 'citations'}
                print(json.dumps(dict(id=row['id'], question=reading.get('question'),
                                      judgement=reading.get('judgement'), validation=validation,
                                      elapsed_ms=reading.get('elapsed_ms'), error=row.get('error')),
                                 ensure_ascii=False), flush=True)
            report['state'] = 'attempted'
    except BaseException as exc:
        report['state'] = 'interrupted'
        report['fatal_error'] = dict(phase=phase, type=type(exc).__name__, message=str(exc),
                                     elapsed_ms=(time.perf_counter() - started) * 1000)
        raise
    finally:
        save(path, report)
        if log.exists():
            shutil.copyfile(log, path.parent / 'runtime.log')
    # Completion is operational only. Human review must grade event/source meaning.
    return int(any('error' in row for row in report['rows']))


def resume_startup(path):
    """Explicit new attempt only when no question ever reached inference."""
    path = Path(path).resolve()
    if not path.is_relative_to(RUN_HOME.resolve()) or path.name != 'report.json':
        raise ValueError('Resume only a repository experiment report')
    report = json.loads(path.read_text(encoding='utf8'))
    if (report['state'] != 'interrupted' or report.get('fatal_error', {}).get('phase') != 'startup'
            or any('reading' in row or 'error' in row for row in report['rows'])):
        raise ValueError('Only startup failures with no inference can be resumed')
    report['previous_attempt'] = str(path)
    report.pop('fatal_error')
    report['state'] = 'prepared'
    for row in report['rows']:
        row['semantic_review'] = 'pending'
    folder = RUN_HOME / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    output = folder / 'report.json'
    save(output, report)
    return run(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--prepare', action='store_true', help='Read source and print the pair window; no model')
    modes.add_argument('--run', metavar='PREPARED_REPORT', help='Run the prepared pair once with unchanged limits')
    modes.add_argument('--resume-startup', metavar='FAILED_REPORT',
                       help='Preserve a startup-only failure and attempt the identical inputs in a new directory')
    args = parser.parse_args()
    if args.resume_startup:
        return resume_startup(args.resume_startup)
    return prepare() if args.prepare else run(args.run)


if __name__ == '__main__':
    raise SystemExit(main())
