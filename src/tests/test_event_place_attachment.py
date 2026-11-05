"""Synthetic location contrasts; no model, corpus or database needed."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import evidence_pipeline as E


class EventPlace(unittest.TestCase):
    def audit(self,text,subject='甲公',event='即位'):
        return E.audit(dict(text_orig=text,book_title='测试',chunk_id=1),
                       E.parse(subject+'在哪里'+event),[])

    def test_direct_location_clues_across_subjects_and_events(self):
        for subject,event,verb in [('甲公','即位','即位'),('乙侯','去世','卒'),('丙君','即位','即位')]:
            for prep,location in [('於','東宮'),('于','西城'),('在','南州')]:
                with self.subTest(subject=subject,verb=verb,location=location):
                    a=self.audit(subject+verb+prep+location+'。',subject,event)
                    self.assertEqual(a['status'],'clues_present')
                    self.assertFalse(a['relation_verified'])

    def test_other_person_and_other_action_cannot_supply_location(self):
        for text in ('乙侯在东宫，甲公即位。','甲公即位，乙侯在西城。',
                     '甲公即位，甲公在东宫宴群臣。','甲公即位，遂至东宫。',
                     '甲公即位后至西城。','甲公即位于东宫攻西城。'):
            with self.subTest(text=text):
                self.assertEqual(self.audit(text)['answer_slot_evidence']['place'],[])

    def test_does_not_borrow_between_observations(self):
        a=self.audit('甲公卒，乙侯卒于西城。','甲公','去世')
        self.assertEqual(a['status'],'insufficient')
        self.assertEqual(a['answer_slot_evidence']['place'],[])

    def test_offsets_and_source_quotes_preserved(self):
        text='乙侯来朝。\n甲 公 即位 於 東 宮。'
        a=self.audit(text)
        self.assertEqual(a['status'],'clues_present')
        quote=a['answer_slot_evidence']['place'][0]
        self.assertIn(quote,text)
        o=a['subject_syntax']['observations'][0]
        self.assertEqual(text[slice(*o['predicate_span'])],'即位')

    def test_negation_reports_and_unknown_construction_stay_unconfirmed(self):
        for text in ('甲公未即位于东宫。','传言甲公即位于东宫。',
                     '甲公即位于乙侯之后。','甲公即位于某处。','在东宫，甲公即位。'):
            with self.subTest(text=text):
                self.assertEqual(self.audit(text)['answer_slot_evidence']['place'],[])


if __name__=='__main__':unittest.main()
