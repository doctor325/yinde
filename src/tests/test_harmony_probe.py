import json
import unittest
from scripts.setup_evidence_reader import MODELS, model_spec
from tests.context_reader.probe_harmony import HarmonyReader, validate_template, verify_probe
from tests.context_reader.run_native_reasoning import ROOT


class HarmonyTests(unittest.TestCase):
    def test_pinned_asset_and_isolated_configuration(self):
        plan = json.loads((ROOT/'src/docs/gpt_oss_local_comparison_plan.json').read_text(encoding='utf8'))
        selected = plan['selected']
        spec, assets = model_spec('gpt-oss-20b')
        self.assertEqual(spec['asset']['sha256'], selected['sha256'])
        self.assertEqual(spec['asset']['bytes'], selected['bytes'])
        self.assertIn('/'+selected['revision']+'/', spec['asset']['url'])
        self.assertEqual(len(assets), 2)
        self.assertIn('qwen3-4b', MODELS)
        self.assertEqual(HarmonyReader().reasoning_arguments()[-2:], ['--reasoning-budget','512'])

    def test_native_framing_guard(self):
        validate_template('<|start|>system<|message|>Reasoning: medium<|end|><|start|>assistant')
        for text in (None, '<think>', '<|start|>user<|message|>medium',
                     '<|start|>system<|message|>high<|start|>assistant'):
            with self.assertRaises(ValueError):
                validate_template(text)

    def test_probe_distinguishes_completion_budget_and_arithmetic(self):
        trace = {'completed': True}
        reply = {'content': '2', 'reasoning_content': 'analysis'}
        checks = verify_probe(trace, reply, 'activated, budget=512 tokens')
        self.assertTrue(checks['channels_verified'])
        self.assertTrue(checks['computation_verified'])
        self.assertTrue(checks['budget_activated'])
        self.assertFalse(checks['budget_forced_end'])
        checks = verify_probe({'completed': False}, dict(reply, content='3'), '')
        self.assertFalse(checks['channels_verified'])
        self.assertFalse(checks['computation_verified'])
        self.assertFalse(checks['budget_activated'])
