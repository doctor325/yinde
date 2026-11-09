import json
from pathlib import Path
import sqlite3
import unittest
from scripts.site import bm25
from search import evidence_pipeline as E


class Repairs(unittest.TestCase):
    def test_fts_statistics_follow_actual_build(self):
        with self.subTest('single and repeated builds'):
            conn=sqlite3.connect(':memory:')
            self.addCleanup(conn.close)
            conn.execute('CREATE TABLE passages(passage_id INTEGER PRIMARY KEY,normalized_text TEXT)')
            conn.execute("CREATE VIRTUAL TABLE passages_fts USING fts5(normalized_text,content='passages',content_rowid='passage_id',tokenize='trigram')")
            for i,text in enumerate(('甲公乙侯','甲公乙侯来朝','丙君丁伯'),1):
                conn.execute('INSERT INTO passages VALUES(?,?)',(i,text))
            conn.execute("INSERT INTO passages_fts(passages_fts) VALUES('rebuild')")
            for _ in range(2):
                # FTS5 flushes its averages record at transaction commit.
                conn.commit()
                totals=bm25.totals_stored('passages_fts',conn)
                self.assertEqual(bm25.verify_against_sqlite('passages_fts',conn,('甲公乙侯',),*totals),[])
                conn.execute('INSERT INTO passages_fts(rowid,normalized_text) SELECT passage_id,normalized_text FROM passages')

    def test_real_relative_time_has_source_anchor(self):
        rows=json.loads((Path(__file__).parent/'evidence_acceptance/existing17.json').read_text(encoding='utf8'))
        case=next(r for r in rows if r['id']=='time-han')
        result=E.audit(case['chunk'],E.parse(case['query']),[])
        self.assertEqual(result['status'],'clues_present')
        ref=result['time_references'][0]
        self.assertEqual(ref['expression'],'明年')
        self.assertIn('地節元年',ref['anchor']['quote'])
        self.assertIsNone(ref['absolute_year'])
        self.assertFalse(result['relation_verified'])

    def test_relative_time_cannot_cross_paragraph_or_guess_anchor(self):
        from search.relative_time import reference
        for prefix in ('','元年，来朝。\n\n','元年，来朝。二年，出兵。'):
            text=prefix+'明年，大将军甲公薨。'
            start=len(prefix);a=text.index('甲公')
            observation=dict(start=start,subject_span=[a,a+2])
            self.assertIsNone(reference(dict(text_orig=text),observation))


if __name__=='__main__':unittest.main()
