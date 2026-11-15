"""One brief explanation per exposed case, with frozen 4B baseline inputs."""
import argparse
import json
import shutil
import time
import uuid
from search import brief_evidence_diagnostic as B
from search.local_evidence_reader import DEFAULT_HOME, LocalReader
from tests.context_reader import compare_models as C, run_windowed as W

ROOT = DEFAULT_HOME / 'brief-diagnostic'


def prepare(baseline):
    # Reuse strict baseline checks: model/receipt, frozen gold, question parsing,
    # context, numbered evidence, source revision and existing prompt fingerprint.
    report = C.prepare(baseline)
    report['baseline_schema'] = report.pop('schema')
    report['baseline_schema_sha256'] = report.pop('schema_sha256')
    report['baseline_prompt_sha256'] = report.pop('prompt_sha256')
    report.update(version=B.VERSION, candidate_key='qwen3-4b',
                  diagnostic_prompt_sha256=W.digest(B.SYSTEM), generation=B.GENERATION,
                  scope='same exposed pair and parser outputs; prose interface diagnostic, not production acceptance',
                  changed=['system instructions adapted to prose', 'JSON response_format omitted'],
                  unchanged=['model', 'runtime settings', 'user payload', 'source window',
                             'question parsing', 'gold', '650 output tokens', '120 second request timeout'])
    for row in report['rows']:
        if W.digest(row['payload']) != row['payload_sha256']:
            raise ValueError('Frozen user payload changed')
        row['request_sha256'] = W.digest(B.request_payload(row['payload']))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    report = prepare(args.baseline)
    folder = ROOT / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    output = folder/'report.json'
    def save():
        W.save(output, report)
    log = DEFAULT_HOME/'reader-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder/'previous-runtime.log')
    report['state'] = 'running'
    save()
    print('Report: ' + str(output), flush=True)
    phase = 'startup'
    try:
        with LocalReader(model='qwen3-4b', timeout=120) as reader:
            report.update(candidate_model=reader.receipt, startup_ms=reader.startup_ms)
            if reader.receipt != report['baseline_model']:
                raise ValueError('Candidate and baseline model receipts differ')
            save()
            phase = 'judgement'
            for i, row in enumerate(report['rows']):
                started = time.perf_counter()
                try:
                    row['brief'] = B.read(reader, row['payload'], report['evidence'])
                except (OSError, ValueError, KeyError) as exc:
                    row['error'] = dict(type=type(exc).__name__, message=str(exc))
                row['elapsed_ms'] = (time.perf_counter()-started)*1000
                save()
                brief = row.get('brief', {})
                print(json.dumps(dict(id=row['id'], text=brief.get('text'), completed=brief.get('completed'),
                                      ms=row['elapsed_ms'], usage=brief.get('usage'), error=row.get('error')),
                                 ensure_ascii=False), flush=True)
                if 'error' in row:
                    for remaining in report['rows'][i+1:]:
                        remaining['not_run'] = 'previous request failed; no retry or queue into cancelling server'
                    break
            report['state'] = 'attempted'
    except BaseException as exc:
        report.update(state='interrupted', fatal_error=dict(phase=phase, type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save()
        if log.exists():
            shutil.copyfile(log, folder/'runtime.log')
    # Operational completion only; no automatic semantic success grade.
    return int(any('error' in r or not r.get('brief', {}).get('completed') for r in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
