import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.setup_evidence_reader import ASSETS, model_spec
from search.local_evidence_reader import LocalReader
from tests.context_reader import run_native_reasoning as R


class NativeReasoningTests(unittest.TestCase):
    def setUp(self):
        self.execution = json.loads(R.PLAN.read_text(encoding='utf8'))['proposed_execution']
        self.payload = dict(original_question='问事件', parsed_question={'actor':'甲'},
            evidence={'e1':'甲去，乙留。'}, opening_context=[], incomplete=[])
        self.evidence = [dict(id='e1', text='甲去，乙留。', complete=True, role='event_context', parts=[])]

    def test_fixed_candidate_and_legacy_defaults(self):
        plan = json.loads(R.PLAN.read_text(encoding='utf8'))
        spec, assets = model_spec(R.MODEL)
        self.assertEqual(assets[1]['sha256'], plan['selected']['sha256'])
        self.assertEqual(assets[1]['bytes'], plan['selected']['bytes'])
        self.assertEqual(assets[1]['url'], plan['selected']['url'])
        self.assertEqual(LocalReader().assets, ASSETS)
        self.assertEqual(LocalReader().reasoning_arguments(), ['--reasoning','off'])
        reader = R.NativeReader()
        self.assertEqual(reader.compute, 'cpu')
        self.assertEqual(reader.timeout, 120)
        self.assertEqual(reader.gpu_arguments(), R.C.CpuComparisonReader().gpu_arguments())
        self.assertEqual(reader.reasoning_arguments(), ['--reasoning','on','--jinja','--reasoning-format','deepseek'])

    def test_request_removes_only_mode_marker_preserves_payload(self):
        before = copy.deepcopy(self.payload)
        request = R.make_request(R.B.SYSTEM, self.payload, self.execution)
        self.assertEqual(request['messages'][0]['content'] + '/no_think', R.B.SYSTEM)
        self.assertEqual(json.loads(request['messages'][1]['content']), before)
        self.assertEqual(self.payload, before)
        self.assertEqual(request['max_tokens'], 2048)
        self.assertTrue(request['chat_template_kwargs']['enable_thinking'])
        self.assertNotIn('response_format', request)
        with self.assertRaises(ValueError):
            R.make_request(R.B.SYSTEM, dict(self.payload, gold='forbidden'), self.execution)
        with self.assertRaises(ValueError):
            R.make_request(R.B.SYSTEM+'extra instruction', self.payload, self.execution)

    def test_frozen_files_reject_drift(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            path = Path(temp) / 'fixture.json'
            path.write_bytes(b'{}\r\n')
            sha = hashlib.sha256(b'{}\n').hexdigest()
            self.assertEqual(R.frozen(path, sha), {})
            path.write_bytes(b'{"changed":true}\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                R.frozen(path, sha)

    def response(self, text='依据不足[e1]', finish='stop', reasoning='internal'):
        return dict(choices=[dict(finish_reason=finish, message=dict(content=text,
            reasoning_content=reasoning))], usage={'completion_tokens':10})

    def test_reasoning_or_truncation_cannot_be_scored_as_final_answer(self):
        for response in (self.response(None), self.response('片段','length'), self.response('<think>尚未完成')):
            self.assertFalse(R.judge(response, self.evidence)['completed'])
        judged = R.judge(self.response(), self.evidence)
        self.assertTrue(judged['completed'])
        self.assertFalse(judged['semantic_verified'])

    def report(self):
        request = R.make_request(R.B.SYSTEM, self.payload, self.execution)
        return dict(model_calls=0, held_out_calls=0, rows=[dict(id=str(i),
            split='exposed_development' if i<2 else 'held_out_reader',
            request=copy.deepcopy(request), request_sha256=R.digest(request),
            evidence=self.evidence, not_run='pending') for i in range(8)])

    def test_incomplete_stops_without_retry_keeps_all_rows(self):
        for outcome in (self.response(None), TimeoutError('bounded timeout')):
            report = self.report()
            reader = Mock()
            reader.request.side_effect = [outcome]
            with patch.object(R, 'own_process_memory', return_value={'available':False}):
                R.run_rows(reader, report, Mock())
            reader.request.assert_called_once()
            self.assertEqual(len(report['rows']), 8)
            self.assertEqual(report['model_calls'], 1)
            self.assertEqual(report['held_out_calls'], 0)
            self.assertTrue(all('not_run' in r for r in report['rows'][1:]))

    def test_completed_batch_uses_all_eight_without_automatic_semantic_acceptance(self):
        report = self.report()
        reader = Mock()
        reader.request.return_value = self.response()
        with patch.object(R, 'own_process_memory', return_value={'available':False}):
            R.run_rows(reader, report, Mock())
        self.assertEqual(reader.request.call_count, 8)
        self.assertEqual(report['held_out_calls'], 6)
        self.assertTrue(all(not r['result']['semantic_verified'] for r in report['rows']))
        for call in reader.request.call_args_list:
            self.assertEqual(json.loads(call.args[1]['messages'][1]['content']), self.payload)

    def test_offline_profile_changes_only_timeout_and_selection(self):
        report = self.report()
        report.update(model=R.MODEL, plan_sha256='plan', input_sha256='input',
            gold_sha256='gold', execution=copy.deepcopy(self.execution))
        previous = copy.deepcopy(report)
        previous['model_calls'] = 1
        previous['rows'][0]['error'] = {'type':'TimeoutError'}
        before = copy.deepcopy(report)
        diagnostic = R.offline_diagnostic(report, previous)
        self.assertEqual(report, before)
        self.assertEqual(diagnostic['rows'], report['rows'][:1])
        self.assertEqual(len(diagnostic['unselected_ids']), 7)
        self.assertEqual(diagnostic['execution']['request_timeout_seconds'], 360)
        self.assertEqual(diagnostic['execution']['semantic_calls_max'], 1)
        self.assertEqual(diagnostic['interactive_limit_ms'], 120000)
        self.assertEqual(report['execution']['request_timeout_seconds'], 120)
        previous['rows'][0]['request_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'inventory drift'):
            R.offline_diagnostic(report, previous)

    def test_offline_profile_rejects_template_change_and_keeps_latency_separate(self):
        identity = {k:k for k in ('template_sha256','native_prefill_sha256','model_receipt',
            'runtime_receipt','launch_arguments','reasoning_arguments')}
        R.verify_offline_runtime(identity, copy.deepcopy(identity))
        changed = dict(identity, template_sha256='changed')
        with self.assertRaisesRegex(ValueError, 'runtime drift'):
            R.verify_offline_runtime(changed, identity)
        report = self.report()
        report['rows'] = report['rows'][:1]
        report['interactive_limit_ms'] = 120000
        reader = Mock()
        reader.request.return_value = self.response()
        with patch.object(R, 'own_process_memory', return_value={'available':False}), \
                patch.object(R.time, 'perf_counter', side_effect=[0,180]):
            R.run_rows(reader, report, Mock())
        self.assertTrue(report['rows'][0]['result']['completed'])
        self.assertFalse(report['rows'][0]['interactive_completed_within_limit'])
        self.assertFalse(report['rows'][0]['result']['semantic_verified'])


if __name__ == '__main__':
    unittest.main()
