import json,unittest
from pathlib import Path
from search import person_references as P,evidence_pipeline as E
class References(unittest.TestCase):
 def test_independent_sources(self):
  for row in json.loads(Path(__file__).with_name('evidence_acceptance').joinpath('reference_sources.json').read_text(encoding='utf8')):
   source=row['chunk']['text_orig'];pos=source.find(row['short']+'曰')
   self.assertGreaterEqual(pos,0)
   self.assertEqual(P.resolve(source,row['full'],pos,pos+len(row['short']))['status'],'unresolved')
 def test_development_gold(self):
  rows=json.loads(Path(__file__).with_name('evidence_acceptance').joinpath('existing17.json').read_text(encoding='utf8'))
  row=next(r for r in rows if r['id']=='place-jin')
  audit=E.audit(row['chunk'],E.parse(row['query']),[])
  self.assertEqual(audit['status'],'clues_present')
  o=next(o for o in audit['subject_syntax']['observations'] if o.get('references'))
  for basis in o['references'][0]['basis']:self.assertEqual(row['chunk']['text_orig'][basis['start']:basis['end']],basis['quote'])
 def test_competition_and_scope(self):
  for prefix in ('八月甲坚自将。九月乙坚自将。','八月甲坚自将。曰：','八月甲坚自将。\n'):
   source=prefix+'丙公破坚於北城。';pos=source.rfind('坚')
   self.assertEqual(P.resolve(source,'甲坚',pos,pos+1)['status'],'unresolved')
  for event in ('丙公未破坚於北城。','丙公之子破坚於北城。','坚破丙公於北城。'):
   self.assertEqual(E.audit(dict(text_orig='八月甲坚自将。'+event,book_title='甲史'),E.parse('丙公在哪里击败甲坚'),[])['status'],'insufficient')
