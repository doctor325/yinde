"""Synthetic scope regressions, not historical relevance/accuracy labels."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search.relation_context import assess_context


def assess(text, subject='甲公', predicate='卒'):
    start=text.index(subject)
    at=text.index(predicate,start+len(subject))
    return assess_context(text,start,at,at+len(predicate),subject_end=start+len(subject))


class Context(unittest.TestCase):
    def test_other_clause_negation_does_not_negate_event(self):
        for name in ('甲公','乙侯','子平','子安'):
            for t in (name+'卒，援军未至。','援军未至，'+name+'卒。',name+'卒，子不在侧。'):
                with self.subTest(text=t):
                    self.assertEqual(assess(t,name)['qualifications'],[])

    def test_direct_negation_is_retained(self):
        for t in ('甲公未卒。','甲公不卒。','甲公弗卒。'):
            self.assertEqual(assess(t)['qualifications'],['negated'])

    def test_conditional_and_report_scope_cross_comma(self):
        for t,flag in (('传言军败，甲公卒。','reported'),('若军败，甲公卒。','hypothetical'),
                       ('傳聞甲公卒。','reported'),('甲公卒之说未可信。','reported')):
            self.assertIn(flag,assess(t)['qualifications'])

    def test_reported_denial_is_not_verified(self):
        r=assess('传言甲公卒，实未死。')
        self.assertIn('reported',r['qualifications']);self.assertFalse(r['relation_verified'])

    def test_sentence_boundary_blocks_qualifier_transfer(self):
        for sep in ('。','；','！','？','\n'):
            self.assertEqual(assess('传言乙侯将至'+sep+'甲公卒。')['qualifications'],[])

    def test_title_is_not_future_tense(self):
        self.assertEqual(assess('将军甲公卒。')['qualifications'],[])
        self.assertIn('hypothetical',assess('甲公将卒。')['qualifications'])

    def test_interrupted_subject_leaves_scope_unresolved(self):
        self.assertIn('unresolved_scope',assess('甲公出兵，乙侯卒。')['qualifications'])

    def test_exact_quotes_and_offsets(self):
        text='前文。傳言甲公未卒。'
        r=assess(text)
        self.assertEqual(r['qualifications'],['negated','reported'])
        for e in r['evidence']:self.assertEqual(text[e['start']:e['end']],e['quote'])

    def test_invalid_spans(self):
        for offsets in ((-1,2,3),(0,0,1),(0,3,2),(0,2,9),(True,2,3)):
            with self.assertRaises(ValueError):assess_context('甲公卒。',*offsets,subject_end=2)
        with self.assertRaises(ValueError):assess_context('甲公卒。',0,2,3)

    def test_subject_characters_are_not_qualifiers(self):
        for name in ('莫甲','若乙','无丙','将丁'):
            self.assertEqual(assess(name+'卒。',name)['qualifications'],[])

    def test_pre_subject_wish_or_causative_is_unresolved(self):
        for prefix in ('欲','愿','恐','令','使','命'):
            self.assertIn('unresolved_scope',assess(prefix+'甲公卒。')['qualifications'])


if __name__=='__main__':unittest.main()
