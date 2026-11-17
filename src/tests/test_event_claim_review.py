import copy
import json
import unittest
from unittest.mock import Mock
from search import event_claim_review as V, source_first_reader as S


class ClaimReviewTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [dict(id='e1', text='甲因病去官，乙留任。', role='event_context', complete=True,
                             parts=[dict(block_index=1, start=0, end=10, quote='甲因病去官，乙留任。')])]
        self.event = dict(actor='甲', action='去官', object='官职', reason='病', state='occurred',
                          attribution='narrator', references=['e1'])

    def response(self, verdict='supported', references=None):
        return dict(**V.binding(self.event, 'reason', self.evidence), completed=True,
            output=dict(verdict=verdict, references=['e1'] if references is None else references,
                        explanation='核对同一事件的原因依据'))

    def test_request_excludes_original_query_gold_and_extractor_confidence(self):
        event = dict(self.event, original_question='PRIVATE_QUERY', gold='PRIVATE_GOLD', confidence=1,
                     verdict='supported')
        request = V.request(event, 'reason', self.evidence)
        body = json.loads(request['messages'][1]['content'])
        self.assertEqual(set(body['event']), set(V.FIELDS))
        self.assertNotIn('PRIVATE_', json.dumps(request))
        self.assertEqual(request['max_tokens'], 180)
        with self.assertRaises(ValueError):
            V.request(event, 'verdict', self.evidence)

    def test_valid_reference_and_model_agreement_are_not_certification(self):
        result = V.assess(self.event, 'reason', self.evidence, self.response())
        self.assertEqual(result['status'], 'review_supported')
        self.assertFalse(result['trusted'])
        self.assertEqual(result['citations'][0]['parts'], self.evidence[0]['parts'])
        for verdict in ('contradicted', 'insufficient'):
            self.assertEqual(V.assess(self.event, 'reason', self.evidence, self.response(verdict))['status'],
                             'review_' + verdict)

    def test_review_cannot_be_replayed_for_another_actor_cause_field_or_source(self):
        review = self.response()
        for key, value in [('actor', '乙'), ('reason', '饥'), ('action', '任官')]:
            changed = dict(self.event, **{key: value})
            self.assertEqual(V.assess(changed, 'reason', self.evidence, review)['status'], 'unconfirmed')
        self.assertEqual(V.assess(self.event, 'actor', self.evidence, review)['status'], 'unconfirmed')
        evidence = copy.deepcopy(self.evidence)
        evidence[0]['parts'][0]['block_index'] = 2
        self.assertEqual(V.assess(self.event, 'reason', evidence, review)['status'], 'unconfirmed')

    def test_missing_unknown_or_identity_only_evidence_does_not_support_claim(self):
        self.assertEqual(V.assess(self.event, 'reason', self.evidence)['status'], 'unconfirmed')
        self.assertEqual(V.assess(dict(self.event, state='unknown'), 'state', self.evidence)['status'], 'unconfirmed')
        for kind in ('role', 'complete'):
            evidence = copy.deepcopy(self.evidence)
            evidence[0][kind] = 'opening_context' if kind == 'role' else False
            review = self.response()
            review.update(V.binding(self.event, 'reason', evidence))
            self.assertEqual(V.assess(self.event, 'reason', evidence, review)['status'], 'unconfirmed')
        self.assertEqual(V.assess(self.event, 'reason', self.evidence, self.response(references=[]))['status'], 'unconfirmed')

    def test_incomplete_or_invalid_review_does_not_retry(self):
        for finish, raw in [('length', '{}'), ('stop', '{'), ('stop', json.dumps(dict(
                verdict='supported', references=['e99'], explanation='invalid')) )]:
            reader = Mock()
            reader.request.return_value = dict(choices=[dict(finish_reason=finish, message={'content': raw})])
            result = V.review(reader, self.event, 'reason', self.evidence)
            self.assertFalse(result['completed'])
            self.assertEqual(result['raw'], raw)
            reader.request.assert_called_once()

    def test_compare_no_longer_calls_nonempty_reason_available(self):
        ledger = dict(version=S.VERSION, completed=True, source_sha256=S.digest(self.evidence),
                      output=dict(coverage='complete', events=[self.event]))
        question = {k:self.event[k] for k in S.ROLES}
        result = S.compare(question, ledger, self.evidence)
        self.assertTrue(result['events'][0]['reason_proposed'])
        self.assertFalse(result['events'][0]['reason_available'])
        reviews = {'event-1': {'reason': self.response('insufficient')}}
        result = S.compare(question, ledger, self.evidence, reviews=reviews)
        audit = result['events'][0]['content_audit']
        self.assertEqual(audit['fields']['reason']['status'], 'review_insufficient')
        self.assertFalse(audit['content_trusted'])
        self.assertEqual(self.event['reason'], '病')  # Original proposal preserved.


if __name__ == '__main__':
    unittest.main()
