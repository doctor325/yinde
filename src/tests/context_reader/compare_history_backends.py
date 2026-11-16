"""Bounded replay of one saved failure; no semantic scoring or held-out calls."""
import copy
import hashlib
import itertools
import json
import re
import shutil
import time
import uuid

from search import brief_evidence_diagnostic as B
from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import compare_backends as C, run_local_pilot as P

BASELINE = DEFAULT_HOME / 'local-vulkan-pilot/8d9ac8e020d5/report.json'
BASELINE_SHA256 = '9835d44a715b897a85676e77f3144b20e75d6c8daba15fcaccbce1ae06f71088'
CASE_ID = 'chenping_positive'
MAX_TOKENS = 64
TIMEOUT = 120
G_RUN_MIN = 16


def build_case(baseline, prepared):
    old = next(row for row in baseline['rows'] if row['id'] == CASE_ID)
    frozen = next(row for row in prepared['rows'] if row['id'] == CASE_ID)
    if old['payload'] != frozen['payload'] or old['evidence'] != frozen['evidence']:
        raise ValueError('Original question/evidence drift')
    for field in ('model', 'portable_inputs_sha256', 'prompt_sha256', 'generation'):
        if baseline[field] != prepared[field]:
            raise ValueError('Original configuration drift: ' + field)
    original = B.request_payload(old['payload'])
    if P.digest(original) != old['request_sha256'] or old['request_sha256'] != frozen['request_sha256']:
        raise ValueError('Original request drift')
    request = copy.deepcopy(original)
    request['max_tokens'] = MAX_TOKENS
    return dict(id=CASE_ID, request=request, original_request_sha256=P.digest(original),
                original_max_tokens=original['max_tokens'], request_sha256=C.digest(request),
                messages_sha256=C.digest(original['messages']))


def prepare():
    raw = BASELINE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BASELINE_SHA256:
        raise ValueError('Saved failure report changed')
    baseline = json.loads(raw)
    # Reuses the existing source mapping checks; no new parsing or selection.
    case = build_case(baseline, P.prepare())
    asset = next(a for a in baseline['model_receipt']['assets'] if a['name'].endswith('.gguf'))
    files = baseline['runtime_receipt']['files']
    case['expected_runtime'] = dict(model_sha256=asset['sha256'],
                                   executable_sha256=files['llama-server.exe'],
                                   runtime_files_sha256=C.digest(files))
    return case


def observe(response):
    choice = response['choices'][0]
    text = choice['message']['content']
    if not isinstance(text, str):
        raise ValueError('Non-text response; cannot inspect output prefix')
    match = re.search('G{' + str(G_RUN_MIN) + ',}', text)
    runs = [(char, sum(1 for _ in group)) for char, group in itertools.groupby(text)]
    longest = max(runs, key=lambda item: item[1], default=('', 0))
    return dict(text=text, finish_reason=choice['finish_reason'],
                completed=choice['finish_reason'] == 'stop' and bool(text.strip()),
                g_signature=bool(match), g_run_start=match.start() if match else None,
                g_run_length=len(match[0]) if match else 0,
                longest_character_run=dict(character=longest[0], length=longest[1]),
                usage=response.get('usage', {}), semantic_acceptance=False)


def measure(reader, case, expected_identity=None):
    template = reader.request('/props').get('chat_template')
    if not isinstance(template, str) or not template:
        raise ValueError('Missing template; no inference sent')
    identity = dict(model_sha256=reader.model_spec['asset']['sha256'],
                    executable_sha256=reader.runtime_receipt['files']['llama-server.exe'],
                    runtime_files_sha256=C.digest(reader.runtime_receipt['files']),
                    template_sha256=C.digest(template), request_sha256=C.digest(case['request']))
    if any(identity[k] != v for k, v in case['expected_runtime'].items()):
        raise ValueError('Runtime differs from original failed run; no inference sent')
    if identity['request_sha256'] != case['request_sha256']:
        raise ValueError('Bounded request changed; no inference sent')
    if expected_identity is not None and identity != expected_identity:
        raise ValueError('CPU/GPU identity drift; no inference sent')
    started = time.perf_counter()
    result = observe(reader.request('/v1/chat/completions', copy.deepcopy(case['request'])))
    result['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
    return dict(identity=identity, result=result)


def summarize(rows):
    if len(rows) != 2 or any('error' in r or 'result' not in r for r in rows):
        return dict(comparable=False, status='incomplete', semantic_acceptance=False)
    comparable = [r['compute'] for r in rows] == list(C.MODES) and rows[0]['identity'] == rows[1]['identity']
    flags = tuple(r['result']['g_signature'] for r in rows)
    status = {(False, False): 'g_signature_not_reproduced_in_prefix',
              (False, True): 'g_signature_gpu_only', (True, False): 'g_signature_cpu_only',
              (True, True): 'g_signature_both'}[flags] if comparable else 'identity_drift'
    return dict(comparable=comparable, status=status, semantic_acceptance=False)


def main():
    case = prepare()  # Stop before starting any model if the frozen inputs drift.
    folder = DEFAULT_HOME / 'history-backend-comparison' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    report = dict(version='history-backend-prefix-v1', state='prepared', case=case,
                  baseline_sha256=BASELINE_SHA256, rows=[], held_out_calls=0, retries=0,
                  fixed=dict(context=8192, cpu_threads=4, timeout_seconds=TIMEOUT),
                  g_signature_min_run=G_RUN_MIN, semantic_acceptance=False,
                  change_from_original='max_tokens only: 650 to 64; unchanged messages',
                  change_between_arms='compute device and offloaded layers only')

    def save():
        report['summary'] = summarize(report['rows'])
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')

    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    log = DEFAULT_HOME / 'reader-vulkan-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder / 'previous-runtime.log')
    identity = None
    try:
        for mode in C.MODES:
            row = dict(compute=mode)
            report['rows'].append(row)
            report['state'] = 'running'
            save()
            reader = C.DiagnosticReader(mode)
            reader.timeout = TIMEOUT
            try:
                with reader:
                    row.update(startup_ms=reader.startup_ms, gpu_arguments=reader.gpu_arguments())
                    row.update(measure(reader, case, identity))
                    identity = row['identity']
            except Exception as exc:
                row['error'] = dict(type=type(exc).__name__, message=str(exc))
            finally:
                if log.exists():
                    shutil.copyfile(log, folder / (mode + '-runtime.log'))
                save()
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if 'error' in row:
                break
        report['state'] = 'attempted'
    finally:
        save()
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)
    # Exit 0 means the diagnostic was collected, not that either model passed.
    return int(not report['summary']['comparable'])


if __name__ == '__main__':
    raise SystemExit(main())
