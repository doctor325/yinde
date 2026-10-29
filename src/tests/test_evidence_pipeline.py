import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import evidence_pipeline as E, punctuated_search as S


def chunk(text,cid=1,**kw):
    return dict(chunk_id=cid,page_id=1,book_id='a',book_title='甲史',revision_id=1,
                source_sha256='source',layer='body',heading='传',text_orig=text,
                parts=[dict(block_index=cid,start=0,end=len(text))],**kw)


class Evidence(unittest.TestCase):
    def test_question_slots(self):
        for q,slot in [('甲公什么时候去世','time'),('甲公在哪里即位','place'),('甲公为何辞官','reason'),('甲公在位多少年','quantity')]:
            self.assertIn(slot,E.parse(q)['answer_slots'])

    def test_source_location_is_not_event_location(self):
        for noun in ('史料','记载','文献','证据'):
            self.assertNotIn('place',E.parse('甲公担任司业的'+noun+'在哪里？')['answer_slots'])
        self.assertIn('place',E.parse('甲公在哪里即位的记载？')['answer_slots'])

    def test_each_channel_keeps_its_twentieth_candidate(self):
        channels=[(name,[dict(chunk=chunk('候选',start+i)) for i in range(35)])
                  for name,start in [('semantic',1),('lexical',101),('relation',201),('grounded',301)]]
        hits=E.admit(channels)
        self.assertEqual(len(hits),80)
        ids={h['chunk']['chunk_id'] for h in hits}
        for last in (20,120,220,320):self.assertIn(last,ids)
        for excluded in (21,121,221,321):self.assertNotIn(excluded,ids)

    def test_overlap_does_not_steal_quota_and_provenance_is_complete(self):
        dense=[dict(chunk=chunk('候选',i),semantic_score=.5) for i in range(1,41)]
        lexical=[dict(chunk=chunk('候选',i)) for i in range(15,45)]
        hits=E.admit([('semantic',dense),('lexical',lexical)])
        self.assertEqual(len(hits),34)
        both=next(h for h in hits if h['chunk']['chunk_id']==34)
        self.assertEqual(both['channel_ranks'],{'semantic':34,'lexical':20})
        self.assertEqual(both['semantic_score'],.5)

    def test_grounded_prefix_is_scoped_and_does_not_become_identity(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('''CREATE TABLE books(book_id TEXT,title TEXT);
            INSERT INTO books VALUES('a','甲史'),('b','乙史');
            CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,search_text TEXT,payload TEXT);
            CREATE VIRTUAL TABLE chunk_fts USING fts5(search_text,tokenize='trigram');''')
        for cid,text,book in [(1,'甲乙丙入东城。','a'),(2,'甲乙丙来朝。','a'),(3,'甲乙丙出兵东城。','b')]:
            c=chunk(text,cid);c['book_id']=book
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)',(cid,book,S.normalize(text),json.dumps(c)))
            db.execute('INSERT INTO chunk_fts(rowid,search_text) VALUES(?,?)',(cid,S.normalize(text)))
        p=E.parse('甲乙丙出兵东城的史料有哪些？')
        original=json.dumps(p)
        hits,report=E.grounded_fallback(db,p,[],book='a')
        self.assertEqual(report['hypothesis'],'甲乙丙')
        self.assertFalse(report['identity_verified'])
        self.assertEqual([h['chunk']['chunk_id'] for h in hits],[1,2])
        self.assertEqual(json.dumps(p),original)
        self.assertTrue(p['unsupported'])
        self.assertEqual(E.grounded_fallback(db,p,hits,book='a')[0],[])

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

    def test_focus_selects_event_and_keeps_exact_source_offsets(self):
        c=chunk('甲公来朝，乙侯卒。\n元年，甲公卒。丙伯为相。')
        f=E.focus(c,E.parse('甲公何时去世'))
        self.assertTrue(f['selected'])
        self.assertEqual(f['text'],'元年，甲公卒。')
        self.assertEqual(f['text'],c['text_orig'][f['start']:f['end']])

    def test_focus_does_not_remove_negation_or_reports(self):
        for text in ('传言元年甲公卒，实未死。','元年，甲公未死。'):
            c=chunk(text);f=E.focus(c,E.parse('甲公何时去世'))
            self.assertEqual(f['text'],text)
            self.assertEqual(E.audit(c,E.parse('甲公何时去世'),[])['status'],'insufficient')

    def test_unparsed_focus_keeps_candidate_instead_of_inventing_evidence(self):
        c=chunk('甲公来朝。乙侯卒。')
        self.assertEqual(E.focus(c,E.parse('甲公有什么贡献'))['text'],c['text_orig'])

    def evaluation_database(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('''CREATE TABLE books(book_id TEXT,title TEXT);
            CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,search_text TEXT,payload TEXT);''')
        chunks=[chunk('元年，甲公卒。',1),chunk('甲公来朝。',2)]
        for c in chunks:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)',(c['chunk_id'],c['book_id'],S.normalize(c['text_orig']),json.dumps(c)))
        return db,chunks

    def test_model_rank_is_distinct_from_final_rank_and_audit_is_not_truth(self):
        db,chunks=self.evaluation_database()
        r=E.evaluate('甲公何时去世',db,[],[],rerank=lambda q,t:dict(scores=[-1.,1.],ms=1))
        rows=r['pipeline']['candidate_diagnostics']
        self.assertEqual(rows[0]['model_rank'],2)
        self.assertEqual(rows[0]['final_rank'],1)
        self.assertTrue(all(not h['evidence_audit']['relation_verified'] for h in r['hits']))
        self.assertEqual(r['candidate_ids'],[1,2])

    def test_invalid_model_output_is_rejected(self):
        db,_=self.evaluation_database()
        for scores in ([1.],[1.,float('nan')],['1','2']):
            with self.subTest(scores=scores), self.assertRaises(ValueError):
                E.evaluate('甲公何时去世',db,[],[],rerank=lambda q,t:dict(scores=scores,ms=1))


if __name__=='__main__':unittest.main()
