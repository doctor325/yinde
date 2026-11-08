import hashlib
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import rebuild_corpus
from scripts.pipeline import validate, sqlite_store
from tests import _tmp


class RebuildAudit(unittest.TestCase):
    def setUp(self):
        self.folder = Path(_tmp.mkdtemp(dir=Path(__file__).resolve().parents[1]/'data'))
        self.addCleanup(shutil.rmtree, self.folder)

    def test_source_marker_evidence_preserves_failure(self):
        text = '<pb:KR2a0038_WYG_WYG0299-0802c>¶'
        (self.folder/'source.txt').write_text(text, encoding='utf8')
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE passages(file_id,row_no,char_start,char_end,seq,kind,layer,status,text_orig)')
        db.execute('INSERT INTO passages VALUES(1,1,NULL,NULL,1,\'page\',\'structure\',\'ok\',?)',(text,))
        row = dict(file_id=1,file_name='source.txt',book_id='test',origin_path='source.txt',
                   sha256=hashlib.sha256(text.encode()).hexdigest(),meta_json='{}')
        result = validate.validate_file(db.cursor(),row,self.folder)
        self.assertTrue(result['body_ok'] and result['sha256_ok'])
        self.assertEqual(result['pb_bad'],1)
        self.assertEqual(result['pb_errors'][0]['row_no'],1)
        self.assertEqual(result['pb_errors'][0]['text_orig'],text)
        self.assertTrue(result['pb_errors'][0]['in_source'])

    def test_comparison_detects_id_and_text_changes(self):
        for filename,values in [('a.db',(1,'原文')),('b.db',(2,'原文')),('c.db',(1,'异文'))]:
            with closing(sqlite3.connect(self.folder/filename)) as db:
                db.execute('CREATE TABLE books(id INTEGER,text TEXT)')
                db.execute('INSERT INTO books VALUES(?,?)',values)
                db.commit()
        with patch.object(sqlite_store,'_TABLES',('books',)):
            self.assertTrue(rebuild_corpus.compare(self.folder/'a.db',self.folder/'a.db')['books']['equal'])
            self.assertFalse(rebuild_corpus.compare(self.folder/'a.db',self.folder/'b.db')['books']['equal'])
            self.assertFalse(rebuild_corpus.compare(self.folder/'a.db',self.folder/'c.db')['books']['equal'])

    def test_unified_retained_entry_routes_without_touching_live_state(self):
        from scripts.pipeline import run_all
        with patch.object(rebuild_corpus,'main',return_value=0) as rebuild:
            self.assertEqual(run_all.main(['--retained-output','src/data/candidate']),0)
            rebuild.assert_called_once_with(['--output','src/data/candidate'])
