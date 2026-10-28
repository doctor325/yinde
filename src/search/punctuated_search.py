"""Search a separate, source-bound punctuated edition; never relabel old hits."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from search import punctuated, zh

VERSION = 'punctuated-chunks-v1'
MAX_CHARS = 240
OVERLAP = 40
# Windows script conversion leaves this common traditional function word intact.
# Apply only to lookup expressions, never to quotations or source identities.
LOOKUP_EQUIVALENTS = (('於','于'),)


def folder():
    return punctuated.index_path().parent / 'search-index'


def identity(path):
    s = path.stat()
    return dict(bytes=s.st_size, mtime_ns=s.st_mtime_ns)


def normalize(text):
    # Punctuation is ignored only in a derived lookup field, never in the quote.
    return ''.join(c for c in zh.to_simplified(text).lower()
                   if unicodedata.category(c)[0] in 'LN')


def tokens(query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ValueError('检索内容须为1至300字')
    def lookup(t):
        t = normalize(t)
        for old,new in LOOKUP_EQUIVALENTS:t=t.replace(old,new)
        return t
    terms = list(dict.fromkeys(lookup(t) for t in query.split()))
    terms = [t for t in terms if t]
    if not terms or len(terms) > 12:
        raise ValueError('请输入文字，最多12组空格分隔的词句')
    return terms


def iter_chunks(conn):
    """Contiguous source blocks, with hard boundaries at layers/tables/images."""
    rows = conn.execute('SELECT c.*,b.title AS book_title FROM chapters c JOIN books b USING(book_id) ORDER BY c.page_id')
    cid = 0
    for row in rows:
        d = dict(row)
        if d['status'] not in ('readable','insufficient_punctuation','partial_render'):
            continue
        blocks = json.loads(d['blocks_json'])
        runs, current, layer, heading = [], [], None, ''
        def flush():
            nonlocal current
            if current:
                runs.append((current, layer, heading))
                current = []
        for i, b in enumerate(blocks):
            kind = b.get('kind')
            next_layer = 'annotation' if kind == 'annotation' else 'body' if kind in ('body','glyph') else None
            if next_layer != layer or next_layer is None:
                flush()
            if kind == 'heading':
                heading = b.get('text', '')
            layer = next_layer
            if layer and b.get('text'):
                current.append((i,b['text']))
        flush()
        for run, layer, heading in runs:
            text = ''.join(t for _,t in run)
            offsets, at = [], 0
            for block, value in run:
                offsets.append((block,at,at+len(value)))
                at += len(value)
            start = 0
            while start < len(text):
                end = min(start + MAX_CHARS, len(text))
                if end < len(text):
                    boundaries = [m.end()+start for m in re.finditer(r'[。！？；\n]',text[start:end])]
                    end = max([b for b in boundaries if b >= start+MAX_CHARS//2], default=end)
                quote = text[start:end]
                if normalize(quote):
                    cid += 1
                    parts = [dict(block_index=i,start=max(start,a)-a,end=min(end,b)-a)
                             for i,a,b in offsets if a < end and b > start]
                    yield dict(chunk_id=cid,page_id=d['page_id'],book_id=d['book_id'],book_title=d['book_title'],
                               title=d['title'],heading=heading,layer=layer,status=d['status'],reason=d['reason'],
                               text_orig=quote,parts=parts,edition='维基文库录入本 · 标点版',
                               revision_id=d['revision_id'],source_url=d['source_url'],history_url=d['history_url'],
                               license_url=d['license_url'],attribution=d['attribution'],source_sha256=d['source_sha256'],
                               text_sha256=hashlib.sha256(quote.encode()).hexdigest())
                if end == len(text):
                    break
                start = max(start+1,end-OVERLAP)


def status():
    try:
        manifest = json.loads((folder()/'manifest.json').read_text(encoding='utf8'))
        if (not manifest.get('complete') or manifest.get('version') != VERSION
                or manifest['library'] != identity(punctuated.index_path())):
            return dict(ready=False,reason='标点版索引尚未完成或已过期')
        if not (folder()/'chunks.sqlite').is_file() or not (folder()/'vectors.npy').is_file():
            return dict(ready=False,reason='标点版索引文件缺失')
        return dict(ready=True,reason='直接检索标点版独立索引',manifest=manifest)
    except (OSError,ValueError,KeyError):
        return dict(ready=False,reason='标点版索引尚未完成')


@contextmanager
def connect(path=None):
    if path is None:
        if not status()['ready']:
            raise FileNotFoundError(status()['reason'])
        path = folder()/'chunks.sqlite'
    conn = sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA temp_store=MEMORY')
        yield conn
    finally:
        conn.close()


def lexical(conn, query, book=None, page=1, page_size=20, *, exists_only=False):
    terms = tokens(query)
    if not 1 <= page <= 10000 or not 1 <= page_size <= 100:
        raise ValueError('页码或每页数量超出范围')
    if book and not conn.execute('SELECT 1 FROM books WHERE book_id=?',(book,)).fetchone():
        return dict(hits=[],total=0,book_available=False)
    # The FTS sidecar preserves simplified source text. An equivalence-expanded
    # term must not be prefiltered by just one form (which would lose the other).
    longer = [t for t in terms if len(t) >= 3 and not any(new in t for _,new in LOOKUP_EQUIVALENTS)]
    table = 'chunks c'
    if not longer and conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='chunks_lookup'").fetchone():
        # A covering index avoids scanning large citation JSON for short words.
        # LIKE remains the final predicate; no candidate or rank is approximated.
        table += ' INDEXED BY chunks_lookup'
    where, args = [], []
    if longer:
        table += ' JOIN chunk_fts ON chunk_fts.rowid=c.chunk_id'
        where.append('chunk_fts MATCH ?')
        args.append(' AND '.join('"'+t.replace('"','""')+'"' for t in longer))
    for term in terms:
        expression = 'c.search_text'
        for old,new in LOOKUP_EQUIVALENTS:
            if new in term:expression=f"replace({expression},'{old}','{new}')"
        where.append(expression+' LIKE ?')
        args.append('%'+term+'%')  # normalize retained only letters/numbers.
    if book:
        where.append('c.book_id=?');args.append(book)
    sql = ' FROM '+table+' WHERE '+' AND '.join(where)
    if exists_only:
        present = conn.execute('SELECT 1'+sql+' LIMIT 1',args).fetchone() is not None
        return dict(hits=[],total=int(present),book_available=True)
    total = conn.execute('SELECT count(*)'+sql,args).fetchone()[0]
    order = 'bm25(chunk_fts),c.chunk_id' if longer else 'c.chunk_id'
    rows = conn.execute('SELECT c.payload'+sql+' ORDER BY '+order+' LIMIT ? OFFSET ?',
                        args+[page_size,(page-1)*page_size]).fetchall()
    return dict(hits=[dict(chunk=json.loads(r[0]),channels=['keyword']) for r in rows],total=total,book_available=True)


def fused(dense, batches, limit=20):
    candidates = {}
    # One lexical channel, interleaved across rule probes to limit entity-only domination.
    lexical_hits, seen = [], set()
    for offset in range(max((len(b) for b in batches),default=0)):
        for batch in batches:
            if offset < len(batch):
                hit = batch[offset];cid = hit['chunk']['chunk_id']
                if cid not in seen:
                    seen.add(cid);lexical_hits.append(hit)
    for label,hits in (('semantic',dense),('keyword',lexical_hits[:60])):
        for rank,hit in enumerate(hits,1):
            cid = hit['chunk']['chunk_id']
            value = candidates.setdefault(cid,dict(chunk=hit['chunk'],channels=[],ranks={},rrf=0))
            value['channels'].append(label);value['ranks'][label]=rank;value['rrf']+=1/(60+rank)
            if 'semantic_score' in hit:value['semantic_score']=hit['semantic_score']
    selected = []
    def overlap(a,b):
        if a['page_id']!=b['page_id']:return False
        shared = sum(max(0,min(x['end'],y['end'])-max(x['start'],y['start']))
                     for x in a['parts'] for y in b['parts'] if x['block_index']==y['block_index'])
        return shared/max(1,min(len(a['text_orig']),len(b['text_orig'])))>=.8
    for value in sorted(candidates.values(),key=lambda h:(-h['rrf'],h['chunk']['chunk_id'])):
        if not any(overlap(value['chunk'],v['chunk']) for v in selected):selected.append(value)
        if len(selected)>=limit:break
    return selected


def run(query, book=None, mode='keyword', page=1, cur=None):
    tokens(query)
    if mode not in ('keyword','semantic','hybrid','expanded','relation','evidence'):
        raise ValueError('未知标点版检索方式')
    if mode != 'keyword' and page != 1:
        raise ValueError('语义候选仅提供前20条')
    started = time.perf_counter()
    timings = {}
    with connect() as conn:
        phase = time.perf_counter()
        raw = lexical(conn,query,book,page)
        timings['literal_ms'] = round((time.perf_counter()-phase)*1000,2)
        plan, probes, dense = None, [], []
        batches = [raw['hits']] if mode != 'semantic' else []
        if mode in ('expanded','relation','evidence') and not raw['total'] and raw['book_available']:
            from search.semantic_candidates import plan_query
            # Request-local memoization only: repeated grounding checks share
            # the same read connection, edition and book scope.
            grounded = {}
            def exists(term):
                if term not in grounded:
                    grounded[term] = bool(lexical(conn,term,book,exists_only=True)['total'])
                return grounded[term]
            phase = time.perf_counter()
            plan = plan_query(cur,query,book,literal_exists=exists)
            timings['planning_ms'] = round((time.perf_counter()-phase)*1000,2)
            phase = time.perf_counter()
            for q in plan['search_queries']:
                probe_started = time.perf_counter()
                found = lexical(conn,q,book,page_size=20)
                batches.append(found['hits']);probes.append(dict(query=q,total=found['total'],
                    elapsed_ms=round((time.perf_counter()-probe_started)*1000,2)))
            timings['expanded_lexical_ms'] = round((time.perf_counter()-phase)*1000,2)
        if mode != 'keyword' and raw['book_available']:
            from search.semantic_bridge import worker
            phase = time.perf_counter()
            dense = worker.query(query,book=book,top_k=60,_punctuated=True)['hits']
            timings['semantic_ms'] = round((time.perf_counter()-phase)*1000,2)
        phase = time.perf_counter()
        hits = raw['hits'] if mode == 'keyword' else fused(dense,batches)
        timings['fusion_ms'] = round((time.perf_counter()-phase)*1000,2)
        experiment = None
        if mode == 'relation' and raw['book_available']:
            from search import punctuated_relevance
            experiment = punctuated_relevance.evaluate(query,conn,dense,book,baseline_hits=hits)
            hits = experiment['hits']
            timings['relation_ms'] = experiment['elapsed_ms']
        evidence = None
        if mode == 'evidence' and raw['book_available']:
            from search import evidence_pipeline
            evidence = evidence_pipeline.evaluate(query,conn,dense,batches,book)
            hits = evidence['hits']
            timings['evidence_ms'] = evidence['pipeline']['elapsed_ms']
        result = dict(query=query,book=book,mode=mode,page=page,page_size=20,hits=hits,
                      total=raw['total'] if mode=='keyword' else len(hits),book_available=raw['book_available'],
                      query_plan=plan,lexical_searches=probes,source_kind='wikisource',
                      candidate_counts=dict(semantic=len(dense),keyword=sum(len(b) for b in batches)),
                      elapsed_ms=round((time.perf_counter()-started)*1000,2),timings=timings,model_generation_calls=0)
        if evidence is not None:
            result['query_plan'] = evidence['query_plan']
            result['evidence_pipeline'] = evidence['pipeline']
            result['audited_candidate_ids'] = evidence['candidate_ids']
        if experiment is not None:
            result['baseline_query_plan'] = plan
            result['query_plan'] = experiment['query_plan']
            result['relation_experiment'] = {key:experiment[key] for key in ('selection','candidate_scan')}
            result['relation_experiment']['relation_verified'] = False
            result['relation_experiment']['candidate_counts'] = experiment['candidate_counts']
    import logging,uuid
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger('yinde.punctuated.search')
    if not logger.handlers:
        logger.addHandler(RotatingFileHandler(punctuated.index_path().parent/'search-queries.jsonl',maxBytes=5000000,backupCount=2,encoding='utf8'))
        logger.setLevel(logging.INFO);logger.propagate=False
    result['trace_id']=uuid.uuid4().hex
    logger.info(json.dumps({**{k:v for k,v in result.items() if k!='hits'},'selected':[
        dict(chunk_id=h['chunk']['chunk_id'],page_id=h['chunk']['page_id'],revision_id=h['chunk']['revision_id'],
             **({'evidence_audit':h['evidence_audit'],'context_relevance_score':h['context_relevance_score']} if 'evidence_audit' in h else {}),
             **({'relation_clues':h['relation_clues']} if 'relation_clues' in h else {})) for h in hits]},ensure_ascii=False))
    return result


class DenseIndex:
    def __init__(self, encoder):
        import numpy as np
        self.np,self.encoder = np,encoder
        if not status()['ready']:raise ValueError(status()['reason'])
        self.signature = (folder()/'manifest.json').read_bytes()
        self.manifest = json.loads(self.signature)
        from search.semantic_local import ENCODER_VERSION
        from search.semantic_bridge import state_dir
        receipt=json.loads((state_dir()/'onnx-receipt.json').read_text(encoding='utf8'))
        if (self.manifest['encoder_version']!=ENCODER_VERSION
                or self.manifest['encoder_artifact_sha256']!=receipt['onnx_sha256']):
            raise ValueError('标点索引与编码器不一致')
        with (folder()/'chunks.sqlite').open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()!=self.manifest['chunks_sha256']:
                raise ValueError('标点片段索引校验失败')
        self.vectors=np.load(folder()/'vectors.npy',mmap_mode='r',allow_pickle=False)
        if self.vectors.shape!=tuple(self.manifest['vector_shape']) or self.vectors.dtype!=np.dtype('float32'):
            raise ValueError('标点向量矩阵不完整')

    def query(self, query, book=None, top_k=60):
        if not status()['ready'] or (folder()/'manifest.json').read_bytes()!=self.signature:
            raise ValueError('标点索引变化，请重启查询进程')
        np=self.np
        with connect() as conn:
            ids=None if not book else np.array([r[0]-1 for r in conn.execute('SELECT chunk_id FROM chunks WHERE book_id=? ORDER BY chunk_id',(book,))],dtype='int64')
            if ids is not None and not len(ids):return dict(hits=[])
            vector=self.encoder.encode([query],query=True)[0]
            scores=self.vectors@vector if ids is None else np.empty(len(ids),dtype='float32')
            if ids is not None:
                for start in range(0,len(ids),4096):scores[start:start+4096]=self.vectors[ids[start:start+4096]]@vector
            k=min(max(1,min(int(top_k),100)),len(scores))
            selected=np.argpartition(-scores,k-1)[:k] if k else []
            selected=sorted(selected,key=lambda i:(-float(scores[i]),int(i)))
            hits=[]
            for i in selected:
                cid=int(i if ids is None else ids[i])+1
                chunk=json.loads(conn.execute('SELECT payload FROM chunks WHERE chunk_id=?',(cid,)).fetchone()[0])
                hits.append(dict(chunk=chunk,semantic_score=float(scores[i])))
            if (folder()/'manifest.json').read_bytes()!=self.signature:raise ValueError('查询中索引变化')
            return dict(hits=hits)
