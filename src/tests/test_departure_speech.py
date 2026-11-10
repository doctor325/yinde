import json
from pathlib import Path
import unittest
from search import evidence_pipeline as E

class Speech(unittest.TestCase):
    def test_frozen_real_case_and_source_spans(self):
        rows=json.loads(Path(__file__).with_name('evidence_acceptance').joinpath('causal_independent6.json').read_text(encoding='utf8'))
        c=next(x['chunk'] for x in rows if x['id']=='shu_reason')
        a=E.audit(c,E.parse('疏广为什么辞官？'),[])
        self.assertEqual(a['status'],'clues_present')
        for o in a['causal_event_explanations']:
            self.assertEqual(c['text_orig'][o['start']:o['end']],o['quote'])
            self.assertEqual(o['source_attribution'],'speaker_explanation')
        self.assertEqual(E.audit(c,E.parse('疏广是否因为真的生病才辞官？'),[])['status'],'insufficient')

    def test_other_people_and_controls(self):
        for name in ('公孙明','东方平'):
            short=name[-1]
            text=short+'谓友曰：「吾闻知足不辱，功遂身退也。如此不去，惧有后悔，归老故乡不亦善乎？」即日移病。满二月赐告，'+short+'遂称笃，上疏乞骸骨。'
            c=dict(text_orig=text,book_title='甲史',heading=name,layer='body',revision_id=1,source_sha256='fixture')
            p=E.parse(name+'为什么辞官？')
            self.assertEqual(E.audit(c,p,[])['status'],'clues_present')
            for bad in (text.replace(short+'遂','他遂'),text.replace('上疏乞','未上疏乞'),text.replace('谓友','命友'),text.replace('移病。','移病。另官入朝。'), '传闻'+text,text.replace('归老故乡','朝廷议事').replace('功遂身退','立功建业')):
                self.assertEqual(E.audit(dict(c,text_orig=bad),p,[])['status'],'insufficient',bad)
            self.assertEqual(E.audit(dict(c,heading='他人'),p,[])['status'],'insufficient')
