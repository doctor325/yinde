"""Run the frozen Harmony comparison, pausing for source review after each answer."""
import argparse
import json

from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import run_native_reasoning as native
from tests.context_reader.probe_harmony import HarmonyReader, validate_template
from tests.context_reader.stream_trace import capture


PLAN_SHA = '1ae2a68e14b46c8d33efdd38e22ca0d360a1cc3b635d46bfc6ce6dadc9e9075c'
PROBE_SHA = '3a59e735a3fb36191678b490e18ac4e88b91ddded240f67227be461b78261072'


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def run():
    plan = native.frozen(native.ROOT / 'src/docs/gpt_oss_local_comparison_plan.json', PLAN_SHA)
    probe = native.frozen(DEFAULT_HOME / 'harmony-compatibility/33351226f8f3/report.json',
                          PROBE_SHA, normalize_newlines=False)
    prepared, reference = native.prepare()
    rows = prepared['rows']
    order = plan['stages']['development'] + plan['stages']['held_out']
    if [row['id'] for row in rows] != order or probe['state'] != 'completed':
        raise ValueError('Frozen inventory or compatibility mismatch')
    reader = HarmonyReader()
    execution = plan['execution']
    root = DEFAULT_HOME / 'harmony-comparison-v1'
    root.mkdir(exist_ok=False)
    report = dict(plan_sha256=PLAN_SHA, probe_sha256=PROBE_SHA, state='starting',
                  calls=0, held_out_calls=0, pending=order.copy(), rows=[])
    path = root / 'report.json'
    try:
        with reader:
            if (native.C.verify_runtime(reader, reference) != probe['template_sha256']
                    or reader.receipt != probe['model_receipt']
                    or reader.reasoning_arguments() != probe['arguments']):
                raise ValueError('Model or native runtime drift')
            report['startup_ms'] = reader.startup_ms
            for row in rows:
                case = row['id']
                folder = root / case
                folder.mkdir()
                request = dict(row['request'], reasoning_effort=execution['reasoning_effort'],
                               chat_template_kwargs={'reasoning_effort': execution['reasoning_effort']})
                rendered = reader.request('/apply-template', dict(messages=request['messages'],
                                          chat_template_kwargs=request['chat_template_kwargs']))['prompt']
                validate_template(rendered)
                if any(message['content'] not in rendered for message in request['messages']):
                    raise ValueError('Rendered instructions or evidence missing')
                save(folder/'request.json', request)
                record = dict(id=case, split=row['split'], request_sha256=native.digest(request),
                              rendered_sha256=native.digest(rendered))
                report['rows'].append(record)
                report['pending'].remove(case)
                report['calls'] += 1
                report['held_out_calls'] += int(case in plan['stages']['held_out'])
                report['state'] = 'running'
                save(path, report)
                print('Running '+case, flush=True)
                log_offset = reader.log_path.stat().st_size
                trace, response = capture(reader, request, folder)
                with reader.log_path.open('rb') as log:
                    log.seek(log_offset)
                    logs = log.read()
                result = native.judge(response, row['evidence'])
                record.update(trace=trace, usage=result['usage'], citations=result['citation_inventory'],
                              peak_memory=native.own_process_memory(reader),
                              budget_active=b'activated, budget=512 tokens' in logs,
                              forced_end=b'budget exhausted, forcing end sequence' in logs)
                if not (record['budget_active'] and trace['completed'] and result['completed']
                        and trace['reasoning_chars'] and '<|' not in result['text']):
                    report['state'] = 'incomplete_or_invalid_runtime'
                    return 1
                report['state'] = 'awaiting_source_review'
                save(path, report)
                print(result['text'], flush=True)
                review = input('Source review (PASS or FAIL, then a space and reason): ').strip()
                verdict, _, reason = review.partition(' ')
                if verdict not in ('PASS', 'FAIL') or not reason.strip():
                    raise ValueError('Missing manual source review')
                record['review'] = dict(verdict=verdict, reason=reason)
                if verdict == 'FAIL':
                    report['state'] = 'stopped_at_semantic_failure'
                    return 1
                save(path, report)
            report['state'] = 'all_cases_reviewed_pass'
            return 0
    except BaseException as error:
        report.update(state='interrupted_or_failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        save(path, report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, action='store_true')
    parser.parse_args()
    raise SystemExit(run())
