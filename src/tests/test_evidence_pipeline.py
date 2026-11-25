import json
from pathlib import Path
import re
import sqlite3
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import evidence_pipeline as E, punctuated_search as S


def chunk(text,cid=1,**kw):
    return dict(chunk_id=cid,page_id=1,book_id='a',book_title='甲史',revision_id=1,
                source_sha256='source',layer='body',heading='传',text_orig=text,
                parts=[dict(block_index=cid,start=0,end=len(text))],**kw)


class Evidence(unittest.TestCase):
    def test_activity_separates_person_event_and_question(self):
        cases = [('甲湖报捷那会儿，王某正忙什么？', '王某', '甲湖', '报捷', '忙什么'),
                 ('东门交战的时候，欧阳某在做什么？', '欧阳某', '东门', '交战', '做什么'),
                 ('孙某刚在西城起兵那会儿在干什么？', '孙某', '西城', '起兵', '干什么'),
                 ('西城被围那会儿，王某在干什么？', '王某', '西城', '被围', '干什么')]
        for query, person, anchor, trigger_action, ask in cases:
            with self.subTest(query=query):
                plan = E.parse(query)
                frame = plan['activity_question']
                self.assertEqual(plan['subject'][0]['text'], person)
                self.assertEqual(plan['constraints'][0]['text'], anchor)
                self.assertEqual((frame['trigger_action'], frame['asked_activity']), (trigger_action, ask))
        self.assertNotIn('activity_question', E.parse('甲湖报捷那会儿，王某在哪里？'))
        original = E.parse('王某接到战报时在做什么')['activity_question']
        self.assertEqual(original['kind'], 'receipt')
        self.assertNotIn('anchor', original)

    def test_activity_admits_scoped_short_names_but_keeps_audit_unconfirmed(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('''CREATE TABLE books(book_id TEXT,title TEXT);
            INSERT INTO books VALUES('a','甲史');
            CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,page_id INTEGER,search_text TEXT,payload TEXT);
            CREATE VIRTUAL TABLE chunk_fts USING fts5(search_text,tokenize='trigram');''')
        passages = [(1, '王某，字敬之。', '王某', 'body'),
                    (2, '贼既破，有驿书至，某方与客饮。', '王某', 'body'),
                    (3, '有驿书至，某命使还。', '王某', 'body'),
                    (4, '甲湖报捷，刘某方与客饮。', '刘某', 'body'),
                    (5, '甲湖报捷，王某方与客饮。', '校勘记', 'body'),
                    (6, '甲湖报捷，王某方与客饮。', '王某', 'annotation')]
        for cid, source, heading, layer in passages:
            c = chunk(source, cid)
            c.update(heading=heading, layer=layer)
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)', (cid, 'a', 1, S.normalize(source), json.dumps(c)))
            db.execute('INSERT INTO chunk_fts VALUES(?)', (S.normalize(source),))

        with patch('search.biography_lookup.resolved', return_value=[]):
            result = E.evaluate('甲湖报捷那会儿，王某正忙什么？', db, [], [], book='a',
                                rerank=lambda query, texts: dict(scores=[0] * len(texts), ms=0))
        self.assertEqual(result['pipeline']['candidate_scan']['scanned'], 3)
        activity = [d['chunk_id'] for d in result['pipeline']['candidate_diagnostics']
                    if 'activity_trigger' in d['channel_ranks']]
        self.assertEqual(activity, [2])
        hit = next(h for h in result['hits'] if h['chunk']['chunk_id'] == 2)
        self.assertEqual(hit['evidence_audit']['status'], 'insufficient')

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

    def test_semantic_relation_overlap_backfills_distinct_candidates(self):
        dense = [dict(chunk=chunk('候选', i), semantic_score=.5) for i in range(1, 61)]
        relation = [*dense[4:24], *dense[:4], *dense[24:]]
        lexical = [dict(chunk=chunk('字面候选', 26))]
        hits = E.admit((('semantic', dense), ('lexical', lexical), ('relation', relation)))
        ids = [h['chunk']['chunk_id'] for h in hits]
        extra = list(range(25, 42))
        extra.remove(26)  # Already in the lexical channel's first 20.
        self.assertEqual(ids, [*range(1, 21), 26, *range(21, 25), *extra])
        self.assertEqual(hits[-1]['channel_ranks'], {'semantic': 41, 'relation': 41})
        self.assertEqual(hits[-1]['semantic_score'], .5)

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

    def test_reference_requires_a_citation_or_comparison_at_the_match(self):
        phrase = '甲公破敌于城'
        for text in (
            '故史書曰：《紀》書「甲公破敵于城」，以勸來者。',
            '乙君問曰：「甲公破敵于城，果有之邪？」',
            '乙君責臣曰：『此過何用愈甲公破敵于城？',
        ):
            with self.subTest(text=text):
                self.assertTrue(E._quoted_reference(phrase, text))
        for text in (
            '卒曰：「甲公破敵于城，臣親見之。」',
            '乙君曰：「甲公破敵于城。另事果有之邪？」',
            '《紀》書「甲公破敵于城」。甲公破敵于城。',
            '《紀》書『甲公破敵于城」。',
        ):
            with self.subTest(text=text):
                self.assertFalse(E._quoted_reference(phrase, text))

    def test_source_order_only_swaps_supported_competitors_within_the_same_tier(self):
        query = '甲公破敌于城'
        plan = dict(unsupported=True, answer_slots=[])
        cited = dict(chunk=chunk('乙君曰：「何用愈甲公破敵于城？」'), channels=['lexical'],
                     context_relevance_score=5, tier=0)
        direct = dict(chunk=chunk('甲公將兵攻城，敵軍敗走。', 2), channels=['semantic'],
                      context_relevance_score=4.7, tier=0)
        unrelated = dict(chunk=chunk('其他', 3), channels=['semantic'], context_relevance_score=1, tier=0)

        def priority(hit):
            return hit['tier']

        hits = [cited, direct, unrelated]
        decision = E._order_by_source(query, plan, hits, priority)
        self.assertEqual(decision, dict(narrative_chunk_id=2, reference_chunk_id=1))
        self.assertEqual(hits, [direct, cited, unrelated])
        for candidate in (
            {**direct, 'tier': 1},
            {**direct, 'context_relevance_score': 3},
            {**direct, 'channels': ['relation']},
            {**direct, 'chunk': chunk('乙君曰：「甲公將兵攻城，敵軍敗走。」', 2)},
            {**direct, 'chunk': chunk('甲公入朝。\n\n敵兵圍城。', 2)},
        ):
            with self.subTest(candidate=candidate):
                hits = [cited, candidate, unrelated]
                self.assertIsNone(E._order_by_source(query, plan, hits, priority))
                self.assertEqual(hits, [cited, candidate, unrelated])

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

    def test_colloquial_advocacy_keeps_roles_and_background_separate(self):
        examples = (
            ('边境告急后，王甲为什么劝晋王别派兵打西秦？',
             dict(person='王甲', recipient='晋王', target='西秦', stance='劝止', background='边境告急后')),
            ('李乙为什么建议趁早攻打西秦，别拖到冬天？',
             dict(person='李乙', target='西秦', stance='主张及早', deadline='冬天')),
            ('北城兵败后，李乙为什么不愿把都城搬到北边？',
             dict(person='李乙', target='北边', stance='反对')),
            ('王甲不让儿子当官，是因为怕朝廷迁走吗？',
             dict(person='王甲', target='儿子', reason_hypothesis='怕朝廷迁走')),
        )
        for query, expected in examples:
            with self.subTest(query=query):
                plan = E.parse(query)
                self.assertEqual(plan['subject'][0]['text'], expected['person'])
                self.assertEqual(plan['answer_slots'], ['reason'])
                for key, value in expected.items():
                    self.assertEqual(plan['colloquial_advocacy'][key], value)
        self.assertNotIn('colloquial_advocacy', E.parse('王甲为什么要离任？'))

    def test_colloquial_advocacy_recalls_speech_without_confirming_its_roles(self):
        db, _ = self.evaluation_database()
        anchor = dict(chunk('司马甲，字文恭。', 10), heading='司马甲', page_id=2)
        bodies = (
            anchor,
            dict(anchor, chunk_id=11, text_orig='晋王欲出兵攻西秦，甲进谏曰：「今边郡困乏，难于动兵。'),
            dict(anchor, chunk_id=12, text_orig='难于动兵。愿王察之。」王乃止。'),
            dict(anchor, chunk_id=13, text_orig='又谏出兵，边郡困乏。', revision_id=3),
            dict(anchor, chunk_id=14, text_orig='甲与客论盐铁。'),
            dict(anchor, chunk_id=15, text_orig='甲进谏曰：「今攻东夷，难于动兵。」'),
        )
        db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?)',
                       [(c['chunk_id'], c['book_id'], c['page_id'], S.normalize(c['text_orig']), json.dumps(c))
                        for c in bodies])
        query = '司马甲为什么劝晋王别派兵打西秦？'
        result = E.evaluate(query, db, [], [], rerank=lambda q, ts: dict(scores=[0.0] * len(ts), ms=0))
        hits = [h for h in result['hits'] if 'biography_context' in h['channels']]
        self.assertEqual([h['chunk']['chunk_id'] for h in hits], [11, 12])
        reversed_plan = E.parse('晋王为什么劝司马甲别派兵打西秦？')
        self.assertEqual(E.audit(bodies[1], reversed_plan, list(bodies))['status'], 'insufficient')

    def test_colloquial_tax_reform_requires_a_source_bound_explanation(self):
        plan = E.parse('司马甲为什么觉得旧的收税制度应该改？')
        body = dict(chunk('科斂新舊仍積，百姓殘瘁。甲疾其敝，乃請作新稅法以一其名。'), heading='司马甲')
        finding = E.audit(body, plan, [])['attached_evidence'][0]
        self.assertEqual(finding['detail']['quote'], '科斂新舊仍積，百姓殘瘁')
        self.assertEqual(finding['action']['quote'], '請作新稅法以一其名')
        for text in (
            '科斂新舊仍積，百姓殘瘁。乙疾其敝，乃請作新稅法以一其名。',
            '科斂新舊仍積，百姓殘瘁。甲因病請退。甲疾其敝，乃請作新稅法以一其名。',
            '王乙曰：「科斂新舊仍積，百姓殘瘁。甲疾其敝，乃請作新稅法以一其名。」',
        ):
            self.assertEqual(E.audit(dict(body, text_orig=text), plan, [])['status'], 'insufficient', text)

    def test_directional_campaign_recall_requires_the_same_continued_expedition(self):
        plan = E.parse('司马甲为什么劝晋王别派兵打西秦？')
        topic = re.compile('西秦')
        proposal = '其年，晋王欲西讨，甲谏曰：「战必甚难，'
        reply = '请更议之。」王曰：「吾计已定。」'
        continued = '及西讨，前军围城。敌主苏丙率其师东行。'

        def episode(after=continued):
            prose = proposal + reply + '\n\n' + after
            left_end = len(proposal)
            right_start = left_end + len(reply)
            return {cid: dict(chunk(prose[lo:hi], cid), heading='司马甲',
                              parts=[dict(block_index=1, start=lo, end=hi)])
                    for cid, lo, hi in ((10, 0, left_end), (11, left_end - 8, right_start + 5),
                                        (12, right_start, len(prose)))}

        direction_context = E.colloquial_advocacy.directional_context
        peers = episode()
        context = direction_context(peers, 11, '司马甲', topic)
        self.assertIn('晋王欲西讨，甲谏曰', context)
        self.assertIn('及西讨，前军围城。敌主苏丙', context)
        self.assertEqual(E.audit(peers[11], plan, list(peers.values()))['status'], 'insufficient')
        for after in ('及东征，前军围城。敌主苏丙率其师东行。',
                      '及西讨，前军围城。三年，又欲攻城。敌主苏丙率师东行。',
                      '及西讨，前军围城。客曰：「敌主苏丙率师东行。」'):
            self.assertIsNone(direction_context(episode(after), 11, '司马甲', topic), after)
        peers[12]['revision_id'] = 2
        self.assertIsNone(direction_context(peers, 11, '司马甲', topic))

    def test_military_dissuasion_requires_a_continuous_named_speaker_and_recipient(self):
        plan = E.parse('司马甲为什么劝晋王别派兵打西秦？')
        opening = '晋王与群臣议，欲出兵击西秦。甲上书谏曰：「臣闻兵不可轻举。'
        detail = '今边民困乏，屯粮无继，难于动兵'
        continuation = '兵不可轻举。' + detail + '。」王从之。'
        offset = len(opening) - len('兵不可轻举。')
        previous = dict(chunk(opening, 10), heading='司马甲',
                        parts=[dict(block_index=1, start=0, end=len(opening))])

        def body(text):
            return dict(previous, chunk_id=11, text_orig=text,
                        parts=[dict(block_index=1, start=offset, end=offset + len(text))])

        current = body(continuation)
        record = E.audit(current, plan, [previous])['attached_evidence'][0]
        self.assertEqual(record['speaker']['quote'], '甲上书谏曰')
        self.assertEqual(record['recipient']['quote'], '晋王')
        self.assertEqual(record['action']['quote'], '欲出兵击西秦')
        self.assertEqual(record['detail']['quote'], detail)
        self.assertEqual([record[key]['chunk_id'] for key in ('speaker','action','detail')], [10, 10, 11])
        for altered in (
            body('兵不可轻举。」李乙曰：「' + detail + '。」'),
            body('兵不可轻举。李乙曰：' + detail + '。」'),
            body('兵可暂且不议。' + detail + '。」'),
            dict(current, source_sha256='different'),
        ):
            self.assertEqual(E.audit(altered, plan, [previous])['status'], 'insufficient')
        for query in ('晋王为什么劝司马甲别出兵？', '晋王为什么劝司马甲别派兵打西秦？',
                      '司马甲为什么劝梁王别派兵打西秦？', '司马甲为什么劝晋王别派兵打东齐？'):
            self.assertEqual(E.audit(current, E.parse(query), [previous])['status'], 'insufficient', query)

    def test_advocacy_reason_must_belong_to_the_asked_action_and_speaker(self):
        campaign = E.parse('司马甲为何坚持讨伐南城？')
        body = dict(chunk('自讨南城，官军屡败。众以转输不逮，请罢兵。甲以贼患日深，终为大患。遂坚请讨伐。'),
                    heading='司马甲')
        result = E.audit(body, campaign, [])
        self.assertEqual(result['status'], 'clues_present')
        clue = result['attached_evidence'][0]
        self.assertEqual(clue['detail']['quote'], '贼患日深，终为大患')
        self.assertEqual(clue['action_context']['quote'], '讨南城')
        self.assertEqual(clue['attribution'], 'narrator_explanation')
        for text in (
            '自讨南城，官军屡败。众以转输不逮，遂请讨南城。',
            '自讨南城，官军屡败。甲以贼患日深，遂坚请讨北城。',
            '自讨南城，官军屡败。又讨北城，甲以贼患日深，遂坚请讨伐。',
            '自讨南城，官军屡败。王壬曰：「甲以贼患日深，遂坚请讨伐。」',
            '自讨南城，官军屡败。或云，甲以贼患日深，遂坚请讨伐。',
        ):
            self.assertEqual(E.audit(dict(body, text_orig=text), campaign, [])['status'], 'insufficient', text)

        farming = E.parse('司马甲为何主张留下步兵屯田？')
        anchor = dict(chunk('王问曰：「何如？」甲奏曰：\n\n臣闻治边务省费。', 20), heading='司马甲')
        proposal = dict(anchor, chunk_id=21, text_orig='今留步士万人屯田。臣愚以为屯田内有省费之利，外有守城之备。')
        finding = E.audit(proposal, farming, [anchor])['attached_evidence'][0]
        self.assertEqual(finding['speaker']['quote'], '甲奏曰：')
        self.assertEqual(finding['detail']['quote'], '屯田内有省费之利，外有守城之备')
        self.assertEqual(finding['attribution'], 'own_statement')
        for prev in (dict(anchor, revision_id=3), dict(anchor, text_orig='乙奏曰：臣闻治边务省费。')):
            self.assertEqual(E.audit(proposal, farming, [prev])['status'], 'insufficient')
        change = dict(proposal, text_orig=proposal['text_orig'].replace('。臣', '。复议留骑兵输粮。臣'))
        self.assertEqual(E.audit(change, farming, [anchor])['status'], 'insufficient')

    def test_speech_reason_only_applies_to_its_request(self):
        reason = '秦兵善战，且舟师涉水，进难而退易'
        action = '臣请为前锋以御敌'
        speech = f'司马甲谏曰：「{reason}，{action}，陛下宜驻北城。」'
        plan = E.parse('司马甲为何建议为前锋？')
        evidence = E.audit(chunk(speech), plan, [])['attached_evidence'][0]
        self.assertEqual(evidence['speaker']['quote'], '司马甲谏曰：')
        self.assertEqual(evidence['detail']['quote'], reason)
        self.assertEqual(evidence['action']['quote'], action)
        for changed in (
            speech.replace('司马甲谏', '司马乙谏'),
            f'王丙曰：「据闻{speech}」',
            speech.replace('，臣请', '。」乙曰：「臣请'),
            speech.replace('，臣请', '，臣请先议粮道；臣请'),
            speech.replace('以御敌', '以报私恩'),
        ):
            self.assertEqual(E.audit(chunk(changed), plan, [])['status'], 'insufficient', changed)
        for query in ('司马甲为何建议驻北城？', '司马乙为何建议为前锋？'):
            self.assertEqual(E.audit(chunk(speech), E.parse(query), [])['status'], 'insufficient', query)

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

    def test_dated_execution_is_admitted_beyond_the_name_channel_quota(self):
        db, _ = self.evaluation_database()
        clues = [chunk('王甲为将军。', number) for number in range(10, 36)]
        clues.extend([chunk('汉五年为郎中，后诛王甲。', 38), chunk('永兴三年，诛王甲。', 40)])
        db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?)',
                       [(c['chunk_id'], c['book_id'], c['page_id'], S.normalize(c['text_orig']), json.dumps(c))
                        for c in clues])
        result = E.evaluate('王甲何时被处死？', db, [], [],
                            rerank=lambda q, ts: dict(scores=[0.] * len(ts), ms=0))
        hit = result['hits'][0]
        self.assertEqual(hit['chunk']['chunk_id'], 40)
        self.assertIn('attached_event', hit['channels'])
        self.assertGreater(hit['channel_ranks']['relation'], E.CHANNEL_LIMIT)
        self.assertEqual(hit['evidence_audit']['attached_evidence'][0]['detail']['quote'], '永兴三年')

    def test_capture_short_name_requires_a_continuous_source_and_patient_role(self):
        db, _ = self.evaluation_database()
        anchor = dict(chunk('乃送王甲还北。甲乃即位。', 10), heading='李乙')
        episode = [
            anchor,
            dict(anchor, chunk_id=11, text_orig='甲与敌战。李丙走至北关，为甲所擒。'),
            dict(anchor, chunk_id=12, text_orig='甲大败，走至石门，遇贼被擒。'),
            dict(anchor, chunk_id=13, text_orig='甲走至西门，被擒。', revision_id=2),
            dict(anchor, chunk_id=14, text_orig='甲走至南关，被擒。'),
            dict(anchor, chunk_id=20, text_orig='送王甲还北。甲乃即位。'),
            dict(anchor, chunk_id=21, text_orig='遂迎钱甲还朝。甲乃领兵。甲走至新城，被擒。'),
            dict(anchor, chunk_id=22, text_orig='甲走至东关，被擒。'),
        ]
        db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?)',
                       [(c['chunk_id'], c['book_id'], c['page_id'], S.normalize(c['text_orig']), json.dumps(c))
                        for c in episode])
        self.assertEqual(E.audit(episode[1], E.parse('王甲在哪里被俘虏？'), [])['status'], 'insufficient')
        result = E.evaluate('王甲在哪里被俘虏？', db, [], [],
                            rerank=lambda q, ts: dict(scores=[0.] * len(ts), ms=0))
        attached = [hit for hit in result['hits'] if 'attached_event' in hit['channels']]
        self.assertEqual([hit['chunk']['chunk_id'] for hit in attached], [12])
        clue = attached[0]['evidence_audit']['attached_evidence'][0]
        self.assertEqual(clue['subject_anchor']['quote'], '王甲还北。甲')
        self.assertEqual(clue['detail']['quote'], '石门')


if __name__=='__main__':unittest.main()
