"""Attested local appellations and a closed direct-patient continuation."""
import re
from search import zh

def appellations(chunk,subject,peers):
    edges=[]
    if zh.to_simplified(chunk.get('heading','')).strip()!=subject:return edges
    keys=('page_id','revision_id','source_sha256','heading','book_id','layer')
    for c in peers:
        if not all(c.get(k)==chunk.get(k) for k in keys):continue
        text=zh.to_simplified(c['text_orig'])
        if len(text)!=len(c['text_orig']):continue
        m=re.match(r'\s*(?P<alias>[\u3400-\u9fff]{2,4})者，[\u3400-\u9fff]{1,24}，名(?P<given>[\u3400-\u9fff]{1,2})，',text)
        if not m or not subject.endswith(m['given']):continue
        edges.append(dict(subject=subject,alias=m['alias'],scope={k:chunk.get(k) for k in keys},
            source_chunk=c['chunk_id'],start=m.start(),end=m.end(),quote=c['text_orig'][m.start():m.end()]))
    return edges if len({e['alias'] for e in edges})==1 else []

def observations(chunk,plan,peers=()):
    if (len(plan['clues'])!=1 or {g['kind'] for g in plan['relation_groups']}!={'death'}):return []
    subject=plan['clues'][0]['text'];edges=appellations(chunk,subject,peers)
    forms=[subject]+[e['alias'] for e in edges]
    text=zh.to_simplified(chunk['text_orig'])
    if len(text)!=len(chunk['text_orig']):return []
    result=[]
    for sentence in re.finditer(r'[^。！？；\n]+[。！？；]?',text):
        quote=sentence.group()
        if re.search(r'[「」『』“”：:]|曰|云|传言|传闻|若|欲|未|不',quote):continue
        for form in set(forms):
            # No comma, actor change or extra participant may intervene.
            for m in re.finditer(r'(?:攻|捕|执)(?P<patient>'+re.escape(form)+r')，(?P<action>(?:遂|乃)?杀之)(?=$|[，。！？；]|于|於)',quote):
                prefix=quote[:m.start()]
                if re.search(r'令|使|命|将$',prefix):continue
                origin=sentence.start();a,b=m.span('patient');c,d=m.span('action')
                result.append(dict(kind='death',polarity='positive_syntax',qualifications=[],
                    quote=chunk['text_orig'][sentence.start():sentence.end()],start=sentence.start(),end=sentence.end(),
                    subject_span=[origin+a,origin+b],predicate_span=[origin+c,origin+d],argument_role='patient',
                    references=[dict(mention='之',span=[origin+d-1,origin+d],antecedent_span=[origin+a,origin+b],
                        candidates=[dict(text=form,role='direct_patient')],excluded=[dict(text=prefix,reason='actor_not_patient')],
                        reason='same_actor_closed_patient_continuation')],appellations=edges,
                    source={k:chunk.get(k) for k in ('page_id','revision_id','source_sha256')},relation_verified=False))
    return result
