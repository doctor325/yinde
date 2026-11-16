"""Exactly two exposed prose judgements, using frozen model-neutral inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from search import brief_evidence_diagnostic as B, numbered_evidence as N
from search.local_evidence_reader import DEFAULT_HOME, LocalReader
from tests.context_reader.prepare_strong_reader import digest

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PAIR = ('chenping_positive', 'chenping_contrast')


def select_pair(inputs):
    if inputs['system'] != B.SYSTEM:
        raise ValueError('Frozen system prompt changed')
    if len(inputs['rows']) != 8 or len({r['id'] for r in inputs['rows']}) != 8:
        raise ValueError('Frozen eight-case inventory changed')
    by_id = {r['id']: r for r in inputs['rows']}
    return [by_id[key] for key in PAIR]


def prepare():
    receipt = json.loads((HERE / 'strong_reader_preparation_r2.json').read_text(encoding='utf8'))
    path = (ROOT / receipt['portable_inputs']['input_path']).resolve()
    if not path.is_relative_to(DEFAULT_HOME):
        raise ValueError('Input path outside experiment')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt['portable_inputs']['sha256_utf8_lf']:
        raise ValueError('Frozen portable inputs changed')
    inputs = json.loads(raw)
    audit = json.loads((path.parent / 'local-audit.json').read_text(encoding='utf8'))
    if digest(audit) != receipt['audit_sha256']:
        raise ValueError('Frozen source/gold audit changed')
    rows = []
    for row in select_pair(inputs):
        source = next(r for r in audit if r['case']['id'] == row['id'])
        evidence = source['evidence']
        if N.evidence_payload(evidence) != {k: row['payload'][k] for k in ('evidence', 'opening_context', 'incomplete')}:
            raise ValueError('Evidence/source mapping drift')
        if row['payload']['original_question'] != source['case']['query']:
            raise ValueError('Question drift')
        rows.append(dict(id=row['id'], payload=row['payload'], evidence=evidence,
                         request_sha256=digest(B.request_payload(row['payload'])), semantic_review=None))
    return dict(state='prepared', model='qwen3-8b', backend='vulkan',
                portable_inputs_sha256=receipt['portable_inputs']['sha256_utf8_lf'],
                prompt_sha256=digest(B.SYSTEM), generation=B.GENERATION,
                question_parse='frozen 4B parser outputs; no new parsing',
                runtime=dict(context=8192, cpu_threads=4, gpu_layers=99, device='Vulkan0', timeout_seconds=120),
                scope='exposed pair only; no held-out calls, no production change', rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vulkan-compatible', action='store_true',
                        help='Use the explicit flash-off plus no-F16 diagnostic profile')
    args = parser.parse_args()
    report = prepare()
    if args.vulkan_compatible:
        from tests.context_reader.probe_vulkan_compatibility import ProbeReader
        from tests.context_reader.compare_backends import digest as identity_digest
        model_reader = ProbeReader('flash-off-no-f16')
        report['compatibility_profile'] = model_reader.profile
        report['runtime'].update(flash_attention='off', child_disable_f16=True)
    else:
        model_reader = LocalReader(model='qwen3-8b', backend='vulkan', timeout=120)
    folder_kind = 'local-vulkan-compatible-pilot' if args.vulkan_compatible else 'local-vulkan-pilot'
    folder = DEFAULT_HOME / folder_kind / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    log = DEFAULT_HOME / 'reader-vulkan-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder / 'previous-runtime.log')
    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    try:
        with model_reader as reader:
            if args.vulkan_compatible:
                template = reader.request('/props').get('chat_template')
                if not isinstance(template, str) or identity_digest(template) != 'ec8f439daaf5791f1da8686759109efa3f5ff5698cd407018a9f96a739c28fae':
                    raise ValueError('Chat template differs from GPU comparison; no inference sent')
                report['template_sha256'] = identity_digest(template)
            report.update(state='running', model_receipt=reader.receipt,
                          runtime_receipt=reader.runtime_receipt, startup_ms=reader.startup_ms,
                          launch_arguments=reader.gpu_arguments())
            save()
            for i, row in enumerate(report['rows']):
                try:
                    row['brief'] = B.read(reader, row['payload'], row['evidence'])
                except (OSError, ValueError, KeyError) as exc:
                    row['error'] = dict(type=type(exc).__name__, message=str(exc))
                save()
                print(json.dumps(dict(id=row['id'], brief=row.get('brief'), error=row.get('error')), ensure_ascii=False), flush=True)
                if 'error' in row or not row.get('brief', {}).get('completed'):
                    for pending in report['rows'][i + 1:]:
                        pending['not_run'] = 'previous request failed/incomplete; no retry'
                    break
            report['state'] = 'attempted'
    except BaseException as exc:
        report.update(state='interrupted', error=dict(type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save()
        if log.exists():
            shutil.copyfile(log, folder / 'runtime.log')
    return int(any(not r.get('brief', {}).get('completed') for r in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
