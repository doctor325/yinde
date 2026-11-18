"""Explicit non-historical compatibility probe; never runs benchmark cases."""
import argparse
import json
import shutil
import uuid
from scripts.setup_evidence_reader import model_spec
from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import run_native_reasoning as R
from tests.context_reader.stream_trace import capture


class HarmonyReader(R.C.CpuComparisonReader):
    def __init__(self):
        super().__init__()
        self.model_key = 'gpt-oss-20b'
        self.model_spec, self.assets = model_spec(self.model_key)
        self.timeout = 360

    def reasoning_arguments(self):
        return ['--jinja', '--reasoning', 'on', '--reasoning-format', 'deepseek',
                '--reasoning-effort', 'medium', '--reasoning-budget', '512']


def validate_template(prompt):
    if not isinstance(prompt, str) or '<|start|>' not in prompt or '<|message|>' not in prompt:
        raise ValueError('Missing native Harmony framing')
    if '<think>' in prompt or '<|start|>assistant' not in prompt:
        raise ValueError('Invalid native assistant prefill')
    if 'medium' not in prompt:
        raise ValueError('Reasoning effort not present in native rendering')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        print('Explicit --run required; no model started')
        return 0
    folder = DEFAULT_HOME / 'harmony-compatibility' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    report = dict(model='gpt-oss-20b', semantic_calls=0, compatibility_calls=0,
                  semantic_acceptance=None, state='prepared')
    reader = HarmonyReader()
    log = DEFAULT_HOME / 'reader-vulkan-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder/'previous-runtime.log')
    print('Report: '+str(folder/'report.json'), flush=True)
    try:
        reference = R.frozen(R.C.REFERENCE, R.C.REFERENCE_SHA256, normalize_newlines=False)
        with reader:
            report.update(template_sha256=R.C.verify_runtime(reader, reference),
                          model_receipt=reader.receipt, runtime_receipt=reader.runtime_receipt,
                          startup_ms=reader.startup_ms, arguments=reader.reasoning_arguments())
            request = dict(model='local', messages=[dict(role='user', content='计算1+1，只给出计算结果。')],
                           temperature=1.0, top_p=.95, top_k=20, min_p=0,
                           presence_penalty=1.5, repeat_penalty=1.0, seed=20261111,
                           max_tokens=2048, reasoning_effort='medium',
                           chat_template_kwargs={'reasoning_effort':'medium'})
            rendered = reader.request('/apply-template', dict(messages=request['messages'],
                chat_template_kwargs=request['chat_template_kwargs']))
            validate_template(rendered.get('prompt'))
            report['rendered_sha256'] = R.digest(rendered['prompt'])
            (folder/'rendered.txt').write_text(rendered['prompt'], encoding='utf8')
            report['compatibility_calls'] = 1
            trace, response = capture(reader, request, folder)
            report.update(trace=trace, response=response, process_memory=R.own_process_memory(reader))
            message = response['choices'][0]['message']
            report['channels_verified'] = bool(trace['completed'] and message['reasoning_content']
                and message['content'].strip() and '<|' not in message['content'])
            report['state'] = 'completed' if report['channels_verified'] else 'incomplete'
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        report.update(state='failed', error=dict(type=type(exc).__name__, message=str(exc)))
    finally:
        if hasattr(reader, 'log_path') and log.exists():
            shutil.copyfile(log, folder/'runtime.log')
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(state=report['state'],semantic_calls=0)),flush=True)
    return int(report['state'] != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
