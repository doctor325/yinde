"""Opt-in indexed implementation of variant LIKE retrieval; not default engine.

The experiment applies necessary FTS conditions to prune a superset, then runs
the original exact LIKE predicates/order/pagination. No BM25 reranking is added.
"""
import re
from search import engine as E, variants as V, zh

VERSION = 'variant-like-prefilter-v1'


def candidate_filter(cur, groups):
    indexed = {'passages_fts': [], 'passages_bg': []}
    trigram = E.fts_tokenizer(cur) == 'trigram'
    bigram = E.has_bigram_fts(cur)
    for group in groups:
        seeds = []
        for form in group:
            runs = re.findall(r'[\u3400-\u9fff]{2,}', form)
            if not runs:
                break
            seeds.append(max(runs, key=len))
        # Every spelling must be represented; never silently drop an OR arm.
        if len(seeds) != len(group) or not seeds:
            continue
        if trigram and all(len(s) >= 3 for s in seeds):
            table = 'passages_fts'
        elif bigram:
            table = 'passages_bg'
            seeds = [s[:2] for s in seeds]
        else:
            continue
        indexed[table].append('('+' OR '.join(E.fts_match_text([s]) for s in dict.fromkeys(seeds))+')')
    where, args = [], []
    for table, expressions in indexed.items():
        if expressions:
            where.append(f'p.passage_id IN (SELECT rowid FROM {table} WHERE {table} MATCH ?)')
            args.append(' AND '.join(expressions))
    return where, args


def run_search(cur, q, book=None, edition=None, page=1, page_size=20, variants=False):
    groups = V.keyword_groups(q) if variants else []
    if not groups or not any(len(g)>1 for g in groups):
        return E.run_search(cur,q,book=book,edition=edition,page=page,page_size=page_size,variants=variants)
    if page < 1:
        raise ValueError('页码从 1 开始')
    page_size=min(max(page_size,1),E.PAGE_SIZE_MAX)
    q_trad=zh.to_traditional(q.strip())
    bid=E.resolve_book(cur,book)
    edition=E.resolve_edition(edition)
    where,args=["p.kind = 'passage'"],[]
    if bid:
        where.append('b.book_id = ?');args.append(bid)
    if edition:
        where.append('LOWER(b.family) = ?');args.append(edition)
    for group in groups:
        conditions=[E._like_cond(t) for t in group]
        where.append('('+' OR '.join(c for c,_ in conditions)+')')
        args.extend(a for _,a in conditions)
    filters,values=candidate_filter(cur,groups)
    where.extend(filters);args.extend(values)
    total,rows=E._query(cur,where,args,'ORDER BY b.book_id, f.file_no, p.row_no, p.seq',page,page_size)
    return {'q':q.strip(),'q_traditional':q_trad,'mode':'like','book':bid or '全部',
            'edition':edition or '全部','total':total,'page':page,'page_size':page_size,
            'items':[E._shape_row(r) for r in rows]}


def lexical(cur, query, book=None):
    from search.semantic_candidates import lexical as planned
    return planned(cur,query,book,search_fn=run_search)
