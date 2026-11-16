import copy
import unittest
from unittest.mock import Mock

from tests.context_reader import compare_backends as C


class BackendComparisonTests(unittest.TestCase):
    def response(self, text='12', finish='stop'):
        return dict(choices=[dict(message={'content': text}, finish_reason=finish)], usage={})

    def test_only_compute_arguments_differ(self):
        cpu, gpu = C.DiagnosticReader('cpu'), C.DiagnosticReader('vulkan')
        self.assertEqual(cpu.backend, gpu.backend)
        self.assertEqual(cpu.assets, gpu.assets)
        self.assertEqual(cpu.model_key, gpu.model_key)
        self.assertEqual(cpu.gpu_arguments(), ['-ngl', '0', '--device', 'none'])
        self.assertEqual(gpu.gpu_arguments(), ['-ngl', '99', '--device', 'Vulkan0'])
        with self.assertRaises(ValueError):
            C.DiagnosticReader('auto')

    def test_exact_answer_requires_complete_output(self):
        self.assertTrue(C.judge(self.response(' 12\n'))['exact_answer'])
        for text, finish in [('12', 'length'), ('GGGG', 'stop'), ('1212', 'stop'), ('', 'stop')]:
            self.assertFalse(C.judge(self.response(text, finish))['exact_answer'])
        self.assertFalse(C.judge(self.response())['semantic_acceptance'])

    def test_request_is_frozen_and_missing_template_blocks_inference(self):
        reader = Mock(model_spec={'asset': {'sha256': 'model'}},
                      runtime_receipt={'files': {'llama-server.exe': 'exe'}})
        reader.request.side_effect = [{'chat_template': 'template'}, self.response()]
        before = copy.deepcopy(C.REQUEST)
        result = C.measure(reader)
        self.assertEqual(C.REQUEST, before)
        self.assertEqual(reader.request.call_args.args, ('/v1/chat/completions', before))
        self.assertEqual(result['identity']['request_sha256'], C.digest(before))
        reader.reset_mock()
        reader.request.side_effect = [{'chat_template': None}]
        with self.assertRaises(ValueError):
            C.measure(reader)
        reader.request.assert_called_once_with('/props')

    def test_different_templates_or_incomplete_comparisons_are_not_passes(self):
        cpu = dict(identity={'template': 'a'}, result={'exact_answer': True})
        gpu = dict(identity={'template': 'a'}, result={'exact_answer': False})
        self.assertEqual(C.summarize([cpu, gpu])['status'], 'cpu_pass_gpu_fail')
        gpu['identity']['template'] = 'b'
        self.assertFalse(C.summarize([cpu, gpu])['comparable'])
        self.assertEqual(C.summarize([cpu])['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
