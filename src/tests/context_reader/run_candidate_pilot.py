"""Pinned alternative local reader. Default: offline preparation, no inference."""
import argparse
import hashlib
import json
import shutil
import uuid

from scripts.setup_evidence_reader import model_spec
from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import run_local_pilot as P
from tests.context_reader.compare_backends import digest
from tests.context_reader.probe_vulkan_compatibility import CpuComparisonReader

MODEL = 'qwen2.5-14b-instruct'
REFERENCE = DEFAULT_HOME / 'local-cpu-comparison-pilot/9c69842a23b8/report.json'
REFERENCE_SHA256 = '881860e2e5abcbe4214f6ccb3322a3c0589f2f5bb231f4ab38021fceb352ddb6'


class CandidateReader(CpuComparisonReader):
    """Keep the completed CPU arm's binary, device, flags and child environment."""
    def __init__(self):
        super().__init__()
        self.model_key = MODEL
        self.model_spec, self.assets = model_spec(MODEL)


def prepare():
    raw = REFERENCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA256:
        raise ValueError('Saved CPU baseline changed')
    reference = json.loads(raw)
    report = P.prepare()
    # Verify the old model's exact requests first; only then substitute model.
    P.validate_comparison_inputs(report, reference)
    if reference['backend'] != 'cpu' or reference['state'] != 'attempted':
        raise ValueError('Baseline must be the completed CPU attempt')
    report.update(model=MODEL, backend='cpu', distribution=reference['distribution'],
                  runtime=dict(reference['runtime']), reference_sha256=REFERENCE_SHA256,
                  compatibility_profile=reference['compatibility_profile'],
                  changed=['model weights/architecture/tokenizer/native chat template'],
                  semantic_acceptance=None, held_out_calls=0, model_calls=0,
                  comparison_limit='Candidate comparison, not size-only causation or throughput benchmark')
    return report, reference


def verify_runtime(reader, reference):
    if reader.runtime_receipt['files'] != reference['runtime_receipt']['files']:
        raise ValueError('CPU runtime files changed; no inference sent')
    if reader.gpu_arguments() != reference['launch_arguments']:
        raise ValueError('CPU launch arguments changed; no inference sent')
    template = reader.request('/props').get('chat_template')
    if not isinstance(template, str) or not template:
        raise ValueError('Missing native template; no inference sent')
    # A different model has a different native template. Record, never replace
    # it with the 8B template or treat a valid template as a semantic pass.
    return digest(template)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Run only the two frozen exposed cases on CPU')
    args = parser.parse_args(argv)
    report, reference = prepare()
    folder = DEFAULT_HOME / 'alternative-reader-pilot' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    output = folder / 'report.json'

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')

    save()
    print('Report: ' + str(output), flush=True)
    if not args.run:
        print('Prepared only: 2 exposed cases; 0 model calls; 0 held-out calls.')
        return 0
    log = DEFAULT_HOME / 'reader-vulkan-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder / 'previous-runtime.log')
    reader = CandidateReader()
    log_started = False
    try:
        with reader:
            log_started = True
            report.update(template_sha256=verify_runtime(reader, reference), state='running',
                          model_receipt=reader.receipt, runtime_receipt=reader.runtime_receipt,
                          startup_ms=reader.startup_ms, launch_arguments=reader.gpu_arguments())
            save()
            for i, row in enumerate(report['rows']):
                if P.digest(P.B.request_payload(row['payload'])) != row['request_sha256']:
                    raise ValueError('Request drift; no inference sent')
                report['model_calls'] += 1
                save()
                try:
                    row['brief'] = P.B.read(reader, row['payload'], row['evidence'])
                except (OSError, ValueError, KeyError) as exc:
                    row['error'] = dict(type=type(exc).__name__, message=str(exc))
                save()
                print(json.dumps(dict(id=row['id'], completed=row.get('brief', {}).get('completed', False),
                                      ms=row.get('brief', {}).get('ms'), error=row.get('error'))), flush=True)
                if not row.get('brief', {}).get('completed'):
                    for pending in report['rows'][i + 1:]:
                        pending['not_run'] = 'Previous call failed/incomplete; no retry'
                    break
            report['state'] = 'attempted'
    except BaseException as exc:
        report.update(state='interrupted', error=dict(type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save()
        # Preserve a startup log if this invocation opened it; never mislabel
        # a previous run's log as belonging to a missing/unverified model.
        if (log_started or hasattr(reader, 'log_path')) and log.exists():
            shutil.copyfile(log, folder / 'runtime.log')
    return int(any(not r.get('brief', {}).get('completed') for r in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
