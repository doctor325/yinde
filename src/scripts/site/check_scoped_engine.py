"""Build a private export from the reference DB before running parity checks.

Never overwrite frontend/data or data-demo. Full export can be expensive; this
is an explicit stage acceptance command, not a default unit-test prerequisite.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
from scripts.pipeline import config
from api import db
from scripts.site import export_site, check_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('checks', nargs='+', choices=('dual-text', 'zh', 'norm', 'dl', 'search',
        'entities', 'question', 'ranking', 'result-block', 'aggregate', 'retrieve'))
    args = parser.parse_args()
    root = config.HISTORY_AI_DIR.resolve()
    out = args.out.resolve()
    allowed = root / 'data'
    if not out.is_relative_to(allowed) or out == allowed:
        parser.error('--out must be a dedicated directory under src/data')
    out.mkdir(parents=True, exist_ok=True)
    corpus = out / 'corpus.json'
    with closing(db.connect()) as conn:
        conn.execute('BEGIN')
        export_site.write_corpus(conn.cursor(), corpus)
        export_site.write_sections(conn.cursor(), out / 'sections.json')
        books=db.list_books(conn.cursor())
        files={}
        for book in books:
            for f in db.list_files(conn.cursor(),book['book_id']):
                files[str(f['file_id'])]=db.get_file(conn.cursor(),f['file_id'])
        export_site.write_json(books,out / 'books.json')
        export_site.write_json(files,out / 'files.json')
        sources = [list(r) for r in conn.execute(
            'SELECT book_id,origin_path,sha256 FROM files ORDER BY origin_path')]
    with corpus.open('rb') as stream:
        digest=hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = dict(database=str(config.DB_PATH), corpus=str(corpus),
                    source_digest=hashlib.sha256(json.dumps(sources,ensure_ascii=True,
                        separators=(',', ':')).encode()).hexdigest(),
                    corpus_sha256=digest)
    (out / 'scope.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    check_engine.CORPUS = corpus
    return check_engine.main(['check_scoped_engine', *args.checks])


if __name__ == '__main__':
    raise SystemExit(main())
