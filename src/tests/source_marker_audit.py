"""Exact source-hash and marker multiplicity gate for audited source defects."""
from collections import Counter
import json
from pathlib import Path


def check(validation, conn):
    expected=json.loads(Path(__file__).with_name('source_marker_exceptions.json').read_text(encoding='utf8'))
    actual={r['file']:r for r in validation['bad_files']}
    if set(actual)!=set(expected):
        raise AssertionError('Unexpected source anomaly files')
    for name,item in expected.items():
        row=actual[name]
        sources=conn.execute('SELECT origin_path,sha256 FROM files WHERE file_name=?',(name,)).fetchall()
        if [tuple(s) for s in sources]!=[(item['origin_path'],item['sha256'])]:
            raise AssertionError('Source provenance changed: '+name)
        markers=Counter((m['row_no'],m['marker']) for m in row['pb_errors'])
        wanted=Counter((m['row_no'],m['marker']) for m in item['markers'])
        if markers!=wanted or row['pb_bad']!=sum(wanted.values()):
            raise AssertionError('Unexpected marker or position: '+name)
        if not all(row[k] for k in ('sha256_ok','meta_ok','body_ok')) or row['kr_bad']:
            raise AssertionError('Source reconstruction failure: '+name)
        if not all(m['in_source'] for m in row['pb_errors']):
            raise AssertionError('Marker not present in source: '+name)
