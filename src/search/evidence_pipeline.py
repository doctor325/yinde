"""Local question -> multiple retrieval channels -> contextual evidence audit.

Pairwise model scores measure relevance, never entailment or historical truth.
The conservative audit reports unmet slots rather than generating an answer.
"""
import json
import math
import re
import time
from search import punctuated_relevance as R, punctuated_search as S, zh

VERSION = 'context-evidence-v2'
# Each independent channel keeps its own quota before deduplication. A shared
# 24-slot round robin silently discarded even a channel's tenth-ranked hit.
CHANNEL_LIMIT = 20
ANSWER_PATTERNS = {
    'time':r'[元一二三四五六七八九十百千〇零\d]+年|[正一二三四五六七八九十冬腊]+月|[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]',
    'place':r'(?:于|於|至|在)[\u3400-\u9fff]{1,12}',
    'reason':r'(?:以|因|坐|为|為)[^。；]{1,16}(?:故|罪|丧|喪)|故|缘|緣',
    'quantity':r'[一二三四五六七八九十百千\d]+(?:年|次|卷|人|里)',
}


def parse(query):
    p=R.plan(query);text=zh.to_simplified(query)
    # Asking where to find a record is not asking where its event happened.
    text=re.sub(r'(?:史料|记载|文献|证据)(?:在)?(?:哪里|何处|什么地方)', '', text)
    slots=[]
    for name,pattern in (('time',r'何时|什么时候|哪一年|时间'),('place',r'哪里|何处|什么地方'),
                         ('reason',r'为什么|为何|原因'),('quantity',r'多少|几次|几年|几卷')):
        if re.search(pattern,text):slots.append(name)
    p.update(answer_slots=slots,subject=p['clues'][:1],constraints=p['clues'][1:],
             unsupported=not bool(p['clues'] and p['relation_groups']),pipeline_version=VERSION)
    return p


def grounded_fallback(conn, p, subject, book=None):
    """Corpus-grounded prefix, only when the parser's whole subject has no hits.

    A lexical hypothesis is not an identified person or a parsed relationship.
    Keep the original plan/audit unsupported. No names or answers are encoded.
    """
    report=dict(attempts=0, hypothesis=None, identity_verified=False)
    if subject or not p['clues']:
        return [], report
    fragment=p['clues'][0]['text']
    for size in range(min(len(fragment)-1, 12), 1, -1):
        prefix=fragment[:size]
        report['attempts']+=1
        if not S.lexical(conn, prefix, book, exists_only=True)['total']:
            continue
        plan={**p, 'clues':[dict(text=prefix, forms=[S.normalize(prefix)], scope_titles=[])]}
        pool, scan=R.candidates(conn, plan, book)
        # Remaining query bigrams retain event/object clues without requiring
        # an exact modern verb to be present in classical source wording.
        remainder=S.normalize(fragment[size:]+''.join(c['text'] for c in p['clues'][1:]))
        grams=set(remainder[i:i+2] for i in range(len(remainder)-1))
        pool.sort(key=lambda h:(-sum(g in S.normalize(h['chunk']['text_orig']) for g in grams),
                                h['chunk']['chunk_id']))
        report.update(hypothesis=prefix, fragment=fragment, remaining_bigrams=sorted(grams), scan=scan)
        return pool, report
    return [], report


def admit(channels):
    """Retain each channel's top 20; record provenance even past its quota."""
    selected={}
    for name, hits in channels:
        for h in hits[:CHANNEL_LIMIT]:
            cid=h['chunk']['chunk_id']
            if cid not in selected:
                selected[cid]=dict(chunk=h['chunk'], channels=[], channel_ranks={})
    for name, hits in channels:
        for rank,h in enumerate(hits,1):
            cid=h['chunk']['chunk_id']
            if cid in selected:
                value=selected[cid]
                if name not in value['channels']:
                    value['channels'].append(name);value['channel_ranks'][name]=rank
                if 'semantic_score' in h:value['semantic_score']=h['semantic_score']
    return list(selected.values())


def focus(chunk, p):
    """Choose an exact source sentence for model attention, not an answer.

    Never cut away qualifications inside the selected sentence. If the rule
    plan offers no event/object alignment, leave the whole candidate intact.
    """
    best=None
    for match in re.finditer(r'[^。！？；\n]+[。！？；]?',chunk['text_orig']):
        f=R.features({**chunk,'text_orig':match.group(0)},p)
        if not (f['relation_groups_near'] or len(f['clues_present'])>=2):continue
        key=(int(f['all_clues']),len(f['clues_present']),len(f['relation_groups_near']),
             len(f['subject_syntax']['positive_groups']))
        if best is None or key>best[0]:best=(key,match)
    if best is None:
        return dict(start=0,end=len(chunk['text_orig']),text=chunk['text_orig'],selected=False)
    match=best[1]
    return dict(start=match.start(),end=match.end(),text=match.group(0),selected=True)


def context(conn, chunk):
    """Adjacent index chunks only within the exact page/revision/layer/heading."""
    result=[]
    for cid in (chunk['chunk_id']-1,chunk['chunk_id'],chunk['chunk_id']+1):
        row=conn.execute('SELECT payload FROM chunks WHERE chunk_id=?',(cid,)).fetchone()
        if row:
            c=json.loads(row[0])
            if all(c.get(k)==chunk.get(k) for k in ('page_id','book_id','revision_id','source_sha256','layer','heading')):
                result.append(c)
    return result


def audit(chunk, p, neighbors):
    f=R.features(chunk,p)
    missing=[]
    if p['unsupported']:missing.append('无法可靠解析问题关系')
    if not f['all_clues']:missing.append('主体或约束未在当前片段同时出现')
    observations=f['subject_syntax']['observations']
    positive=[o for o in observations if o['polarity']=='positive_syntax']
    needed={g['kind'] for g in p['relation_groups']}
    if not needed.issubset({o['kind'] for o in positive}) or not needed:
        missing.append('缺少全部关系的明确主谓句式')
    # A nearby date/place in another passage cannot silently answer this event.
    slot_evidence={}
    for slot in p['answer_slots']:
        quotes=[o['quote'] for o in positive if re.search(ANSWER_PATTERNS[slot],zh.to_simplified(o['quote']))]
        slot_evidence[slot]=list(dict.fromkeys(quotes))
        if not quotes:missing.append('当前事件句缺少'+{'time':'时间','place':'地点','reason':'原因','quantity':'数量'}[slot]+'线索')
    # Context is available for reading/model scoring, but not identity invention.
    adjacent_only=any(R.features(c,p)['all_clues'] for c in neighbors if c['chunk_id']!=chunk['chunk_id'])
    return dict(status='clues_present' if not missing else 'insufficient',missing=missing,
                answer_slot_evidence=slot_evidence,subject_syntax=f['subject_syntax'],
                context_has_clues=adjacent_only,relation_verified=False,
                note='要素与句式检查，不等于答案正确；跨句指代、否定与因果未核实。')


def evaluate(query,conn,dense,batches,book=None,rerank=None):
    started=time.perf_counter();p=parse(query)
    subject,scan=R.candidates(conn,p,book)
    _,relation_ranked=R.rank(dense,subject,p)
    literal=S.fused([],batches,limit=60)
    fallback,grounding=grounded_fallback(conn,p,subject,book)
    channels=(('semantic',dense),('lexical',literal),('relation',relation_ranked),('grounded',fallback))
    hits=admit(channels);texts=[]
    candidate_ids=[h['chunk']['chunk_id'] for h in hits]
    for h in hits:
        c=h['chunk'];neighbors=context(conn,c)
        h['context_chunks']=neighbors
        h['evidence_audit']=audit(c,p,neighbors)
        # Center the pairwise comparison on the event sentence. Large unrelated
        # annal entries next to a six-character event used to dominate scoring.
        f=h['evidence_focus']=focus(c,p)
        text=c['book_title']+' '+c.get('heading','')+'\n当前原文：'+f['text']
        before=c['text_orig'][max(0,f['start']-60):f['start']]
        after=c['text_orig'][f['end']:f['end']+60]
        prev=[n['text_orig'][-60:] for n in neighbors if n['chunk_id']<c['chunk_id']]
        nxt=[n['text_orig'][:60] for n in neighbors if n['chunk_id']>c['chunk_id']]
        text+='\n前文：'+(before or '\n'.join(prev))+'\n后文：'+(after or '\n'.join(nxt))
        h['context_truncated']=len(text)>1000;texts.append(text[:1000])
    model_report={'scores':[],'truncated_indices':[],'ms':0}
    if hits:
        if rerank is None:
            from search.semantic_bridge import worker
            rerank=worker.rerank
        model_report=rerank(query,texts)
        scores=model_report['scores']
        if len(scores)!=len(hits) or not all(isinstance(s,(float,int)) and math.isfinite(s) for s in scores):
            raise ValueError('上下文模型返回了无效评分')
        for i,(h,score) in enumerate(zip(hits,scores)):
            h['context_relevance_score']=score
            h['context_truncated']|=i in model_report.get('truncated_indices',[])
            h['retrieval_rank']=i+1
    model_ranks={h['chunk']['chunk_id']:i for i,h in enumerate(sorted(hits,
        key=lambda h:(-h['context_relevance_score'],h['retrieval_rank'])),1)}
    hits.sort(key=lambda h:(h['evidence_audit']['status']!='clues_present',-h['context_relevance_score'],h['retrieval_rank']))
    diagnostics=[dict(chunk_id=h['chunk']['chunk_id'], channel_ranks=h['channel_ranks'],
                      model_rank=model_ranks[h['chunk']['chunk_id']],final_rank=i,
                      score=h['context_relevance_score'], audit_status=h['evidence_audit']['status'],
                      displayed=i<=20) for i,h in enumerate(hits,1)]
    return dict(hits=hits[:20],query_plan=p,pipeline=dict(version=VERSION,
        candidate_counts=dict(semantic=len(dense),lexical=len(literal),subject=len(subject),context_scored=len(hits)),
        channel_quota=CHANNEL_LIMIT,grounded_fallback=grounding,
        admission={name:dict(available=len(items),reserved=min(len(items),CHANNEL_LIMIT),
                            admitted=sum(name in h['channels'] for h in hits)) for name,items in channels},
        candidate_diagnostics=diagnostics,
        candidate_scan=scan,reranker_ms=model_report['ms'],
        audit_clues_present=sum(h['evidence_audit']['status']=='clues_present' for h in hits[:20]),
        model='BAAI/bge-reranker-base',model_generation_calls=0,
        elapsed_ms=round((time.perf_counter()-started)*1000,2)),candidate_ids=candidate_ids)
