"""Local question -> multiple retrieval channels -> contextual evidence audit.

Pairwise model scores measure relevance, never entailment or historical truth.
The conservative audit reports unmet slots rather than generating an answer.
"""
import json
import math
import re
import time
from search import advocacy_recall, annal_events, event_attachment, event_recall, execution_recall, punctuated_relevance as R, punctuated_search as S, source_catalog, zh
from search import activity_context, colloquial_advocacy, reward_recall

VERSION = 'context-evidence-v2'
# Each independent channel keeps its own quota before deduplication. A shared
# 24-slot round robin silently discarded even a channel's tenth-ranked hit.
CHANNEL_LIMIT = 20
RERANK_REQUEST_LIMIT = 100  # Local semantic worker limit per call.
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
    if observation.get('date_span') and observation.get('subject_basis')=='source_local_office_chain':
        return True
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


def _activity_plan(text):
    if activity := activity_context.parse(text):
        p = R.plan(activity['person'] or '')
        if activity['kind'] == 'reward':
            names = dict.fromkeys((activity['person'], activity['anchor'], activity['recipient']))
            p['clues'] = [dict(text=name, forms=[S.normalize(name)], scope_titles=[])
                          for name in names if name]
        elif anchor := activity.get('anchor'):
            person = activity['person']
            if [clue['text'] for clue in p['clues']] != [person]:
                p['clues'] = [dict(text=person, forms=[S.normalize(person)], scope_titles=[])]
            p['clues'].append(dict(text=anchor, forms=[S.normalize(anchor)], scope_titles=[]))
        p.update(subject=p['clues'][:1], constraints=p['clues'][1:], relation_groups=[], answer_slots=[],
                 activity_question=activity, general_departure=None,
                 unsupported=True, pipeline_version=VERSION)
        return p


def parse(query, *, include_activity=True):
    from search.office_events import general_departure_frame
    text=zh.to_simplified(query)
    if include_activity and (activity_plan := _activity_plan(text)):
        return activity_plan
    colloquial = colloquial_advocacy.parse(text)
    if colloquial:
        p = R.plan(colloquial['person'])
        for clue in (colloquial['target'], colloquial.get('place')):
            if clue:
                p['clues'].append(dict(text=clue, forms=[S.normalize(clue)], scope_titles=[]))
        p.update(subject=p['clues'][:1], constraints=p['clues'][1:],
                 relation_groups=[dict(kind='advocacy', terms=list(colloquial_advocacy.terms(colloquial)))],
                 answer_slots=['reason'], colloquial_advocacy=colloquial,
                 general_departure=None, unsupported=False, pipeline_version=VERSION)
        return p
    advocacy = advocacy_recall.parse(text)
    if advocacy:
        p = R.plan(advocacy['person'])
        terms = [advocacy['person'], advocacy['target'] or advocacy['topic']]
        p['clues'] = [dict(text=term, forms=[S.normalize(term)], scope_titles=[]) for term in terms]
        p.update(subject=p['clues'][:1], constraints=p['clues'][1:],
                 relation_groups=[dict(kind='advocacy', terms=list(advocacy_recall.TERMS))],
                 answer_slots=['reason'], advocacy_event=advocacy,
                 general_departure=None, unsupported=False, pipeline_version=VERSION)
        return p
    execution = execution_recall.parse(text)
    if execution:
        p = R.plan(execution['person'])
        preceding = execution['preceding_event']
        clues = [execution['person']]
        if preceding:
            clues.append(preceding['object'])
        p['clues'] = [dict(text=term, forms=[S.normalize(term)], scope_titles=[]) for term in clues]
        p.update(subject=p['clues'][:1], constraints=p['clues'][1:],
                 relation_groups=[dict(kind='execution', terms=list(execution_recall.terms(execution)))],
                 answer_slots=[execution['answer_slot']], execution_event=execution,
                 general_departure=None, unsupported=False, pipeline_version=VERSION)
        return p
    if capture := event_attachment.capture_query(text):
        p = R.plan(capture['person'])
        p.update(subject=p['clues'][:1], constraints=[],
                 relation_groups=[dict(kind='capture', terms=['被擒', '被俘', '所擒', '所禽'])],
                 answer_slots=['place'], capture_event=capture,
                 general_departure=None, unsupported=False, pipeline_version=VERSION)
        return p
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
             general_departure=general_departure_frame(text),
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
    """Keep every channel's first 20 and reuse semantic/relation duplicates."""
    selected={}
    for name, hits in channels:
        for h in hits[:CHANNEL_LIMIT]:
            cid=h['chunk']['chunk_id']
            if cid not in selected:
                selected[cid]=dict(chunk=h['chunk'], channels=[], channel_ranks={})

    # Relation ranking includes the dense results as input. Its overlap with
    # the semantic channel can hide most of the next relevant dense results.
    ranked = dict(channels)
    semantic = ranked.get('semantic', ())
    relation = ranked.get('relation', ())
    semantic_head = {h['chunk']['chunk_id'] for h in semantic[:CHANNEL_LIMIT]}
    relation_head = {h['chunk']['chunk_id'] for h in relation[:CHANNEL_LIMIT]}
    budget = min(RERANK_REQUEST_LIMIT, len(selected) + len(semantic_head & relation_head))
    for hit in semantic[CHANNEL_LIMIT:]:
        if len(selected) >= budget:
            break
        chunk = hit['chunk']
        selected.setdefault(chunk['chunk_id'], dict(chunk=chunk, channels=[], channel_ranks={}))

    for name, hits in channels:
        for rank,h in enumerate(hits,1):
            cid=h['chunk']['chunk_id']
            if cid in selected:
                value=selected[cid]
                if name not in value['channels']:
                    value['channels'].append(name);value['channel_ranks'][name]=rank
                if 'semantic_score' in h:value['semantic_score']=h['semantic_score']
                if name == 'biography_context' and 'campaign_context' in h:
                    value['campaign_context'] = h['campaign_context']
                if name == 'reward_recall':
                    value.update(reward_focus=h['reward_focus'], reward_context=h['reward_context'])
                if name == 'source_reading':
                    value['reading_support'] = h['reading_support']
                if name == 'named_chapter':
                    value['named_chapter'] = h.get('named_chapter')
                if name == 'quoted_context':
                    value['quoted_context'] = True
    return list(selected.values())


def focus(chunk, p):
    """Choose an exact source sentence for model attention, not an answer.

    Never cut away qualifications inside the selected sentence. If the rule
    plan offers no event/object alignment, leave the whole candidate intact.
    """
    if (frame := p.get('activity_question')) and (span := activity_context.focus(chunk, frame)):
        return span
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


def audit(chunk, p, neighbors, annal=(), *, attachment=None):
    f=R.features(chunk,p)
    if attachment is None:
        advocacy=p.get('advocacy_event')
        if colloquial := p.get('colloquial_advocacy'):
            attachment = colloquial_advocacy.evidence(chunk, colloquial, neighbors)
        elif advocacy:
            attachment = advocacy_recall.evidence(chunk,advocacy,neighbors)
        else:
            attachment = event_attachment.attach(chunk,p,neighbors)
    missing=[]
    if p['unsupported']:missing.append('无法可靠解析问题关系')
    from search.office_events import observations as office_observations
    from search.departure_speech import observations as speech_observations
    from search.causal_evidence import attributed_observations
    speech=speech_observations(chunk,p)
    office=office_observations(chunk,p,neighbors)
    attributed=attributed_observations(chunk,p)
    annal=list(annal)+office+speech+attributed
    scoped = [o for o in annal if o['polarity']=='positive_syntax']
    if not f['all_clues'] and not (scoped or attachment):missing.append('主体或约束未在当前片段同时出现')
    observations=f['subject_syntax']['observations']+list(annal)
    from search.relative_time import reference
    observations=[dict(o) for o in observations]
    for o in observations:
        if o['polarity']=='positive_syntax':
            ref=reference(chunk,o)
            if ref:o['time_reference']=ref
    positive=[o for o in observations if o['polarity']=='positive_syntax']
    needed={g['kind'] for g in p['relation_groups']}
    positive_groups={o['kind'] for o in positive}
    if attachment:
        positive_groups.add(attachment.get('kind', 'capture' if p.get('capture_event') else 'execution'))
    if not needed or not needed.issubset(positive_groups):
        missing.append('缺少全部关系的明确主谓句式')
    # A nearby date/place in another passage cannot silently answer this event.
    slot_evidence={}
    for slot in p['answer_slots']:
        if attachment and attachment['slot']==slot:
            slot_evidence[slot]=[attachment['event']['quote']]
            continue
        if slot in ('object','outcome'):
            slot_evidence[slot]=[o['quote'] for o in positive if o.get('resolved_object' if slot=='object' else 'outcome_span')]
            if not slot_evidence[slot]:missing.append('缺少可溯源的动作对象')
            continue
        if slot=='reason' and p.get('office_event'):
            slot_evidence[slot]=[o['quote'] for o in positive if o.get('reason_span') and o.get('event_type')==p['office_event']['action'] and o.get('office')==p['office_event']['office']]
            if not slot_evidence[slot]:missing.append('所问任职事件缺少绑定原因')
            continue
        if slot=='reason' and (speech or any(o.get('reason_span') for o in attributed)):
            slot_evidence[slot]=list(dict.fromkeys(o['quote'] for o in speech+attributed if o.get('reason_span')))
            continue
        if slot=='reason' and p.get('general_departure'):
            quotes=[o['quote'] for o in office if o['kind']=='cause' and o.get('reason_span')]
            if quotes:
                slot_evidence[slot]=list(dict.fromkeys(quotes))
                continue
        checker={'time':time_attached,'place':place_attached}.get(slot)
        quotes=[o['quote'] for o in positive if (checker(o) if checker else (not o.get('subject_basis') or (slot=='office' and o.get('subject_basis')=='biography_recipient_chain')) and
                (re.search(ANSWER_PATTERNS[slot],zh.to_simplified(o['quote'])) or
                 (slot=='time' and o.get('time_reference',{}).get('status')=='anchored_relative')))]
        slot_evidence[slot]=list(dict.fromkeys(quotes))
        if not quotes:missing.append('当前事件句缺少'+{'time':'时间','place':'地点','reason':'原因','quantity':'数量','office':'官职'}[slot]+'线索')
    # Context is available for reading/model scoring, but not identity invention.
    adjacent_only=any(R.features(c,p)['all_clues'] for c in neighbors if c['chunk_id']!=chunk['chunk_id'])
    from search.causal_evidence import source_labels, event_explanations
    causal_events=event_explanations(chunk,p)+[o for o in speech+office+attributed
        if o['kind']=='cause' and 'explanation' in o and 'event_span' not in o]
    causal_labels=source_labels(chunk) if 'cause' in needed else []
    return dict(status='clues_present' if not missing else 'insufficient',missing=missing,
                causal_source_labels=causal_labels,
                causal_event_explanations=causal_events,
                time_references=[o['time_reference'] for o in positive if 'time_reference' in o],
                answer_slot_evidence=slot_evidence,
                attached_evidence=[attachment] if attachment else [],
                subject_syntax={**f['subject_syntax'],'observations':observations,
                                'positive_groups':sorted(positive_groups)},
                context_has_clues=adjacent_only,relation_verified=False,
                time_attachment_version='local-event-date-v1',
                place_attachment_version='local-event-place-v1',
                note='要素与句式检查，不等于答案正确；跨句指代、否定与因果未核实。')


def biography_context_candidates(conn,p,subject,book=None,*,semantic=()):
    """Recall within source-attested biographies; never promote heading to actor."""
    if colloquial := p.get('colloquial_advocacy'):
        return colloquial_advocacy.candidates(conn,colloquial,subject,book,semantic=semantic)
    if advocacy := p.get('advocacy_event'):
        return advocacy_recall.candidates(conn,advocacy,subject,book)
    if execution := p.get('execution_event'):
        return execution_recall.candidates(conn,execution,subject,book)
    kinds={g['kind'] for g in p['relation_groups']}
    frame=p.get('office_event')
    imprisonment='imprisonment' in kinds and len(p['clues'])==1
    departure=kinds=={'cause','resignation'} and len(p['clues'])==1
    if not (imprisonment or departure or frame):
        return [],dict(pages=0,scanned=0)
    if imprisonment:
        terms=('狱','系','坐','诬','证')
    elif frame and frame['action']=='appointment_refusal':
        terms=('不应命','不就','不拜','辞不受','不至')
    else:
        terms=('乞骸骨','致仕','解印','去县','去官','去位','辞官','自解归','解官','去郡','乞退')
        if frame:
            terms+=('辄去，除吏名',)
    name=p['clues'][0]['text']
    office=frame['office'] if frame else None
    keys=('page_id','book_id','revision_id','source_sha256','heading','layer')
    anchors={}
    for hit in subject:
        chunk=hit['chunk']
        if book and chunk['book_id']!=book:
            continue
        heading=zh.to_simplified(chunk.get('heading','')).strip()
        opening=re.match(r'\s*'+re.escape(name)+r'[，,]?字',zh.to_simplified(chunk['text_orig']))
        if chunk.get('layer')!='body' or not opening:
            continue
        if imprisonment:
            if heading not in ('',name):
                continue
        elif heading!=name or not chunk.get('revision_id') or not chunk.get('source_sha256'):
            continue
        anchors[tuple(chunk.get(k) for k in keys)]=chunk
    ranked=[]
    scanned=0
    for scope,anchor in anchors.items():
        peers={}
        for row in conn.execute('SELECT payload FROM chunks WHERE page_id=? AND book_id=?',
                                (anchor['page_id'],anchor['book_id'])):
            peer=json.loads(row[0])
            if tuple(peer.get(k) for k in keys)==scope:
                peers[peer['chunk_id']]=peer
        normalized={cid:zh.to_simplified(peer['text_orig']) for cid,peer in peers.items()}
        scanned+=len(peers)
        for cid,text in normalized.items():
            matches=sum(term in text for term in terms)
            if not matches:
                continue
            office_rank=0
            if office and office not in text:
                if not any(office in normalized.get(adjacent,'') for adjacent in (cid-1,cid+1)):
                    continue
                office_rank=1
            ranked.append(((office_rank,-matches,cid),dict(chunk=peers[cid])))
    ranked.sort(key=lambda item:item[0])
    return [hit for _,hit in ranked],dict(pages=len(anchors),scanned=scanned)


def _quoted_reference(phrase, text):
    """Recognize a citation or comparison that contains the literal match."""
    text = zh.to_simplified(text)
    total = S.normalize(text).count(phrase)
    if not total:
        return False
    pairs = {'「': '」', '『': '』', '“': '”'}
    stack, spans = [], []
    for i, char in enumerate(text):
        if char in pairs:
            if not stack:
                start = i
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return False
            if not stack:
                spans.append((start, i))
    if stack:
        spans.append((start, len(text)))
    references = 0
    for start, end in spans:
        quoted = text[start+1:end]
        if S.normalize(quoted) == phrase and re.search(
                r'《[^》\n]{1,24}》(?:书|载|记|云|曰)[：:\s]*$', text[:start]):
            references += 1
            continue
        for sentence in re.split(r'[。！？!?；;\n]', quoted):
            before, match, after = S.normalize(sentence).partition(phrase)
            if match and phrase not in after and (
                re.search(r'(?:何[以用]|孰)(?:愈|异于|过)$', before)
                or re.fullmatch(r'[\u3400-\u9fff]{0,12}果(?:真)?有之(?:邪|耶|乎)', after)
            ):
                references += 1
    return references == total


def _order_by_source(query, plan, hits, priority):
    """Swap the first two only when clear reference wording obscures nearby narrative."""
    if (len(hits) < 2 or not plan['unsupported'] or plan['answer_slots']
            or not re.fullmatch(r'[\u3400-\u9fff]{6,48}', query.strip())):
        return None
    reference, narrative = hits[:2]
    c = narrative['chunk']
    # Guard the existing evidence tier and leave large model-score gaps intact.
    if (priority(reference) != priority(narrative) or 'lexical' not in reference['channels']
            or 'semantic' not in narrative['channels'] or c['layer'] != 'body'
            or narrative['context_relevance_score'] <= 0
            or reference['context_relevance_score'] - narrative['context_relevance_score'] > 1):
        return None
    phrase = S.normalize(query)
    if not _quoted_reference(phrase, reference['chunk']['text_orig']):
        return None
    # Look for the query opening and two further content characters in a single
    # stretch of narration. This is a proximity check, not event verification.
    clues = set(phrase[2:]) - set(phrase[:2]) - set('之其于於而以为也')
    for passage in re.split(r'\n+| {2,}', event_attachment.narrative(c['text_orig'])):
        prose = S.normalize(passage)
        if phrase[:2] in prose and len(clues.intersection(prose)) >= 2:
            hits[:2] = [narrative, reference]
            return dict(narrative_chunk_id=c['chunk_id'], reference_chunk_id=reference['chunk']['chunk_id'])
    return None


def _chapter_terms(query, plan):
    names, labels = source_catalog.mentions(query)
    excluded = {S.normalize(value) for value in (*names, *labels)}
    terms = []
    for clue in plan.get('clues', ()):
        for value in (clue.get('text'), *(clue.get('forms') or ())):
            key = S.normalize(value or '')
            if len(key) >= 2 and key not in excluded and key not in terms:
                terms.append(key)
    return terms


def _shared_size(text, term):
    for size in range(min(len(term), 12), 1, -1):
        for start in range(len(term) - size + 1):
            if term[start:start + size] in text:
                return size
    return 0


def _chapter_fallback(conn, locations, terms, limit):
    """Last resort when the ordinary channels missed an explicitly named chapter."""
    scored = []
    for book_id, page_id in locations:
        for (payload,) in conn.execute(
                'SELECT payload FROM chunks WHERE book_id=? AND page_id=?', (book_id, page_id)):
            c = json.loads(payload)
            if c.get('layer') != 'body':
                continue
            prose = S.normalize(c.get('text_orig', ''))
            score = max((_shared_size(prose, term) for term in terms), default=0)
            if score >= 2:
                scored.append((score, c['chunk_id'], c))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [c for _, _, c in scored[:limit]]


def named_chapter_candidates(conn, query, plan, book, channels, per_chapter=2):
    """Promote chunks from chapters named in the question.

    The ordinary channels already rank semantic matches; this channel only
    keeps their chunks that fall inside a resolved chapter, so it adds coverage
    without inventing a second relevance model. A chapter with no pooled chunk
    gets a bounded lexical fallback.
    """
    names, labels = source_catalog.mentions(query)
    if not labels:
        return []
    books = []
    for name in names:
        books.extend(source_catalog.book_ids(name))
    books = list(dict.fromkeys(books))
    if book:
        books = [value for value in books if value == book] if books else [book]
    elif not books:
        books = list(source_catalog.catalogued_books())
    pages, by_label = {}, {}
    for label in labels:
        for book_id in books:
            targets = source_catalog.chapter_targets(book_id, label)
            if not targets:
                continue
            for page_id in source_catalog.page_ids(conn, book_id, targets):
                pages.setdefault((book_id, page_id), label)
                by_label.setdefault(label, []).append((book_id, page_id))
    if not pages:
        return []
    pool, seen = [], set()
    for _, hits in channels:
        for hit in hits:
            chunk = hit['chunk']
            if chunk['chunk_id'] not in seen:
                seen.add(chunk['chunk_id'])
                pool.append(chunk)
    selected, counts = [], {}
    for chunk in pool:
        label = pages.get((chunk['book_id'], chunk['page_id']))
        if label is None or counts.get(label, 0) >= per_chapter:
            continue
        counts[label] = counts.get(label, 0) + 1
        selected.append(dict(chunk=chunk, named_chapter=label))
    terms = _chapter_terms(query, plan)
    for label, locations in by_label.items():
        if counts.get(label, 0) or not terms:
            continue
        for chunk in _chapter_fallback(conn, locations, terms, per_chapter):
            selected.append(dict(chunk=chunk, named_chapter=label))
    return selected


_QUOTED = re.compile(r'《([^》]{1,40})》[^“「]{0,16}[“「]([^”」]{6,60})[”」]')
_SPEECH_END = re.compile(r'(?:曰|云|謂|谓|報|报|言|對|对|問|问|答|書|书)[：:]\s*$')
_HEADING_SPLIT = re.compile(r'[\s·・•‧、，,/]+')
_GENERIC_NAMES = {'列傳', '列传', '本紀', '本纪', '世家', '太史公曰', '史評', '史评', '附'}
_SHORT_STOP = set('曰云謂谓報报言對对問问答書书之其此是以與与乃為为不')


def _heading_names(chunk):
    names = []
    for token in _HEADING_SPLIT.split(chunk.get('heading', '') or ''):
        token = token.strip()
        if not 2 <= len(token) <= 4 or token in _GENERIC_NAMES:
            continue
        if not all('㐀' <= char <= '鿿' for char in token):
            continue
        names.append(token)
        if token[-1] not in _SHORT_STOP:
            names.append(token[-1])
    return list(dict.fromkeys(names))


def quoted_context_candidates(conn, query, channels, book=None, limit=1):
    """Add the paragraph that introduces a quoted speaker.

    A direct quotation often starts mid-letter, so the page's opening sentence
    naming the author and addressee is not what lexical search matches. This
    channel only acts when the question names a book, quotes a passage and asks
    who said it; it then keeps the same-page paragraph that starts with a name
    and ends with a speech cue.
    """
    match = _QUOTED.search(query)
    if not match or '谁' not in query:
        return []
    books = set(source_catalog.book_ids(match[1]))
    if not books:
        return []
    passages, anchors = set(), []
    for _, hits in channels:
        for hit in hits:
            chunk = hit['chunk']
            if chunk['book_id'] in books and source_catalog.quote_matches(
                    chunk.get('text_orig', ''), match[2]):
                passages.add((chunk['book_id'], chunk['page_id']))
                anchors.append(chunk)
    if len(passages) != 1:
        return []
    book_id, page_id = next(iter(passages))
    if book and book_id != book:
        return []
    names = [name for chunk in anchors for name in _heading_names(chunk)]
    keys = [source_catalog.normalize(name) for name in dict.fromkeys(names)]
    keys = [key for key in keys if key]
    if not keys:
        return []
    scored = []
    for (payload,) in conn.execute(
            'SELECT payload FROM chunks WHERE book_id=? AND page_id=?', (book_id, page_id)):
        chunk = json.loads(payload)
        if chunk.get('layer') != 'body':
            continue
        for paragraph in re.split(r'\n\s*\n', chunk.get('text_orig', '')):
            paragraph = paragraph.strip()
            if not 8 <= len(paragraph) <= 200:
                continue
            key = source_catalog.normalize(paragraph)
            if not any(key.startswith(name) for name in keys) or not _SPEECH_END.search(paragraph):
                continue
            score = len(paragraph) + (20 if '書' in paragraph or '书' in paragraph else 0)
            scored.append((score, chunk['chunk_id'], chunk))
    scored.sort(key=lambda row: (-row[0], row[1]))
    selected, seen = [], set()
    for _, chunk_id, chunk in scored:
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        selected.append(dict(chunk=chunk, quoted_context=True))
        if len(selected) >= limit:
            break
    return selected


def evaluate(query,conn,dense,batches,book=None,rerank=None,*,activity_places=()):
    started=time.perf_counter()
    activity_plan = _activity_plan(zh.to_simplified(query))
    activity = activity_plan['activity_question'] if activity_plan else {}
    # The earlier receipt/unrest patterns do not supply a retrieval anchor.
    p = activity_plan if 'anchor' in activity else parse(query, include_activity=False)
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
    reward_hits, activity_hits = [], []
    if activity.get('kind') == 'reward':
        reward_hits = reward_recall.candidates(conn, activity, subject, book, place_forms=activity_places)
    elif 'anchor' in activity:
        activity_hits = activity_context.candidates(conn, activity, subject, identity, book)
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
    biography_context,biography_scan=biography_context_candidates(conn,p,subject,book,semantic=dense)
    attached_events,attachments=event_recall.candidates(conn,p,subject)
    channels=(('attached_event',attached_events),('biography_context',biography_context),
              ('semantic',dense),('lexical',literal),('relation',relation_ranked),
              ('biography',biography),('annal_event',event_hits),('grounded',fallback))
    if activity_hits:
        channels = (('activity_trigger', activity_hits), *channels)
    if reward_hits:
        channels = (('reward_recall', reward_hits), *channels)
    from search.reading_grounding import recall as recall_reading
    if reading_hits := recall_reading(conn, query, book):
        channels = (('source_reading', reading_hits), *channels)
    if named_hits := named_chapter_candidates(conn, query, p, book, channels):
        channels = (('named_chapter', named_hits), *channels)
    if quoted_hits := quoted_context_candidates(conn, query, channels, book):
        channels = (('quoted_context', quoted_hits), *channels)
    hits=admit(channels);texts=[]
    p = activity_plan or p
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
        h['evidence_audit']=audit(c,p,neighbors,observations,attachment=attachments.get(c['chunk_id']))
        if activity.get('kind') == 'siege':
            if linked := activity_context.siege_evidence(conn, c, activity, identity):
                h['evidence_audit'].update(status='clues_present', missing=[], activity_evidence=linked,
                                           answer_slot_evidence={'activity': [linked['citations']['当时行动']['quote']]})
        # Center the pairwise comparison on the event sentence. Large unrelated
        # annal entries next to a six-character event used to dominate scoring.
        f=h['evidence_focus']=h.get('reward_focus') or focus(c,p)
        attachment = next(iter(h['evidence_audit']['attached_evidence']), None)
        if attachment and attachment.get('kind') == 'advocacy':
            event=attachment['event']
            f=h['evidence_focus']=dict(start=event['start'],end=event['end'],text=event['quote'],selected=True)
        if observations and not f['selected']:
            observation=next((o for o in observations if o['polarity']=='positive_syntax'),observations[0])
            f=h['evidence_focus']=dict(start=observation['start'],end=observation['end'],
                                       text=observation['quote'],selected=True)
        text=c['book_title']+' '+c.get('heading','')+'\n当前原文：'+f['text']
        before=c['text_orig'][max(0,f['start']-60):f['start']]
        after=c['text_orig'][f['end']:f['end']+60]
        prev=[n['text_orig'][-60:] for n in neighbors if n['chunk_id']<c['chunk_id']]
        nxt=[n['text_orig'][:60] for n in neighbors if n['chunk_id']>c['chunk_id']]
        if not f.get('activity_context'):
            text+='\n前文：'+(before or '\n'.join(prev))+'\n后文：'+(after or '\n'.join(nxt))
        if campaign := h.get('campaign_context'):
            text += '\n同篇出兵前后：' + campaign
        if reward_context := h.get('reward_context'):
            text += '\n同篇战事前文：' + reward_context
        h['context_truncated']=len(text)>1000;texts.append(text[:1000])
    model_report={'scores':[],'truncated_indices':[],'ms':0}
    ranking_query = query
    frame = p.get('colloquial_advocacy')
    if frame and frame['reason_hypothesis']:
        # A user's proposed explanation can be false. Rank by the event so
        # material explaining its actual cause can still reach the front.
        ranking_query = f"{frame['person']}为什么{frame['action']}？"
    if hits:
        if rerank is None:
            from search.semantic_bridge import worker
            rerank=worker.rerank
        model_report=rerank(ranking_query,texts)
        scores=model_report['scores']
        if len(scores)!=len(hits) or not all(isinstance(s,(float,int)) and math.isfinite(s) for s in scores):
            raise ValueError('上下文模型返回了无效评分')
        for i,(h,score) in enumerate(zip(hits,scores)):
            h['context_relevance_score']=score
            h['context_truncated']|=i in model_report.get('truncated_indices',[])
            h['retrieval_rank']=i+1
    model_ranks={h['chunk']['chunk_id']:i for i,h in enumerate(sorted(hits,
        key=lambda h:(-h['context_relevance_score'],h['retrieval_rank'])),1)}
    from search.polity_evidence import review_hits, review_natural_hits
    from search.receipt_evidence import review_hits as review_receipts
    review_natural_hits(conn, frame, hits)
    if activity.get('kind') == 'reward':
        review_receipts(conn, activity, [h for h in hits if 'reward_recall' in h['channels']], place_forms=activity_places)

    def priority(h):
        return (
            'reading_support' not in h,
            'quoted_context' not in h['channels'],
            'named_chapter' not in h['channels'],
            {'supported': 0, 'different_roles': 1}.get(h.get('advice_evidence', {}).get('status'), 2),
            h['evidence_audit']['status'] != 'clues_present',
        )

    hits.sort(key=lambda h:(*priority(h),-h['context_relevance_score'],h['retrieval_rank']))
    source_order = _order_by_source(query, p, hits, priority)
    if activity.get('kind') == 'receipt':
        review_receipts(conn, activity, hits[:20], place_forms=activity_places)
    diagnostics=[dict(chunk_id=h['chunk']['chunk_id'], channel_ranks=h['channel_ranks'],
                      model_rank=model_ranks[h['chunk']['chunk_id']],final_rank=i,
                      score=h['context_relevance_score'], audit_status=h['evidence_audit']['status'],
                      displayed=i<=20) for i,h in enumerate(hits,1)]
    review_hits(conn, frame, hits[:20])
    return dict(hits=hits[:20],query_plan=p,pipeline=dict(version=VERSION,
        candidate_counts=dict(semantic=len(dense),lexical=len(literal),subject=len(subject),context_scored=len(hits)),
        biography_sources=identity,biography_context_scan=biography_scan,
        channel_quota=CHANNEL_LIMIT,grounded_fallback=grounding,
        backfilled_candidates=sum(min(h['channel_ranks'].values()) > CHANNEL_LIMIT for h in hits),
        admission={name:dict(available=len(items),reserved=min(len(items),CHANNEL_LIMIT),
                            admitted=sum(name in h['channels'] for h in hits)) for name,items in channels},
        candidate_diagnostics=diagnostics, source_order=source_order,
        candidate_scan=scan,reranker_ms=model_report['ms'],rerank_query=ranking_query,
        audit_clues_present=sum(h['evidence_audit']['status']=='clues_present' for h in hits[:20]),
        model='BAAI/bge-reranker-base',model_generation_calls=0,
        elapsed_ms=round((time.perf_counter()-started)*1000,2)),candidate_ids=candidate_ids)
