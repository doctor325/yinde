"""Extract and bind the roles and reasons in quoted military advice."""
import re

from search import event_attachment as A, evidence_context, zh


_HAN = r'[\u3400-\u9fff]'
_INTRO = re.compile(rf'(?P<speaker>{_HAN}{{1,16}}?)(?:固谏|谏|争)曰[：:]\s*$')
_PROPOSAL = re.compile(
    rf'(?:^|(?<=[。；，、\n]))\s*(?P<proposer>{_HAN}{{0,10}}?)(?:将|欲)'
    rf'(?P<action>(?:亲|出兵|发兵|举兵)?(?P<direction>[东西南北])?'
    rf'[征伐讨攻击](?P<target>{_HAN}{{0,10}}))[。；，]\s*$'
)
_ACKNOWLEDGE = re.compile(rf'\s*(?P<recipient>{_HAN}{{1,8}}?)(?:从之|纳之|然之|不从|不纳)[。；，]')
_FORBID = re.compile(r'(?:不可(?:以)?|不宜|未可|毋|勿)(?:再)?(?:征|伐|讨|攻|进|行师|动兵|出兵)')
_RISK = re.compile(r'(?:将)?恐[^，。]{2,}|难以[^，。]{2,}|不敌|无功|不支')
_ALTERNATIVE = re.compile(r'(?:请|宜|愿)(?:先|姑|且|暂)[^。！？；]{1,24}(?:征|伐|讨|攻|守|待)')


def quotes(text):
    """Yield outer quotation boundaries with nested citations inside their turn."""
    stack = []
    for index, character in enumerate(text):
        if character in '「『':
            if not stack:
                start = index
            stack.append('」' if character == '「' else '』')
        elif character in '」』':
            if not stack or character != stack.pop():
                raise ValueError('Unbalanced quotation')
            if not stack:
                yield start, index
    if stack:
        raise ValueError('Unclosed quotation')


def _speaker(fragment, names):
    matches = [name for name in names if fragment.endswith(name)]
    return max(matches, key=len) if matches else None


def _reasons(body, target):
    """Find explanations in this turn using local argument structure."""
    reasons = []
    sentences = evidence_context.units(body)
    for index, (start, end) in enumerate(sentences):
        sentence = body[start:end].lstrip()
        start = end - len(sentence)
        if (conclusion := _FORBID.search(sentence)) and '，' in sentence[:conclusion.start()]:
            reasons.append((start, end))
        elif _RISK.search(sentence):
            reasons.append((start, end))
        elif target and index + 1 < len(sentences):
            premise, connector, result = sentence.partition('则')
            following = body[slice(*sentences[index + 1])]
            if connector and target in premise and result.strip('。！？ ') and _ALTERNATIVE.match(following.lstrip()):
                reasons.append((start, end))
    return reasons


def extract(window, names, targets):
    """Extract independent source turns before looking at any question."""
    text = zh.to_simplified(window['text_orig'])
    if len(text) != len(window['text_orig']):
        return []
    try:
        boundaries = list(quotes(text))
    except ValueError:
        return []
    turns = []
    previous = 0
    for start, end in boundaries:
        before = text[previous:start]
        base = previous
        previous = end + 1
        introduction = _INTRO.search(before)
        if not introduction:
            continue
        speaker = _speaker(introduction['speaker'], names)
        proposal = _PROPOSAL.search(before[:introduction.start()])
        if not speaker or not proposal:
            continue
        title = proposal['proposer']
        recipient_span = (base + proposal.start('proposer'), base + proposal.end('proposer'))
        body = A.narrative(text[start + 1:end])
        if not title and '陛下' in body:
            response = _ACKNOWLEDGE.match(text, end + 1)
            if response:
                title = response['recipient']
                recipient_span = response.span('recipient')
        if title not in names or names[title] == names[speaker]:
            continue
        source_target = proposal['target'] or proposal['action']
        target = targets.get(source_target, source_target)
        if not target or not (proposal['target'] or source_target in targets):
            continue
        reasons = _reasons(body, proposal['target'])
        if not reasons:
            continue
        speaker_end = base + introduction.end('speaker')
        turns.append(dict(person=names[speaker], addressee=names[title], target=target,
                          speaker=A.excerpt(window, speaker_end - len(speaker), speaker_end),
                          recipient=A.excerpt(window, *recipient_span),
                          action=A.excerpt(window, base + proposal.start('action'), base + proposal.end('action')),
                          reasons=[A.excerpt(window, start + 1 + lo, start + 1 + hi) for lo, hi in reasons],
                          turn=A.excerpt(window, start, end + 1)))
    return turns


def match(plan, turns):
    frame = plan.get('colloquial_advocacy') or {}
    if frame.get('kind') != 'dissuade_attack':
        return None
    matching = [
        turn for turn in turns
        if (turn['person'], turn['addressee'], turn['target'])
        == (frame['person'], frame['recipient'], frame['target'])
    ]
    if len(matching) != 1:
        return None
    turn = matching[0]
    detail = turn['reasons'][0]
    return dict(slot='reason', kind='advocacy', attribution='speaker_explanation', support='dissuasion',
                detail=detail, event=detail, action=turn['action'], speaker=turn['speaker'], recipient=turn['recipient'])
