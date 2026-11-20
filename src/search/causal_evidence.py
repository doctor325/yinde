"""Source attribution and bound event/reason pairs.

These labels describe what a source says, not whether its explanation is true.
Source classifications never supply a missing subject or promote a candidate.
"""
import re
from search import zh
from search.relation_context import assess_context


def _biography(chunk, name):
    return (chunk.get('layer') == 'body' and chunk.get('revision_id') and chunk.get('source_sha256')
            and zh.to_simplified(chunk.get('heading', '')).strip() == name)


def _narration_at(text, offset):
    return all(text.rfind(a, 0, offset) <= text.rfind(b, 0, offset)
               for a, b in [('「', '」'), ('『', '』'), ('“', '”')])


def _family_departure(chunk, text, name):
    if not _biography(chunk, name):
        return []
    actor = '|'.join(map(re.escape, dict.fromkeys((name, name[1:], name[-1:]))))
    pattern = re.compile(
        rf'(?:^|[。；！？\n])\s*(?P<record>(?:得监|出为|除|拜)[^，。；「」\n]{{2,12}}[，。]'
        rf'(?P<reason>(?:父母|二亲|其亲)(?:又)?不(?:欲|愿)(?:其)?行)[，。]'
        rf'(?P<actor>{actor})(?:即|遂|乃)(?P<event>解官归养|去官归养))(?=[。；，]|$)'
    )
    source = chunk['text_orig']
    results = []
    for m in pattern.finditer(text):
        start, end = m.span('record')
        paragraph = text[:start].rpartition('\n\n')[2]
        if not _narration_at(text, start) or (m['actor'] != name and name not in paragraph):
            continue
        results.append(dict(start=start, end=end, quote=source[start:end], subject_span=list(m.span('actor')),
            reason_span=list(m.span('reason')), event_span=list(m.span('event')), event_type='resignation',
            attribution='narrator_explanation', subject_basis='local_appointment_and_departure',
            explanation='史料将归养离任与此相连：'+source[slice(*m.span('reason'))],
            qualification='', relation_verified=False))
    return results


def _demotion_observations(chunk, text, name):
    if not _biography(chunk, name):
        return []
    actor = '|'.join(map(re.escape, dict.fromkeys((name, name[1:], name[-1:]))))
    pattern = re.compile(
        rf'(?:^|[。；！？\n」])\s*(?P<record>(?P<speaker>上|帝)曰：「'
        rf'(?P<subject>{actor})(?:言|上疏言)[^「」\n]{{2,55}}。'
        r'(?P<reason>至谓[^「」！？\n]{2,60}?)[，,]何言[^「」！？\n]{1,18}[！？]'
        rf'(?P=subject)为人臣[^「」\n]{{2,35}}不可赦[。！？]?」'
        rf'(?:[。；，]?[于於]是[^。；「」\n]{{0,36}}?以罪(?P=subject)太重[，,]因事言之[，,])?'
        r'[。；，]?(?P<event>乃贬为[^，。；「」\n]{2,12})(?=[。；，]|$))'
    )
    source = chunk['text_orig']
    results = []
    for m in pattern.finditer(text):
        start, end = m.span('record')
        if not _narration_at(text, start) or (m['subject'] != name and name not in text[max(0, start - 120):start]):
            continue
        value = dict(start=start, end=end, quote=source[start:end], subject_span=list(m.span('subject')),
            speaker_span=list(m.span('speaker')), predicate_span=list(m.span('event')),
            reason_span=list(m.span('reason')), subject_basis='source_local_demotion',
            source_attribution='speaker_explanation', polarity='positive_syntax', qualifications=[],
            explanation='原文记载君主在贬谪前指责：'+source[slice(*m.span('reason'))], relation_verified=False)
        results.extend(dict(value, kind=kind) for kind in ('cause', 'demotion'))
    return results


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


def event_explanations(chunk, plan):
    """Local event/reason pairs. Attribution is not verification of truth.

    A title licenses a two-character short name only in its own body and only
    for this closed explicit frame; it never supplies an omitted actor.
    """
    if len(plan['clues']) != 1 or {g['kind'] for g in plan['relation_groups']} != {'cause','resignation'}:
        return []
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(source)!=len(text):return []
    name=plan['clues'][0]['text'];names=[name]
    if (len(name)>=3 and zh.to_simplified(chunk.get('heading','')).strip()==name
            and chunk.get('layer')=='body' and chunk.get('revision_id') and chunk.get('source_sha256')):
        names.append(name[-2:])
    reports=[]
    for m in re.finditer(r'(?P<actor>'+ '|'.join(map(re.escape,names))+r')(?P<reason>深耻之|以(?:父|母|亲)丧)(?:，遂)?(?P<event>称病去郡|去位|去官|辞官)(?=$|[，。；！？])',text):
        # Require a clause boundary: refuse 他称甲..., 未甲... and arbitrary bridges.
        prefix=text[:m.start()]
        if prefix and prefix[-1] not in '，。；！？\n ：:':continue
        # A preceding closing mark can restore narrative scope. An open quote
        # cannot be promoted to narrator explanation. Unmarked speech is unresolved.
        prior=text[:m.end()]
        if any(prior.rfind(a)>prior.rfind(b) for a,b in [('「','」'),('『','』'),('“','”')]):continue
        clause=re.split(r'[。；！？\n]',prefix)[-1]
        if re.search(r'曰|云|传言|诬|若|欲|未|不|：|:',clause):continue
        reports.append(dict(start=m.start(),end=m.end(),quote=source[m.start():m.end()],
            subject_span=list(m.span('actor')),reason_span=list(m.span('reason')),
            event_span=list(m.span('event')),event_type='resignation',
            attribution='narrator_explanation',subject_basis='explicit_name' if m['actor']==name else 'heading_and_explicit_short_name',
            explanation='史书记载其离职原因为：'+source[m.start('reason'):m.end('reason')],
            qualification='称病是原文记载的对外理由，未核实真实病情。' if '称病' in m['event'] else '',
            relation_verified=False))
    return reports + _family_departure(chunk, text, name)


def attributed_observations(chunk, plan):
    """Evidence of an attributed explanation, with source offsets and roles."""
    if chunk.get('layer') != 'body' or not chunk.get('source_sha256') or not chunk.get('revision_id'):
        return []
    if len(plan['clues']) != 1:
        return []
    source=chunk['text_orig'];text=zh.to_simplified(source)
    if len(source)!=len(text):
        return []
    kinds={g['kind'] for g in plan['relation_groups']}
    name=plan['clues'][0]['text'];full=re.escape(name)
    results=[]
    if kinds=={'cause','resignation'} and plan.get('answer_slots')==['reason']:
        for report in event_explanations(chunk,plan):
            if not report['qualification'] and report['subject_basis'] != 'local_appointment_and_departure':
                continue
            value={**report,'polarity':'positive_syntax','qualifications':[],
                   'predicate_span':report['event_span'],'source_attribution':report['attribution']}
            results.extend(dict(value,kind=kind) for kind in ('resignation','cause'))
        return results
    if kinds=={'cause','demotion'} and plan.get('answer_slots')==['reason']:
        return _demotion_observations(chunk, text, name)
    if kinds=={'cause','policy'} and plan.get('answer_slots')==['outcome']:
        pattern=(r'(?:^|[。\n])\s*(?:太史公|史臣)曰：(?:[^「」\n]{0,160}?[，。；](?:而)?)?'
                 r'(?P<subject>'+full+r')(?P<predicate>(?:刻削|削)诸侯)，遂使'
                 r'(?P<outcome>[\u3400-\u9fff]{1,12}(?:俱起|起兵|叛乱))(?=[，。；])')
        attribution='historian_explanation'
    elif kinds=={'cause','punishment'} and plan.get('answer_slots')==['reason']:
        short=re.escape(name[-1:])
        pattern=(r'(?:^|[。；」])对曰：「(?P<subject>'+full+r')[^「」\n]{1,180}?'
                 r'[。；][后後]遭(?P<reason>[\u3400-\u9fff]{1,12}事)，遂'
                 r'(?P<predicate>下(?:'+full+'|'+short+r')蚕室)[。；][^「」\n]{0,100}」')
        attribution='reported_explanation'
    else:
        return []
    for m in re.finditer(pattern,text):
        if any(text.rfind(a,0,m.start())>text.rfind(b,0,m.start()+1)
               for a,b in [('「','」'),('『','』'),('“','”')]):
            continue
        value=dict(start=m.start(),end=m.end(),quote=source[m.start():m.end()],
                   subject_span=list(m.span('subject')),predicate_span=list(m.span('predicate')),
                   subject_basis='explicit_source_attribution',source_attribution=attribution,
                   polarity='positive_syntax',qualifications=[],relation_verified=False)
        if 'reason' in m.groupdict():
            value['reason_span']=list(m.span('reason'))
            value['explanation']='原文对答将受刑与此事相连：'+source[slice(*m.span('reason'))]
        else:
            value['outcome_span']=list(m.span('outcome'))
            value['explanation']='史家将此事记为后果：'+source[slice(*m.span('outcome'))]
        results.extend(dict(value,kind=kind) for kind in sorted(kinds))
    return results
