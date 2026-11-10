import unittest
from search import evidence_pipeline as E

class OfficeEvents(unittest.TestCase):
    def test_queries_and_realistic_role_controls(self):
        for name in ('东方明','公孙平'):
            c=dict(book_title='甲史',heading=name,layer='body',revision_id=1,source_sha256='fixture')
            q=E.parse(name+'为什么不接受太子洗马征召？')
            self.assertEqual(q['office_event']['office'],'太子洗马')
            self.assertEqual(q['office_event']['action'],'appointment_refusal')
            good=name+'，字子明。诏征为太子洗马。'+name[-1]+'以祖母年高，无人奉养，遂不应命。'
            self.assertEqual(E.audit(dict(c,text_orig=good),q,[])['status'],'clues_present')
            for bad in (good.replace('不应命','应命'),good.replace('太子洗马','太子家令'),good.replace('遂不应命','未遂不应命'),good.replace(name[-1]+'以','他以'),good.replace(name[-1]+'以','传言'+name[-1]+'以')):
                self.assertEqual(E.audit(dict(c,text_orig=bad),q,[])['status'],'insufficient',bad)
            departure=E.parse(name+'为什么辞去太子洗马？')
            self.assertEqual(E.audit(dict(c,text_orig=good),departure,[])['status'],'insufficient')

    def test_office_specific_departure(self):
        c=dict(book_title='甲史',heading='东方明',layer='body',revision_id=1,source_sha256='fixture',text_orig='  以亲老家贫，起为州祭酒，不堪吏职，少日自解归。')
        self.assertEqual(E.audit(c,E.parse('东方明为什么辞去州祭酒？'),[])['status'],'clues_present')
        for query in ('东方明为什么辞去彭泽令？','东方明为什么拒绝州祭酒征召？'):
            self.assertEqual(E.audit(c,E.parse(query),[])['status'],'insufficient')
        for prefix in ('传言','若','「'):
            self.assertEqual(E.audit(dict(c,text_orig=prefix+c['text_orig'].strip()),E.parse('东方明为什么辞去州祭酒？'),[])['status'],'insufficient')
