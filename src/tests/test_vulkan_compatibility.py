import os
import json
import unittest
from unittest.mock import patch

from search.local_evidence_reader import LocalReader
from tests.context_reader import probe_vulkan_compatibility as P


class VulkanCompatibilityTests(unittest.TestCase):
    def test_each_profile_changes_one_option_without_cpu_fallback(self):
        base = P.ProbeReader('baseline').gpu_arguments()
        self.assertEqual(base, ['-ngl', '99', '--device', 'Vulkan0', '-lv', '4'])
        self.assertEqual(P.ProbeReader('flash-off').gpu_arguments(), base + ['--flash-attn', 'off'])
        self.assertEqual(P.ProbeReader('no-f16').gpu_arguments(), base)
        self.assertEqual(P.PROFILES['no-f16'][1], {'GGML_VK_DISABLE_F16': '1'})
        self.assertEqual(P.ProbeReader('flash-off-no-f16').gpu_arguments(),
                         P.ProbeReader('flash-off').gpu_arguments())
        self.assertEqual(P.PROFILES['flash-off-no-f16'][1], P.PROFILES['no-f16'][1])
        self.assertEqual(P.STEPS['combined'], ('flash-off-no-f16',))
        self.assertEqual(LocalReader().gpu_arguments(), ['-ngl', '0'])
        with self.assertRaises(ValueError):
            P.ProbeReader('auto-fix')

    def test_override_only_reaches_child_and_patch_restores_after_failure(self):
        parent_before = dict(os.environ)
        source = {'TMP': 'repo-tmp'}
        seen = []

        def enter(reader):
            seen.append(P.V.environment(reader.home))
            raise RuntimeError('simulated startup failure')

        with patch.object(P.V, 'environment', side_effect=lambda home: dict(source)) as factory:
            with patch.object(LocalReader, '__enter__', enter):
                for profile in ('baseline', 'flash-off', 'no-f16', 'flash-off-no-f16'):
                    with self.assertRaises(RuntimeError):
                        P.ProbeReader(profile).__enter__()
                    self.assertIs(P.V.environment, factory)
        disabled = {**source, 'GGML_VK_DISABLE_F16': '1'}
        self.assertEqual(seen, [source, source, disabled, disabled])
        self.assertEqual(source, {'TMP': 'repo-tmp'})
        self.assertEqual(dict(os.environ), parent_before)

    def test_combined_comparison_rejects_changed_saved_input(self):
        case, identity = {'request': 'frozen'}, {'model': 'same'}
        row = dict(profile='flash-off', identity=identity, result={'completed': False},
                   configuration_log=['flash_attn = disabled'])
        raw = json.dumps(dict(case=case, rows=[row])).encode('utf8')
        with patch.object(type(P.FLASH_REFERENCE), 'read_bytes', return_value=raw):
            with self.assertRaises(ValueError):
                P.combined_reference(case, identity)
            with patch.object(P, 'FLASH_REFERENCE_SHA256', P.hashlib.sha256(raw).hexdigest()):
                result = P.combined_reference(case, identity)
                self.assertTrue(result['reused_prior_result'])
                self.assertFalse(result['result']['completed'])
                with self.assertRaises(ValueError):
                    P.combined_reference({'request': 'changed'}, identity)


if __name__ == '__main__':
    unittest.main()
