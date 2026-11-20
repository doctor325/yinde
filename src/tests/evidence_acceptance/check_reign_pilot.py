"""Build a provisional manifest from the reviewed Qin annal samples."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path

from api import db
from scripts.reign_test_gate import ROOT, inventory, sample


def reviewed_pool(conn, pilot, period):
    assert period['emperor'] == pilot['expected_ruler']
    assert period['reign_years_as_source'] == pilot.get('reign_years_as_source', 'B.C.246 B.C.210')
    origin=pilot['origin_path']
    digest=pilot['sha256']
    corpus = (ROOT/'corpus/kanripo').resolve()
    raw = (corpus/origin).resolve(strict=True)
    assert raw.is_relative_to(corpus)
    assert hashlib.sha256(raw.read_bytes()).hexdigest()==digest
    section=conn.execute(
        'SELECT s.first_row,s.last_row FROM sections s JOIN files f USING(file_id) '
        'WHERE s.label=? AND f.origin_path=? AND f.sha256=?',
        (pilot['chapter'],origin,digest)
    ).fetchone()
    assert section

    def verify(row, quote):
        records=conn.execute(
            'SELECT p.text_orig FROM passages p JOIN files f USING(file_id) '
            'WHERE f.origin_path=? AND f.sha256=? AND p.row_no=? AND p.kind=?',
            (origin,digest,row,'passage')
        ).fetchall()
        assert quote and any(quote in record[0] for record in records), (row,quote)
        return dict(origin_path=origin,sha256=digest,row_no=row,quote=quote)

    bounds = [verify(x['row'], x['quote']) for x in pilot.get('period_bounds', [])]
    start, end = [item['row_no'] for item in bounds] if bounds else section
    assert section[0] <= start < end <= section[1]
    year_rows = sorted({case['year_row'] for case in pilot['cases']})
    pool = []
    for case in pilot['cases']:
        row, year = case['row'], case['year_row']
        assert start <= year < row <= end
        assert year == max(anchor for anchor in year_rows if anchor < row)
        pool.append(dict(kind='event', content=case['content'], query=case['query'],
                         period_verified=True, period_source=period['source'], period_bounds=bounds,
                         period_evidence=verify(year,case['year']), gold_sources=[verify(row,case['quote'])]))
    return pool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, help='Write the provisional result to a new file under src/data')
    args = parser.parse_args()
    output = args.out.resolve() if args.out else None
    if output and (not output.is_relative_to((ROOT/'src/data').resolve()) or output.exists()):
        parser.error('--out must be a new path under src/data')
    paths = sorted(Path(__file__).parent.glob('reign_qin_*pilot.json'))
    pilots = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
    assert pilots and len({pilot['seed'] for pilot in pilots}) == 1
    registry = inventory()
    periods = {period['id']:period for period in registry['periods']}
    with closing(db.connect()) as conn:
        pools = {pilot['period_id']:reviewed_pool(conn,pilot,periods[pilot['period_id']]) for pilot in pilots}
    result = sample(registry,pools,pilots[0]['seed'])
    readiness = {item['id']:item['selected'] for item in result['periods'] if item['selected']}
    assert readiness == dict.fromkeys(pools,5)
    result['reviewed_pools'] = pools
    if output:
        with output.open('x',encoding='utf-8') as fp:
            json.dump(result,fp,ensure_ascii=False,indent=2)
    print(json.dumps(dict(reviewed_candidates=sum(map(len,pools.values())), ready_periods=readiness,
                          provisional_periods=len(periods), coverage_pass=result['coverage_pass']),
                     ensure_ascii=False))


if __name__=='__main__':
    main()
