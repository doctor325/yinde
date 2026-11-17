import copy
import unittest
from search.local_evidence_reader import LocalReader
from tests.context_reader import run_local_pilot as P


class LocalVulkanPilotTests(unittest.TestCase):
    def test_cpu_defaults_preserved_and_gpu_requires_explicit_backend(self):
        reader = LocalReader()
        self.assertEqual(reader.model_key, 'qwen3-4b')
        self.assertEqual(reader.gpu_arguments(), ['-ngl', '0'])
        self.assertEqual(LocalReader(backend='vulkan').gpu_arguments(), ['-ngl', '99', '--device', 'Vulkan0'])
        with self.assertRaises(ValueError):
            LocalReader(backend='automatic')

    def test_exposed_pair_only_preserves_exact_payload(self):
        rows = [dict(id=k, payload={'original_question': k}) for k in P.PAIR]
        rows += [dict(id='holdout' + str(i), payload={'gold': 'MUST_NOT_SEND'}) for i in range(6)]
        inputs = dict(system=P.B.SYSTEM, rows=rows)
        before = copy.deepcopy(inputs)
        self.assertEqual(P.select_pair(inputs), rows[:2])
        self.assertEqual(inputs, before)
        inputs['system'] += 'drift'
        with self.assertRaises(ValueError):
            P.select_pair(inputs)

    def test_missing_or_duplicate_inventory_is_rejected(self):
        for rows in ([], [dict(id='duplicate')] * 8):
            with self.assertRaises(ValueError):
                P.select_pair(dict(system=P.B.SYSTEM, rows=rows))

    def test_cpu_comparison_changes_only_compute_arguments(self):
        from tests.context_reader.probe_vulkan_compatibility import ProbeReader, CpuComparisonReader
        gpu, cpu = ProbeReader('flash-off-no-f16'), CpuComparisonReader()
        self.assertEqual(cpu.backend, gpu.backend)  # Same verified distribution.
        self.assertEqual(cpu.assets, gpu.assets)
        self.assertEqual(cpu.profile, gpu.profile)  # Same child env and flash flag.
        self.assertEqual(cpu.timeout, gpu.timeout)
        self.assertEqual(cpu.gpu_arguments()[:4], ['-ngl', '0', '--device', 'none'])
        self.assertEqual(cpu.gpu_arguments()[4:], gpu.gpu_arguments()[4:])

    def test_full_comparison_rejects_changed_questions_or_generation(self):
        original = dict(model='qwen3-8b', portable_inputs_sha256='inputs', prompt_sha256='system',
                        generation={'max_tokens': 650},
                        runtime=dict(context=8192, cpu_threads=4, timeout_seconds=120),
                        rows=[dict(id=k, payload={'query': k}, evidence=['source'], request_sha256=k)
                              for k in P.PAIR])
        P.validate_comparison_inputs(original, copy.deepcopy(original))
        changed = copy.deepcopy(original)
        changed['rows'][1]['payload']['query'] = 'different event'
        with self.assertRaises(ValueError):
            P.validate_comparison_inputs(changed, original)
        changed = copy.deepcopy(original)
        changed['generation']['max_tokens'] = 64
        with self.assertRaises(ValueError):
            P.validate_comparison_inputs(changed, original)


if __name__ == '__main__':
    unittest.main()
