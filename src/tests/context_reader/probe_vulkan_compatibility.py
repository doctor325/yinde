"""Single-option GPU probes. No production settings or parent env changes."""
import argparse
import hashlib
import json
import re
import shutil
import uuid
from unittest.mock import patch

from scripts import setup_reader_vulkan as V
from search.local_evidence_reader import DEFAULT_HOME
from tests.context_reader import compare_history_backends as H

REFERENCE = DEFAULT_HOME / 'history-backend-comparison/e09e86a76614/report.json'
REFERENCE_SHA256 = 'f726953b77ac9808da57fe292ef44f3e7d2836044f05d3c25ed4c733509c0c89'
PROFILES = {
    'baseline': ([], {}),
    'flash-off': (['--flash-attn', 'off'], {}),
    'no-f16': ([], {'GGML_VK_DISABLE_F16': '1'}),
}


class ProbeReader(H.C.DiagnosticReader):
    def __init__(self, profile):
        if profile not in PROFILES:
            raise ValueError('Unsupported GPU diagnostic profile')
        self.profile = profile
        super().__init__('vulkan')
        self.timeout = H.TIMEOUT

    def gpu_arguments(self):
        return super().gpu_arguments() + ['-lv', '4'] + PROFILES[self.profile][0]

    def __enter__(self):
        original = V.environment

        def child_environment(home):
            env = original(home)
            env.update(PROFILES[self.profile][1])
            return env

        # Scoped to this isolated, sequential diagnostic. Only the owned child
        # receives overrides; do not set os.environ or edit user/system config.
        with patch.object(V, 'environment', child_environment):
            return super().__enter__()


def prepare():
    raw = REFERENCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA256:
        raise ValueError('Previous CPU/GPU diagnostic changed')
    reference = json.loads(raw)
    case = H.prepare()
    if case != reference['case'] or not reference['summary']['comparable']:
        raise ValueError('Fixed request or prior comparison drift')
    return case, reference


def log_configuration(log):
    if not log.exists():
        return []
    wanted = re.compile(r'flash_attn|Flash Attention|offloaded \d+/\d+|fp16:|Vulkan0 (model|KV|compute) buffer')
    return [line for line in log.read_text(encoding='utf8', errors='replace').splitlines()
            if wanted.search(line)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--step', choices=('flash', 'f16'), default='flash')
    args = parser.parse_args()
    profiles = ('baseline', 'flash-off') if args.step == 'flash' else ('no-f16',)
    case, reference = prepare()
    identity = reference['rows'][1]['identity']
    folder = DEFAULT_HOME / 'vulkan-compatibility' / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    report = dict(version='vulkan-compatibility-v1', state='prepared', step=args.step,
                  reference_sha256=REFERENCE_SHA256, case=case, rows=[],
                  shared_instrumentation=['-lv', '4'], max_tokens=H.MAX_TOKENS,
                  timeout_seconds=H.TIMEOUT, retries=0, held_out_calls=0,
                  semantic_acceptance=False, parent_environment_changed=False)

    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')

    log = DEFAULT_HOME / 'reader-vulkan-runtime.log'
    if log.exists():
        shutil.copyfile(log, folder / 'previous-runtime.log')
    save()
    print('Report: ' + str(folder / 'report.json'), flush=True)
    try:
        for profile in profiles:
            row = dict(profile=profile, child_env_overrides=PROFILES[profile][1])
            report['rows'].append(row)
            report['state'] = 'running'
            save()
            try:
                with ProbeReader(profile) as reader:
                    row.update(startup_ms=reader.startup_ms, arguments=reader.gpu_arguments())
                    row.update(H.measure(reader, case, identity))
            except Exception as exc:
                row['error'] = dict(type=type(exc).__name__, message=str(exc))
            finally:
                if log.exists():
                    shutil.copyfile(log, folder / (profile + '-runtime.log'))
                row['configuration_log'] = log_configuration(folder / (profile + '-runtime.log'))
                save()
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if 'error' in row:
                break
        report['state'] = 'attempted'
    finally:
        save()
    # Collection success is not GPU correctness or semantic acceptance.
    return int(len(report['rows']) != len(profiles) or any('error' in r for r in report['rows']))


if __name__ == '__main__':
    raise SystemExit(main())
