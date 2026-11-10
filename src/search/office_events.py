"""Bound office refusal/departure frames, with source-local event evidence."""
import re
from search import zh


def query_frame(text):
    m=re.fullmatch(r'(?P<person>[\u3400-\u9fff]{2,6})(?:为什么|为何)(?P<action>不接受|拒绝|辞去|辞任)(?P<office>[\u3400-\u9fff]{2,10}?)(?:的)?(?:征召|任命)?[？?]?',text)
    if not m:return None
    return dict(person=m['person'],office=m['office'],action='appointment_refusal' if m['action'] in ('不接受','拒绝') else 'resignation',state='not_accepted' if m['action'] in ('不接受','拒绝') else 'departed')


def observations(chunk,plan):
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
    return result
