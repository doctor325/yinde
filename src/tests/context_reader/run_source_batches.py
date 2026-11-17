"""One source-order batch per invocation; cached attempts never cause new calls."""
import argparse
import json
import shutil
import uuid
from search import source_first_batches as B
from search.local_evidence_reader import DEFAULT_HOME
from scripts.setup_evidence_reader import model_spec
from tests.context_reader import run_source_first as R

HOME = DEFAULT_HOME / 'source-first-batches'
TEMPLATE = 'bbfb42f2b5a4a3766b7c3864d6b133ed57964397fe37b1352e1d9d704f0d7d14'


def prepare():
    old, reference = R.prepare()
    P = R.C.P
    receipt = json.loads((P.HERE / 'strong_reader_preparation_r2.json').read_text(encoding='utf8'))
    audit_path = P.ROOT / receipt['portable_inputs']['input_path']
    audit = json.loads((audit_path.parent / 'local-audit.json').read_text(encoding='utf8'))
    if P.digest(audit) != receipt['audit_sha256']:
        raise ValueError('Source audit changed')
    source = next(r for r in audit if r['case']['id'] == old['rows'][0]['id'])
    if source['evidence'] != old['evidence']:
        raise ValueError('Source/position mapping drift')
    tasks = B.plan(old['evidence'], source['context']['source'])
    execution = dict(model=R.C.MODEL, assets=model_spec(R.C.MODEL)[1],
        runtime_files=reference['runtime_receipt']['files'], runtime=old['runtime'],
        template_sha256=TEMPLATE, reader_version=B.S.VERSION)
    return dict(version=B.VERSION, state='prepared', tasks=tasks, execution=execution,
        max_calls=1, model_calls=0, held_out_calls=0, per_call_timeout_seconds=120,
        worst_case_request_seconds=120 * sum(not t['blocked'] for t in tasks),
        max_output_tokens_per_call=650, semantic_acceptance=None), reference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    report, reference = prepare()
    folder = HOME / 'runs' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    cache = HOME / 'cache'
    def save():
        report['progress'] = B.progress(report['tasks'], report['execution'], cache)
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    print(json.dumps(report['progress']), flush=True)
    if not args.run:
        return 0
    pending = next((t for t in report['tasks'] if not t['blocked'] and
                    B.cached(cache, t, report['execution']) is None), None)
    if pending is None:
        print('No unattempted units; no model startup or automatic retry.')
        return 0
    report['selected_core_ids'] = pending['core_ids']
    reader = R.C.CandidateReader()
    try:
        with reader:
            if R.C.verify_runtime(reader, reference) != TEMPLATE:
                raise ValueError('Native template changed; no batch call')
            report.update(state='running', startup_ms=reader.startup_ms,
                          launch_arguments=reader.gpu_arguments(), model_calls=1)
            save()
            result, reused = B.run_one(reader, pending, report['execution'], cache)
            report.update(state='attempted', result=result, cache_reused=reused)
    except BaseException as exc:
        report.update(state='interrupted', error=dict(type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save()
        if hasattr(reader, 'log_path') and reader.log_path.exists():
            shutil.copyfile(reader.log_path, folder / 'runtime.log')
    print(json.dumps(dict(core=report['selected_core_ids'], completed=result['completed'],
                          ms=result['ms'], error=result.get('error'))), flush=True)
    return int(not result['completed'])


if __name__ == '__main__':
    raise SystemExit(main())
