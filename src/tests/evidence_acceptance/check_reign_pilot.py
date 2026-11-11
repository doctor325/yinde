"""Resolve the reviewed Qin pilot against source bytes and the reference database."""
from contextlib import closing
import hashlib
import json
from pathlib import Path

from api import db
from scripts.reign_test_gate import ROOT, inventory, sample


def main():
    pilot=json.loads(Path(__file__).with_name('reign_qin_pilot.json').read_text(encoding='utf-8'))
    registry=inventory()
    period=next(p for p in registry['periods'] if p['id']==pilot['period_id'])
    assert period['emperor']==pilot['expected_ruler']
    assert period['reign_years_as_source']=='B.C.246 B.C.210'
    origin=pilot['origin_path']
    digest=pilot['sha256']
    raw=ROOT/'corpus/kanripo'/origin
    assert hashlib.sha256(raw.read_bytes()).hexdigest()==digest
    pool=[]
    with closing(db.connect()) as conn:
        section=conn.execute(
            'SELECT s.first_row,s.last_row FROM sections s JOIN files f USING(file_id) '
            'WHERE s.label=? AND f.origin_path=? AND f.sha256=?',
            (pilot['chapter'],origin,digest)
        ).fetchone()
        assert section

        def verify(row, quote):
            record=conn.execute(
                'SELECT p.text_orig FROM passages p JOIN files f USING(file_id) '
                'WHERE f.origin_path=? AND f.sha256=? AND p.row_no=?',
                (origin,digest,row)
            ).fetchone()
            assert record and quote in record[0], (row,quote)
            return dict(origin_path=origin,sha256=digest,row_no=row,quote=quote)

        for case in pilot['cases']:
            assert section[0] <= case['year_row'] < case['row'] <= section[1]
            pool.append(dict(kind='event',content=case['content'],query=case['query'],
                             period_verified=True,
                             period_evidence=verify(case['year_row'],case['year']),
                             gold_sources=[verify(case['row'],case['quote'])]))
    result=sample(registry,{period['id']:pool},pilot['seed'])
    assert len(result['cases'])==5
    assert not result['coverage_pass']
    print(json.dumps(dict(reviewed_candidates=len(pool),covered_periods=1,
                          provisional_periods=len(registry['periods']),sample=result['cases']),
                     ensure_ascii=False))


if __name__=='__main__':
    main()
