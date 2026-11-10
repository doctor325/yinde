"""Source-attributed retirement intentions plus an explicit same-actor petition.

This closed frame supports a reported motive, never verified illness or truth.
"""
import re
from search import zh


def observations(chunk,plan):
    if (plan.get('office_event') or len(plan['clues'])!=1 or
        {g['kind'] for g in plan['relation_groups']}!={'cause','resignation'} or
        plan.get('answer_slots')!=['reason']):return []
    name=plan['clues'][0]['text'];source=chunk['text_orig'];text=zh.to_simplified(source)
    if (len(text)!=len(source) or zh.to_simplified(chunk.get('heading','')).strip()!=name
        or chunk.get('layer')!='body' or not chunk.get('revision_id') or not chunk.get('source_sha256')):return []
    # A one-character given-name mention is allowed only as an explicit speaker
    # and repeated explicit petitioner within this exact source heading.
    actor='(?:'+re.escape(name)+'|'+re.escape(name[-1:])+')'
    result=[]
    pattern=r'(?P<speaker>'+actor+r')谓[\u3400-\u9fff]{1,4}曰：「(?P<reason>[^「」\n]{8,180})」'
    for m in re.finditer(pattern,text):
        if m.start() and text[m.start()-1] not in '。；！？\n':continue
        before=text[:m.start()]
        if before.rfind('「')>before.rfind('」'):continue
        reason=m['reason']
        if not (re.search(r'吾|我',reason) and re.search(r'归老|身退',reason) and re.search(r'惧有后悔|知止|知足',reason)):continue
        # Only a brief response and narrative procedure may intervene; a new
        # paragraph, allegation, hypothetical departure or actor change refuses.
        tail=text[m.end():].split('\n\n')[0][:100]
        event=re.search(r'(?P<actor>'+actor+r')遂称笃，上疏(?P<action>乞骸骨)',tail)
        if not event:continue
        bridge=tail[:event.start()]
        if not re.fullmatch(r'(?:[\u3400-\u9fff]{1,4}(?:叩头)?曰：「(?:从大人议|从公议|诺|唯)。」)?(?:即日)?(?:父子|俱|皆|遂|乃)*移病。(?:满[一二三四五六七八九十]+月)?赐告，',bridge):continue
        a=m.end()+event.start();b=m.end()+event.end()
        for kind in ('cause','resignation'):
            result.append(dict(kind=kind,polarity='positive_syntax',qualifications=[],
                start=m.start(),end=b,quote=source[m.start():b],subject_basis='attributed_departure_speech',
                subject_identity=name,subject_span=list(m.span('speaker')),predicate_span=[a,b],
                reason_span=list(m.span('reason')),source_attribution='speaker_explanation',
                event_state='petition_to_retire',relation_verified=False,
                explanation=name+'自述退职考虑：'+source[m.start('reason'):m.end('reason')],
                qualification='原文记载的是人物陈述与乞退；移病、称笃不能证明真实病情。'))
    return result
