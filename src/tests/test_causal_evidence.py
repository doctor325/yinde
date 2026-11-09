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
