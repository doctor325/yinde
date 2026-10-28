"""Independent source editions for reading. Never writes the main history DB."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from scripts.pipeline import config
from search import zh


def index_path():
    root = Path(config.PROJECT_ROOT).resolve()
    path = (root / 'src/data/punctuated/library.sqlite').resolve()
    if not path.is_relative_to(root):
        raise ValueError('标点本目录超出项目范围')
    return path


@contextmanager
def connect():
    path = index_path()
    if not path.is_file():
        raise FileNotFoundError('标点本尚未完成下载入库')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA temp_store=MEMORY')
        yield conn
    finally:
        conn.close()


def books():
    if not index_path().is_file():
        return {'available': False, 'books': []}
    with connect() as c:
        from search.punctuated_search import status
        ready = status()['ready']
        return {'available': True, 'books': [dict(r) for r in c.execute('SELECT * FROM books ORDER BY position')],
                'edition': '维基文库录入本', 'search_index_ready': ready,
                'search_scope': '标点版已接入主检索与本地语义索引' if ready else '本入口只检索新增版本；尚未纳入语义索引'}


def chapters(book_id, query='', page=1):
    if not isinstance(query, str) or len(query) > 100 or page < 1:
        raise ValueError('检索词最多100字，页码从1开始')
    with connect() as c:
        book = c.execute('SELECT * FROM books WHERE book_id=?', (book_id,)).fetchone()
        if not book:
            raise KeyError('没有这部标点版史书')
        where, args = ['book_id=?'], [book_id]
        if query.strip():
            # Literal contains, including punctuation. Script forms are derived only.
            term = zh.to_simplified(query.strip()).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where += ["status='readable'", "search_text LIKE ? ESCAPE '\\'"]
            args += ['%' + term + '%']
        condition = ' AND '.join(where)
        total = c.execute('SELECT COUNT(*) FROM chapters WHERE ' + condition, args).fetchone()[0]
        rows = c.execute('SELECT page_id,title,status,punctuation_count,body_chars,source_url,reason FROM chapters WHERE '
                         + condition + ' ORDER BY sort_number,title LIMIT 100 OFFSET ?', args + [(page-1)*100]).fetchall()
        return {'book': dict(book), 'rows': [dict(r) for r in rows], 'total': total, 'page': page,
                'page_size': 100, 'query': query}


def chapter(page_id):
    with connect() as c:
        row = c.execute('SELECT * FROM chapters WHERE page_id=?', (page_id,)).fetchone()
        if not row:
            raise KeyError('没有这篇标点本')
        result = dict(row)
        result['blocks'] = json.loads(result.pop('blocks_json'))
        result.pop('search_text')
        return result
