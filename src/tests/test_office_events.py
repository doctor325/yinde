import unittest
from search import evidence_pipeline as E

class OfficeEvents(unittest.TestCase):
    def test_dated_departure_refuses_intervening_actor(self):
        plan=E.parse('东方明什么时候辞官？')
        base=dict(book_title='甲史',heading='东方明',layer='body',revision_id=1,source_sha256='fixture')
        opening='东方明字子明。除奉朝请。'
        dated='永明十年，上表辞禄，诏许之。'
        self.assertEqual(E.audit(dict(base,text_orig=opening+dated),plan,[])['status'],'clues_present')
        for inserted in ('李君入朝。','张公曰：「当归。」','长子从军。'):
            self.assertEqual(E.audit(dict(base,text_orig=opening+inserted+dated),plan,[])['status'],'insufficient')

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

    def test_county_departure_requires_attested_office_overlap_and_same_actor(self):
        c=dict(chunk_id=2,page_id=1,book_id='a',book_title='甲史',heading='东方明',layer='body',revision_id=1,source_sha256='source')
        text='。督邮至，明叹曰：「吾不能折腰事人！」建元二年，解印去县。'
        prefix='以为清河县令。在县，未尝扰民'
        overlap=5
        c.update(text_orig=text,parts=[dict(block_index=1,start=len(prefix),end=len(prefix)+len(text))])
        previous=dict(c,chunk_id=1,text_orig=prefix+text[:overlap],parts=[dict(block_index=1,start=0,end=len(prefix)+overlap)])
        plan=E.parse('东方明为什么辞去清河县令？')
        audit=E.audit(c,plan,[previous])
        self.assertEqual(audit['status'],'clues_present')
        o=audit['causal_event_explanations'][0]
        self.assertEqual(o['quote'],text[o['start']:o['end']])
        source=o['office_source']
        self.assertEqual(previous['text_orig'][source['start']:source['end']],'清河县令')
        for other in (dict(previous,revision_id=2),dict(previous,text_orig=previous['text_orig'][:-1]+'异'),
                      dict(previous,text_orig=previous['text_orig'].replace('未尝扰民','改任他官'))):
            self.assertEqual(E.audit(c,plan,[other])['status'],'insufficient')
        self.assertEqual(E.audit(c,E.parse('东方明为什么辞去平原县令？'),[previous])['status'],'insufficient')
        self.assertEqual(E.audit(dict(c,text_orig=text.replace('明叹','他叹')),plan,[previous])['status'],'insufficient')
