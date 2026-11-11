"""Bound office refusal/departure frames, with source-local event evidence."""
import re
from search import zh


def query_frame(text):
    m=re.fullmatch(r'(?P<person>[\u3400-\u9fff]{2,6})(?:为什么|为何)(?P<action>不接受|拒绝|辞去|辞任)(?P<office>[\u3400-\u9fff]{2,10}?)(?:的)?(?:征召|任命)?[？?]?',text)
    if not m:return None
    return dict(person=m['person'],office=m['office'],action='appointment_refusal' if m['action'] in ('不接受','拒绝') else 'resignation',state='not_accepted' if m['action'] in ('不接受','拒绝') else 'departed')


def observations(chunk,plan,neighbors=()):
    frame=plan.get('office_event')
    if not frame:return []
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(source)!=len(text):return []
    name=frame['person'];office=frame['office']
    # Heading association licenses this closed appointment/departure chain only.
    scoped=(zh.to_simplified(chunk.get('heading','')).strip()==name and chunk.get('layer')=='body'
            and chunk.get('source_sha256') and chunk.get('revision_id'))
    result=[]
    for sent in re.finditer(r'[^。；！？\n]+[。；！？]?',text):
        t=sent.group();origin=sent.start()
        origin+=len(t)-len(t.lstrip());t=t.lstrip()
        if any(text[:origin].rfind(a)>text[:origin].rfind(b) for a,b in [('「','」'),('『','』'),('“','”')]):continue
        if re.search(r'[「」『』“”：:]|曰|云|传闻|传言|诬|若|欲|未',t):continue
        if frame['action']=='appointment_refusal':
            # The appointment must precede this refusal in the same paragraph.
            paragraph=text[:origin].split('\n\n')[-1]
            if not re.search(r'(?:征为|徵为|召为|授)'+re.escape(office)+r'[。；，]?$',paragraph):continue
            actors=[name]
            if scoped and name in text[:origin] and len(name)>=2:actors.append(name[-1:])
            pattern=r'(?P<subject>'+ '|'.join(map(re.escape,actors))+r')以(?P<reason>[^，。；]{1,16}(?:，[^，。；]{1,12})?)，遂(?P<event>不应命|不就)[。；]?$'
        else:
            if not scoped:continue
            pattern=r'以[^，。；]{1,12}，起为'+re.escape(office)+r'，(?P<reason>不堪吏职)，少日(?P<event>自解归)[。；]?$'
        m=re.fullmatch(pattern,t)
        if not m:continue
        for kind in ('cause',frame['action']):
            result.append(dict(kind=kind,polarity='positive_syntax',qualifications=[],start=origin,end=sent.end(),quote=source[origin:sent.end()],
                predicate_span=[origin+m.start('event'),origin+m.end('event')],reason_span=[origin+m.start('reason'),origin+m.end('reason')],
                office=office,event_type=frame['action'],event_state=frame['state'],subject_basis='source_local_office_chain',
                subject_identity=name,source_attribution='narrator_explanation',relation_verified=False))
    if scoped and frame['action']=='resignation':
        result.extend(_county_departure(chunk,text,frame,neighbors))
    return result


def _county_departure(chunk,text,frame,neighbors):
    """Bind a quoted objection and actual departure to the attested county office."""
    office=frame['office']
    parts=chunk.get('parts',())
    if not office.endswith(('令','长')) or not parts:
        return []
    first=parts[0]
    scope=('page_id','book_id','revision_id','source_sha256','heading','layer')
    actor=r'(?:'+re.escape(frame['person'])+'|'+re.escape(frame['person'][-1])+r')'
    speech=re.compile(r'(?:^|[，。；])(?P<speaker>'+actor+r')(?:叹|歎|慨然)?曰：「(?P<reason>[^「」\n]{4,80})」'
                      r'(?:[\u3400-\u9fff]{0,4}[元一二三四五六七八九十]+年，)?(?P<event>解印去县|弃官去县)(?=[，。；]|$)')
    for neighbor in neighbors:
        if neighbor['chunk_id']!=chunk['chunk_id']-1 or any(neighbor.get(k)!=chunk.get(k) for k in scope):
            continue
        previous=neighbor.get('parts',())
        if not previous:
            continue
        last=previous[-1]
        if not (last['block_index']==first['block_index'] and last['start']<first['start']<last['end']<=first['end']):
            continue
        source=neighbor['text_orig']
        overlap=last['end']-first['start']
        if source[-overlap:]!=chunk['text_orig'][:overlap]:
            continue
        prefix_start=len(source)-(last['end']-last['start'])
        prefix=zh.to_simplified(source[prefix_start:len(source)-overlap])
        if len(prefix)!=len(source)-overlap-prefix_start:
            continue
        appointments=list(re.finditer(r'(?:以为|拜为|授为)(?P<office>[^，。；]{2,10})。在县[，。]',prefix))
        if not appointments or appointments[-1]['office']!=office:
            continue
        appointment=appointments[-1]
        prior=prefix[:appointment.start()]
        if any(prior.rfind(a)>prior.rfind(b) for a,b in [('「','」'),('『','』'),('“','”')]):
            continue
        for m in speech.finditer(text):
            bridge=prefix[appointment.end():]+text[:m.start('speaker')]
            if len(bridge)>180 or re.search(r'\n\s*\n|改任|迁为|徙为|拜为|授为|以为|不就|不应命|代之|免官|去县|传言|传闻',bridge):
                continue
            if any(bridge.rfind(a)>bridge.rfind(b) for a,b in [('「','」'),('『','』'),('“','”')]):
                continue
            if not re.search(r'^(?:吾|我)(?:不能|不愿|不肯)[^。；]{0,48}(?:折腰|事[^，。；！？]{1,12})',m['reason']):
                continue
            start=m.start('speaker');end=m.end('event')
            quote=chunk['text_orig']
            office_start=prefix_start+appointment.start('office')
            office_end=prefix_start+appointment.end('office')
            return [dict(kind=kind,polarity='positive_syntax',qualifications=[],start=start,end=end,
                         quote=quote[start:end],subject_span=list(m.span('speaker')),predicate_span=list(m.span('event')),
                         reason_span=list(m.span('reason')),office=office,event_type='resignation',event_state='departed',
                         subject_identity=frame['person'],subject_basis='source_local_office_chain',
                         source_attribution='speaker_explanation',relation_verified=False,
                         office_source=dict(chunk_id=neighbor['chunk_id'],start=office_start,end=office_end,
                                            quote=source[office_start:office_end]),
                         explanation=frame['person']+'离任前自述：'+quote[m.start('reason'):m.end('reason')],
                         qualification='原文将自述与离任相连，其他动机尚待查证。') for kind in ('cause','resignation')]
    return []
