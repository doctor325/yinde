"""Subject substitution, polarity and quotation/source invariants."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import punctuated_relevance as R
from test_punctuated_relevance import chunk


class Roles(unittest.TestCase):
    def test_death_subject_metamorphic(self):
        for name in ('甲公','乙侯','丙君','丁伯','子平','子安','戊王','己卿'):
            plan=R.plan(name+'何时去世？')
            for sentence in (name+'卒。',name+'薨。',name+'遂死。',name+'殁于军中。'):
                with self.subTest(name=name,text=sentence):
                    f=R.features(chunk(sentence),plan)
                    self.assertEqual(f['subject_syntax']['positive_groups'],['death'])
            for sentence in (name+'杀敌将，敌将卒。', name+'之父卒。',name+'令使者杀敌将，敌将卒。',
                             name+'问疾于他人，他人薨。',name+'卒为名臣。',name+'卒业于乡校。',
                             name+'未死。',name+'不死。','传言'+name+'卒，实未死。','若'+name+'卒，则立其子。',
                             '欲'+name+'死。',name+'将死。',name+'出兵。敌将卒。'):
                with self.subTest(name=name,text=sentence):
                    f=R.features(chunk(sentence),plan)
                    self.assertEqual(f['subject_syntax']['positive_groups'],[])
                    self.assertFalse(f['relation_verified'])

    def test_accession_distinguishes_installer_and_installed(self):
        p=R.plan('甲王在哪里即位？')
        self.assertEqual(R.features(chunk('甲王即位于东宫。'),p)['subject_syntax']['positive_groups'],['accession'])
        self.assertEqual(R.features(chunk('甲王立乙公为太子。'),p)['subject_syntax']['positive_groups'],[])

    def test_appointment_not_other_official(self):
        p=R.plan('甲公担任司徒的记载')
        self.assertEqual(R.features(chunk('王命甲公为司徒。'),p)['subject_syntax']['positive_groups'],['appointment'])
        for t in ('甲公请王命乙侯为司徒。','甲公与司徒乙侯同至。','甲公以乙侯为司徒。'):
            self.assertEqual(R.features(chunk(t),p)['subject_syntax']['positive_groups'],[])

    def test_source_quotes_exact_even_traditional(self):
        text='　元年，甲公薨。\n丙君未死。'
        f=R.features(chunk(text),R.plan('甲公去世的记载'))
        for obs in f['subject_syntax']['observations']:
            self.assertEqual(text[obs['start']:obs['end']],obs['quote'])
            self.assertFalse(obs['relation_verified'])

    def test_unknown_is_not_rejection_or_assertion(self):
        p=R.plan('甲公与乙侯是什么关系？')
        f=R.features(chunk('甲公，乙侯之父也。'),p)
        self.assertEqual(f['subject_syntax']['observations'],[])
        self.assertFalse(f['relation_verified'])

    def test_subject_swap_changes_syntax_preference(self):
        for subject,other in (('甲公','乙侯'),('乙侯','甲公')):
            dense=[dict(chunk=chunk(subject+'杀'+other+'，'+other+'卒。',1),semantic_score=.9)]
            lexical=[dict(chunk=chunk(subject+'卒。',2),channels=['relation_clues'])]
            hits,_=R.rank(dense,lexical,R.plan(subject+'何时去世？'))
            self.assertEqual(hits[0]['chunk']['chunk_id'],2)


if __name__=='__main__':unittest.main()
