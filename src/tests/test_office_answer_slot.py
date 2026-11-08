import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import evidence_pipeline as E


class OfficeAnswerSlot(unittest.TestCase):
    def audit(self,query,text):
        return E.audit(dict(text_orig=text,book_title='甲史'),E.parse(query),[])

    def test_question_is_not_literal_constraint(self):
        for name in ('甲公','乙侯','丙伯'):
            for wording in ('担任过什么官职','担任哪些官职','出任何种职务'):
                query=name+wording
                plan=E.parse(query)
                self.assertEqual([c['text'] for c in plan['clues']],[name])
                self.assertEqual(plan['answer_slots'],['office'])
                self.assertEqual(self.audit(query,name+'為尚書左僕射。')['status'],'clues_present')

    def test_distractors_and_qualifications(self):
        for text in ('甲公来朝，乙侯為尚書左僕射。','乙侯授甲公兵器。',
                     '甲公之父為尚書左僕射。','甲公未為尚書左僕射。',
                     '傳言甲公為尚書左僕射。','甲公欲為尚書左僕射。',
                     '甲公為人寬厚。','甲公為尚書左僕射之父。'):
            with self.subTest(text=text):
                self.assertEqual(self.audit('甲公担任过什么官职',text)['status'],'insufficient')

    def test_named_office_and_date_constraints_survive(self):
        self.assertEqual(E.parse('甲公担任过司徒')['constraints'][0]['text'],'过司徒')
        self.assertEqual(self.audit('甲公何时担任什么官职','甲子，甲公為司徒。')['status'],'clues_present')
        self.assertEqual(self.audit('甲公何时担任什么官职','甲公為司徒。甲子，乙侯來。')['status'],'insufficient')
