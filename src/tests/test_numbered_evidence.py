import unittest
import copy
import json
from unittest.mock import patch
from search import numbered_evidence as N


class NumberedEvidence(unittest.TestCase):
    def context(self):
        return dict(source={'page_id':1},boundary_cut=False,segments=[
            dict(role='event_context',block_index=1,start=0,end=7,text='甲曰：「母老，'),
            dict(role='event_context',block_index=2,start=0,end=8,text='不赴任。」遂歸。')])

    def test_cross_block_quote_and_offsets(self):
        c=self.context();units=N.numbered(c)
        self.assertEqual(units[0]['text'],'甲曰：「母老，不赴任。」遂歸。')
        self.assertEqual(''.join(p['quote'] for p in units[0]['parts']),units[0]['text'])
        self.assertTrue(units[0]['complete'])
        for s,p in zip(c['segments'],units[0]['parts']):
            self.assertEqual(s['text'][p['start']:p['end']],p['quote'])

    def test_roles_and_cuts(self):
        c=self.context();c['segments'][1]['role']='opening_context'
        result=N.numbered(c)
        self.assertEqual(len(result),3)
        self.assertEqual([u['role'] for u in result],['event_context','opening_context','opening_context'])
        self.assertTrue(all(not u['complete'] for u in result if '「' in u['text'] or '」' in u['text']))
        c=self.context();c['boundary_cut']=True
        self.assertFalse(N.numbered(c)[0]['complete'])

    def test_guard_does_not_claim_semantic_truth(self):
        c=self.context();es=N.numbered(c)
        q=dict(actor='甲',action='拒任',object='',requested='原因')
        v=dict(verdict='matched',actor='甲',action='拒任',object='',answer='父亡',
            attribution='narrator',state='refused',rationale='错误解释也可引用真编号',
            references={k:['e1'] for k in ('actor','action','answer','attribution')})
        v['references']['object']=[]
        self.assertFalse(N.guard(v,q,es,c)['semantic_verified'])
        v['references']['answer']=['e999']
        self.assertFalse(N.guard(v,q,es,c)['eligible'])
        v['references']['answer']=[]
        self.assertFalse(N.guard(v,q,es,c)['eligible'])

    def test_wire_preserves_text_flags_and_keeps_offsets_local(self):
        es=N.numbered(self.context())
        es.append(dict(es[0],id='e2',role='opening_context',complete=False,text='甲\n「乙」\\"'))
        original=copy.deepcopy(es);wire=N.evidence_payload(es)
        self.assertEqual(es,original)
        self.assertEqual(wire['evidence'],{e['id']:e['text'] for e in es})
        self.assertEqual(wire['opening_context'],['e2'])
        self.assertEqual(wire['incomplete'],['e2'])
        self.assertNotIn('parts',wire)
        self.assertEqual(json.loads(N.encode(wire)),wire)
        with self.assertRaises(ValueError):N.evidence_payload(es+[es[0]])
        with self.assertRaises(ValueError):N.evidence_payload([dict(es[0],role='unrecognized')])

    def test_timeout_preserves_completed_question_and_phase(self):
        q=dict(output=dict(actor='甲',action='拒任',object='',requested='原因'),ms=1,usage={})
        saved=[]
        with patch.object(N,'generate',side_effect=[q,TimeoutError('timed out')]):
            with self.assertRaises(N.ReadError) as error:
                N.read(None,'甲为何拒任',self.context(),lambda r:saved.append(copy.deepcopy(r)))
        report=error.exception.report
        self.assertEqual(report['question'],q)
        self.assertEqual(report['error']['phase'],'judgement')
        self.assertFalse(report['completed'])
        self.assertTrue(any('question' in s and 'error' not in s for s in saved))
        self.assertEqual(saved[-1]['error']['type'],'TimeoutError')

    def test_question_failure_does_not_call_judgement(self):
        with patch.object(N,'generate',side_effect=ValueError('bad response')) as generate:
            with self.assertRaises(N.ReadError) as error:N.read(None,'甲是谁',self.context())
        self.assertEqual(generate.call_count,1)
        self.assertEqual(error.exception.report['phase'],'question')

if __name__=='__main__':unittest.main()
