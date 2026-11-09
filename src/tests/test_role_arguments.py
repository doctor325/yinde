import unittest
from search import evidence_pipeline as E

class Arguments(unittest.TestCase):
    def audit(self,q,t):return E.audit(dict(text_orig=t,book_title='甲史'),E.parse(q),[])
    def test_coordinated_subject_and_local_object(self):
        for actor,target,short in (('甲公','乙坚','坚'),('丙侯','丁成','成'),('戊伯','己玄','玄')):
            q=actor+'在哪里击败'+target
            t=target+'自将。'+actor+'等又破'+target+'於北城。'
            self.assertEqual(self.audit(q,t)['status'],'clues_present')
            t=target+'自将，'+actor+'等又破'+short+'於北城。'
            a=self.audit(q,t)
            self.assertEqual(a['status'],'clues_present')
            o=a['subject_syntax']['observations'][0]
            self.assertTrue(o['alias_basis']);self.assertEqual(t[slice(*o['object_span'])],short)
            for bad in (actor+'等又破'+short+'於北城。',target+'自将。'+actor+'破'+short+'於北城。',
                        target+'自将，'+actor+'之子破'+short+'於北城。',
                        target+'自将，'+actor+'等未破'+short+'於北城。',
                        target+'曰：「'+actor+'等破'+short+'於北城。」'):
                self.assertEqual(self.audit(q,bad)['status'],'insufficient',bad)
            self.assertEqual(self.audit(target+'在哪里击败'+actor,t)['status'],'insufficient')
    def test_passive_patient_and_location(self):
        for patient,killer in (('甲公','乙侯'),('丙伯','丁君')):
            q=patient+'在哪里被杀'
            t=patient+'為'+killer+'所殺於北城。'
            a=self.audit(q,t)
            self.assertEqual(a['status'],'clues_present')
            self.assertEqual(a['subject_syntax']['observations'][0]['argument_role'],'patient')
            for bad in (killer+'為'+patient+'所殺於北城。',patient+'欲為'+killer+'所殺於北城。',
                        '傳言'+t,'曰：「'+t+'」'):
                self.assertEqual(self.audit(q,bad)['status'],'insufficient',bad)
