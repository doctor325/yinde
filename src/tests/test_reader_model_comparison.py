import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.setup_evidence_reader import ASSETS, MODELS
from search.local_evidence_reader import LocalReader
from search import numbered_evidence as N, numbered_event_contract as E
from tests.context_reader import compare_models as C


class ModelComparisonTests(unittest.TestCase):
    def test_default_model_and_shared_runtime_are_unchanged(self):
        reader = LocalReader()
        self.assertEqual(reader.model_key, 'qwen3-4b')
        self.assertEqual(reader.assets, ASSETS)
        other = LocalReader(model='qwen3-8b')
        self.assertEqual(reader.assets[0], other.assets[0])
        self.assertNotEqual(reader.model_spec['receipt'], other.model_spec['receipt'])
        self.assertEqual(other.model_spec['asset']['bytes'], 5027783488)
        with self.assertRaises(KeyError):
            LocalReader(model='../untrusted.gguf')

    def test_candidate_never_falls_back_to_installed_base(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            (root/'receipt.json').write_text(json.dumps(dict(assets=ASSETS, model=MODELS['qwen3-4b']['id'])))
            reader = LocalReader(home=root, model='qwen3-8b')
            with patch('search.local_evidence_reader.subprocess.Popen') as spawn:
                with self.assertRaises(FileNotFoundError):
                    reader.__enter__()
                spawn.assert_not_called()
            (root/'receipt-qwen3-8b.json').write_text((root/'receipt.json').read_text())
            with self.assertRaisesRegex(ValueError, 'receipt'):
                reader.__enter__()

    def fixture(self):
        context = dict(source={'page_id':1}, boundary_cut=False, segments=[
            dict(role='event_context', block_index=1, start=0, end=7, text='甲懼禍，辭乙。')])
        evidence = N.numbered(context)
        question = dict(actor='甲', action='离开', object='乙', requested='原因', mode='open', proposed_answer='')
        cases = [dict(id=key, query='甲为何离开乙？', expected='supported', expected_answer='惧祸') for key in C.W.PAIR]
        rows = []
        for case in cases:
            payload = dict(original_question=case['query'], parsed_question=question, **N.evidence_payload(evidence))
            reading = dict(completed=True, version=E.VERSION, evidence=evidence, question={'output':question},
                           judgement=dict(input_chars=len(E.JUDGE)+len(N.encode(payload))), validation={})
            rows.append(dict(case, reading=reading, semantic_review={'passed':False}))
        receipt = {'sha256':'frozen'}
        old = dict(state='attempted', frozen=receipt, reader_version=E.VERSION,
                   prompt_sha256=C.W.digest(E.PARSE+E.JUDGE), context=context, context_sha256=C.W.digest(context),
                   model={'receipt':dict(assets=list(ASSETS), model=MODELS['qwen3-4b']['id'])}, rows=rows)
        return old, cases, receipt

    def test_baseline_inputs_preserved_and_drift_rejected(self):
        old, cases, receipt = self.fixture()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            path = root/'report.json'
            with patch.object(C.W, 'RUN_HOME', root), patch.object(C.W, 'pair', return_value=(cases,receipt)):
                path.write_text(json.dumps(old), encoding='utf8')
                before = path.read_bytes()
                report = C.prepare(path)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(report['context'], old['context'])
                self.assertEqual(report['rows'][0]['fixed_question'], old['rows'][0]['reading']['question']['output'])
                for mutation in ('prompt', 'source', 'question', 'model'):
                    changed = copy.deepcopy(old)
                    if mutation == 'prompt': changed['prompt_sha256'] = 'changed'
                    if mutation == 'source': changed['context']['segments'][0]['text'] = '改写'
                    if mutation == 'question': changed['rows'][0]['query'] = '其他问题'
                    if mutation == 'model': changed['model']['receipt']['model'] = 'different'
                    path.write_text(json.dumps(changed), encoding='utf8')
                    with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                        C.prepare(path)

    def test_request_drift_rejected_before_model_dispatch(self):
        row = dict(payload={'original_question':'甲为何离开乙？'})
        schema = {'type':'object'}
        model = Mock()
        proxy = C.RecordingReader(model, row, schema)
        payload = dict(model='local', **C.GENERATION,
                       messages=[dict(role='system',content=E.JUDGE),dict(role='user',content=N.encode(row['payload']))],
                       response_format=dict(type='json_schema',json_schema=dict(name='evidence_v2',strict=True,schema=schema)))
        proxy.request('/v1/chat/completions', payload)
        self.assertEqual(row['request_sha256'], C.W.digest(payload))
        model.request.assert_called_once()
        changed = dict(payload, max_tokens=999)
        with self.assertRaisesRegex(ValueError, 'drift'):
            proxy.request('/v1/chat/completions', changed)
        model.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
