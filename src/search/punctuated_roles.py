"""Conservative surface-syntax evidence; not general historical role resolution.

Only explicit subject-predicate patterns receive a positive syntax signal.
Negation, reports and hypothetical clauses remain candidates but get no such
signal. Unrecognized constructions stay unknown, never false or unsupported.
"""
import re
from search import zh
from search.relation_context import assess_context

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
        'accession':name+bridge+r'(?:即位|践祚|登极)',
    }
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
        # Preserve source offsets through whitespace removal, then scope each
        # qualifier to the particular subject/predicate observation.
        for kind in sorted(kinds):
            pattern=patterns.get(kind)
            if not pattern:continue
            for match in re.finditer(pattern,clause):
                a,b=match.span('subject');c,d=match.span('predicate')
                context=assess_context(quote,offsets[a],offsets[c],offsets[d-1]+1,
                                       subject_end=offsets[b-1]+1,assignment=kind=='appointment')
                context_flags=context['qualifications']
                # A name embedded in a reported relative's event is not an
                # explicit predicate of that name (e.g. '甲公之父卒'). Patterns
                # above intentionally do not bridge 之父/令/使/命/他人.
                observations.append(dict(kind=kind,polarity='qualified' if context_flags else 'positive_syntax',
                    qualifications=context_flags,quote=quote,start=clause_match.start(),end=clause_match.end(),
                    context_evidence=[dict(e,start=e['start']+clause_match.start(),end=e['end']+clause_match.start()) for e in context['evidence']],
                    relation_verified=False))
    return dict(positive_groups=sorted({o['kind'] for o in observations if o['polarity']=='positive_syntax'}),
                observations=observations,relation_verified=False)
