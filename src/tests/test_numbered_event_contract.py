import copy
import unittest
from unittest.mock import patch
from search import numbered_evidence as N, numbered_event_contract as E


class EventContractTests(unittest.TestCase):
    def fixtures(self):
        text = '劉生懼禍，還印於楚主，遂亡。'
        context = dict(source={'page_id': 1}, boundary_cut=False, segments=[
            dict(role='event_context', block_index=1, start=0, end=len(text), text=text)])
        question = dict(actor='刘生', action='离开', object='楚主', requested='原因',
                        mode='verify', proposed_answer='惧祸')
        event = dict(actor='劉生', action='亡', object='楚主', attribute='懼禍',
                     state='departed', attribution='narrator', references={k: ['e1'] for k in E.FIELDS})
        value = dict(source_event=event, comparison=dict(actor='same', action='same', object='same', claim='supported'),
                     verdict='matched', answer='因为惧祸', rationale='还印后离开')
        return context, question, value

    def test_verification_retains_exact_claim_and_negation(self):
        _, question, _ = self.fixtures()
        query = '刘生离开楚主是因为不愿赴任吗？'
        question['proposed_answer'] = '不愿赴任'
        self.assertEqual(E.question_errors(question, query), [])
        question['proposed_answer'] = '拒绝出仕'
        self.assertTrue(E.question_errors(question, query))
        question.update(mode='open', proposed_answer='')
        self.assertEqual(E.question_errors(question, '刘生为什么离开？'), [])

    def test_claim_is_sent_to_judgement_without_becoming_source_fact(self):
        context, question, value = self.fixtures()
        snapshots = []
        with patch.object(N, 'generate', side_effect=[dict(output=question), dict(output=value)]) as generate:
            report = E.read(None, '刘生是因为惧祸离开楚主吗？', context,
                            lambda r: snapshots.append(copy.deepcopy(r)))
        payload = generate.call_args_list[1].args[2]
        self.assertEqual(payload['parsed_question'], question)
        self.assertEqual(payload['evidence'], {'e1': context['segments'][0]['text']})
        self.assertTrue(report['completed'])
        self.assertFalse(report['validation']['semantic_verified'])
        self.assertTrue(any('question' in r and 'judgement' not in r for r in snapshots))

    def test_citation_cannot_substitute_another_person_for_object(self):
        context, question, value = self.fixtures()
        value['source_event']['object'] = '魏王'
        result = E.guard(value, question, N.numbered(context), context)
        self.assertFalse(result['eligible'])
        self.assertIn('source phrase absent from its citations: object', result['errors'])

    def test_mismatch_and_unsubstantiated_claim_block_confirmation(self):
        context, question, value = self.fixtures()
        value['comparison'].update(object='different', claim='unsupported')
        result = E.guard(value, question, N.numbered(context), context)
        self.assertFalse(result['eligible'])
        self.assertIn('event mismatch: object', result['errors'])
        self.assertIn('claim is not supported', result['errors'])
        value.update(verdict='insufficient', answer='否')
        self.assertIn('insufficient evidence must not produce an answer',
                      E.guard(value, question, N.numbered(context), context)['errors'])

    def test_bad_question_stops_before_judgement_and_semantics_stays_unverified(self):
        context, question, value = self.fixtures()
        with patch.object(N, 'generate', return_value=dict(output=question)) as generate:
            with self.assertRaises(N.ReadError) as error:
                E.read(None, '刘生为何离开？', context)
        self.assertEqual(generate.call_count, 1)
        self.assertIn('question', error.exception.report)
        # Literal citations cannot establish whether the event was refused or departed.
        value['source_event'].update(state='refused', attribution='accusation')
        self.assertFalse(E.guard(value, question, N.numbered(context), context)['semantic_verified'])


if __name__ == '__main__':
    unittest.main()
