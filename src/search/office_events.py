"""Bound office refusal/departure frames, with source-local event evidence."""
import re
from search import zh

SCOPE = ('page_id','book_id','revision_id','source_sha256','heading','layer')
BOUNDARY = r'(?:^|[。；！？\n])'
HAN = r'[\u3400-\u9fff]'


def query_frame(text):
    m=re.fullmatch(r'(?P<person>[\u3400-\u9fff]{2,6})(?:为什么|为何)(?P<action>不接受|拒绝|辞去|辞任)(?P<office>[\u3400-\u9fff]{2,10}?)(?:的)?(?:征召|任命)?[？?]?',text)
    if not m:return None
    return dict(person=m['person'],office=m['office'],action='appointment_refusal' if m['action'] in ('不接受','拒绝') else 'resignation',state='not_accepted' if m['action'] in ('不接受','拒绝') else 'departed')


def general_departure_frame(text):
    general=re.fullmatch(r'(?P<person>'+HAN+r'{2,6})(?:为什么|为何|什么时候|何时|哪一年)辞官[？?]?',text)
    if general:
        return dict(person=general['person'],office=None,action='resignation',state='departed')


def _outside_quotes(text, offset):
    return all(text.rfind(a,0,offset)<=text.rfind(b,0,offset)
               for a,b in [('「','」'),('『','』'),('“','”')])


def _event(chunk, frame, match, *, office=None, office_source=None, speaker=False):
    source=chunk['text_orig']
    start,end=match.span('record')
    reason=match.groupdict().get('reason')
    date=match.groupdict().get('date')
    value=dict(polarity='positive_syntax',qualifications=[],start=start,end=end,
               quote=source[start:end],predicate_span=list(match.span('event')),
               office=office or frame['office'],event_type=frame['action'],event_state=frame['state'],
               subject_identity=frame['person'],subject_basis='source_local_office_chain',
               source_attribution='speaker_explanation' if speaker else 'narrator_explanation',
               relation_verified=False)
    if office_source:
        value['office_source']=office_source
    if date:
        value['date_span']=list(match.span('date'))
    if reason:
        a,b=match.span('reason')
        value.update(reason_span=[a,b],explanation=('离任前自述：' if speaker else '史书记载：')+source[a:b])
    kinds=[frame['action']]
    if reason:
        kinds.append('cause')
    return [dict(value,kind=kind) for kind in kinds]


def _scoped_events(chunk, text, frame, neighbors):
    name=frame['person']
    actor=r'(?:'+'|'.join(re.escape(form) for form in dict.fromkeys((name,name[1:])))+r')'
    office=frame['office']
    if frame['action']=='appointment_refusal':
        title=re.escape(office)
        # A named appointment decree is performative. A reported conversation is not.
        decree_actor=r'(?:'+re.escape(name)+'|'+re.escape(name[1:])+'|'+re.escape(name[-1])+r')'
        decree=(r'(?:诏|诏书)曰：「[^「」\n]{0,120}'+re.escape(name)+r'[^「」\n]{0,100}'
                r'以(?P<appointee>'+decree_actor+r')为'+title+r'。」(?P=appointee)')
        direct=(r'(?:[征徵]为|召为)'+title+r'[，。]'+actor+
                r'|(?:闻其名|钦其名行)，(?:[征徵]为|召为)'+title+r'，')
        pattern=r'(?P<record>(?:'+decree+'|'+direct+r')(?P<event>固辞)(?P<reason>以疾|以病|笃疾)[。；])'
        result=[]
        for m in re.finditer(pattern,text):
            lead=re.split(r'[。；！？\n]',text[:m.start()])[-1]
            tail=re.split(r'[「」『』“”]',text[m.end():],maxsplit=1)[0]
            if (not _outside_quotes(text,m.start()) or
                    re.search(r'若|欲|拟|擬|未|不|传闻|传言|或言',lead) or
                    text[m.end()+len(tail):m.end()+len(tail)+1] in ('」','』','”')):
                continue
            result.extend(_event(chunk,frame,m))
        return result
    if office is not None:
        return _registered_departure(chunk,text,frame,neighbors,actor)
    result=[]
    joint=(BOUNDARY+r'\s*(?P<record>(?:[后後][一二三四五六七八九十数]*月[余馀]，)?以'
           r'(?P<reason>'+HAN+r'{2,12})，与'+HAN+r'{2,22}(?:、'+HAN+r'{2,22})*'
           +r'(?P<event>俱乞骸骨)，皆赐[^。；「」\n]{1,48}，罢。'+actor
           +r'为(?P<office>'+HAN+r'{2,10})，凡[^。；\n]{1,12}免。)')
    for m in re.finditer(joint,text):
        if _outside_quotes(text,m.start('record')) and not re.search(r'不|未|欲|或|闻|诬',m['reason']):
            result.extend(_event(chunk,frame,m,office=m['office']))
    dated=(BOUNDARY+r'\s*(?P<record>(?P<date>'+HAN+r'{2}[元一二三四五六七八九十]+年)，'
           r'(?P<event>上表辞禄)[，。]诏许之(?=[，。；]))')
    for m in re.finditer(dated,text):
        before=re.split(r'\n\s*\n',text[:m.start('record')].strip())[-1].lstrip()
        # The same biographical paragraph opens by naming the subject and records
        # an appointment. Stop at narrative cues that could switch the actor.
        appointment=re.search(r'(?:除|拜)(?P<office>'+HAN+r'{2,8})。',before)
        if not (before.startswith(name) and appointment and _outside_quotes(text,m.start('record'))):
            continue
        bridge=before[appointment.end():]
        # Allow closed continuations of the appointee's conduct or an
        # impersonal assessment. An arbitrary sentence can introduce a new actor.
        if not re.fullmatch(
            r'(?:虽在'+HAN+r'{1,8}，(?:闭影|杜门|屏居)不交'+HAN+r'{1,8}，唯以'+HAN+r'{1,8}[为爲]务。)?'
            r'(?:'+HAN+r'{2,8}，(?:多|皆|悉)取决焉。)?',bridge
        ):
            continue
        result.extend(_event(chunk,frame,m,office=appointment['office']))
    return result


def _registered_departure(chunk,text,frame,neighbors,actor):
    """Link the speaker's departure to an appointment in the same source block."""
    speech=re.compile(r'[。；」](?P<record>'+actor+r'因[^「」曰。；\n]{2,45}，曰：「'
                      r'(?P<reason>[^「」\n]{4,80})」(?P<event>遂命驾而归))。')
    for match in speech.finditer(text):
        after=text[match.end():]
        if not re.match(r'[^「」\n]{0,85}?府以其辄去，除吏名。',after):
            continue
        if not re.search(r'羁宦|仕宦|名爵',match['reason']):
            continue
        for peer,source,prefix_start,overlap in _previous_overlap(chunk,neighbors):
            prefix=zh.to_simplified(source[prefix_start:len(source)-overlap])
            if len(prefix)!=len(source)-overlap-prefix_start:
                continue
            appointments=list(re.finditer(r'辟为(?P<office>'+HAN+r'{2,12})。',prefix))
            if not appointments or appointments[-1]['office']!=frame['office']:
                continue
            appointment=appointments[-1]
            if not _outside_quotes(prefix,appointment.start()):
                continue
            bridge=prefix[appointment.end():]+text[:match.start('record')]
            if (len(bridge)>200 or not _outside_quotes(bridge,len(bridge)) or
                    re.search(r'\n\s*\n|改任|迁为|徙为|拜为|授为|辟为|不就|不应命|代之|免官',bridge)):
                continue
            a,b=(prefix_start+i for i in appointment.span('office'))
            return _event(chunk,frame,match,speaker=True,office_source=dict(
                chunk_id=peer['chunk_id'],start=a,end=b,quote=source[a:b]))
    return []


def observations(chunk,plan,neighbors=()):
    frame=plan.get('office_event') or plan.get('general_departure')
    if not frame:return []
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(source)!=len(text):return []
    name=frame['person'];office=frame['office']
    # Heading association licenses this closed appointment/departure chain only.
    scoped=(zh.to_simplified(chunk.get('heading','')).strip()==name and chunk.get('layer')=='body'
            and chunk.get('source_sha256') and chunk.get('revision_id'))
    result=_scoped_events(chunk,text,frame,neighbors) if scoped else []
    if office is None:
        return result
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


def _previous_overlap(chunk,neighbors):
    parts=chunk.get('parts',())
    if not parts:
        return
    first=parts[0]
    for neighbor in neighbors:
        if neighbor['chunk_id']!=chunk['chunk_id']-1 or any(neighbor.get(k)!=chunk.get(k) for k in SCOPE):
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
        yield neighbor,source,prefix_start,overlap


def _county_departure(chunk,text,frame,neighbors):
    """Bind a quoted objection and actual departure to the attested county office."""
    office=frame['office']
    if not office.endswith(('令','长')):
        return []
    actor=r'(?:'+re.escape(frame['person'])+'|'+re.escape(frame['person'][-1])+r')'
    speech=re.compile(r'(?:^|[，。；])(?P<speaker>'+actor+r')(?:叹|歎|慨然)?曰：「(?P<reason>[^「」\n]{4,80})」'
                      r'(?:[\u3400-\u9fff]{0,4}[元一二三四五六七八九十]+年，)?(?P<event>解印去县|弃官去县)(?=[，。；]|$)')
    for neighbor,source,prefix_start,overlap in _previous_overlap(chunk,neighbors):
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
