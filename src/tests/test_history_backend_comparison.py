import copy
import unittest
from unittest.mock import Mock

from tests.context_reader import compare_history_backends as H


class HistoryBackendComparisonTests(unittest.TestCase):
    def fixture(self):
        payload = dict(original_question='某人为何离开？', evidence={'e1': '原文'})
        request = H.B.request_payload(payload)
        row = dict(id=H.CASE_ID, payload=payload, evidence=[], request_sha256=H.P.digest(request))
        prepared = dict(model='qwen3-8b', portable_inputs_sha256='inputs',
                        prompt_sha256='prompt', generation=H.B.GENERATION, rows=[row])
        return prepared, copy.deepcopy(prepared)

    def test_only_output_cap_changes_and_inputs_are_not_mutated(self):
        baseline, prepared = self.fixture()
        before = copy.deepcopy(baseline)
        case = H.build_case(baseline, prepared)
        restored = copy.deepcopy(case['request'])
        restored['max_tokens'] = case['original_max_tokens']
        self.assertEqual(H.P.digest(restored), baseline['rows'][0]['request_sha256'])
        self.assertEqual(case['request']['max_tokens'], 64)
        self.assertEqual(baseline, before)
        self.assertEqual(prepared, before)
        prepared['rows'][0]['payload']['evidence']['e1'] += '改字'
        with self.assertRaises(ValueError):
            H.build_case(baseline, prepared)
        prepared = copy.deepcopy(before)
        prepared['rows'][0]['request_sha256'] = 'drift'
        with self.assertRaises(ValueError):
            H.build_case(baseline, prepared)

    def test_signature_is_not_a_semantic_grade(self):
        def response(text, stop='length'):
            return dict(choices=[dict(message={'content': text}, finish_reason=stop)])
        repeated = H.observe(response('{"' + 'G' * 64))
        self.assertTrue(repeated['g_signature'])
        self.assertEqual(repeated['g_run_start'], 2)
        self.assertEqual(repeated['g_run_length'], 64)
        self.assertFalse(repeated['completed'])
        for text in ('陈平因恐惧而离开', 'X' * 64, 'G' * 15, ''):
            result = H.observe(response(text, 'stop'))
            self.assertFalse(result['g_signature'])
            self.assertFalse(result['semantic_acceptance'])

    def test_template_or_request_drift_blocks_generation(self):
        baseline, prepared = self.fixture()
        case = H.build_case(baseline, prepared)
        files = {'llama-server.exe': 'exe'}
        case['expected_runtime'] = dict(model_sha256='model', executable_sha256='exe',
                                        runtime_files_sha256=H.C.digest(files))
        reader = Mock(model_spec={'asset': {'sha256': 'model'}}, runtime_receipt={'files': files})
        response = dict(choices=[dict(message={'content': '片段'}, finish_reason='length')])
        reader.request.side_effect = [{'chat_template': 'template'}, response]
        row = H.measure(reader, case)
        self.assertEqual(reader.request.call_args.args, ('/v1/chat/completions', case['request']))
        reader.reset_mock()
        reader.request.side_effect = [{'chat_template': 'different'}]
        with self.assertRaises(ValueError):
            H.measure(reader, case, row['identity'])
        reader.request.assert_called_once_with('/props')
        reader.reset_mock()
        reader.request.side_effect = [{'chat_template': 'template'}]
        case['request']['max_tokens'] += 1
        with self.assertRaises(ValueError):
            H.measure(reader, case)
        reader.request.assert_called_once_with('/props')

    def test_incomplete_or_drifted_comparison_is_not_a_result(self):
        cpu = dict(compute='cpu', identity={'same': True}, result={'g_signature': False})
        gpu = dict(compute='vulkan', identity={'same': True}, result={'g_signature': True})
        self.assertEqual(H.summarize([cpu, gpu])['status'], 'g_signature_gpu_only')
        gpu['result']['g_signature'] = False
        result = H.summarize([cpu, gpu])
        self.assertEqual(result['status'], 'g_signature_not_reproduced_in_prefix')
        self.assertFalse(result['semantic_acceptance'])
        gpu['identity']['same'] = False
        self.assertFalse(H.summarize([cpu, gpu])['comparable'])
        self.assertEqual(H.summarize([cpu])['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
