import unittest
from search import evidence_pipeline as E
from search.causal_evidence import source_labels


class CausalEvidence(unittest.TestCase):
    def test_explicit_reason_and_role_controls(self):
        for name,other in [('甲公','乙侯'),('丙君','丁伯')]:
            plan=E.parse(name+'为什么辞官？')
            c=dict(text_orig=name+'以母丧去位。',book_title='甲史')
            self.assertEqual(E.audit(c,plan,[])['status'],'clues_present')
            for text in (other+'以母丧去位。',name+'来朝，'+other+'以母丧去位。',
                         '传言'+name+'以母丧去位。','若'+name+'以母丧去位。',
                         name+'未以母丧去位。',name+'以母丧未去位。'):
                self.assertEqual(E.audit(dict(c,text_orig=text),plan,[])['status'],'insufficient')

    def test_source_labels_do_not_answer_question(self):
        c=dict(text_orig='史臣曰：或诬告谋反。繫两月，无可证者。',book_title='甲史')
        labels=source_labels(c)
        self.assertEqual({x['kind'] for x in labels},{'historian_explanation','accusation','insufficient_proof'})
        for x in labels:
            self.assertEqual(c['text_orig'][x['start']:x['end']],x['quote'])
            self.assertFalse(x['answers_cause'])
        self.assertEqual(E.audit(c,E.parse('甲公为什么被关进监狱？'),[])['status'],'insufficient')


class EventExplanationTests(unittest.TestCase):
    def test_attributed_evidence_preserves_event_roles(self):
        import json
        from pathlib import Path
        cases={c['id']:c for c in json.loads(Path(__file__).with_name('evidence_acceptance').joinpath(
            'causal7.json').read_text(encoding='utf8'))}
        for key in ('sima','chao','wang'):
            case=cases[key]
            audit=E.audit(case['chunk'],E.parse(case['query']),[])
            self.assertEqual(audit['status'],'clues_present',key)
            self.assertFalse(audit['relation_verified'])
        for key in ('bao','yue'):
            case=cases[key]
            self.assertEqual(E.audit(case['chunk'],E.parse(case['query']),[])['status'],'insufficient')
        text='史臣曰：非甲公削诸侯，遂使七国俱起。'
        c=dict(text_orig=text,book_title='甲史',layer='body',revision_id=1,source_sha256='fixture')
        self.assertEqual(E.audit(c,E.parse('甲公削藩导致了什么事？'),[])['status'],'insufficient')

    def test_independent_names_and_event_boundary(self):
        from search.causal_evidence import event_explanations
        for name in ('诸葛明','东方平'):
            plan=E.parse(name+'为什么辞官？')
            base=dict(heading=name,layer='body',revision_id=1,source_sha256='test',book_title='甲史')
            text=name[-2:]+'深耻之，遂称病去郡。'
            reports=event_explanations(dict(base,text_orig=text),plan)
            self.assertEqual(len(reports),1)
            r=reports[0]
            self.assertEqual(text[slice(*r['reason_span'])],'深耻之')
            self.assertEqual(text[slice(*r['event_span'])],'称病去郡')
            for bad in ('他深耻之，遂称病去郡。','传言'+text,'若'+text,'「'+text+'」',name+'以父母皆老，辞不就。后解官归养。',name+'深耻之，遂命他人去郡。'):
                self.assertEqual(event_explanations(dict(base,text_orig=bad),plan),[],bad)
            self.assertEqual(event_explanations(dict(base,text_orig=text,heading='他人'),plan),[])

    def test_frozen_sources(self):
        import json
        from pathlib import Path
        from search.causal_evidence import event_explanations
        cases=json.loads(Path(__file__).with_name('evidence_acceptance').joinpath('causal7.json').read_text(encoding='utf8'))
        reports={c['id']:event_explanations(c['chunk'],E.parse(c['query'])) for c in cases}
        self.assertTrue(reports['wang'])
        for key in ('bao','sima','han','yue','chao'):self.assertEqual(reports[key],[])
