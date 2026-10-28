import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import evidence_pipeline as E


def chunk(text,cid=1,**kw):
    return dict(chunk_id=cid,page_id=1,book_id='a',book_title='甲史',revision_id=1,
                source_sha256='source',layer='body',heading='传',text_orig=text,
                parts=[dict(block_index=cid,start=0,end=len(text))],**kw)


class Evidence(unittest.TestCase):
    def test_question_slots(self):
        for q,slot in [('甲公什么时候去世','time'),('甲公在哪里即位','place'),('甲公为何辞官','reason'),('甲公在位多少年','quantity')]:
            self.assertIn(slot,E.parse(q)['answer_slots'])

    def test_name_only_not_answer(self):
        c=chunk('甲公来朝。')
        self.assertEqual(E.audit(c,E.parse('甲公何时去世'),[c])['status'],'insufficient')

    def test_other_subject_and_negation(self):
        for t in ('元年，甲公杀乙侯，乙侯卒。','元年，甲公未死。','传言元年甲公卒，实未死。'):
            self.assertEqual(E.audit(chunk(t),E.parse('甲公何时去世'),[])['status'],'insufficient')

    def test_adjacent_date_does_not_answer_event(self):
        c=chunk('甲公卒。');adj=chunk('三年，乙侯来朝。',2)
        self.assertEqual(E.audit(c,E.parse('甲公何时去世'),[c,adj])['status'],'insufficient')

    def test_explicit_date_is_clue_not_truth(self):
        c=chunk('元年，甲公卒。');a=E.audit(c,E.parse('甲公何时去世'),[c])
        self.assertEqual(a['status'],'clues_present');self.assertFalse(a['relation_verified'])

    def test_context_does_not_cross_version_or_layer(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE chunks(chunk_id INTEGER,payload TEXT)')
        center=chunk('甲公卒。',2);other=chunk('注：疑。',1);other['layer']='annotation'
        next=chunk('乙侯来。',3);next['revision_id']=2
        for c in (other,center,next):db.execute('INSERT INTO chunks VALUES(?,?)',(c['chunk_id'],json.dumps(c)))
        self.assertEqual(E.context(db,center),[center])

    def test_unknown_relation_remains_insufficient(self):
        c=chunk('甲公，乙侯之父也。')
        self.assertEqual(E.audit(c,E.parse('甲公与乙侯是什么关系'),[c])['status'],'insufficient')


if __name__=='__main__':unittest.main()
