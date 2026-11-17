import copy
import json
import unittest
from unittest.mock import Mock, patch
from search import source_first_reader as S
from tests.context_reader import run_source_first as R


class SourceFirstTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [dict(id='e1', text='甲奉詔拜乙為守。', role='event_context', complete=True,
                             parts=[dict(block_index=1, start=0, end=8, quote='甲奉詔拜乙為守。')])]
        self.event = dict(actor='甲', action='任命', object='乙', reason='奉诏',
                          state='occurred', attribution='narrator', references=['e1'])
        self.question = dict(actor='甲', action='任命', object='乙', requested='原因',
                             mode='verify', proposed_answer='因私怨')
        self.ledger = dict(version=S.VERSION, completed=True, source_sha256=S.digest(self.evidence),
                           output=dict(coverage='complete', events=[copy.deepcopy(self.event)]))

    def compare(self, question=None, ledger=None, evidence=None):
        return S.compare(question or self.question, ledger or self.ledger, evidence or self.evidence)

    def test_extractor_cannot_receive_question_gold_or_history(self):
        evidence = copy.deepcopy(self.evidence)
        evidence[0].update(question='PRIVATE_QUERY', gold='PRIVATE_GOLD', history='PRIVATE_HISTORY')
        request = S.extraction_request(evidence)
        self.assertEqual(request, S.extraction_request(self.evidence))
        self.assertEqual(len(request['messages']), 2)
        self.assertEqual(set(json.loads(request['messages'][1]['content'])),
                         {'evidence', 'opening_context', 'incomplete'})
        self.assertNotIn('PRIVATE_', json.dumps(request))

    def test_actor_object_and_action_cannot_be_borrowed_from_question(self):
        for role, replacement in [('actor', '丙'), ('object', '丁'), ('action', '击败')]:
            question = dict(self.question, **{role: replacement})
            with self.subTest(role=role):
                result = self.compare(question)
                self.assertEqual(result['status'], 'unconfirmed')
                self.assertEqual(result['events'][0]['event'][role], self.event[role])
                self.assertEqual(result['events'][0]['alignment'][role], 'unresolved_difference')
        self.assertEqual(self.compare(dict(self.question, actor='乙', object='甲'))['candidates'], [])

    def test_matching_roles_are_not_answer_or_reason_confirmation(self):
        before = copy.deepcopy(self.ledger)
        result = self.compare()
        self.assertEqual(result['status'], 'needs_semantic_review')
        self.assertEqual(result['answer'], '')
        self.assertFalse(result['semantic_verified'])
        self.assertEqual(result['question']['proposed_answer'], '因私怨')
        self.assertEqual(result['events'][0]['event']['reason'], '奉诏')
        self.assertEqual(self.ledger, before)

    def test_different_events_keep_separate_causes_and_multiple_matches(self):
        self.ledger['output']['events'][0]['reason'] = ''
        self.ledger['output']['events'].append(dict(self.event, object='丙', reason='因病'))
        result = self.compare()
        self.assertEqual(result['candidates'], ['event-1'])
        self.assertFalse(result['events'][0]['reason_available'])
        self.assertEqual(result['events'][0]['event']['reason'], '')
        self.ledger['output']['events'].append(dict(self.event, reason='另一次任命'))
        result = self.compare()
        self.assertEqual(result['candidates'], ['event-1', 'event-3'])
        self.assertEqual(result['status'], 'needs_semantic_review')

    def test_negation_proposal_refusal_and_reported_voice_stay_unconfirmed(self):
        for key, value in [('state', 'negated'), ('state', 'proposed'), ('state', 'refused'),
                           ('state', 'unknown'), ('attribution', 'speaker'),
                           ('attribution', 'reported'), ('attribution', 'accusation')]:
            ledger = copy.deepcopy(self.ledger)
            ledger['output']['events'][0][key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(self.compare(ledger=ledger)['status'], 'unconfirmed')

    def test_unknown_alias_or_missing_role_is_not_treated_as_same_person(self):
        for target in ('', '乙公'):
            result = self.compare(dict(self.question, object=target))
            self.assertEqual(result['candidates'], [])
        self.assertEqual(self.compare(dict(self.question, action='授官'))['candidates'], [])

    def test_incomplete_identity_only_or_wrong_source_cannot_pass(self):
        for field, value in [('complete', False), ('role', 'opening_context')]:
            evidence = copy.deepcopy(self.evidence)
            evidence[0][field] = value
            ledger = copy.deepcopy(self.ledger)
            ledger['source_sha256'] = S.digest(evidence)
            self.assertEqual(self.compare(ledger=ledger, evidence=evidence)['status'], 'unconfirmed')
        moved = copy.deepcopy(self.evidence)
        moved[0]['parts'][0]['block_index'] = 2
        self.assertIn('missing_incomplete_or_different_source_ledger', self.compare(evidence=moved)['errors'])

    def test_truncated_or_invalid_model_output_is_preserved_without_retry(self):
        reader = Mock()
        for reason, text in [('length', '{'), ('stop', 'invalid'),
                             ('stop', json.dumps(dict(coverage='complete', events=[dict(self.event, references=['e99'])])) )]:
            reader.reset_mock()
            reader.request.return_value = {'choices': [dict(finish_reason=reason, message={'content': text})]}
            result = S.extract(reader, self.evidence)
            self.assertFalse(result['completed'])
            self.assertEqual(result['raw'], text)
            self.assertEqual(self.compare(ledger=result)['status'], 'unconfirmed')
            reader.request.assert_called_once()

    def test_one_source_reading_can_be_reused_for_unrelated_questions(self):
        reader = Mock()
        reader.request.return_value = {'choices': [dict(finish_reason='stop',
            message={'content': json.dumps(self.ledger['output'])})]}
        ledger = S.extract(reader, self.evidence)
        self.assertEqual(self.compare(ledger=ledger)['candidates'], ['event-1'])
        self.assertEqual(self.compare(dict(self.question, actor='戊'), ledger=ledger)['candidates'], [])
        reader.request.assert_called_once()

    def test_preparation_freezes_request_independent_of_query_and_gold(self):
        rows = [dict(id=str(i), evidence=self.evidence, expected='PRIVATE_GOLD', payload=dict(
            original_question='PRIVATE_QUERY', parsed_question=dict(self.question, object=target)))
            for i, target in enumerate(('乙', '丙'))]
        previous = dict(rows=rows, runtime={'timeout_seconds': 120}, portable_inputs_sha256='frozen')
        with patch.object(R.C, 'prepare', return_value=(previous, {})):
            report, _ = R.prepare()
            rows[1]['payload']['original_question'] = 'CHANGED_QUERY'
            second, _ = R.prepare()
        self.assertEqual(report['request_sha256'], second['request_sha256'])
        self.assertNotIn('PRIVATE_', json.dumps(report['request']))
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(report['rows'][0]['question'], self.question)


if __name__ == '__main__':
    unittest.main()
