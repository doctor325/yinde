"""Recall events from confirmed annals and conservatively attribute 帝/上."""
import re
from search import zh
from search.relation_context import assess_context

DATE_UNIT = (r'(?:[元一二三四五六七八九十百千〇零\d]+年|[正一二三四五六七八九十冬腊]+月|'
             r'[初一二三四五六七八九十廿卅\d]+日|[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]|[春夏秋冬闰])')
DATE_PREFIX = re.compile(r'(?:'+DATE_UNIT+r'[\s，,、]*)*')
VERBS = {'accession': r'即(?:皇帝)?位|践祚|登极',
         'appointment': r'命|以|拜|授|擢',
         'military': r'亲征|征|伐|攻|讨|破|败|克|战'}
REPORT = re.compile(r'[「」『』“”‘’：:]|(?:曰|云|言|奏|称|谓|传闻)')
SENTENCE = re.compile(r'[^。！？；\n]+[。！？；]?')
OFFICE = r'(?:宰相|丞相|[左右]丞相|参知政事|枢密使|同中书门下平章事|尚书[左右]仆射|御史大夫|太尉|太傅|司空|司徒|[吏户礼兵刑工]部尚书)'


def observations(chunk, plan, query):
    """Keep candidates even when syntax cannot safely bind the actor."""
    source = chunk['text_orig']
    text = zh.to_simplified(source)
    if len(text) != len(source) or chunk.get('layer') != 'body':
        return []
    parts = chunk.get('parts', [])
    # Overlapping chunks can begin inside a quotation or an existing clause.
    safe_start = bool(parts and parts[0]['start'] == 0)
    result = []
    for group in plan['relation_groups']:
        kind = group['kind']
        if kind not in VERBS:
            continue
        verb = VERBS[kind]
        if kind == 'military':
            if '战败' in query:
                continue
            if re.search(r'击败|战胜|攻取|收复',query):
                verb = r'破|败|克'
            else:
                verb = r'亲征|[南北东西]?[征伐]|攻|讨'
        delegated = r'(?P<delegated>命|遣|诏)[^，,。！？；]{0,32}?' if kind=='military' else None
        action = r'(?P<verb>'+verb+r')'
        if delegated:
            action = '(?:'+delegated+')?'+action
        pattern = re.compile(r'(?P<subject>[帝上])(?:遂|乃|复|又|未|不|欲|将){0,2}'+action)
        for sentence in SENTENCE.finditer(text):
            quote = sentence.group()
            for match in pattern.finditer(quote):
                a,b = match.span('subject')
                c,d = match.span('verb')
                tail = quote[d:]
                constraints = plan['constraints']
                argument = None
                if kind == 'appointment':
                    # A named recipient immediately followed by an office marker
                    # is distinct from commanding an officer to do something.
                    recipient = constraints[0]['forms'] if len(constraints)==1 else []
                    if recipient and not re.search(r'被|受命|获封|担任|出任|改任|任职|封为|谁',query):
                        names = '|'.join(map(re.escape,recipient))
                        argument = re.match(r'(?:'+names+r')为'+OFFICE+r'(?=$|[，,。；！？])',tail)
                    bound = argument is not None
                elif kind == 'military':
                    if len(constraints)==1:
                        names = '|'.join(map(re.escape,constraints[0]['forms']))
                        argument = re.match(r'(?:'+names+r')(?=$|[，,。；！？]|于|於)',tail)
                    bound = argument is not None
                else:
                    bound = not constraints
                if argument:
                    d += argument.end()
                offset = sentence.start()
                qualifiers = assess_context(source,offset+a,offset+c,offset+d,
                                            subject_end=offset+b)['qualifications']
                if (not safe_start or REPORT.search(text[:sentence.end()])
                        or not DATE_PREFIX.fullmatch(quote[:a].strip())
                        or not quote.endswith(('。','！','？'))):
                    qualifiers.append('unresolved_annal_actor')
                if not bound or len(plan['relation_groups'])!=1:
                    qualifiers.append('unresolved_event_arguments')
                if delegated and match['delegated']:
                    qualifiers.append('delegated_action')
                result.append(dict(kind=kind,polarity='qualified' if qualifiers else 'positive_syntax',
                    qualifications=qualifiers,quote=source[offset:sentence.end()],start=offset,end=sentence.end(),
                    subject_span=[offset+a,offset+b],predicate_span=[offset+c,offset+d],
                    subject_basis='annal_scope',relation_verified=False))
    return result
