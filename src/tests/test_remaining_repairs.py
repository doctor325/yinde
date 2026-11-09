from pathlib import Path
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from search import evidence_pipeline as E, punctuated_relevance as R, block_page_cache as C


class Remaining(unittest.TestCase):
    def test_real_siege_patient(self):
        cases=json.loads((Path(__file__).parent/'evidence_acceptance/existing17.json').read_text(encoding='utf8'))
        case=next(c for c in cases if c['id']=='battle-jin')
        audit=E.audit(case['chunk'],E.parse(case['query']),[])
        self.assertEqual(audit['status'],'clues_present')
        obs=next(o for o in audit['subject_syntax']['observations'] if o.get('resolved_object'))
        self.assertEqual(obs['resolved_object'],'石季龍')
        a,b=obs['object_span']
        self.assertEqual(case['chunk']['text_orig'][a:b],obs['resolved_object'])

    def test_siege_rejects_actor_change_reports_and_negation(self):
        plan=E.parse('甲公击退了谁对东城的围攻？')
        for text in ('乙侯将丙君围东城，平西将军甲公击走之。',):
            self.assertEqual(E.audit(dict(text_orig=text,book_title='甲史'),plan,[])['status'],'clues_present')
        for text in ('传言乙侯将丙君围东城，平西将军甲公击走之。',
                     '乙侯将丙君围东城，平西将军甲公未击走之。',
                     '乙侯将丙君围东城，甲公令丁伯击走之。'):
            self.assertEqual(E.audit(dict(text_orig=text,book_title='甲史'),plan,[])['status'],'insufficient')

    def test_real_modified_object(self):
        cases=json.loads((Path(__file__).parent/'evidence_acceptance/existing17.json').read_text(encoding='utf8'))
        case=next(c for c in cases if c['id']=='battle-tang')
        self.assertEqual(E.audit(case['chunk'],E.parse(case['query']),[])['status'],'clues_present')
        plan=E.parse('甲公在哪里袭击乙侯？')
        for text in ('甲公袭突厥乙侯于东城。','甲公袭契丹乙侯于东城。'):
            self.assertEqual(E.audit(dict(text_orig=text,book_title='甲史'),plan,[])['status'],'clues_present')
        for text in ('乙侯袭突厥甲公于东城。','甲公袭丙君乙侯于东城。','甲公未袭突厥乙侯于东城。'):
            self.assertEqual(E.audit(dict(text_orig=text,book_title='甲史'),plan,[])['status'],'insufficient')

    def test_cause_parser_and_syntax(self):
        plan=E.parse('甲公削藩导致了什么事？')
        self.assertEqual([c['text'] for c in plan['clues']],['甲公'])
        self.assertEqual(E.audit(dict(text_orig='甲公刻削诸侯，遂使七国俱起。',book_title='甲史'),plan,[])['status'],'clues_present')
        for text in ('甲公未刻削诸侯，遂使七国俱起。','传言甲公刻削诸侯，遂使七国俱起。',
                     '乙侯刻削诸侯，遂使七国俱起。','甲公刻削诸侯。七国俱起。'):
            self.assertEqual(E.audit(dict(text_orig=text,book_title='甲史'),plan,[])['status'],'insufficient')
        p=E.parse('为什么甲公被关进监狱？')
        self.assertEqual([c['text'] for c in p['clues']],['甲公'])
        self.assertEqual(E.audit(dict(text_orig='甲公系狱，无可证者。',book_title='甲史'),p,[])['status'],'insufficient')

    def test_cache_mutation_and_database_generation(self):
        root=Path(__file__).parents[1]/'data'
        with tempfile.TemporaryDirectory(dir=root) as td:
            path=Path(td)/'cache.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE x(a)');conn.commit()
                token=C.key(conn.cursor(),('q',))
                value=dict(results=[dict(text='原文',passage_ids=[1])])
                C.put(token,value);C.get(token)['results'][0]['text']='改'
                self.assertEqual(C.get(token),value)
                self.assertEqual(C.get(token,0,1),value)
                self.assertEqual(C.get(token,1,2),dict(results=[]))
                stamp=path.stat();os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns+1000000))
                self.assertIsNone(C.get(C.key(conn.cursor(),('q',))))
                conn.execute('BEGIN')
                self.assertIsNone(C.key(conn.cursor(),('q',)))


if __name__=='__main__':unittest.main()
