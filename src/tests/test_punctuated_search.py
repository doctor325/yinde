"""Independent edition identity, literal retrieval and hard source boundaries."""
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import punctuated_search as S


class Search(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE books(book_id TEXT PRIMARY KEY,title TEXT);
          CREATE TABLE chapters(page_id INTEGER,book_id TEXT,title TEXT,status TEXT,reason TEXT,blocks_json TEXT,
           revision_id INTEGER,source_url TEXT,history_url TEXT,license_url TEXT,attribution TEXT,source_sha256 TEXT);
          INSERT INTO books VALUES('a','甲史'),('b','乙史');''')
        self.blocks=[{'kind':'body','text':'甲乙丙，遷於東都。'},
                     {'kind':'annotation','text':'注：丁戊己。'},
                     {'kind':'body','text':'庚辛'}, {'kind':'image','data':'data:image/png;base64,AA=='},
                     {'kind':'body','text':'壬癸。'}, {'kind':'table','tag':'td'},
                     {'kind':'body','text':'子丑寅。'}, {'kind':'table','tag':'td','closing':True},
                     {'kind':'body','text':'卯辰巳。'}, {'kind':'heading','text':'次章'},
                     {'kind':'body','text':'天地玄黃，宇宙洪荒。'*60}]
        self.db.execute('INSERT INTO chapters VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (1,'a','甲史/卷1','partial_render','源标记',json.dumps(self.blocks),10,'source','history','license','credit','hash'))
        self.chunks=list(S.iter_chunks(self.db))
        self.db.executescript('''CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,page_id INTEGER,search_text TEXT,payload TEXT);
           CREATE VIRTUAL TABLE chunk_fts USING fts5(search_text,content='chunks',content_rowid='chunk_id',tokenize='trigram');''')
        for c in self.chunks:self.db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',
            (c['chunk_id'],c['book_id'],c['page_id'],S.normalize(c['text_orig']),json.dumps(c)))
        self.db.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")

    def test_source_parts_exact_and_all_text_covered(self):
        coverage={i:set() for i,b in enumerate(self.blocks) if b['kind'] in ('body','annotation')}
        for c in self.chunks:
            self.assertEqual(c['text_orig'],''.join(self.blocks[p['block_index']]['text'][p['start']:p['end']] for p in c['parts']))
            self.assertEqual(c['source_sha256'],'hash');self.assertEqual(c['revision_id'],10)
            self.assertEqual(c['status'],'partial_render')
            self.assertLessEqual(len(c['text_orig']),S.MAX_CHARS)
            for p in c['parts']:coverage[p['block_index']].update(range(p['start'],p['end']))
        for i,covered in coverage.items():self.assertEqual(covered,set(range(len(self.blocks[i]['text']))))

    def test_annotation_image_and_table_are_boundaries(self):
        for query in ('東都注','己庚辛','庚辛壬癸','子丑寅卯辰巳'):
            self.assertEqual(S.lexical(self.db,query)['total'],0,query)
        note=S.lexical(self.db,'丁戊己')['hits'][0]['chunk']
        self.assertEqual(note['layer'],'annotation')

    def test_script_punctuation_and_conjunction(self):
        a=S.lexical(self.db,'甲乙丙，迁于东都。')
        b=S.lexical(self.db,'甲乙丙 遷於東都')
        self.assertEqual([h['chunk']['chunk_id'] for h in a['hits']],[h['chunk']['chunk_id'] for h in b['hits']])
        self.assertTrue(a['total'])
        self.assertEqual(a['hits'][0]['chunk']['text_orig'],'甲乙丙，遷於東都。')
        self.assertGreater(S.lexical(self.db,'東都')['total'],0)
        self.assertGreater(S.lexical(self.db,'遷')['total'],0)

    def test_book_filter_and_missing_edition(self):
        self.assertEqual(S.lexical(self.db,'甲乙丙','b')['total'],0)
        self.assertFalse(S.lexical(self.db,'甲乙丙','uncollected')['book_available'])
        self.assertTrue(S.lexical(self.db,'甲乙丙','a')['book_available'])

    def test_grounding_probe_preserves_existence_without_counting_every_hit(self):
        for query in ('甲乙丙','东都','天地','不存在','迁于东都','甲乙丙 东都'):
            for book in (None,'a','b','missing'):
                full=S.lexical(self.db,query,book)
                probe=S.lexical(self.db,query,book,exists_only=True)
                self.assertEqual(bool(full['total']),bool(probe['total']))
                self.assertEqual(full['book_available'],probe['book_available'])

    def test_covering_index_preserves_count_order_and_pagination(self):
        cases=[(q,book,page,probe) for q in ('甲乙丙','天地','遷','迁于东都','不存在')
               for book in (None,'a','b') for page in (1,2) for probe in (False,True)]
        before=[S.lexical(self.db,q,book,page,2,exists_only=probe) for q,book,page,probe in cases]
        self.db.execute('CREATE INDEX chunks_lookup ON chunks(book_id,search_text)')
        after=[S.lexical(self.db,q,book,page,2,exists_only=probe) for q,book,page,probe in cases]
        self.assertEqual(before,after)

    def test_invalid_and_sql_operators_not_executed(self):
        for q in ('','  ','*_%','甲'*301):
            with self.assertRaises(ValueError):S.lexical(self.db,q)
        self.assertEqual(S.lexical(self.db,"' OR 1=1 --")['total'],0)
        with self.assertRaises(ValueError):S.lexical(self.db,'甲',page=0)

    def test_no_false_semantic_channel_and_dedup(self):
        raw=S.lexical(self.db,'天地玄黃')['hits']
        fused=S.fused([], [raw,raw])
        self.assertTrue(fused)
        self.assertTrue(all(h['channels']==['keyword'] and 'semantic_score' not in h for h in fused))
        dense=[dict(chunk=raw[0]['chunk'],semantic_score=.7)]
        both=S.fused(dense,[raw])
        self.assertEqual(both[0]['channels'],['semantic','keyword'])
        self.assertEqual(both[0]['semantic_score'],.7)

    def test_raw_fallback_not_silently_used(self):
        with patch.object(S,'status',return_value=dict(ready=False,reason='stale')):
            with self.assertRaises(FileNotFoundError):
                with S.connect():pass

    def test_relation_is_explicit_and_fallback_keeps_expanded_results(self):
        from contextlib import nullcontext
        from search import semantic_candidates, semantic_bridge, punctuated_relevance
        raw=S.lexical(self.db,'甲乙丙')['hits']
        with patch.object(S,'connect',side_effect=lambda: nullcontext(self.db)), \
             patch.object(semantic_candidates,'plan_query',return_value=dict(search_queries=['甲乙丙'])), \
             patch.object(semantic_bridge.worker,'query',return_value=dict(hits=[dict(chunk=raw[0]['chunk'],semantic_score=.7)])) as worker, \
             patch('logging.getLogger'):
            base=S.run('甲乙丙的相关记载',mode='expanded')
            result=S.run('甲乙丙的相关记载',mode='relation')
            self.assertEqual(worker.call_count,2)  # One embedding query per request, including fallback.
            self.assertEqual(result['hits'],base['hits'])
            self.assertEqual(result['relation_experiment']['selection'],'baseline_fallback')
            self.assertNotIn('ranked',result)
            with patch.object(punctuated_relevance,'evaluate',side_effect=AssertionError('experiment must not run')):
                for mode in ('keyword','semantic','hybrid','expanded'):
                    self.assertNotIn('relation_experiment',S.run('甲乙丙',mode=mode))
            count=worker.call_count
            missing=S.run('甲乙丙',book='missing',mode='relation')
            self.assertFalse(missing['book_available']);self.assertEqual(missing['hits'],[])
            self.assertEqual(worker.call_count,count)
            with self.assertRaises(ValueError):S.run('甲乙丙',mode='relation',page=2)

    def test_relation_route_keeps_book_scope_and_exact_source(self):
        from contextlib import nullcontext
        from search import semantic_candidates, semantic_bridge
        with patch.object(S,'connect',side_effect=lambda: nullcontext(self.db)), \
             patch.object(semantic_candidates,'plan_query',return_value=dict(search_queries=['甲乙丙'])), \
             patch.object(semantic_bridge.worker,'query',return_value=dict(hits=[])), \
             patch('logging.getLogger'):
            result=S.run('甲乙丙迁往东都',book='a',mode='relation')
            self.assertEqual(result['relation_experiment']['selection'],'relation_clues')
            self.assertFalse(result['relation_experiment']['relation_verified'])
            for hit in result['hits']:
                self.assertEqual(hit['chunk']['book_id'],'a')
                self.assertIn(hit['chunk'],self.chunks)
            self.assertEqual(result['model_generation_calls'],0)
            self.assertEqual(S.run('甲乙丙迁往东都',book='b',mode='relation')['hits'],[])

    def test_grounding_cache_is_request_local_and_timings_are_reported(self):
        from contextlib import nullcontext
        from search import semantic_candidates, semantic_bridge
        calls = []
        def lexical(conn, query, book=None, *args, **kwargs):
            calls.append((query,book,kwargs.get('exists_only',False)))
            return dict(hits=[],total=0,book_available=True)
        def plan(cur, query, book, *, literal_exists):
            self.assertFalse(literal_exists('甲乙'))
            self.assertFalse(literal_exists('甲乙'))
            return dict(search_queries=['甲乙 卒'])
        with patch.object(S,'connect',side_effect=lambda: nullcontext(self.db)), \
             patch.object(S,'lexical',side_effect=lexical), \
             patch.object(semantic_candidates,'plan_query',side_effect=plan), \
             patch.object(semantic_bridge.worker,'query',return_value=dict(hits=[])), \
             patch('logging.getLogger'):
            for book in ('a','b','a'):
                result=S.run('甲乙何时卒',book,mode='expanded')
                self.assertEqual(set(result['timings']),
                    {'literal_ms','planning_ms','expanded_lexical_ms','semantic_ms','fusion_ms'})
                self.assertTrue(all(ms>=0 for ms in result['timings'].values()))
                self.assertEqual(result['model_generation_calls'],0)
        self.assertEqual([c for c in calls if c[2]],
                         [('甲乙','a',True),('甲乙','b',True),('甲乙','a',True)])


if __name__=='__main__':unittest.main()
