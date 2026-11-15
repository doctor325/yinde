import copy
import json
import unittest
from unittest.mock import Mock, patch
from search import brief_evidence_diagnostic as B
from tests.context_reader import run_brief_diagnostic as R


class BriefDiagnosticTests(unittest.TestCase):
    def evidence(self):
        return [dict(id='e1', text='甲辭乙而歸。', complete=True,
                     parts=[dict(block_index=1, start=0, end=6, quote='甲辭乙而歸。')])]

    def test_user_payload_and_parameters_are_preserved_without_schema(self):
        payload = dict(original_question='甲因病辭乙嗎？', parsed_question={'proposed_answer':'因病'},
                       evidence={'e1':'甲辭乙而歸。'}, opening_context=[], incomplete=[])
        original = copy.deepcopy(payload)
        request = B.request_payload(payload)
        self.assertEqual(payload, original)
        self.assertEqual(json.loads(request['messages'][1]['content']), payload)
        self.assertEqual(request['max_tokens'], 650)
        self.assertEqual(request['seed'], 20261111)
        self.assertEqual(request['temperature'], 0)
        self.assertNotIn('response_format', request)

    def test_inventory_preserves_offsets_and_never_grades_false_claim(self):
        evidence = self.evidence()
        # Correct ID does not make the invented illness a supported cause.
        result = B.citation_inventory('甲因病離開乙[e1]，他人則留任[E999]。', evidence)
        self.assertEqual(result['unknown'], ['e999'])
        self.assertEqual(result['citations'][0]['parts'], evidence[0]['parts'])
        self.assertFalse(result['semantic_verified'])
        self.assertNotIn('eligible', result)
        self.assertEqual(B.citation_inventory('無引用', evidence)['references'], [])
        evidence[0]['complete'] = False
        self.assertEqual(B.citation_inventory('[e1]', evidence)['incomplete'], ['e1'])

    def test_incomplete_output_is_kept_without_silent_retry(self):
        reader = Mock()
        reader.request.return_value = dict(choices=[dict(finish_reason='length', message={'content':'甲離開乙[e1]'})])
        result = B.read(reader, {'evidence':{'e1':'甲辭乙而歸。'}}, self.evidence())
        self.assertFalse(result['completed'])
        self.assertEqual(result['text'], '甲離開乙[e1]')
        reader.request.assert_called_once()
        reader.request.side_effect = TimeoutError('deadline')
        with self.assertRaises(TimeoutError):
            B.read(reader, {}, self.evidence())
        self.assertEqual(reader.request.call_count, 2)

    def test_preparation_does_not_reselect_or_leak_gold_into_request(self):
        payload = {'original_question':'甲为何离开乙？', 'evidence':{'e1':'甲辭乙而歸。'}}
        report = dict(schema={'type':'object'}, schema_sha256='schema', prompt_sha256='oldprompt',
                      rows=[dict(payload=payload, payload_sha256=R.W.digest(payload), expected_answer='PRIVATE_GOLD')])
        with patch.object(R.C, 'prepare', return_value=copy.deepcopy(report)):
            prepared = R.prepare('baseline')
        self.assertEqual(prepared['rows'][0]['payload'], payload)
        self.assertEqual(prepared['candidate_key'], 'qwen3-4b')
        self.assertNotIn('schema', prepared)
        wire = json.dumps(B.request_payload(prepared['rows'][0]['payload']), ensure_ascii=False)
        self.assertNotIn('PRIVATE_GOLD', wire)
        self.assertEqual(prepared['rows'][0]['request_sha256'], R.W.digest(B.request_payload(payload)))
        report['rows'][0]['payload_sha256'] = 'drift'
        with patch.object(R.C, 'prepare', return_value=report), self.assertRaises(ValueError):
            R.prepare('baseline')


if __name__ == '__main__':
    unittest.main()
