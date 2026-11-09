import unittest
from search import evidence_pipeline as E
class Enfeoffment(unittest.TestCase):
 def test_recipient(self):
  for name in ('甲平','乙成','丙玄'):
   q=name+'被封为什么侯'
   p=E.parse(q)
   self.assertEqual(p['answer_slots'],['office'])
   self.assertEqual([c['text'] for c in p['clues']],[name])
   for text in (name+'有功，封'+name[1:]+'北城侯。','封'+name+'北城侯。'):
    self.assertEqual(E.audit(dict(text_orig=text,book_title='甲史'),p,[])['status'],'clues_present')
   for text in (name+'有功。封'+name[1:]+'北城侯。',name+'有功，未封'+name[1:]+'北城侯。',name+'曰：「封'+name[1:]+'北城侯。」',name+'封丁伯北城侯。'):
    self.assertEqual(E.audit(dict(text_orig=text,book_title='甲史'),p,[])['status'],'insufficient',text)
