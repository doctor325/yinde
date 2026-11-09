import json,unittest
from pathlib import Path
from search import patient_references as P,evidence_pipeline as E,zh
class PatientReferences(unittest.TestCase):
 def test_independent_sources(self):
  for row in json.loads(Path(__file__).with_name('evidence_acceptance').joinpath('patient_sources.json').read_text(encoding='utf8')):
   chunk=row['chunk'];plan=E.parse(zh.to_simplified(row['patient'])+'在哪里被杀')
   observations=P.observations(chunk,plan)
   self.assertTrue(observations,row['patient'])
   for o in observations:
    ref=o['references'][0];self.assertEqual(chunk['text_orig'][slice(*ref['span'])],'之')
    self.assertEqual(chunk['text_orig'][slice(*ref['antecedent_span'])],row['patient'])
 def test_negative_roles_and_reports(self):
  p=E.parse('甲公在哪里被杀')
  for text in ('甲公攻乙侯，殺之於北城。','乙侯攻甲公，又使丙伯殺之於北城。','乙侯未攻甲公，殺之於北城。','曰：「乙侯攻甲公，殺之於北城。」','乙侯攻甲公，丙伯殺之於北城。'):
   self.assertEqual(P.observations(dict(text_orig=text),p),[],text)
 def test_attested_appellation(self):
  base=dict(page_id=1,revision_id=2,source_sha256='abc',heading='甲平',book_id='a',layer='body')
  opening=dict(base,chunk_id=1,text_orig='北君者，某國公子也，名平，姓甲氏。')
  chunk=dict(base,chunk_id=2,text_orig='某國攻北君，殺之於北城。')
  plan=E.parse('甲平在哪里被杀');o=P.observations(chunk,plan,[opening])
  self.assertEqual(E.audit(chunk,plan,[],o)['status'],'clues_present')
  self.assertTrue(o);self.assertTrue(o[0]['appellations'])
  self.assertFalse(P.observations(chunk,plan,[dict(opening,revision_id=3)]))

 def test_real_development_patient(self):
  from search import punctuated_search as S
  with S.connect(S.folder()/'chunks.sqlite') as conn:
   chunk=json.loads(conn.execute('SELECT payload FROM chunks WHERE chunk_id=9870').fetchone()[0])
   peers=[json.loads(r[0]) for r in conn.execute('SELECT payload FROM chunks WHERE page_id=?',(chunk['page_id'],))]
   plan=E.parse('商鞅在哪里被杀')
   o=P.observations(chunk,plan,peers)
   self.assertEqual(E.audit(chunk,plan,[],o)['status'],'clues_present')
   self.assertTrue(o[0]['appellations'])
