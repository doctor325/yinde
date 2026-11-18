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
