import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.setup_evidence_reader import ASSETS, QWEN25_REVISION, model_spec
from search.local_evidence_reader import LocalReader
from tests.context_reader import run_candidate_pilot as C


class AlternativeReaderTests(unittest.TestCase):
    def test_all_shards_are_pinned_and_default_receipt_unchanged(self):
        spec, assets = model_spec(C.MODEL)
        self.assertEqual(len(assets), 4)
        self.assertEqual(spec['asset'], assets[1])
        self.assertEqual(sum(a['bytes'] for a in assets[1:]), 8988110496)
        for i, a in enumerate(assets[1:], 1):
            self.assertTrue(a['name'].endswith(f'{i:05d}-of-00003.gguf'))
            self.assertIn('/resolve/' + QWEN25_REVISION + '/', a['url'])
            self.assertRegex(a['sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(LocalReader().assets, ASSETS)
        self.assertEqual(len(LocalReader(model='qwen3-8b').assets), 2)

    def test_missing_or_corrupt_last_shard_blocks_startup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            home = Path(directory)
            reader = C.CandidateReader()
            reader.home = home
            fake = []
            for i, asset in enumerate(reader.assets):
                content = f'fixture-{i}'.encode()
                fake.append(dict(asset, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
                (home / asset['name']).write_bytes(content)
            reader.assets = tuple(fake)
            (home / reader.model_spec['receipt']).write_text(json.dumps(
                dict(assets=fake, model=reader.model_spec['id'])), encoding='utf8')
            last = home / fake[-1]['name']
            with patch('search.local_evidence_reader.subprocess.Popen') as spawn:
                last.unlink()
                with self.assertRaises(FileNotFoundError):
                    reader.__enter__()
                last.write_bytes(b'fixture-X')  # Same size, wrong hash.
                with self.assertRaisesRegex(ValueError, 'integrity'):
                    reader.__enter__()
                spawn.assert_not_called()

    def test_candidate_keeps_cpu_profile_and_changes_only_model_spec(self):
        reader, previous = C.CandidateReader(), C.CpuComparisonReader()
        self.assertEqual(reader.model_key, C.MODEL)
        self.assertNotEqual(reader.assets, previous.assets)
        self.assertEqual(reader.gpu_arguments(), previous.gpu_arguments())
        self.assertEqual(reader.profile, previous.profile)
        self.assertEqual(reader.timeout, previous.timeout)
        self.assertEqual(reader.compute, 'cpu')

    def test_runtime_gate_records_native_template_and_rejects_drift(self):
        reader = Mock(runtime_receipt={'files': {'server': 'sha'}})
        reader.gpu_arguments.return_value = ['-ngl', '0']
        reader.request.return_value = {'chat_template': 'different native template'}
        reference = dict(runtime_receipt=reader.runtime_receipt, launch_arguments=['-ngl', '0'])
        self.assertEqual(C.verify_runtime(reader, reference), C.digest('different native template'))
        reader.request.assert_called_once_with('/props')
        reader.gpu_arguments.return_value = ['-ngl', '99']
        with self.assertRaisesRegex(ValueError, 'arguments'):
            C.verify_runtime(reader, reference)
        reader.request.assert_called_once()

    def test_preparation_preserves_requests_and_has_no_generation(self):
        original = dict(model='qwen3-8b', portable_inputs_sha256='inputs', prompt_sha256='system',
                        generation={'max_tokens': 650}, runtime=dict(context=8192, cpu_threads=4, timeout_seconds=120),
                        rows=[dict(id=k, payload={'query': k}, evidence=['source'], request_sha256=k)
                              for k in C.P.PAIR])
        reference = dict(copy.deepcopy(original), backend='cpu', state='attempted',
                         distribution='same binary', compatibility_profile='flash-off-no-f16')
        raw = json.dumps(reference).encode()
        with patch.object(C, 'REFERENCE') as path, patch.object(C, 'REFERENCE_SHA256', hashlib.sha256(raw).hexdigest()), \
             patch.object(C.P, 'prepare', side_effect=lambda: copy.deepcopy(original)), \
             patch.object(C, 'CandidateReader') as reader:
            path.read_bytes.return_value = raw
            report, _ = C.prepare()
            self.assertEqual(report['rows'], original['rows'])
            self.assertEqual(report['model_calls'], 0)
            self.assertIsNone(report['semantic_acceptance'])
            reader.assert_not_called()
            original['rows'][1]['payload']['query'] = 'wrong event'
            with self.assertRaisesRegex(ValueError, 'drift'):
                C.prepare()


if __name__ == '__main__':
    unittest.main()
