"""Local question -> multiple retrieval channels -> contextual evidence audit.

Pairwise model scores measure relevance, never entailment or historical truth.
The conservative audit reports unmet slots rather than generating an answer.
"""
import json
import math
import re
import time
from search import punctuated_relevance as R, punctuated_search as S, zh

VERSION = 'context-evidence-v1'
LIMIT = 24
ANSWER_PATTERNS = {
    'time':r'[元一二三四五六七八九十百千〇零\d]+年|[正一二三四五六七八九十冬腊]+月|[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]',
    'place':r'(?:于|於|至|在)[\u3400-\u9fff]{1,12}',
    'reason':r'(?:以|因|坐|为|為)[^。；]{1,16}(?:故|罪|丧|喪)|故|缘|緣',
    'quantity':r'[一二三四五六七八九十百千\d]+(?:年|次|卷|人|里)',
}


def parse(query):
    p=R.plan(query);text=zh.to_simplified(query)
    slots=[]
    for name,pattern in (('time',r'何时|什么时候|哪一年|时间'),('place',r'哪里|何处|什么地方'),
                         ('reason',r'为什么|为何|原因'),('quantity',r'多少|几次|几年|几卷')):
        if re.search(pattern,text):slots.append(name)
    p.update(answer_slots=slots,subject=p['clues'][:1],constraints=p['clues'][1:],
             unsupported=not bool(p['clues'] and p['relation_groups']),pipeline_version=VERSION)
    return p


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
    # Reserve slots for all three channels; don't let subject-only hits consume
    # the entire cross-encoder budget. Deduplicate by stable source chunk id.
    selected={}
    channels=(('semantic',dense),('lexical',literal),('relation',relation_ranked))
    for i in range(LIMIT):
        for name,hits in channels:
            if i>=len(hits):continue
            h=hits[i];cid=h['chunk']['chunk_id']
            if cid not in selected and len(selected)<LIMIT:
                selected[cid]=dict(chunk=h['chunk'],channels=[],semantic_score=h.get('semantic_score'))
            if cid in selected and name not in selected[cid]['channels']:selected[cid]['channels'].append(name)
        if len(selected)>=LIMIT:break
    hits=list(selected.values());texts=[]
    for h in hits:
        c=h['chunk'];neighbors=context(conn,c)
        h['context_chunks']=neighbors
        h['evidence_audit']=audit(c,p,neighbors)
        # Always show candidate first so truncation never removes its evidence.
        text=c['book_title']+' '+c.get('heading','')+'\n当前片段：'+c['text_orig']
        extra=[n['text_orig'] for n in neighbors if n['chunk_id']!=c['chunk_id']]
        text+='\n邻接上下文：'+'\n'.join(extra)
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
    hits.sort(key=lambda h:(h['evidence_audit']['status']!='clues_present',-h['context_relevance_score'],h['retrieval_rank']))
    return dict(hits=hits[:20],query_plan=p,pipeline=dict(version=VERSION,
        candidate_counts=dict(semantic=len(dense),lexical=len(literal),subject=len(subject),context_scored=len(hits)),
        candidate_scan=scan,reranker_ms=model_report['ms'],
        audit_clues_present=sum(h['evidence_audit']['status']=='clues_present' for h in hits[:20]),
        model='BAAI/bge-reranker-base',model_generation_calls=0,
        elapsed_ms=round((time.perf_counter()-started)*1000,2)),candidate_ids=list(selected))
