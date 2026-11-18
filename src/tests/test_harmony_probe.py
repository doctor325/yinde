import json
import unittest
from scripts.setup_evidence_reader import MODELS, model_spec
from tests.context_reader.probe_harmony import HarmonyReader, validate_template
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
