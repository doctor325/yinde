"""Explicit relation-clue experiment over unchanged punctuated source chunks.

No historical names or answers are encoded. Clues are lexical hypotheses, not
verified entity identities, argument roles, causal claims or truth judgements.
"""
import json
import re
import time
from search import punctuated_search as S, zh

VERSION = 'punctuated-relation-clues-v3'
RULES = (
    ('death', ('去世','逝世','死亡','死的','死了','卒于'), ('卒','薨','崩','殁','死')),
    ('birth', ('出生','诞生'), ('生','诞')),
    ('accession', ('即位','登基','继位','被立为'), ('即位','立','践祚')),
    ('appointment', ('担任','出任','被任命为','任命为','任命','任职','改任','获封','封为'), ('拜','为','授','知','封','徙','入相')),
    ('movement', ('迁往','迁移','迁都','逃往','出使','到达','进入','入城'), ('迁','徙','奔','使','至','入')),
    ('military', ('击败','战胜','战败','攻打','攻取','征伐','收复','讨伐'), ('破','败','伐','克','战','复','讨')),
    ('document', ('撰写','著书','编写','制定','编纂','进呈','开通'), ('撰','作','修','著','上','造','通')),
    ('exam', ('考中','考取','中举'), ('进士','及第','登第','举')),
    ('resignation', ('离开官职','辞官','罢免','罢官','贬谪','被贬'), ('去官','免','罢','贬','安置')),
    ('kinship', ('父亲','母亲','儿子','亲属','父子','婚姻','娶妻'), ('父','母','子','姊','弟','娶','妻')),
    ('quantity', ('多少','几次','几年','几卷'), ('一','二','三','四','五','六','七','八','九','十','百','千','万')),
)
FILLER = ('哪些史料记载','哪些文献记载','相关记载','相关史料','史料记载','的记载','的史料',
          '什么时候','在什么地方','在哪一年','有什么关系','的关系','有哪些记载','有哪些','在哪里',
          '是什么','有哪些史料','史料','证据','记载','为什么','为何','如何','怎样','之前是谁',
          '再次','以后','之前','之后','有关','关于','查找','的经过','的时间','的事','的用途','的卷数',
          '何时','是否','谁是')
DYNASTY_BOOKS = {'唐':('旧唐书','新唐书'),'宋':('宋史',),'明':('明史',),'晋':('晋书',)}


def plan(query):
    text=zh.to_simplified(query)
    rules=[(name,asks,terms) for name,asks,terms in RULES if any(a in text for a in asks)]
    # Objects (e.g. a parent) are kept as constraints even when they trigger a class.
    separators=list(FILLER)+[a for _,asks,_ in rules for a in asks if a not in ('父亲','母亲','儿子')]
    pattern='|'.join(re.escape(x) for x in sorted(set(separators),key=len,reverse=True))
    body=re.sub(pattern,'|',text)
    body=body.replace('与|','|')
    # Never split a one-character name prefix from 和 (郑和, 张和, etc.).
    body=re.sub(r'([\u3400-\u9fff]{2,})[与和]([\u3400-\u9fff]{2,})',r'\1|\2',body)
    body=re.sub(r'(?<=[\u3400-\u9fff]{2})在(?=[\u3400-\u9fff]{2})','|',body)
    body=re.sub(r'[从到因的？?，,。；;：:！!]|与$', '|',body)
    fragments=[]
    for f in re.findall(r'[\u3400-\u9fff]{2,16}',body):
        f=f.removeprefix('哪些').removeprefix('是').removesuffix('了')
        if f in ('地方','时间','官职','事情','发生','亲属','关系'):continue
        if len(f)>=2 and f not in fragments:fragments.append(f)
    clues=[dict(text=f,forms=[S.normalize(f)],scope_titles=[]) for f in fragments[:6]]
    for c in clues:
        f=c['text']
        if len(f)==3 and f[0] in DYNASTY_BOOKS and f.endswith(('祖','宗')):
            c['scoped_form']=f[1:];c['scope_titles']=list(DYNASTY_BOOKS[f[0]])
        if f in ('母亲','父亲','儿子'):
            c['forms'].append({'母亲':'母','父亲':'父','儿子':'子'}[f])
        if f=='宰相':c['forms']+=['丞相','同平章事','入相','为相']
    return dict(version=VERSION,clues=clues,relation_groups=[dict(kind=n,terms=list(t)) for n,_,t in rules],
                relation_verified=False,notes=['字面线索与近邻仅用于选取候选，未核实角色、否定、因果或人物同一性。'])


def forms_for(clue, chunk):
    forms=list(clue['forms'])
    if 'scoped_form' in clue and S.normalize(chunk['book_title']) in clue['scope_titles']:
        forms.append(clue['scoped_form'])
    return forms


def features(chunk, query_plan):
    text=S.normalize(chunk['text_orig'])
    clues=query_plan['clues']
    positions=[]
    for clue in clues:
        positions.append([m.span() for form in forms_for(clue,chunk) for m in re.finditer(re.escape(form),text)])
    covered=[i for i,p in enumerate(positions) if p]
    # Use punctuation-bounded clauses for relation proximity; normalized search
    # text alone would incorrectly join unrelated sentences.
    clauses=[S.normalize(x) for x in re.split(r'[。！？；\n]',chunk['text_orig']) if x.strip()]
    groups=[]
    for group in query_plan['relation_groups']:
        bound=False
        for clause in clauses:
            subjects=[m.span() for form in (forms_for(clues[0],chunk) if clues else []) for m in re.finditer(re.escape(form),clause)]
            predicates=[m.span() for term in group['terms'] for m in re.finditer(re.escape(term),clause)]
            if any(max(0,c-b,a-d)<=24 for a,b in subjects for c,d in predicates):bound=True;break
        if bound:groups.append(group['kind'])
    from search.punctuated_roles import assess
    return dict(clues_present=[clues[i]['text'] for i in covered],clues_total=len(clues),
                all_clues=bool(clues) and len(covered)==len(clues),relation_groups_near=groups,
                subject_syntax=assess(chunk,query_plan,forms_for),
                relation_verified=False)


def candidates(conn, query_plan, book=None, budget=4000):
    """Collect a bounded subject pool before relation selection, not first 20 rows."""
    if not query_plan['clues']:return [],dict(scanned=0,truncated=False)
    clue=query_plan['clues'][0]
    forms=clue['forms']
    conditions=['search_text LIKE ?' for _ in forms];args=['%'+f+'%' for f in forms]
    if 'scoped_form' in clue:
        ids=[r[0] for r in conn.execute('SELECT book_id,title FROM books') if S.normalize(r[1]) in clue['scope_titles']]
        if ids:
            conditions.append('(search_text LIKE ? AND book_id IN ('+','.join('?' for _ in ids)+'))')
            args+=['%'+clue['scoped_form']+'%']+ids
    where='('+' OR '.join(conditions)+')'
    if book:where+=' AND book_id=?';args.append(book)
    indexed=" INDEXED BY chunks_lookup" if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_lookup' AND type='index'").fetchone() else ''
    rows=conn.execute('SELECT chunk_id FROM chunks'+indexed+' WHERE '+where+' ORDER BY chunk_id LIMIT ?',args+[budget+1]).fetchall()
    hits=[dict(chunk=json.loads(conn.execute('SELECT payload FROM chunks WHERE chunk_id=?',(r[0],)).fetchone()[0]),channels=['relation_clues']) for r in rows[:budget]]
    return hits,dict(scanned=min(len(rows),budget),truncated=len(rows)>budget,budget=budget)


def rank(dense, lexical, query_plan):
    pool={h['chunk']['chunk_id']:dict(h) for h in lexical}
    for i,h in enumerate(dense):
        c=pool.setdefault(h['chunk']['chunk_id'],dict(h,channels=['semantic']))
        c['semantic_score']=h['semantic_score'];c['semantic_rank']=i+1
        if 'semantic' not in c['channels']:c['channels']=c['channels']+['semantic']
    for c in pool.values():c['relation_clues']=features(c['chunk'],query_plan)
    def key(h):
        f=h['relation_clues']
        return (-int(f['all_clues']),-len(f['subject_syntax']['positive_groups']),
                -len(f['relation_groups_near']),-len(f['clues_present']),
                h.get('semantic_rank',1000),h['chunk']['chunk_id'])
    ranked=sorted(pool.values(),key=key)
    # Reuse overlap exclusion, then restore the channel and evidence metadata.
    chosen=S.fused([], [ranked],limit=20)
    return [pool[h['chunk']['chunk_id']] for h in chosen],ranked


def evaluate(query, conn, dense, book=None, *, baseline_hits=None, baseline_pool=None):
    started=time.perf_counter();p=plan(query)
    lexical,scan=candidates(conn,p,book)
    selected,ranked=rank(dense,lexical,p)
    supported=bool(p['relation_groups'] and any(h['relation_clues']['all_clues']
                   and h['relation_clues']['relation_groups_near'] for h in ranked))
    if not supported and baseline_hits is not None:
        selected=baseline_hits
        ranked=baseline_pool if baseline_pool is not None else baseline_hits
    return dict(hits=selected,ranked=ranked,query_plan=p,candidate_scan=scan,
                needs_baseline=not supported,selection='relation_clues' if supported else 'baseline_fallback',
                elapsed_ms=round((time.perf_counter()-started)*1000,2),
                candidate_counts=dict(semantic=len(dense),keyword=len(lexical)))
