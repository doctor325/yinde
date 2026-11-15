"""Rejudge the unchanged exposed pair with the v4 event contract, once per run."""
import argparse
import json
from pathlib import Path
import uuid
from search import numbered_event_contract as E
from tests.context_reader import run_windowed as W


def prepare(baseline):
    baseline = Path(baseline).resolve()
    if not baseline.is_relative_to(W.RUN_HOME.resolve()) or baseline.name != 'report.json':
        raise ValueError('Baseline must be a repository window report')
    old = json.loads(baseline.read_text(encoding='utf8'))
    cases, receipt = W.pair()
    if old['state'] != 'attempted' or old['frozen'] != receipt:
        raise ValueError('Require completed baseline with unchanged frozen cases')
    if old['context_sha256'] != W.digest(old['context']):
        raise ValueError('Source window changed')
    if [r['id'] for r in old['rows']] != list(W.PAIR):
        raise ValueError('Baseline pair changed')
    for row, case in zip(old['rows'], cases):
        if any(row[key] != case[key] for key in ('id', 'query', 'expected', 'expected_answer')):
            raise ValueError('Question/gold changed')
    report = {k: old[k] for k in ('frozen', 'scope', 'context', 'context_sha256', 'window', 'selector_version')}
    report.update(version='event-contract-dev-pair-v1', state='prepared',
                  baseline_report=str(baseline), baseline_sha256=W.digest(old),
                  reader_version=E.VERSION, prompt_sha256=W.digest(E.PARSE + E.JUDGE),
                  request_timeout_seconds=120, rows=[dict(
                      id=c['id'], query=c['query'], expected=c['expected'],
                      expected_answer=c['expected_answer'], semantic_review='pending') for c in cases])
    folder = W.RUN_HOME / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    output = folder / 'report.json'
    W.save(output, report)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    return W.run(prepare(args.baseline), reader_module=E)


if __name__ == '__main__':
    raise SystemExit(main())
