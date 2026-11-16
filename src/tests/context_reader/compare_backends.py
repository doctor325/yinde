"""Two fresh processes, one frozen short request, same binary/model/template.

CPU and GPU use the same verified Vulkan distribution. This checks runtime
output correctness only; it does not load any historical evidence or gold.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid

from search.local_evidence_reader import DEFAULT_HOME, LocalReader

REQUEST = dict(model='local', temperature=0, seed=20261111, max_tokens=32,
               chat_template_kwargs={'enable_thinking': False}, messages=[
                   dict(role='system', content='请按要求作答，不要解释，不要添加标点。/no_think'),
                   dict(role='user', content='请计算7加5，并只输出阿拉伯数字结果。')])
EXPECTED = '12'
MODES = ('cpu', 'vulkan')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf8')).hexdigest()


class DiagnosticReader(LocalReader):
    def __init__(self, compute):
        if compute not in MODES:
            raise ValueError('Unsupported diagnostic compute device')
        self.compute = compute
        # Both arms resolve the same executable, verified files and child env.
        super().__init__(model='qwen3-8b', backend='vulkan', timeout=60)

    def gpu_arguments(self):
        return ['-ngl', '0', '--device', 'none'] if self.compute == 'cpu' else super().gpu_arguments()


def judge(response):
    choice = response['choices'][0]
    text = choice['message']['content']
    complete = choice['finish_reason'] == 'stop' and isinstance(text, str) and bool(text.strip())
    return dict(text=text, finish_reason=choice['finish_reason'], completed=complete,
                exact_answer=bool(complete and text.strip() == EXPECTED),
                usage=response.get('usage', {}), semantic_acceptance=False)


def measure(reader):
    props = reader.request('/props')
    template = props.get('chat_template')
    if not isinstance(template, str) or not template:
        raise ValueError('Cannot verify active chat template; no inference sent')
    request = copy.deepcopy(REQUEST)
    identity = dict(model_sha256=reader.model_spec['asset']['sha256'],
                    executable_sha256=reader.runtime_receipt['files']['llama-server.exe'],
                    runtime_files_sha256=digest(reader.runtime_receipt['files']),
                    template_sha256=digest(template), request_sha256=digest(request))
    started = time.perf_counter()
    result = judge(reader.request('/v1/chat/completions', request))
    result['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
    return dict(identity=identity, result=result)


def summarize(rows):
    if len(rows) != 2 or any('error' in row or 'result' not in row for row in rows):
        return dict(comparable=False, status='incomplete', semantic_acceptance=False)
    comparable = rows[0]['identity'] == rows[1]['identity']
    if not comparable:
        status = 'input_or_runtime_identity_drift'
    elif rows[0]['result']['exact_answer'] and rows[1]['result']['exact_answer']:
        status = 'both_pass_short_probe'
    elif rows[0]['result']['exact_answer']:
        status = 'cpu_pass_gpu_fail'
    elif rows[1]['result']['exact_answer']:
        status = 'cpu_fail_gpu_pass'
    else:
        status = 'both_fail_short_probe'
    return dict(comparable=comparable, status=status, semantic_acceptance=False)


def main():
    folder = DEFAULT_HOME / 'backend-comparison' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    report = dict(version='backend-short-probe-v1', state='prepared', request=REQUEST,
                  request_sha256=digest(REQUEST), expected=EXPECTED, rows=[],
                  changed=['compute device and offloaded layers'],
                  fixed=dict(model='qwen3-8b', distribution='b12283 Vulkan x64',
                             context=8192, cpu_threads=4, thinking=False, timeout_seconds=60),
                  historical_evidence_calls=0, retries=0, production_changed=False)
    def save():
        report['summary'] = summarize(report['rows'])
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    log = DEFAULT_HOME / 'reader-vulkan-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder / 'previous-runtime.log')
    try:
        for mode in MODES:
            row = dict(compute=mode)
            report['rows'].append(row)
            report['state'] = 'running'
            save()
            try:
                with DiagnosticReader(mode) as reader:
                    row.update(startup_ms=reader.startup_ms, gpu_arguments=reader.gpu_arguments())
                    row.update(measure(reader))
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
    return int(report['summary']['status'] != 'both_pass_short_probe')


if __name__ == '__main__':
    raise SystemExit(main())
