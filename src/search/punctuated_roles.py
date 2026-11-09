"""Conservative surface-syntax evidence; not general historical role resolution.

Only explicit subject-predicate patterns receive a positive syntax signal.
Negation, reports and hypothetical clauses remain candidates but get no such
signal. Unrecognized constructions stay unknown, never false or unsupported.
"""
import re
from search import zh
from search.relation_context import assess_context
from search.annal_events import OFFICE

VERSION = 'explicit-subject-syntax-v2'


def assess(chunk, plan, forms_for):
    if not plan['clues']:return dict(positive_groups=[],observations=[],relation_verified=False)
    subject=plan['clues'][0]
    forms=sorted(set(forms_for(subject,chunk)),key=len,reverse=True)
    name='(?P<subject>'+'|'.join(re.escape(s) for s in forms)+')'
    objects=[f for c in plan['clues'][1:] for f in forms_for(c,chunk)]
    obj='(?:'+'|'.join(re.escape(s) for s in sorted(set(objects),key=len,reverse=True))+')' if objects else None
    # Closed bridges cannot consume another proper name or a causative verb.
    bridge=r'(?:遂|乃|亦|复|又|已|竟|即|终|未|不|将|欲){0,2}'
    patterns={
        'death':name+bridge+r'(?:卒|薨|崩|殁|死)(?=$|[，。！？；、：」』]|于|於|在|年|时|后|後)',
        'accession':name+bridge+r'(?:即(?:皇帝)?位|践祚|登极)',
    }
    if not obj and 'office' in plan.get('answer_slots',[]):
        # An interrogative office is an answer slot. Require an explicit
        # recipient and a bounded office title in this same predicate.
        office=r'(?:'+OFFICE+r'|翰林学士|中书舍人|御史中丞)'
        patterns['appointment']=name+bridge+r'(?:为|爲|拜|授|任)'+office+r'(?=$|[，。！？；、：」』])'
    if obj:
        patterns.update({
            'appointment':name+bridge+r'(?:为|拜|授|任|知|徙知|封为|入)'+obj,
            'movement':name+bridge+r'(?:迁都|迁|徙|奔|出奔|至|入)(?:于|於)?'+obj,
            'military':name+bridge+r'(?:破|攻取|克|伐)'+obj,
            'document':name+bridge+r'(?:撰|作|修|著|上|造|通)[《〈]?'+obj,
            'exam':name+bridge+r'(?:中|登|举|赐)(?:礼部)?'+obj,
        })
    # A cause adjunct with a family-loss noun is a grammatical construction,
    # not a person-specific assertion. Do not allow arbitrary text in its slot.
    patterns['resignation']=name+bridge+r'(?:以(?:父|母|亲)丧)?(?:去官|辞官|罢官|免官)'
    patterns={k:name+bridge+'(?P<predicate>'+v[len(name+bridge):]+')' for k,v in patterns.items()}
    kinds={g['kind'] for g in plan['relation_groups']}
    observations=[]
    # Keep each quoted observation as an exact slice of the source text.
    for clause_match in re.finditer(r'[^。！？；\n]+[。！？；]?',chunk['text_orig']):
        quote=clause_match.group(0)
        converted=zh.to_simplified(quote)
        if len(converted)!=len(quote):continue
        offsets=[i for i,c in enumerate(converted) if not c.isspace()]
        clause=''.join(converted[i] for i in offsets)
        local_patterns=dict(patterns)
        # Short objects are hypotheses restricted to a preceding full-name
        # anchor in this same sentence. Never add them to global aliases.
        aliases=[]
        if 'military' in kinds:local_patterns.pop('military',None)
        if obj and 'military' in kinds and len(plan['clues'])==2:
            for full in objects:
                if len(full) not in (2,3):continue
                anchor=clause.find(full)
                if anchor<0:continue
                short=full[1:]
                aliases.append((full,short,anchor))
            object_forms=sorted(set(objects+[short for _,short,_ in aliases]),key=len,reverse=True)
            target='(?P<object>'+'|'.join(map(re.escape,object_forms))+')'
            local_patterns['military']=name+r'(?:等)?'+bridge+r'(?P<predicate>(?:破|攻取|克|伐)'+target+r')(?=$|[，。！？；、：」』]|于|於)'
        if 'death' in kinds:
            # The named subject is the patient, not the intervening killer.
            local_patterns['death']=name+bridge+r'(?P<predicate>(?:卒|薨|崩|殁|死|被杀|为[\u3400-\u9fff]{1,6}所杀))(?=$|[，。！？；、：」』]|于|於|在|年|时|后|後)'
        # Preserve source offsets through whitespace removal, then scope each
        # qualifier to the particular subject/predicate observation.
        for kind in sorted(kinds):
            pattern=local_patterns.get(kind)
            if not pattern:continue
            for match in re.finditer(pattern,clause):
                a,b=match.span('subject');c,d=match.span('predicate')
                alias_basis=[]
                if kind=='military':
                    value=match.group('object')
                    if value not in objects:
                        alias_basis=[(full,short,anchor) for full,short,anchor in aliases if short==value and anchor<a]
                        if len(alias_basis)!=1:continue
                # Quoted or reported speech is evidence to read, not an assertion.
                quoted=bool(re.search(r'[「『“：:]|曰|云|称|谓',clause[:a]))
                context=assess_context(quote,offsets[a],offsets[c],offsets[d-1]+1,
                                       subject_end=offsets[b-1]+1,assignment=kind=='appointment')
                context_flags=list(context['qualifications'])
                if quoted:context_flags.append('reported')
                # A name embedded in a reported relative's event is not an
                # explicit predicate of that name (e.g. '甲公之父卒'). Patterns
                # above intentionally do not bridge 之父/令/使/命/他人.
                observations.append(dict(kind=kind,polarity='qualified' if context_flags else 'positive_syntax',
                    qualifications=context_flags,quote=quote,start=clause_match.start(),end=clause_match.end(),
                    subject_span=[clause_match.start()+offsets[a],clause_match.start()+offsets[b-1]+1],
                    predicate_span=[clause_match.start()+offsets[c],clause_match.start()+offsets[d-1]+1],
                    context_evidence=[dict(e,start=e['start']+clause_match.start(),end=e['end']+clause_match.start()) for e in context['evidence']],
                    argument_role='patient' if kind=='death' and re.search(r'被杀|所杀',match['predicate']) else 'agent',
                    object_span=([clause_match.start()+offsets[match.start('object')],clause_match.start()+offsets[match.end('object')-1]+1] if kind=='military' else None),
                    alias_basis=[dict(full=full,short=short,start=clause_match.start()+offsets[anchor],end=clause_match.start()+offsets[anchor+len(full)-1]+1) for full,short,anchor in alias_basis],
                    relation_verified=False))
    # Enfeoffment names the recipient after 封. A short recipient needs a
    # preceding full query name in the exact same source sentence.
    if kinds=={'appointment'} and plan.get('answer_slots')==['office'] and not objects:
        fulls=[f for f in forms if len(f) in (2,3)]
        for sentence in re.finditer(r'[^。！？；\n]+[。！？；]?',chunk['text_orig']):
            quote=sentence.group();text=zh.to_simplified(quote)
            if len(text)!=len(quote):continue
            for full in fulls:
                short=full[1:]
                for m in re.finditer(r'封(?P<subject>'+re.escape(full)+'|'+re.escape(short)+r')(?P<title>[\u3400-\u9fff]{1,5}侯)(?=$|[，。；！？])',text):
                    a,b=m.span('subject');c,d=m.span('title')
                    anchor=text.find(full)
                    shortened=m['subject']!=full
                    if shortened and not (0<=anchor<m.start()):continue
                    # No quoted, hypothetical, negated or delegated assignments.
                    if re.search(r'[「『“：:]|曰|云|谓|传言|传闻',text[:m.start()]):continue
                    prefix=re.split(r'[，,]',text[:m.start()])[-1]
                    if re.search(r'未|不|欲|将|若|令|使',prefix):continue
                    origin=sentence.start()
                    observations.append(dict(kind='appointment',polarity='positive_syntax',qualifications=[],
                        quote=quote,start=origin,end=sentence.end(),subject_span=[origin+a,origin+b],
                        predicate_span=[origin+c,origin+d],argument_role='recipient',
                        alias_basis=([dict(full=full,short=short,start=origin+anchor,end=origin+anchor+len(full))] if shortened else []),
                        relation_verified=False))
    from search.person_references import observations as reference_observations
    observations.extend(reference_observations(chunk,plan))
    return dict(positive_groups=sorted({o['kind'] for o in observations if o['polarity']=='positive_syntax'}),
                observations=observations,relation_verified=False)
