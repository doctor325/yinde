"""Source-block lexical retrieval, isolated from the default passage engine.

The character-position FTS index is derived from existing, audited chunks. It
never crosses their file, edition, layer or structure boundaries. Co-occurrence
and distance are retrieval clues, not resolved people or verified relations.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from search import engine, zh
from search.semantic_bridge import state_dir, SemanticUnavailable

VERSION = 'source-block-lexical-v1'
RETRIEVAL_VERSION = 'indexed-grounding-v2'
ROUTE_LIMIT = 500
RESULT_LIMIT = 40


def normalize(text):
    text = re.sub(r'<pb:[^>]*>|¶|\s+', '', text or '')
    text = re.sub(r'&KR[A-Za-z0-9]+;', '□', text)
    return zh.to_simplified(text)


def tokens(text):
    # ASCII codepoint tokens preserve exact character positions, including
    # one-character verbs. User FTS operators never become executable syntax.
    return ' '.join('u'+format(ord(c), 'x') for c in text)


def phrase(text):
    return '"'+tokens(normalize(text))+'"'


def identity(path):
    stat = Path(path).stat()
    return {'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def source_identity(state):
    return {'manifest_sha256': hashlib.sha256((state/'index/manifest.json').read_bytes()).hexdigest(),
            'chunks': identity(state/'index/chunks.sqlite')}


@contextmanager
def open_index(state=None):
    state = state or state_dir()
    conn = None
    try:
        conn = sqlite3.connect((state/'contextual/chunks.sqlite').as_uri()+'?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA temp_store=MEMORY')
        meta = json.loads(conn.execute('SELECT value FROM metadata WHERE key=?', ('build',)).fetchone()[0])
        if meta.get('version') != VERSION or meta.get('complete') is not True or meta.get('source') != source_identity(state):
            raise SemanticUnavailable('原文片段检索索引与当前语料版本不一致，请重建')
        yield conn
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise SemanticUnavailable('原文片段检索索引尚未就绪，请运行构建工具') from exc
    finally:
        if conn:
            conn.close()


def available(state=None):
    try:
        with open_index(state):
            return True
    except SemanticUnavailable:
        return False


def build(target, state=None):
    """Create a NEW derived DB; callers publish it only after validation."""
    state = state or state_dir()
    target = Path(target).resolve()
    if not target.is_relative_to(state.resolve()) or target.exists():
        raise ValueError('新索引必须在语义实验目录内，且不得覆盖已有文件')
    before = source_identity(state)
    target.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    source = sqlite3.connect((state/'index/chunks.sqlite').as_uri()+'?mode=ro', uri=True)
    dest = sqlite3.connect(target)
    digest = hashlib.sha256()
    try:
        dest.executescript('''PRAGMA temp_store=MEMORY;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE documents(chunk_id INTEGER PRIMARY KEY,book_id TEXT NOT NULL,body TEXT NOT NULL);
            CREATE INDEX documents_book ON documents(book_id);
            CREATE VIRTUAL TABLE body_fts USING fts5(body,content='',tokenize='unicode61');''')
        count = 0
        for cid, bid, payload in source.execute('SELECT chunk_id,book_id,payload FROM chunks ORDER BY chunk_id'):
            chunk = json.loads(payload)
            body = normalize(chunk['text_orig'])
            dest.execute('INSERT INTO documents VALUES(?,?,?)', (cid, bid, body))
            dest.execute('INSERT INTO body_fts(rowid,body) VALUES(?,?)', (cid, tokens(body)))
            digest.update(f'{cid}\0{bid}\0{body}\n'.encode('utf-8'))
            count += 1
            if count % 25000 == 0:
                dest.commit()
                print('indexed', count, flush=True)
        if before != source_identity(state):
            raise ValueError('构建期间原片段索引变化，拒绝发布')
        manifest = json.loads((state/'index/manifest.json').read_text(encoding='utf-8'))
        if count != manifest['chunks'] or not manifest['complete']:
            raise ValueError('片段数不符或原索引不完整')
        dest.execute("INSERT INTO body_fts(body_fts) VALUES('integrity-check')")
        if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('派生索引完整性检查失败')
        meta = {'version': VERSION, 'source': before, 'chunks': count,
                'normalized_sha256': digest.hexdigest(), 'complete': True,
                'elapsed_seconds': round(time.perf_counter()-start, 2)}
        dest.execute('INSERT INTO metadata VALUES(?,?)', ('build', json.dumps(meta)))
        dest.commit()
        return meta
    finally:
        source.close()
        dest.close()


def positions(body, term):
    return [(m.start(), m.end()) for m in re.finditer(re.escape(term), body)]


def clues(body, anchors, relations):
    anchor_spans = [(a, positions(body, a)) for a in anchors]
    relation_spans = [(r, positions(body, r)) for r in relations]
    distances = [max(0, y-u, x-v) for _, aa in anchor_spans for x,u in aa
                 for _, rr in relation_spans for y,v in rr if u <= y or v <= x]
    return {'anchors_present': [a for a, p in anchor_spans if p],
            'relations_present': [r for r, p in relation_spans if p],
            'nearest_gap_chars': min(distances) if distances else None,
            'relation_verified': False}


def retrieve(conn, anchors, relations, book=None, limit=RESULT_LIMIT):
    anchors = list(dict.fromkeys(normalize(x) for x in anchors if normalize(x)))[:4]
    relations = list(dict.fromkeys(normalize(x) for x in relations if normalize(x)))[:8]
    if not anchors:
        return [], []
    anchor_expr = ' AND '.join(phrase(a) for a in anchors)
    relation_expr = '('+' OR '.join(phrase(r) for r in relations)+')' if relations else None
    routes = []
    if relation_expr:
        routes.append(('all_anchors_relation', anchor_expr+' AND '+relation_expr))
    routes.append(('all_anchors', anchor_expr))
    if len(anchors) > 1:
        if relation_expr:
            routes.append(('primary_anchor_relation', phrase(anchors[0])+' AND '+relation_expr))
        routes.append(('primary_anchor', phrase(anchors[0])))
    candidates, trace = {}, []
    for label, expression in routes:
        rows = conn.execute('SELECT d.chunk_id,d.body,bm25(body_fts) AS score FROM body_fts '
                            'JOIN documents d ON d.chunk_id=body_fts.rowid WHERE body_fts MATCH ? '
                            + ('AND d.book_id=? ' if book else '')
                            + 'ORDER BY score,d.chunk_id LIMIT ?',
                            [expression]+([book] if book else [])+[ROUTE_LIMIT+1]).fetchall()
        trace.append({'route': label, 'unit': 'source_chunk', 'anchors': anchors, 'relations': relations,
                      'sampled': min(len(rows), ROUTE_LIMIT), 'truncated': len(rows)>ROUTE_LIMIT})
        for row in rows[:ROUTE_LIMIT]:
            # Phrase positions already enforce literal sequence. Rechecking
            # keeps the feature representation independent from FTS scoring.
            cid = row['chunk_id']
            if cid not in candidates:
                candidates[cid] = {'chunk_id': cid, 'context_clues': clues(row['body'], anchors, relations),
                                   '_bm25': row['score']}
    def key(hit):
        feature = hit['context_clues']
        return (-len(feature['anchors_present']), -bool(feature['relations_present']),
                feature['nearest_gap_chars'] if feature['nearest_gap_chars'] is not None else 100000,
                hit['_bm25'], hit['chunk_id'])
    return sorted(candidates.values(), key=key)[:limit], trace


def lexical(cur, query, book=None):
    from search.semantic_candidates import plan_query
    bid = engine.resolve_book(cur, book)
    with open_index() as conn:
        def exists(term):
            # Grounding needs one occurrence, not a count/ranked preview of the
            # entire corpus. Use the same source-block/script normalization as
            # retrieval, so cross-line literals are valid hypotheses too.
            if not normalize(term):
                return False
            sql = 'SELECT rowid FROM body_fts WHERE body_fts MATCH ?'
            args = [phrase(term)]
            if bid:
                sql += ' AND rowid IN (SELECT chunk_id FROM documents WHERE book_id=?)'
                args.append(bid)
            return conn.execute(sql+' LIMIT 1',args).fetchone() is not None
        plan = plan_query(cur, query, book, literal_exists=exists)
        anchors = plan['literal_hypotheses']
        if not anchors:
            anchors = query.split()[:4]
        hits, trace = retrieve(conn, anchors, plan['relation_terms'], bid)
    state = state_dir()
    source = sqlite3.connect((state/'index/chunks.sqlite').as_uri()+'?mode=ro', uri=True)
    pids = set()
    try:
        for hit in hits:
            row = source.execute('SELECT payload FROM chunks WHERE chunk_id=?', (hit['chunk_id'],)).fetchone()
            if row is None:
                raise SemanticUnavailable('原文片段索引失配')
            hit['chunk'] = json.loads(row[0])
            hit.pop('_bm25', None)
            pids.update(p['passage_id'] for p in hit['chunk']['parts'])
    finally:
        source.close()
    plan = {**plan, 'retrieval_unit': 'source_chunk', 'context_version': RETRIEVAL_VERSION,
            'grounding_unit': 'source_chunk'}
    return hits, trace, plan, sorted(pids)
