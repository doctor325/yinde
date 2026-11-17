"""Prepare/run one question-blind source extraction shared by frozen questions."""
import argparse
import json
import shutil
import uuid
from search import source_first_reader as S
from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import run_candidate_pilot as C


def prepare():
    previous, reference = C.prepare()
    evidence = previous['rows'][0]['evidence']
    if any(row['evidence'] != evidence for row in previous['rows']):
        raise ValueError('This limited pilot requires a shared frozen source window')
    request = S.extraction_request(evidence)
    report = dict(version=S.VERSION, state='prepared', model=C.MODEL,
        changed='source-only extraction once, then deterministic role comparison',
        baseline_inputs_sha256=previous['portable_inputs_sha256'],
        runtime=previous['runtime'], request=request, request_sha256=S.digest(request),
        evidence=evidence, model_calls=0, held_out_calls=0, semantic_acceptance=None,
        rows=[dict(id=r['id'], question=r['payload']['parsed_question'],
                   original_question=r['payload']['original_question']) for r in previous['rows']])
    return report, reference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='One source-only CPU call; no held-out questions')
    args = parser.parse_args(argv)
    report, reference = prepare()
    folder = DEFAULT_HOME / 'source-first-pilot' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    if not args.run:
        print('Prepared: one source-only request, no model calls.')
        return 0
    reader = C.CandidateReader()
    try:
        with reader:
            report.update(template_sha256=C.verify_runtime(reader, reference),
                          model_receipt=reader.receipt, runtime_receipt=reader.runtime_receipt,
                          startup_ms=reader.startup_ms, state='running')
            if S.digest(S.extraction_request(report['evidence'])) != report['request_sha256']:
                raise ValueError('Frozen extraction request drift')
            report['model_calls'] = 1
            save()
            report['extraction'] = S.extract(reader, report['evidence'])
            save()
            for row in report['rows']:
                row['comparison'] = S.compare(row['question'], report['extraction'], report['evidence'])
            report['state'] = 'attempted'
    except BaseException as exc:
        report.update(state='interrupted', error=dict(type=type(exc).__name__, message=str(exc)))
        raise
    finally:
        save()
        if hasattr(reader, 'log_path') and reader.log_path.exists():
            shutil.copyfile(reader.log_path, folder / 'runtime.log')
    print(json.dumps(dict(completed=report['extraction']['completed'],
                          statuses=[r['comparison']['status'] for r in report['rows']])), flush=True)
    return int(not report['extraction']['completed'])


if __name__ == '__main__':
    raise SystemExit(main())
