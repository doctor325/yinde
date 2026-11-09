"""Source attribution and a closed, explicit bereavement/resignation frame.

These labels describe what a source says, not whether its explanation is true.
Source classifications never supply a missing subject or promote a candidate.
"""
import re
from search import zh
from search.relation_context import assess_context


def source_labels(chunk):
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(source)!=len(text):return []
    labels=[]
    patterns=(('accusation',r'诬告|诬奏|诬陷|弹劾'),
              ('insufficient_proof',r'无可证者|莫须有|未有实证'),
              ('historian_explanation',r'太史公曰|史臣曰|论曰'),
              ('reported_explanation',r'对曰|谓宰臣曰|报书曰'))
    for kind,pattern in patterns:
        for m in re.finditer(pattern,text):
            labels.append(dict(kind=kind,start=m.start(),end=m.end(),quote=source[m.start():m.end()],
                               answers_cause=False,relation_verified=False))
    return labels


def observations(chunk,plan):
    kinds={g['kind'] for g in plan['relation_groups']}
    if kinds!={'cause','resignation'} or len(plan['clues'])!=1:return []
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(source)!=len(text):return []
    name=plan['clues'][0]['text']
    pattern=re.escape(name)+r'以(?P<reason>(?:父|母|亲)丧)(?P<event>去位|去官|辞官)(?=$|[，。；！？])'
    result=[]
    for sentence in re.finditer(r'[^。；！？\n]+[。；！？]?',text):
        quote=sentence.group()
        for m in re.finditer(pattern,quote):
            context=assess_context(quote,m.start(),m.start()+len(name),m.end(),subject_end=m.start()+len(name))
            if re.search(r'[「」『』“”：:]|曰|云|诬|传言|若|欲|未|不',quote[:m.start()]):continue
            origin=sentence.start()
            for kind in ('cause','resignation'):
                result.append(dict(kind=kind,polarity='qualified' if context['qualifications'] else 'positive_syntax',
                    qualifications=context['qualifications'],start=origin,end=sentence.end(),
                    quote=source[origin:sentence.end()],subject_span=[origin+m.start(),origin+m.start()+len(name)],
                    predicate_span=[origin+m.start()+len(name),origin+m.end()],
                    reason_span=[origin+m.start('reason'),origin+m.end('reason')],
                    source_attribution='narrator_explanation',relation_verified=False))
    return result
