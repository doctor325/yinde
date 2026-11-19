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

    def test_execution_question_preserves_roles_and_preceding_event(self):
        active = E.parse('司馬某在攻克東城之後，處決了誰？')
        self.assertEqual([clue['text'] for clue in active['clues']], ['司马某', '东城'])
        self.assertEqual(active['answer_slots'], ['object'])
        self.assertEqual(active['execution_event']['role'], 'agent')
        self.assertEqual(active['execution_event']['preceding_event'], dict(action='攻克', object='东城'))
        for query, slot in [('欧阳某什么时候被处死？', 'time'),
                            ('欧阳某被赐死在哪里？', 'place'),
                            ('欧阳某为何被处决？', 'reason')]:
            with self.subTest(query=query):
                plan = E.parse(query)
                self.assertEqual([clue['text'] for clue in plan['clues']], ['欧阳某'])
                self.assertEqual(plan['execution_event']['role'], 'patient')
                self.assertEqual(plan['answer_slots'], [slot])
                self.assertEqual([group['kind'] for group in plan['relation_groups']], ['execution'])
        self.assertNotIn('execution_event', E.parse('欧阳某为何在出征时被处死？'))
        self.assertNotIn('execution_event', E.parse('欧阳某何时去世？'))

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
            CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,page_id INTEGER,search_text TEXT,payload TEXT);
            CREATE VIRTUAL TABLE chunk_fts USING fts5(search_text,tokenize='trigram');''')
        for cid,text,book in [(1,'甲乙丙入东城。','a'),(2,'甲乙丙来朝。','a'),(3,'甲乙丙出兵东城。','b')]:
            c=chunk(text,cid);c['book_id']=book
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',(cid,book,c['page_id'],S.normalize(text),json.dumps(c)))
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
            CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,page_id INTEGER,search_text TEXT,payload TEXT);''')
        chunks=[chunk('元年，甲公卒。',1),chunk('甲公来朝。',2)]
        for c in chunks:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',(c['chunk_id'],c['book_id'],c['page_id'],S.normalize(c['text_orig']),json.dumps(c)))
        return db,chunks

    def test_model_rank_is_distinct_from_final_rank_and_audit_is_not_truth(self):
        db,chunks=self.evaluation_database()
        r=E.evaluate('甲公何时去世',db,[],[],rerank=lambda q,t:dict(scores=[-1.,1.],ms=1))
        rows=r['pipeline']['candidate_diagnostics']
        self.assertEqual(rows[0]['model_rank'],2)
        self.assertEqual(rows[0]['final_rank'],1)
        self.assertTrue(all(not h['evidence_audit']['relation_verified'] for h in r['hits']))
        self.assertEqual(r['candidate_ids'],[1,2])

    def test_biography_context_recall_keeps_source_and_office_scope(self):
        db,_=self.evaluation_database()
        anchor={**chunk('司马某，字子长。'), 'heading':'司馬某'}
        rows=[anchor,
              {**anchor, **dict(chunk_id=10,text_orig='任彭泽令。')},
              {**anchor, **dict(chunk_id=11,text_orig='遂解印去县。')},
              {**anchor, **dict(chunk_id=12,text_orig='彭泽令请致仕。',revision_id=2)},
              {**anchor, **dict(chunk_id=13,text_orig='彭泽令请致仕。',layer='annotation')},
              {**anchor, **dict(chunk_id=14,text_orig='彭泽令请致仕。',heading='他人')},
              {**anchor, **dict(chunk_id=15,text_orig='彭泽令请致仕。',source_sha256='other')},
              {**anchor, **dict(chunk_id=16,text_orig='任州祭酒，自解归。')}]
        for c in rows[1:]:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',
                       (c['chunk_id'],c['book_id'],c['page_id'],S.normalize(c['text_orig']),json.dumps(c)))
        plan=E.parse('司马某为什么辞去彭泽令？')
        hits,scan=E.biography_context_candidates(db,plan,[dict(chunk=anchor)])
        self.assertEqual([h['chunk']['chunk_id'] for h in hits],[11])
        self.assertEqual(scan['pages'],1)
        self.assertEqual(E.audit(hits[0]['chunk'],plan,[])['status'],'insufficient')
        self.assertEqual(E.biography_context_candidates(db,plan,[dict(chunk=anchor)],book='other')[0],[])

    def test_execution_recall_admits_short_name_only_in_matching_source_and_event(self):
        db, _ = self.evaluation_database()
        anchor = {**chunk('某，字公明。', 10), 'heading': '司馬某', 'page_id': 2}
        bodies = [
            anchor,
            dict(anchor, chunk_id=11, text_orig='某悉兵攻東城。'),
            dict(anchor, chunk_id=12, text_orig='某遂拔之，誅其將。'),
            dict(anchor, chunk_id=13, text_orig='東城亦誅其民。', revision_id=2),
            dict(anchor, chunk_id=14, text_orig='東城伏誅。', layer='annotation'),
            dict(anchor, chunk_id=15, text_orig='某居西城，誅降者。'),
            dict(anchor, chunk_id=16, text_orig='某坐誅，年六十。'),
            dict(anchor, chunk_id=17, text_orig='東城伏誅。', heading='他人'),
            dict(anchor, chunk_id=18, text_orig='東城伏誅。', source_sha256='other'),
        ]
        for c in bodies:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',
                       (c['chunk_id'], c['book_id'], c['page_id'], S.normalize(c['text_orig']), json.dumps(c)))
        rerank = lambda query, texts: dict(scores=[0.0] * len(texts), ms=0)
        for query, expected in [('司马某攻破东城后诛杀了谁？', 12),
                                ('司马某何时被处死？', 16)]:
            with self.subTest(query=query):
                result = E.evaluate(query, db, [], [], book='a', rerank=rerank)
                self.assertEqual(result['candidate_ids'], [expected])
                self.assertEqual(result['pipeline']['admission']['biography_context']['admitted'], 1)
                self.assertEqual(result['hits'][0]['evidence_audit']['status'], 'insufficient')
                self.assertEqual(E.evaluate(query, db, [], [], book='b', rerank=rerank)['candidate_ids'], [])

    def test_advocacy_recall_keeps_the_proposed_action_and_its_speaker(self):
        db, _ = self.evaluation_database()
        anchor = {**chunk('欧阳甲，字景明。', 10), 'heading': '歐陽甲', 'page_id': 2}
        other = dict(anchor, heading='司馬乙', page_id=3)
        bodies = [
            anchor,
            dict(anchor, chunk_id=11, text_orig='自討南城，軍不利。甲以久則為患，堅請討伐。'),
            dict(anchor, chunk_id=12, text_orig='自討南城，軍不利。甲以久則為患，堅請討伐北城。'),
            dict(anchor, chunk_id=13, text_orig='自討南城，軍不利。甲以久則為患，未堅請討伐。'),
            dict(anchor, chunk_id=14, text_orig='自討南城，軍不利。王壬奏曰：「以其多患，請討南城。」'),
            dict(anchor, chunk_id=15, text_orig='甲以南城有患，請討南城。', source_sha256='other'),
            dict(other, chunk_id=20, text_orig='司馬乙，字景文。'),
            dict(other, chunk_id=21, text_orig='乙奏曰：\n\n臣以邊民困乏，故曰轉輸不可久。'),
            dict(other, chunk_id=22, text_orig='臣以邊民困乏。今留步士二千屯田，則可省轉輸。'),
            dict(other, chunk_id=24, text_orig='王壬奏曰：\n\n臣以邊民困乏。'),
            dict(other, chunk_id=25, text_orig='臣以邊民困乏。今留步士二千屯田，則可省轉輸。'),
        ]
        for c in bodies:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',
                       (c['chunk_id'], c['book_id'], c['page_id'], S.normalize(c['text_orig']), json.dumps(c)))
        cases = [
            ('欧阳甲为何坚持讨伐南城？', dict(stance='坚持', action='讨伐南城', predicate='讨伐', target='南城'), 11),
            ('司马乙为什么主张留下步兵屯田？', dict(stance='主张', action='留下步兵屯田', topic='屯田', participants='步兵'), 22),
        ]
        for query, fields, expected in cases:
            with self.subTest(query=query):
                plan = E.parse(query)
                self.assertEqual(plan['answer_slots'], ['reason'])
                for key, value in fields.items():
                    self.assertEqual(plan['advocacy_event'][key], value)
                result = E.evaluate(query, db, [], [], rerank=lambda q, ts: dict(scores=[0.0] * len(ts), ms=0))
                hits = [h for h in result['hits'] if 'biography_context' in h['channels']]
                self.assertEqual([h['chunk']['chunk_id'] for h in hits], [expected])
                self.assertEqual(hits[0]['evidence_audit']['status'], 'insufficient')
        self.assertNotIn('advocacy_event', E.parse('欧阳甲为何离开南城？'))

    def test_invalid_model_output_is_rejected(self):
        db,_=self.evaluation_database()
        for scores in ([1.],[1.,float('nan')],['1','2']):
            with self.subTest(scores=scores), self.assertRaises(ValueError):
                E.evaluate('甲公何时去世',db,[],[],rerank=lambda q,t:dict(scores=scores,ms=1))

    def test_event_detail_must_belong_to_the_asked_event(self):
        cases = [
            ('司马甲攻破东城后诛杀了谁？', '传', '司马甲克东城，斩王壬。', '王壬',
             ('司马甲克西城，斩王壬。', '司马甲克东城，司马乙斩王壬。', '司马甲克东城，斩之。')),
            ('司马甲在哪里被燕军俘虏？', '司马甲', '甲败走至石门，为燕军所擒。', '石门',
             ('甲败走至石门，为秦军所擒。', '甲至石门。乙为燕军所擒。', '甲至石门，马奔北关，为燕军所擒。')),
            ('司马甲何时被处死？', '永兴三年', '秋八月癸亥，杀司马甲。', '秋八月癸亥',
             ('秋八月癸亥，杀司马乙。司马甲卒。', '秋八月癸亥，有言曰：「永兴三年，诛司马甲。」')),
        ]
        for query, heading, positive, detail, negatives in cases:
            with self.subTest(query=query):
                plan = E.parse(query)
                body = dict(chunk(positive), heading=heading)
                result = E.audit(body, plan, [])
                self.assertEqual(result['status'], 'clues_present')
                record = result['attached_evidence'][0]
                quote = record['detail']
                self.assertEqual(positive[quote['start']:quote['end']], detail)
                for text in negatives:
                    self.assertEqual(E.audit(dict(body, text_orig=text), plan, [])['status'], 'insufficient', text)
                self.assertEqual(E.audit(dict(body, layer='annotation'), plan, [])['status'], 'insufficient')
        self.assertEqual(E.audit(chunk('三年八月，遂磔司马甲于市。'), E.parse('司马甲何时被处死？'), [])['status'],
                         'insufficient')

    def test_explicit_death_needs_no_whole_page_query(self):
        db,_=self.evaluation_database()
        statements=[]
        db.set_trace_callback(statements.append)
        E.evaluate('甲公何时去世',db,[],[],rerank=lambda q,t:dict(scores=[0.,0.],ms=1))
        self.assertFalse(any('WHERE page_id=' in sql for sql in statements))


if __name__=='__main__':unittest.main()
