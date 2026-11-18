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


if __name__ == '__main__':
    unittest.main()
