"""Synthetic role/temporal contrasts; no corpus, database or model needed."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import evidence_pipeline as E


class EventTime(unittest.TestCase):
    def audit(self,text,query='甲公何时去世'):
        return E.audit(dict(text_orig=text,book_title='测试',chunk_id=1),E.parse(query),[])

    def test_direct_dates_remain_clues(self):
        for text in ('元年，甲公卒。','三年春正月，甲公卒。','甲公卒于三年。','甲 公 卒於 三 年。'):
            with self.subTest(text=text):
                a=self.audit(text)
                self.assertEqual(a['status'],'clues_present')
                self.assertFalse(a['relation_verified'])

    def test_other_person_or_event_does_not_supply_date(self):
        for text in ('元年乙侯来朝，甲公卒。','甲公卒，三年乙侯来朝。',
                     '元年甲公来朝，甲公卒。','甲公卒，年七十。',
                     '元年，乙侯卒，甲公卒。'):
            with self.subTest(text=text):
                self.assertEqual(self.audit(text)['answer_slot_evidence']['time'],[])

    def test_new_sentence_and_original_offsets(self):
        text='乙侯来朝。\n三年，甲 公卒。'
        a=self.audit(text)
        self.assertEqual(a['status'],'clues_present')
        o=a['subject_syntax']['observations'][0]
        self.assertEqual(text[slice(*o['subject_span'])],'甲 公')
        self.assertEqual(text[slice(*o['predicate_span'])],'卒')

    def test_negation_and_reports_stay_unconfirmed(self):
        for text in ('元年，甲公未死。','传言元年甲公卒，实未死。'):
            self.assertEqual(self.audit(text)['answer_slot_evidence']['time'],[])

    def test_same_rule_applies_to_different_event(self):
        self.assertEqual(self.audit('三年，乙侯即位。','乙侯何时即位')['status'],'clues_present')
        self.assertEqual(self.audit('三年甲公卒，乙侯即位。','乙侯何时即位')['answer_slot_evidence']['time'],[])

    def test_unknown_era_date_is_not_guessed(self):
        self.assertEqual(self.audit('某朝建元三年，甲公卒。')['answer_slot_evidence']['time'],[])


if __name__=='__main__':unittest.main()
