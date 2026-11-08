"""Rebuild retained source-derived inputs in the historical append order.

The live database is read only for comparison, never used as rebuild input.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.pipeline import config, sqlite_store, validate

ROOT = Path(__file__).resolve().parents[2]


def inside(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Path outside repository')
    return path


def inputs():
    # These groups reproduce the original import, followed by the stable merge.
    base = sorted(config.PROCESSED_DIR.glob('parsed_*.jsonl'))
    expansion = ROOT / 'src/data/expansion_20261021/processed'
    if not base or not expansion.is_dir():
        raise FileNotFoundError('Missing retained base or expansion inputs')
    files = base + [p for p in sorted(expansion.glob('parsed_*.jsonl'))
                    if p.name not in {p.name for p in base}]
    provenance = []
    for path in files:
        digest = hashlib.sha256()
        seen = set()
        sources = []
        with inside(path).open('rb') as stream:
            for line in stream:
                digest.update(line)
                if not line.strip():
                    continue
                row = json.loads(line)
                origin = row['book_dir'] + '/' + row['original_file']
                if origin in seen:
                    continue
                seen.add(origin)
                raw = inside(config.LIBRARY_DIR / origin)
                actual = hashlib.sha256(raw.read_bytes()).hexdigest()
                if actual != row['sha256']:
                    raise ValueError('Source hash mismatch: ' + origin)
                sources.append(dict(origin_path=origin, sha256=actual,
                                    book_id=row['book_id'], edition=row.get('edition')))
        provenance.append(dict(input=str(path.relative_to(ROOT)), sha256=digest.hexdigest(), sources=sources))
    return files, provenance


def compare(candidate, reference):
    result = {}
    with closing(sqlite3.connect(Path(candidate).resolve().as_uri()+'?mode=ro', uri=True)) as new, \
            closing(sqlite3.connect(reference.as_uri()+'?mode=ro', uri=True)) as old:
        for table in sqlite_store._TABLES:
            if table == 'import_runs':
                continue  # New build UUID/time are execution metadata.
            columns = [r[1] for r in old.execute(f'PRAGMA table_info({table})')]
            def fingerprint(db):
                digest = hashlib.sha256()
                count = 0
                for row in db.execute(f'SELECT * FROM {table} ORDER BY '+','.join(columns)):
                    digest.update(json.dumps(row, ensure_ascii=True, separators=(',', ':')).encode())
                    digest.update(b'\n')
                    count += 1
                return dict(rows=count, sha256=digest.hexdigest())
            a,b = fingerprint(new),fingerprint(old)
            result[table] = dict(candidate=a, reference=b, equal=a==b)
        result['foreign_keys'] = list(new.execute('PRAGMA foreign_key_check'))
        result['integrity'] = new.execute('PRAGMA integrity_check').fetchone()[0]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = inside(args.output)
    output.mkdir(parents=True, exist_ok=False)
    candidate = output / 'history.db'
    files, provenance = inputs()
    (output/'inputs.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf8')
    stats = sqlite_store.rebuild(candidate, processed_files=files)
    config.METADATA_DIR = output
    validation = validate.run_validation(candidate, quiet=True)
    tables = compare(candidate, inside(config.DB_PATH))
    report = dict(stats=stats, validation=validation, comparison=tables,
                  equal=all(v['equal'] for v in tables.values() if isinstance(v,dict))
                  and not tables['foreign_keys'] and tables['integrity']=='ok',
                  id_policy='All IDs included in exact table comparison; historical base then appended books',
                  excluded_comparison=['import_runs: build UUID and timestamp'])
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(dict(stats=stats, equal=report['equal'], tables=tables), ensure_ascii=False), flush=True)
    return 0 if report['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
