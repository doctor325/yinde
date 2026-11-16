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


if __name__ == '__main__':
    unittest.main()
