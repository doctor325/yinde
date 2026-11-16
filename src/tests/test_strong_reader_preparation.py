import copy
import json
import unittest
from tests.context_reader import prepare_strong_reader as P


class StrongReaderPreparationTests(unittest.TestCase):
    def setUp(self):
        self.scope = json.loads(P.SCOPE.read_text(encoding='utf8'))
        self.question = '甲是否因病離任？'
        self.parsed = dict(actor='甲', action='離任', object='', requested='原因',
                           mode='verify', proposed_answer='因病')
        self.evidence = [dict(id='e1', text='甲辭官歸。', role='event_context', complete=True,
                             parts=[dict(block_index=0, start=0, end=5, quote='甲辭官歸。')],
                             expected_answer='PRIVATE_GOLD', path='PRIVATE_PATH')]

    def test_outbound_contains_only_question_parse_and_original_units(self):
        body = P.make_request(self.scope, self.question, self.parsed, self.evidence)
        text = json.dumps(body, ensure_ascii=False)
        self.assertNotIn('PRIVATE_GOLD', text)
        self.assertNotIn('PRIVATE_PATH', text)
        self.assertNotIn('parts', text)
        self.assertEqual(set(json.loads(body['messages'][0]['content'])),
                         {'original_question', 'parsed_question', 'evidence', 'opening_context', 'incomplete'})
        self.assertEqual(set(body), {'model', 'max_tokens', 'thinking', 'output_config', 'system', 'messages'})

    def test_rejects_incomplete_evidence_and_question_claim_injection(self):
        self.evidence[0]['complete'] = False
        with self.assertRaises(ValueError):
            P.make_request(self.scope, self.question, self.parsed, self.evidence)
        self.evidence[0]['complete'] = True
        self.parsed['proposed_answer'] = '私增原因'
        with self.assertRaises(ValueError):
            P.make_request(self.scope, self.question, self.parsed, self.evidence)

    def test_baseline_drift_is_rejected_instead_of_silently_refreshing(self):
        payload = json.loads(P.make_request(self.scope, self.question, self.parsed, self.evidence)['messages'][0]['content'])
        case = dict(id='test', query=self.question, expected='not_supported', expected_answer='')
        baseline = dict(diagnostic_prompt_sha256=P.digest(P.B.SYSTEM),
                        rows=[dict(case, payload=copy.deepcopy(payload), payload_sha256=P.digest(payload))])
        P.check_baseline(baseline, case, payload)
        payload['evidence']['e1'] = '甲因病辭官歸。'
        with self.assertRaises(ValueError):
            P.check_baseline(baseline, case, payload)

    def test_all_eight_slots_are_budgeted_even_if_a_request_fails(self):
        self.assertEqual(P.reservation_usd(self.scope), 0.161696)
        self.assertLess(P.reservation_usd(self.scope), self.scope['limits']['cost_ceiling_usd'])
        self.assertEqual(self.scope['limits']['message_requests'], len(self.scope['cases']))
        self.assertEqual(self.scope['limits']['retries'], 0)

    def test_exact_gold_may_cross_ids_but_not_gaps_or_other_source_blocks(self):
        gold = dict(block_index=2, start=10, end=14, quote='甲去乙留')
        evidence = [dict(role='event_context', parts=[
            dict(block_index=2, start=10, end=12, quote='甲去'),
            dict(block_index=2, start=12, end=15, quote='乙留。')])]
        self.assertTrue(P.gold_covered(evidence, gold))
        broken = copy.deepcopy(evidence)
        broken[0]['parts'][1].update(start=13, quote='留。')
        self.assertFalse(P.gold_covered(broken, gold))
        broken = copy.deepcopy(evidence)
        broken[0]['parts'][1]['block_index'] = 3
        self.assertFalse(P.gold_covered(broken, gold))
        broken = copy.deepcopy(evidence)
        broken[0]['parts'][1]['quote'] = '丙留。'
        self.assertFalse(P.gold_covered(broken, gold))


if __name__ == '__main__':
    unittest.main()
