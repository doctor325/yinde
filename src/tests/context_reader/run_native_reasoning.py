"""Frozen eight-case local experiment; offline preparation unless --run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from scripts.setup_evidence_reader import model_spec
from search import brief_evidence_diagnostic as B, numbered_evidence as N
from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import run_candidate_pilot as C

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'src/docs/local_reasoning_candidate_plan.json'
PLAN_SHA = '9173ace636d366f5ad0e785c35de60c5b1fb675c028ac9f2bdf7eaff06e7d24b'
MODEL = 'qwen3.5-9b'


def digest(value):
    return hashlib.sha256(N.encode(value).encode('utf8')).hexdigest()


def frozen(path, expected, *, normalize_newlines=True):
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Path outside repository')
    raw = path.read_bytes()
    if normalize_newlines:
        raw = raw.replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Frozen file changed: ' + path.name)
    return json.loads(raw)


def make_request(system, payload, execution):
    if system != B.SYSTEM or not system.endswith('/no_think'):
        raise ValueError('Frozen instruction changed')
    if set(payload) != {'original_question', 'parsed_question', 'evidence', 'opening_context', 'incomplete'}:
        raise ValueError('Unexpected payload fields; no gold/ledger may enter request')
    return dict(model='local', **execution['sampling'],
        max_tokens=execution['max_tokens_including_reasoning'],
        chat_template_kwargs=execution['chat_template_kwargs'], messages=[
            dict(role='system', content=system.removesuffix('/no_think')),
            dict(role='user', content=N.encode(payload))])


class NativeReader(C.CpuComparisonReader):
    def __init__(self):
        super().__init__()
        self.model_key = MODEL
        self.model_spec, self.assets = model_spec(MODEL)

    def reasoning_arguments(self):
        return ['--reasoning', 'on', '--jinja', '--reasoning-format', 'deepseek']


def prepare():
    plan = frozen(PLAN, PLAN_SHA)
    inputs = plan['input']
    packet = frozen(ROOT / inputs['path'], inputs['lf_sha256'])
    frozen(ROOT / inputs['gold_path'], inputs['gold_sha256'])
    if digest(packet['system']) != inputs['system_digest_json_encoding']:
        raise ValueError('Frozen system digest changed')
    selected = plan['selected']
    spec, _ = model_spec(MODEL)
    expected_asset = dict(name=selected['file'], bytes=selected['bytes'],
                          sha256=selected['sha256'], url=selected['url'])
    if spec['id'] != selected['repo'] or spec['asset'] != expected_asset:
        raise ValueError('Model differs from frozen candidate plan')
    receipt = json.loads((Path(__file__).parent / 'strong_reader_preparation_r2.json').read_text(encoding='utf8'))
    audit = json.loads((ROOT / inputs['path']).with_name('local-audit.json').read_text(encoding='utf8'))
    if digest(audit) != receipt['audit_sha256']:
        raise ValueError('Frozen source audit changed')
    if [r['id'] for r in packet['rows']] != [r['id'] for r in inputs['selection']]:
        raise ValueError('Frozen inventory/order changed')
    # Existing runtime receipts bind raw bytes; packet/gold use LF-normalized hashes.
    reference = frozen(C.REFERENCE, C.REFERENCE_SHA256, normalize_newlines=False)
    rows = []
    for item, split in zip(packet['rows'], inputs['selection']):
        source = next(s for s in audit if s['case']['id'] == item['id'])
        evidence, payload = source['evidence'], item['payload']
        if N.evidence_payload(evidence) != {k: payload[k] for k in ('evidence', 'opening_context', 'incomplete')}:
            raise ValueError('Source/number mapping changed')
        if payload['original_question'] != source['case']['query']:
            raise ValueError('Question changed')
        request = make_request(packet['system'], payload, plan['proposed_execution'])
        rows.append(dict(id=item['id'], split=split['split'], evidence=evidence,
            request=request, request_sha256=digest(request), semantic_review=None,
            not_run='Awaiting explicit execution'))
    return dict(version=plan['version'], plan_sha256=PLAN_SHA, state='prepared',
        model=MODEL, backend='cpu', reference_sha256=C.REFERENCE_SHA256,
        input_sha256=inputs['lf_sha256'], gold_sha256=inputs['gold_sha256'],
        acceptance=plan['acceptance'], execution=plan['proposed_execution'],
        model_calls=0, held_out_calls=0, semantic_acceptance=None, rows=rows), reference


def own_process_memory(reader):
    """Only the owned model process; not a system inventory or working-set sum."""
    if os.name != 'nt' or not reader.process:
        return dict(available=False)
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    api.restype = wintypes.BOOL
    if not api(int(reader.process._handle), ctypes.byref(counters), counters.cb):
        return dict(available=False, winerror=ctypes.get_last_error())
    return dict(available=True, working_set_bytes=counters.WorkingSetSize,
                peak_working_set_bytes=counters.PeakWorkingSetSize)


def judge(response, evidence):
    choice = response['choices'][0]
    message = choice['message']
    text = message.get('content')
    if text is None:
        text = ''
    if not isinstance(text, str):
        raise ValueError('Invalid final answer type')
    # Thinking is not an answer. Preserve raw response locally for diagnosis.
    complete = choice['finish_reason'] == 'stop' and bool(text.strip())
    if '<think>' in text or '</think>' in text:
        complete = False
    return dict(text=text, finish_reason=choice['finish_reason'], completed=complete,
        reasoning_chars=len(message.get('reasoning_content') or ''), usage=response.get('usage', {}),
        citation_inventory=B.citation_inventory(text, evidence), semantic_verified=False)


def run_rows(reader, report, save):
    for i, row in enumerate(report['rows']):
        if digest(row['request']) != row['request_sha256']:
            raise ValueError('Request drift; no inference sent')
        report['model_calls'] += 1
        report['held_out_calls'] += int(row['split'] == 'held_out_reader')
        row.pop('not_run', None)
        save()
        started = time.perf_counter()
        try:
            row['response'] = reader.request('/v1/chat/completions', row['request'])
            row['result'] = judge(row['response'], row['evidence'])
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            row['error'] = dict(type=type(exc).__name__, message=str(exc))
        row['ms'] = round((time.perf_counter() - started) * 1000, 2)
        row['process_memory'] = own_process_memory(reader)
        complete = row.get('result', {}).get('completed', False)
        if not complete:
            for pending in report['rows'][i + 1:]:
                pending['not_run'] = 'Previous request failed/incomplete; no retry'
        save()
        print(json.dumps(dict(id=row['id'], completed=complete, ms=row['ms'],
            usage=row.get('result', {}).get('usage'), error=row.get('error')), ensure_ascii=False), flush=True)
        if not complete:
            break


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    report, reference = prepare()
    folder = DEFAULT_HOME / 'native-reasoning-pilot' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)

    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')

    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    if not args.run:
        print('Prepared 8 frozen cases: 2 development + 6 held out; 0 model calls.')
        return 0
    log = DEFAULT_HOME / 'reader-vulkan-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder / 'previous-runtime.log')
    reader = NativeReader()
    try:
        with reader:
            report.update(template_sha256=C.verify_runtime(reader, reference),
                model_receipt=reader.receipt, runtime_receipt=reader.runtime_receipt,
                launch_arguments=reader.gpu_arguments(), reasoning_arguments=reader.reasoning_arguments(),
                startup_ms=reader.startup_ms, startup_memory=own_process_memory(reader))
            request = report['rows'][0]['request']
            rendered = reader.request('/apply-template', dict(messages=request['messages'],
                chat_template_kwargs=request['chat_template_kwargs']))
            prompt = rendered.get('prompt')
            if not isinstance(prompt, str) or not prompt.rstrip().endswith('<think>'):
                raise ValueError('Native thinking prefill not verified; no generation sent')
            report.update(state='running', native_prefill_sha256=digest(prompt))
            save()
            run_rows(reader, report, save)
            report['state'] = 'attempted'
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        report.update(state='failed', error=dict(type=type(exc).__name__, message=str(exc)))
        for row in report['rows']:
            if 'not_run' in row:
                row['not_run'] = 'Runtime/preflight failure; no retry'
    except BaseException:
        report['state'] = 'interrupted'
        raise
    finally:
        save()
        if hasattr(reader, 'log_path') and log.exists():
            shutil.copyfile(log, folder / 'runtime.log')
    print(json.dumps(dict(state=report['state'], model_calls=report['model_calls'],
        held_out_calls=report['held_out_calls'], error=report.get('error'))), flush=True)
    return int(any(not r.get('result', {}).get('completed') for r in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
