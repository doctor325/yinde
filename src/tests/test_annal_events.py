import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import annal_events, evidence_pipeline


class AnnalEvents(unittest.TestCase):
    def audit(self, query, text, start=0, layer='body'):
        chunk=dict(text_orig=text,book_title='甲史',chunk_id=1,parts=[{'start':start}],layer=layer)
        plan=evidence_pipeline.parse(query)
        events=annal_events.observations(chunk,plan,query)
        return events,evidence_pipeline.audit(chunk,plan,[],events)['status']

    def test_direct_subject_and_requested_event(self):
        for query,text in (
            ('甲公何时即位？','甲子，帝即皇帝位。'),
            ('甲公何时任命乙侯？','甲子，上以乙侯為丞相。'),
            ('甲公何时攻打西夏？','甲子，帝親征西夏。'),
        ):
            with self.subTest(query=query):
                self.assertEqual(self.audit(query,text)[1],'clues_present')

    def test_other_actor_report_or_truncated_context(self):
        for text,start in (
            ('乙帝既至，帝即位。',0),
            ('臣曰：「明年。帝即位。」',0),
            ('明年，帝即位。',10),
            ('帝未即位。',0),
        ):
            with self.subTest(text=text):
                events,status=self.audit('甲公何时即位？',text,start)
                self.assertTrue(events)
                self.assertEqual(status,'insufficient')
        self.assertEqual(self.audit('甲公何时即位？','甲子，帝即位。',layer='annotation')[0],[])

    def test_object_and_action_direction(self):
        for query,text in (
            ('甲公何时被任命为丞相？','甲子，帝以乙侯為丞相。'),
            ('甲公何时任命乙侯？','甲子，帝命乙侯攻城。'),
            ('甲公何时任命乙侯？','甲子，帝以乙侯為不可。'),
            ('甲公何时攻打西夏？','甲子，帝征西夏之叛臣。'),
            ('甲公何时击败西夏？','甲子，帝攻西夏。'),
            ('甲公何时攻打西夏？','甲子，帝命乙侯攻西夏。'),
        ):
            with self.subTest(query=query,text=text):
                self.assertEqual(self.audit(query,text)[1],'insufficient')
        events,_=self.audit('甲公何时攻打西夏？','甲子，帝命乙侯攻西夏。')
        self.assertIn('delegated_action',events[0]['qualifications'])

    def test_dated_omitted_actor(self):
        for query,text in (
            ('甲公何时任命乙侯？','庚子，以乙侯參知政事。'),
            ('甲公何时任命乙侯？','戊戌，以乙侯爲吏部尚書。'),
            ('甲公何时攻打西夏？','丁丑，親征西夏，皇太子監國。'),
            ('甲公何时即位？','甲子，即皇帝位。'),
        ):
            with self.subTest(text=text):
                events,status=self.audit(query,text)
                self.assertEqual(status,'clues_present')
                self.assertTrue(events[0]['subject_omitted'])
                self.assertEqual(events[0]['quote'],text)
        self.assertEqual(self.audit('甲公何时攻打西夏？',
                                   '餘文。丁丑，親征西夏。',start=200)[1],'insufficient')

    def test_omitted_actor_boundaries(self):
        for query,text,start in (
            ('甲公何时攻打西夏？','丁丑，將親征西夏。',0),
            ('甲公何时攻打西夏？','丁丑，未親征西夏。',0),
            ('甲公何时攻打西夏？','丁丑，乙侯親征西夏。',0),
            ('甲公何时攻打西夏？','丁丑，攻西夏。',0),
            ('甲公何时攻打西夏？','丁丑，親征西夏。',200),
            ('甲公何时任命乙侯？','臣曰：「庚子，以乙侯爲丞相。」',0),
            ('甲公何时即位？','甲子，帝崩，皇太子即皇帝位。',0),
            ('甲公何时任命乙侯？','庚子，乙侯至，以乙侯爲丞相。',0),
            ('甲公何时任命乙侯？','以乙侯爲丞相。甲子，赦。',0),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.audit(query,text,start)[1],'insufficient')

    def test_recovered_prefix(self):
        source = '前事。丁丑，親征西夏。'
        chunk = dict(text_orig=source[3:],layer='body',parts=[dict(block_index=2,start=3,end=len(source))])
        whole = dict(text_orig=source,parts=[dict(block_index=2,start=0,end=len(source))])
        prefix = annal_events.source_prefix(chunk,[whole,chunk])
        self.assertEqual(prefix,'前事。')
        query = '甲公何时攻打西夏？'
        plan = evidence_pipeline.parse(query)
        events = annal_events.observations(chunk,plan,query,leading_text=prefix)
        self.assertEqual(evidence_pipeline.audit(chunk,plan,[],events)['status'],'clues_present')
        self.assertIsNone(annal_events.source_prefix(chunk,[chunk]))
        events = annal_events.observations(chunk,plan,query,leading_text='臣曰：「前事。')
        self.assertEqual(evidence_pipeline.audit(chunk,plan,[],events)['status'],'insufficient')


if __name__=='__main__':
    unittest.main()
