import copy
import unittest
from tests.context_reader import run_native_reasoning as R


class BudgetTests(unittest.TestCase):
    def test_frozen_comparison_and_single_change(self):
        report, _ = R.prepare()
        old = R.frozen(R.TIMEOUT_REFERENCE, R.TIMEOUT_REFERENCE_SHA, normalize_newlines=False)
        report = R.offline_diagnostic(report, old)
        previous = R.frozen(R.STREAM_REFERENCE, R.STREAM_REFERENCE_SHA, normalize_newlines=False)
        before = copy.deepcopy(report)
        R.budget_diagnostic(report, previous)
        self.assertEqual(report['rows'], before['rows'])
        self.assertEqual(report['execution'], before['execution'])
        self.assertEqual(len(report['rows']), 1)
        self.assertEqual(R.NativeReader(512).reasoning_arguments(),
                         R.NativeReader().reasoning_arguments()+['--reasoning-budget', '512'])
        runtime = dict(previous, reasoning_arguments=R.NativeReader(512).reasoning_arguments())
        R.verify_budget_runtime(runtime, previous)
        runtime['native_prefill_sha256'] = 'changed'
        with self.assertRaises(ValueError):
            R.verify_budget_runtime(runtime, previous)
        report['rows'][0]['request_sha256'] = 'changed'
        with self.assertRaises(ValueError):
            R.budget_diagnostic(report, previous)

    def test_requires_isolated_stream_entry(self):
        with self.assertRaises(SystemExit):
            R.main(['--budget-512'])


class ContrastTests(unittest.TestCase):
    def test_only_frozen_contrast_selected_with_same_configuration(self):
        source, _ = R.prepare()
        inventory = copy.deepcopy(source['rows'])
        previous = R.frozen(R.TIMEOUT_REFERENCE, R.TIMEOUT_REFERENCE_SHA, normalize_newlines=False)
        report = R.offline_diagnostic(source, previous)
        stream = R.frozen(R.STREAM_REFERENCE, R.STREAM_REFERENCE_SHA, normalize_newlines=False)
        R.budget_diagnostic(report, stream)
        positive = copy.deepcopy(report)
        R.select_budget_contrast(report, inventory, positive)
        self.assertEqual(report['rows'], [inventory[1]])
        self.assertEqual(report['execution'], positive['execution'])
        self.assertEqual(report['reasoning_budget'], 512)
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(report['held_out_calls'], 0)
        self.assertEqual(len(report['unselected_ids']), 7)
        self.assertEqual(positive['rows'][0]['id'], 'chenping_positive')
        changed = copy.deepcopy(positive)
        changed['execution']['request_timeout_seconds'] = 600
        with self.assertRaises(ValueError):
            R.select_budget_contrast(changed, inventory, positive)
        changed = copy.deepcopy(inventory)
        changed[1]['split'] = 'held_out_reader'
        with self.assertRaises(ValueError):
            R.select_budget_contrast(copy.deepcopy(positive), changed, positive)

    def test_contrast_requires_fixed_budget(self):
        with self.assertRaises(SystemExit):
            R.main(['--contrast'])
