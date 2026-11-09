"""Source-local reference hypotheses; unresolved references remain explicit."""
import re
from search import zh

def resolve(source, full, start, end):
    text=zh.to_simplified(source)
    result=dict(status='unresolved',full=full,mention=source[start:end],span=[start,end],
                scope=None,basis=[],reason='no_unique_source_anchor')
    if len(text)!=len(source) or len(full) not in (2,3):return result
    short=full[1:]
    if text[start:end]!=short:return result
    left=text.rfind('\n',0,start)+1
    prefix=text[left:start]
    anchors=list(re.finditer(r'(?P<date>[正一二三四五六七八九十冬腊]+月)[，,]?(?P<name>'+re.escape(full)+r')(?:自将|率军|领兵)',prefix))
    if not anchors:return result
    anchor=left+anchors[-1].start('name');between=text[anchor:start]
    result['scope']=[anchor,end]
    if re.search(r'[「」『』“”：:]|曰|云|谓|传言|传闻',between):
        result['reason']='reported_or_quoted_scope';return result
    # Another explicitly introduced commander sharing the suffix competes.
    candidates=re.findall(r'([\u3400-\u9fff]{1,2}'+re.escape(short)+r')(?:自将|率军|领兵)',between)
    if any(not value.endswith(full) for value in candidates):
        result['reason']='competing_antecedent';return result
    result.update(status='bound',reason='local_commander_anchor',basis=[dict(
        start=anchor,end=anchor+len(full),quote=source[anchor:anchor+len(full)])])
    return result

def observations(chunk,plan):
    if len(plan['clues'])!=2 or {g['kind'] for g in plan['relation_groups']}!={'military'}:return []
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(text)!=len(source):return []
    actor=plan['clues'][0]['text'];full=plan['clues'][1]['text']
    if len(full) not in (2,3):return []
    pattern=re.compile(re.escape(actor)+r'(?:等)?(?:又|乃|遂)?(?P<verb>破|克)(?P<object>'+re.escape(full[1:])+r')(?=$|[，。！？；]|于|於)')
    result=[]
    for sentence in re.finditer(r'[^。！？；\n]+[。！？；]?',text):
        for m in pattern.finditer(sentence.group()):
            origin=sentence.start();a=origin+m.start();c=origin+m.start('verb');d=origin+m.end()
            ref=resolve(source,full,origin+m.start('object'),d)
            if ref['status']!='bound':continue
            if re.search(r'[「『“：:]|曰|传言|传闻|若|欲',sentence.group()[:m.start()]):continue
            result.append(dict(kind='military',polarity='positive_syntax',qualifications=[],
                quote=source[sentence.start():sentence.end()],start=sentence.start(),end=sentence.end(),
                subject_span=[a,a+len(actor)],predicate_span=[c,d],object_span=ref['span'],
                argument_role='agent',references=[ref],source={k:chunk.get(k) for k in ('page_id','revision_id','source_sha256')},relation_verified=False))
    return result
