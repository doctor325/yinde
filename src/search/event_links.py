"""A closed siege/counterattack frame, with source offsets for its patient.

This is a syntactic hypothesis, not nearest-name pronoun resolution. Multiple
participants, quotes, modal clauses and intervening actions are not bridged.
"""
import re
from search import zh


def observations(chunk, plan):
    if {g['kind'] for g in plan['relation_groups']}=={'policy','cause'} and len(plan['clues'])==1:
        name=plan['clues'][0]['text'];source=chunk['text_orig'];text=zh.to_simplified(source)
        if len(text)!=len(source):return []
        result=[]
        pattern=re.escape(name)+r'(?P<action>刻?削诸侯)，(?P<link>遂使)(?P<outcome>[^，。！？；\n「」：]{2,24})[。；]?'
        for sentence in re.finditer(r'[^。！？；\n]+[。！？；]?',text):
            m=re.fullmatch(r'\s*'+pattern,sentence.group())
            if not m:continue
            start=sentence.start();a=start+sentence.group().find(name)
            for kind in ('policy','cause'):
                result.append(dict(kind=kind,polarity='positive_syntax',qualifications=[],
                    start=start,end=sentence.end(),quote=source[start:sentence.end()],
                    subject_span=[a,a+len(name)],predicate_span=[start+m.start('action'),start+m.end('link')],
                    outcome_span=[start+m.start('outcome'),start+m.end('outcome')],
                    argument_role='agent',relation_verified=False))
        return result
    if plan.get('object_question')!='besieger' or len(plan['clues'])!=2:return []
    actor,place=(c['text'] for c in plan['clues'])
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(text)!=len(source):return []
    pattern=(r'\s*(?P<commander>[\u3400-\u9fff]{2,4})将(?P<patient>[\u3400-\u9fff]{2,4})围'
             +re.escape(place)+r'，(?:[\u3400-\u9fff]{0,4}将军)?(?P<actor>'+re.escape(actor)
             +r')(?P<action>击走之)(?:[。；]|$)')
    result=[]
    for sentence in re.finditer(r'[^。！？；\n]+[。！？；]?',text):
        if re.search(r'传言|传闻|若|欲|未|不|[「」『』“”：:]|曰',sentence.group()):continue
        m=re.fullmatch(pattern,sentence.group())
        if not m:continue
        origin=sentence.start()
        a,b=m.span('actor');c,d=m.span('action');x,y=m.span('patient')
        result.append(dict(kind='military',polarity='positive_syntax',qualifications=[],
            start=origin,end=sentence.end(),quote=source[origin:sentence.end()],
            subject_span=[origin+a,origin+b],predicate_span=[origin+c,origin+d],
            object_span=[origin+x,origin+y],argument_role='agent',
            resolved_object=source[origin+x:origin+y],
            references=[dict(mention='之',span=[origin+d-1,origin+d],
                antecedent_span=[origin+x,origin+y],reason='closed_siege_counterattack',
                excluded=[dict(text=m['commander'],reason='commander_not_local_besieger')])],
            relation_verified=False))
    return result
