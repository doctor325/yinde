import os
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
                for profile in ('baseline', 'flash-off', 'no-f16'):
                    with self.assertRaises(RuntimeError):
                        P.ProbeReader(profile).__enter__()
                    self.assertIs(P.V.environment, factory)
        self.assertEqual(seen, [source, source, {**source, 'GGML_VK_DISABLE_F16': '1'}])
        self.assertEqual(source, {'TMP': 'repo-tmp'})
        self.assertEqual(dict(os.environ), parent_before)


if __name__ == '__main__':
    unittest.main()
