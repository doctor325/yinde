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
        for key in ('sima','chao','wang','bao'):
            case=cases[key]
            audit=E.audit(case['chunk'],E.parse(case['query']),[])
            self.assertEqual(audit['status'],'clues_present',key)
            self.assertFalse(audit['relation_verified'])
        for key in ('yue',):
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
        self.assertEqual(len(reports['bao']),1)
        bao=next(c['chunk']['text_orig'] for c in cases if c['id']=='bao')
        self.assertEqual(bao[slice(*reports['bao'][0]['reason_span'])],'父母又不欲行')
        self.assertEqual(bao[slice(*reports['bao'][0]['event_span'])],'解官歸養')
        for key in ('sima','han','yue','chao'):self.assertEqual(reports[key],[])

    def test_adjacent_refusal_and_departure_have_different_causes(self):
        plan=E.parse('司马甲为什么辞官？')
        first='司马甲，字元直。以家贫，辞不就。'
        actual='得监和州税，父母又不欲其行，甲遂解官归养。'
        base=dict(heading='司马甲',layer='body',revision_id=1,source_sha256='fixture',book_title='甲史')
        audit=E.audit(dict(base,text_orig=first+actual),plan,[])
        self.assertEqual(audit['status'],'clues_present')
        self.assertEqual(audit['answer_slot_evidence']['reason'],[actual[:-1]])
        for text in (first,first+actual.replace('甲遂','乙遂'),first+'传言'+actual,
                     first+'「'+actual+'」',first+actual.replace('解官归养','辞不就')):
            self.assertEqual(E.audit(dict(base,text_orig=text),plan,[])['status'],'insufficient',text)

    def test_demotion_requires_the_rulers_own_reason_and_actual_penalty(self):
        case=dict(heading='司马甲',layer='body',revision_id=1,source_sha256='fixture',book_title='甲史')
        plan=E.parse('司马甲为什么被贬？')
        text='司马甲方上书。上曰：「甲言我用兵未妥。至谓民力不堪役，何言之过也？甲为人臣，敢于犯上，不可赦！」乃贬为永州司马。'
        audit=E.audit(dict(case,text_orig=text),plan,[])
        self.assertEqual(audit['status'],'clues_present')
        event=audit['causal_event_explanations'][0]
        self.assertEqual(event['source_attribution'],'speaker_explanation')
        self.assertEqual(text[slice(*event['reason_span'])],'至谓民力不堪役')
        for bad in (text.replace('上曰','侍臣曰'),text.replace('乃贬为','拟贬为'),
                    text.replace('乃贬为','乃贬乙为'),text.replace('乃贬为永州司马','乃授为御史')):
            self.assertEqual(E.audit(dict(case,text_orig=bad),plan,[])['status'],'insufficient',bad)
