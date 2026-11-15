"""Two exposed cases, identical judgement requests, one pinned candidate model.

Reuse the recorded 4B results; do not spend model calls repeating successful
execution. Historical timings are not a contemporaneous hardware benchmark.
"""
import argparse
import json
from pathlib import Path
import shutil
import time
import uuid

from search import numbered_evidence as N, numbered_event_contract as E
from search.local_evidence_reader import DEFAULT_HOME, LocalReader
from scripts.setup_evidence_reader import MODELS, ASSETS
from tests.context_reader import run_windowed as W

ROOT = DEFAULT_HOME / 'model-comparison'
GENERATION = dict(temperature=0, seed=20261111, max_tokens=650,
                  chat_template_kwargs={'enable_thinking': False})


def prepare(baseline):
    baseline = Path(baseline).resolve()
    if not baseline.is_relative_to(W.RUN_HOME.resolve()) or baseline.name != 'report.json':
        raise ValueError('Baseline must remain in the repository window directory')
    old = json.loads(baseline.read_text(encoding='utf8'))
    cases, receipt = W.pair()
    if (old['state'] != 'attempted' or old['frozen'] != receipt
            or old['reader_version'] != E.VERSION or old['prompt_sha256'] != W.digest(E.PARSE + E.JUDGE)
            or old['context_sha256'] != W.digest(old['context'])):
        raise ValueError('Baseline contract, frozen cases or source changed')
    if (old['model']['receipt']['assets'] != list(ASSETS)
            or old['model']['receipt']['model'] != MODELS['qwen3-4b']['id']):
        raise ValueError('Baseline is not the pinned 4B model')
    if [r['id'] for r in old['rows']] != list(W.PAIR):
        raise ValueError('Baseline cases changed')
    evidence = N.numbered(old['context'])
    rows = []
    for row, case in zip(old['rows'], cases):
        if any(row[key] != case[key] for key in ('id', 'query', 'expected', 'expected_answer')):
            raise ValueError('Question or gold changed')
        reading = row['reading']
        if (not reading['completed'] or reading['version'] != E.VERSION
                or reading['evidence'] != evidence or 'error' in row):
            raise ValueError('Baseline incomplete or evidence changed')
        question = reading['question']['output']
        if E.question_errors(question, row['query']):
            raise ValueError('Baseline question contract invalid')
        payload = dict(original_question=row['query'], parsed_question=question, **N.evidence_payload(evidence))
        if reading['judgement']['input_chars'] != len(E.JUDGE) + len(N.encode(payload)):
            raise ValueError('Reconstructed judgement input differs from baseline')
        rows.append(dict(id=row['id'], query=row['query'], expected=row['expected'],
                         expected_answer=row['expected_answer'], fixed_question=question,
                         payload=payload, payload_sha256=W.digest(payload),
                         baseline_judgement=reading['judgement'],
                         baseline_validation=reading['validation'],
                         baseline_semantic_review=row['semantic_review'], semantic_review='pending'))
    schema = E.judge_schema([e['id'] for e in evidence])
    return dict(version='controlled-model-comparison-v1', state='prepared',
                baseline_report=str(baseline), baseline_sha256=W.digest(old),
                baseline_model=old['model']['receipt'], candidate_key='qwen3-8b',
                scope='two exposed judgement cases; frozen parser outputs, not a parser or retrieval evaluation',
                context=old['context'], context_sha256=old['context_sha256'], evidence=evidence,
                prompt_sha256=old['prompt_sha256'], schema=schema, schema_sha256=W.digest(schema),
                generation=GENERATION, request_timeout_seconds=120,
                runtime=dict(cpu_threads=4, context_tokens=8192, gpu_layers=0, thinking=False), rows=rows)


class RecordingReader:
    """Reject request drift before dispatch; retain exact request fingerprint."""
    def __init__(self, reader, row, schema):
        self.reader, self.row, self.schema = reader, row, schema

    def request(self, path, payload):
        expected = dict(model='local', **GENERATION,
                        messages=[dict(role='system', content=E.JUDGE),
                                  dict(role='user', content=N.encode(self.row['payload']))],
                        response_format=dict(type='json_schema', json_schema=dict(
                            name='evidence_v2', strict=True, schema=self.schema)))
        if path != '/v1/chat/completions' or payload != expected:
            raise ValueError('Controlled judgement request drift')
        self.row['request_sha256'] = W.digest(payload)
        return self.reader.request(path, payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    report = prepare(args.baseline)
    folder = ROOT / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    output = folder / 'report.json'
    def save():
        W.save(output, report)
    log = DEFAULT_HOME / 'reader-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder / 'previous-runtime.log')
    save()
    print('Report: ' + str(output), flush=True)
    phase = 'startup'
    try:
        with LocalReader(model='qwen3-8b', timeout=120) as reader:
            report['candidate_model'] = reader.receipt
            report['startup_ms'] = reader.startup_ms
            phase = 'judgement'
            for row in report['rows']:
                started = time.perf_counter()
                try:
                    proxy = RecordingReader(reader, row, report['schema'])
                    row['judgement'] = N.generate(proxy, E.JUDGE, row['payload'], report['schema'], 650)
                    row['validation'] = E.guard(row['judgement']['output'], row['fixed_question'],
                                                report['evidence'], report['context'])
                except (OSError, ValueError, KeyError) as exc:
                    row['error'] = dict(type=type(exc).__name__, message=str(exc))
                row['elapsed_ms'] = (time.perf_counter() - started) * 1000
                save()
                print(json.dumps(dict(id=row['id'], judgement=row.get('judgement'),
                                      validation={k:v for k,v in row.get('validation', {}).items() if k!='citations'},
                                      error=row.get('error'), elapsed_ms=row['elapsed_ms']), ensure_ascii=False), flush=True)
                # A timed-out generation may still be cancelling. Do not enqueue
                # the next case into a busy server or silently retry it.
                if 'error' in row:
                    break
            report['state'] = 'attempted'
    except BaseException as exc:
        report.update(state='interrupted', fatal_error=dict(phase=phase, type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save()
        if log.exists():
            shutil.copyfile(log, folder / 'runtime.log')
    return int(any('error' in row or 'judgement' not in row for row in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
