"""Local question -> multiple retrieval channels -> contextual evidence audit.

Pairwise model scores measure relevance, never entailment or historical truth.
The conservative audit reports unmet slots rather than generating an answer.
"""
import json
import math
import re
import time
from search import annal_events, punctuated_relevance as R, punctuated_search as S, zh

VERSION = 'context-evidence-v2'
# Each independent channel keeps its own quota before deduplication. A shared
# 24-slot round robin silently discarded even a channel's tenth-ranked hit.
CHANNEL_LIMIT = 20
ANSWER_PATTERNS = {
    'office':r'(?:'+annal_events.OFFICE+r'|[\u3400-\u9fff]{1,5}侯|翰林学士|中书舍人|御史中丞|[左右]司郎中|召权知[\u3400-\u9fff]{1,4}府)',
    'time':r'[元一二三四五六七八九十百千〇零\d]+年|[正一二三四五六七八九十冬腊]+月|[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]',
    'place':r'(?:于|於|至|在)[\u3400-\u9fff]{1,12}',
    'reason':r'(?:以|因|坐|为|為)[^。；]{1,16}(?:故|罪|丧|喪)|故|缘|緣',
    'quantity':r'[一二三四五六七八九十百千\d]+(?:年|次|卷|人|里)',
}


def time_attached(observation):
    """Conservative local date attachment, not general temporal reasoning.

    A date anywhere in a punctuation-bounded sentence is insufficient: another
    person's action, or another action of the same person, can own that date.
    Unknown era-name constructions remain unconfirmed rather than guessed.
    """
    if observation.get('time_reference',{}).get('status')=='anchored_relative':return True
    quote=observation['quote']
    subject=observation.get('subject_span')
    predicate=observation.get('predicate_span')
    if not subject or not predicate:return False
    origin=observation['start']
    a,b=subject[0]-origin,subject[1]-origin
    c,d=predicate[0]-origin,predicate[1]-origin
    omitted = observation.get('subject_omitted') and observation.get('subject_basis')=='annal_scope'
    if not (0<=a<=b<=c<d<=len(quote) and (a<b or omitted)):return False
    def date_only(text):
        text=re.sub(r'[\s，,、]', '',zh.to_simplified(text))
        return bool(re.fullmatch(annal_events.DATE_UNIT+'+',text) and re.search(ANSWER_PATTERNS['time'],text))
    # Only an otherwise date-only prefix may qualify this explicit event.
    if date_only(quote[:a]):return True
    # Postposed dates must stay in the event's own comma-delimited clause.
    tail=re.split(r'[，,。！？；\n]',quote[d:],maxsplit=1)[0]
    tail=re.sub(r'^\s*(?:于|於|在)', '',tail)
    return date_only(tail)


def place_attached(observation):
    """Recognize a local postposed location clue; ambiguous clauses stay unknown.

    A suffix such as 城/州/宫 is only a surface clue, not place-name validation.
    Neither an earlier location nor a following comma clause can fill this slot.
    """
    quote=observation['quote']
    subject=observation.get('subject_span')
    predicate=observation.get('predicate_span')
    if not subject or not predicate:return False
    origin=observation['start']
    a,b=subject[0]-origin,subject[1]-origin
    c,d=predicate[0]-origin,predicate[1]-origin
    if not 0<=a<b<=c<d<=len(quote):return False
    tail=re.split(r'[，,。！？；\n]',quote[d:],maxsplit=1)[0]
    tail=re.sub(r'\s+', '',zh.to_simplified(tail))
    match=re.fullmatch(r'(?:于|於|在)([\u3400-\u9fff]{1,12})',tail)
    if not match:return False
    location=match[1]
    # Do not swallow a new action/participant into a greedy "location" span.
    if re.search(r'即位|来朝|遂|乃|而|又|未|不|后|令|命|卒|薨|崩|死|攻|杀|伐|奔|迁|徙|见|于|於|在',location):
        return False
    return bool(re.search(r'(?:城|宫|殿|州|郡|县|邑|门|山|川|江|河|水|湖|池|海|关|塞|营|军中|里|铺|口)$',location))


def parse(query):
    text=zh.to_simplified(query)
    office_question = r'(?:担任|出任)(?:过)?(?:什么|哪些|何种)(?:官职|职务)'
    title_question=r'(?:被)?封为(?:什么|何种)(?:侯|爵位|爵)'
    asks_title=bool(re.search(title_question,text))
    text=re.sub(title_question,'获封',text)
    asks_office = bool(re.search(office_question,text))
    p=R.plan(re.sub(office_question,'担任',text))
    siege=re.fullmatch(r'([\u3400-\u9fff]{2,4})(?:击退|击败)了?谁对([\u3400-\u9fff]{2,8})的围攻[？?]?',text)
    if siege:
        p=R.plan(siege[1]+'攻打'+siege[2])
        p['object_question']='besieger'
    # Asking where to find a record is not asking where its event happened.
    text=re.sub(r'(?:史料|记载|文献|证据)(?:在)?(?:哪里|何处|什么地方)', '', text)
    slots=['object'] if siege else []
    if re.search(r'导致|引起|造成',text):slots.append('outcome')
    if asks_office or asks_title:slots.append('office')
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


def audit(chunk, p, neighbors, annal=()):
    f=R.features(chunk,p)
    missing=[]
    if p['unsupported']:missing.append('无法可靠解析问题关系')
    scoped = [o for o in annal if o['polarity']=='positive_syntax']
    if not f['all_clues'] and not scoped:missing.append('主体或约束未在当前片段同时出现')
    observations=f['subject_syntax']['observations']+list(annal)
    from search.relative_time import reference
    observations=[dict(o) for o in observations]
    for o in observations:
        if o['polarity']=='positive_syntax':
            ref=reference(chunk,o)
            if ref:o['time_reference']=ref
    positive=[o for o in observations if o['polarity']=='positive_syntax']
    needed={g['kind'] for g in p['relation_groups']}
    if not needed.issubset({o['kind'] for o in positive}) or not needed:
        missing.append('缺少全部关系的明确主谓句式')
    # A nearby date/place in another passage cannot silently answer this event.
    slot_evidence={}
    for slot in p['answer_slots']:
        if slot in ('object','outcome'):
            slot_evidence[slot]=[o['quote'] for o in positive if o.get('resolved_object' if slot=='object' else 'outcome_span')]
            if not slot_evidence[slot]:missing.append('缺少可溯源的动作对象')
            continue
        checker={'time':time_attached,'place':place_attached}.get(slot)
        quotes=[o['quote'] for o in positive if (checker(o) if checker else (not o.get('subject_basis') or (slot=='office' and o.get('subject_basis')=='biography_recipient_chain')) and
                (re.search(ANSWER_PATTERNS[slot],zh.to_simplified(o['quote'])) or
                 (slot=='time' and o.get('time_reference',{}).get('status')=='anchored_relative')))]
        slot_evidence[slot]=list(dict.fromkeys(quotes))
        if not quotes:missing.append('当前事件句缺少'+{'time':'时间','place':'地点','reason':'原因','quantity':'数量','office':'官职'}[slot]+'线索')
    # Context is available for reading/model scoring, but not identity invention.
    adjacent_only=any(R.features(c,p)['all_clues'] for c in neighbors if c['chunk_id']!=chunk['chunk_id'])
    return dict(status='clues_present' if not missing else 'insufficient',missing=missing,
                time_references=[o['time_reference'] for o in positive if 'time_reference' in o],
                answer_slot_evidence=slot_evidence,
                subject_syntax={**f['subject_syntax'],'observations':observations,
                                'positive_groups':sorted({o['kind'] for o in positive})},
                context_has_clues=adjacent_only,relation_verified=False,
                time_attachment_version='local-event-date-v1',
                place_attachment_version='local-event-place-v1',
                note='要素与句式检查，不等于答案正确；跨句指代、否定与因果未核实。')


def biography_context_candidates(conn,p,subject,book=None):
    """Recall within source-attested biographies; never promote heading to actor."""
    if not any(g['kind']=='imprisonment' for g in p['relation_groups']) or len(p['clues'])!=1:
        return [],dict(pages=0)
    name=p['clues'][0]['text'];anchors={}
    keys=('page_id','book_id','revision_id','source_sha256','heading','layer')
    for h in subject:
        c=h['chunk']
        if book and c['book_id']!=book:continue
        heading=zh.to_simplified(c.get('heading','')).strip()
        opening=re.match(r'\s*'+re.escape(name)+r'[，,]?字',zh.to_simplified(c['text_orig']))
        if (c.get('layer')=='body' and opening and heading in ('',name)):
            anchors[tuple(c.get(k) for k in keys)]=c
    hits=[]
    for scope,c in anchors.items():
        for row in conn.execute('SELECT payload FROM chunks WHERE page_id=?',(c['page_id'],)):
            peer=json.loads(row[0])
            if tuple(peer.get(k) for k in keys)!=scope:continue
            text=zh.to_simplified(peer['text_orig'])
            if any(t in text for t in ('狱','系','坐','诬','证')):hits.append(dict(chunk=peer))
    hits.sort(key=lambda h:(-sum(t in zh.to_simplified(h['chunk']['text_orig']) for t in ('狱','系','坐','诬','证')),h['chunk']['chunk_id']))
    return hits,dict(pages=len(anchors))


def evaluate(query,conn,dense,batches,book=None,rerank=None):
    started=time.perf_counter();p=parse(query)
    subject,scan=R.candidates(conn,p,book)
    _,relation_ranked=R.rank(dense,subject,p)
    literal=S.fused([],batches,limit=60)
    fallback,grounding=grounded_fallback(conn,p,subject,book)
    from search.biography_lookup import resolved
    identity=[]
    biography=[]
    if p['subject']:
        name=p['subject'][0]['text']
        identity=resolved(name)
        if identity:
            biography=S.lexical(conn,name,book,identity=identity)['hits']
    annal={}
    event_hits=[]
    if identity and any(g['kind'] in annal_events.VERBS for g in p['relation_groups']):
        normalized_query=zh.to_simplified(query)
        for entry in identity:
            if book and entry['book_id']!=book:
                continue
            for volume in (entry,*entry.get('continuations', ())):
                page_chunks = [json.loads(row[0]) for row in conn.execute(
                    'SELECT payload FROM chunks WHERE book_id=? AND page_id=?',
                    (entry['book_id'],volume['page_id']))]
                page_chunks = [c for c in page_chunks if c['revision_id']==volume['revision_id']]
                for c in page_chunks:
                    prefix = annal_events.source_prefix(c,page_chunks)
                    events=annal_events.observations(c,p,normalized_query,leading_text=prefix)
                    if events:
                        annal[c['chunk_id']]=events
                        event_hits.append(dict(chunk=c))
        event_hits.sort(key=lambda h:(
            not any(o['polarity']=='positive_syntax' for o in annal[h['chunk']['chunk_id']]),
            -sum(c['text'] in zh.to_simplified(h['chunk']['text_orig']) for c in p['constraints']),
            h['chunk']['chunk_id']))
    biography_context,biography_scan=biography_context_candidates(conn,p,subject,book)
    channels=(('biography_context',biography_context),('semantic',dense),('lexical',literal),('relation',relation_ranked),
              ('biography',biography),('annal_event',event_hits),('grounded',fallback))
    hits=admit(channels);texts=[]
    from search import biography_office, patient_references
    biography_pages={}
    candidate_ids=[h['chunk']['chunk_id'] for h in hits]
    for h in hits:
        c=h['chunk'];neighbors=context(conn,c)
        h['context_chunks']=neighbors
        observations=list(annal.get(c['chunk_id'],()))
        needs_office = p['answer_slots']==['office']
        needs_patient = (len(p['clues'])==1 and
                         {g['kind'] for g in p['relation_groups']}=={'death'})
        # Explicit subjects need no page-wide appellation discovery.
        needs_alias = (needs_patient and zh.to_simplified(c.get('heading','')).strip()==p['clues'][0]['text']
                       and p['clues'][0]['text'] not in zh.to_simplified(c['text_orig']))
        if needs_office or needs_patient:
            page=c['page_id']
            if (needs_office or needs_alias) and page not in biography_pages:
                biography_pages[page]=[json.loads(row[0]) for row in conn.execute(
                    'SELECT payload FROM chunks WHERE page_id=?',(page,))]
            peers=biography_pages.get(page,())
            if needs_office: observations+=biography_office.observations(c,p,peers)
            if needs_patient: observations+=patient_references.observations(c,p,peers)
        h['evidence_audit']=audit(c,p,neighbors,observations)
        # Center the pairwise comparison on the event sentence. Large unrelated
        # annal entries next to a six-character event used to dominate scoring.
        f=h['evidence_focus']=focus(c,p)
        if observations and not f['selected']:
            observation=next((o for o in observations if o['polarity']=='positive_syntax'),observations[0])
            f=h['evidence_focus']=dict(start=observation['start'],end=observation['end'],
                                       text=observation['quote'],selected=True)
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
        biography_sources=identity,
        channel_quota=CHANNEL_LIMIT,grounded_fallback=grounding,
        admission={name:dict(available=len(items),reserved=min(len(items),CHANNEL_LIMIT),
                            admitted=sum(name in h['channels'] for h in hits)) for name,items in channels},
        candidate_diagnostics=diagnostics,
        candidate_scan=scan,reranker_ms=model_report['ms'],
        audit_clues_present=sum(h['evidence_audit']['status']=='clues_present' for h in hits[:20]),
        model='BAAI/bge-reranker-base',model_generation_calls=0,
        elapsed_ms=round((time.perf_counter()-started)*1000,2)),candidate_ids=candidate_ids)
