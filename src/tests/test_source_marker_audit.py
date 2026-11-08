from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.source_marker_audit import check


class SourceMarkerGate(unittest.TestCase):
    def test_exact_source_and_unexpected_changes(self):
        fixture=json.loads(Path(__file__).with_name('source_marker_exceptions.json').read_text(encoding='utf8'))
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE files(file_name,origin_path,sha256)')
            rows=[]
            for name,item in fixture.items():
                conn.execute('INSERT INTO files VALUES(?,?,?)',(name,item['origin_path'],item['sha256']))
                rows.append(dict(file=name,pb_bad=len(item['markers']),kr_bad=0,
                    sha256_ok=True,meta_ok=True,body_ok=True,
                    pb_errors=[dict(m,in_source=True) for m in item['markers']]))
            validation=dict(bad_files=rows)
            check(validation,conn)
            for mutation in ('marker','position','body','extra_file'):
                changed=copy.deepcopy(validation)
                if mutation=='marker':changed['bad_files'][0]['pb_errors'][0]['marker']='<pb:new>'
                if mutation=='position':changed['bad_files'][0]['pb_errors'][0]['row_no']+=1
                if mutation=='body':changed['bad_files'][0]['body_ok']=False
                if mutation=='extra_file':changed['bad_files'].append(dict(file='new.txt'))
                with self.subTest(mutation=mutation),self.assertRaises(AssertionError):
                    check(changed,conn)
            conn.execute("UPDATE files SET sha256='changed'")
            with self.assertRaises(AssertionError):check(validation,conn)
