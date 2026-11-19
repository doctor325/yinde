"""Recall and attribute explanations for an advocated action."""
import re

from search import event_attachment as A, zh
from search.biography_scope import named_biographies


_QUESTION = re.compile(
    r'(?P<person>[\u3400-\u9fff]{2,4})(?:为什么|为何)'
    r'(?P<stance>坚持主张|坚持建议|主张|建议|坚持|提议|力主|力劝)'
    r'(?P<action>[\u3400-\u9fff]{2,20})'
)
_MILITARY = re.compile(r'(讨伐|征讨|征伐)([\u3400-\u9fff]{2,10})')
_RETAIN = re.compile(r'留下?(步兵|骑兵|士兵|部队|军队)([\u3400-\u9fff]{2,10})')
_FORCES = {
    '步兵': '步兵|步士|步卒|步军',
    '骑兵': '骑兵|骑士|骑卒|骑军',
    '士兵': '兵|卒|士', '部队': '兵|卒|军', '军队': '兵|卒|军',
}
_BOUNDARY = r'^|[。！？；，、：:「」\s]'
_STANCE = r'坚请|固请|力请|建请|复请|请|欲|议'
_SPEECH = re.compile(
    rf'(?:{_BOUNDARY})([\u3400-\u9fff]{{1,12}}?)(?:(?:上疏|疏|奏|对|谏)(?:曰[：:]?|[：:])|曰[：:])'
)
_REASON = re.compile(r'以|因|盖|恐|虑|惧|之(利|害)|有(利|害)|可免|可省')
_CAMPAIGN = r'(?:征讨|征伐|讨伐|征|讨|伐)'
_CLAUSE = r'[^，,。；！？「」『』：:\n]'
_CONTINUATION = (
    rf'(?:(?:不时|若不|若使|则|终|不然|否则|故|是以){_CLAUSE}{{0,22}}'
    rf'|{_CLAUSE}{{2,8}}[，,](?:亦|且|将|必){_CLAUSE}{{1,22}})'
)
_EXPLANATION = rf'{_CLAUSE}{{3,32}}(?:[，,]{_CONTINUATION}){{0,5}}'
_PARAGRAPH = re.compile(r'[^\n]+(?:\n(?!\s*\n)[^\n]+)*')
TERMS = ('坚请', '固请', '建请', '上疏', '奏曰', '以为')


def parse(text):
    match = _QUESTION.fullmatch(text.strip().rstrip('？?').rstrip())
    if not match:
        return None
    person, stance, action = match.groups()
    topic = action.removeprefix('继续')
    predicate, target, participants = None, None, None
    if military := _MILITARY.fullmatch(topic):
        predicate, target = military.groups()
    elif retain := _RETAIN.fullmatch(topic):
        participants, topic = retain.groups()
    return dict(person=person, stance=stance, action=action, topic=topic,
                predicate=predicate, target=target, participants=participants)


def _action_patterns(frame):
    topic = re.escape(frame['topic'])
    if frame['target']:
        target = re.escape(frame['target'])
        topic = _CAMPAIGN + target
        # A dropped object is eligible only in a paragraph naming the campaign.
        proposal = rf'(?:{_STANCE})(?:{topic}|{_CAMPAIGN}(?=[。，；！？]|$))'
    elif frame['participants']:
        force = _FORCES[frame['participants']]
        topic = rf'留(?:下)?(?:{force})[^。！？；，]{{0,8}}{topic}'
        proposal = rf'(?:{_STANCE}|今){topic}'
    else:
        proposal = rf'(?:{_STANCE}){topic}'
    return topic, proposal


def candidates(conn, frame, subject, book=None):
    name = frame['person']
    names = {name, name[-1:], name[-2:]}
    actor = '|'.join(map(re.escape, sorted(names, key=len, reverse=True)))
    mention = re.compile(rf'(?:{_BOUNDARY})(?:及|时)?(?:{actor})(?:以|因|谓|恐|虑)')
    topic, proposal = _action_patterns(frame)
    topic_pattern = re.compile(topic)
    proposal_pattern = re.compile(rf'(?<![不勿莫未坚固力建复])(?:{proposal})')
    ranked = []
    pages = scanned = 0
    for peers in named_biographies(conn, name, subject, book):
        pages += 1
        scanned += len(peers)
        texts = {cid: zh.to_simplified(chunk['text_orig']) for cid, chunk in peers.items()}
        for cid, text in texts.items():
            previous = texts.get(cid - 1, '')
            for paragraph in re.split(r'\n\s*\n', text):
                if not topic_pattern.search(paragraph) or not _REASON.search(paragraph):
                    continue
                proposal_hit = proposal_pattern.search(paragraph)
                if not proposal_hit:
                    continue
                speakers = list(_SPEECH.finditer(paragraph, 0, proposal_hit.start()))
                if not speakers and '臣' in paragraph:
                    speakers = list(_SPEECH.finditer(previous))
                    if speakers and '」' in previous[speakers[-1].end():]:
                        speakers = []
                if speakers:
                    if speakers[-1][1] not in names:
                        continue
                elif not mention.search(paragraph):
                    continue
                ranked.append(dict(chunk=peers[cid]))
                break
    return ranked, dict(pages=pages, scanned=scanned)


def _speaker(chunk, text, start, neighbors, names):
    """Locate the current voice with an exact source offset."""
    voices = list(_SPEECH.finditer(text, 0, start))
    if not voices:
        previous = next((p for p in neighbors if p['chunk_id'] == chunk['chunk_id'] - 1 and
                         all(p.get(k) == chunk.get(k) for k in A.SOURCE_SCOPE)), None)
        if not previous:
            return None
        prefix = zh.to_simplified(previous['text_orig'])
        if len(prefix) != len(previous['text_orig']):
            return None
        voices = list(_SPEECH.finditer(prefix))
        if not voices or re.search(r'[」』”’]', prefix[voices[-1].end():]):
            return None
        if re.search(r'[」』”’]', text[:start]):
            return None
        chunk = previous
    elif re.search(r'[」』”’]', text[voices[-1].end():start]):
        return None
    voice = voices[-1]
    if voice[1] in names:
        return A.excerpt(chunk, voice.start(1), voice.end())
    return None


def _campaign_anchor(prefix, topic):
    first = re.match(rf'\s*(?:自|时|方|会)?(?P<topic>{topic})(?=[，,。])', prefix)
    if not first:
        return None
    if re.search(rf'(?:{_BOUNDARY})(?:自|时|方|会|又|复|遂|乃|既|及)?{_CAMPAIGN}'
                 r'(?=[\u3400-\u9fff]{2,10}[，,。])', prefix[first.end():]):
        return None
    return first.span('topic')


def evidence(chunk, frame, neighbors):
    if chunk['layer'] != 'body' or not (chunk.get('revision_id') and chunk.get('source_sha256')):
        return None
    text = zh.to_simplified(chunk['text_orig'])
    if len(text) != len(chunk['text_orig']):
        return None
    name = frame['person']
    names = {name}
    if zh.to_simplified(chunk['heading']).strip() == name:
        names.update((name[-1:], name[-2:]))
    actor = '|'.join(map(re.escape, sorted(names, key=len, reverse=True)))
    topic, proposal = _action_patterns(frame)
    proposal = rf'{proposal}(?=[，,。；！？」』]|$)'
    explanation = re.compile(
        rf'(?:{_BOUNDARY})\s*(?P<event>(?P<actor>{actor}|臣)(?:以|因|恐|虑|惧)'
        rf'(?P<reason>{_EXPLANATION})[，,。](?:故|乃|遂|因)(?P<proposal>{proposal}))'
    )
    benefit = re.compile(
        r'臣(?:愚|窃)?以[为爲](?P<reason>' + re.escape(frame['topic'])
        + rf'{_CLAUSE}{{1,30}}之(?:利|便|备|益)(?:[，,](?:内|外|又){_CLAUSE}{{1,16}}之(?:利|便|备|益))?)'
        r'(?=[，,。；！？」』])'
    )
    narrative = A.narrative(text)
    for paragraph in _PARAGRAPH.finditer(text):
        start, end = paragraph.span()
        for match in explanation.finditer(text, max(0, start - 1), end):
            reason = match['reason']
            if not reason.strip() or re.search(r'[：:《》曰云]|^(?:其事|为言|爲言)$', reason):
                continue
            quote = _speaker(chunk, text, match.start('actor'), neighbors, names) if match['actor'] == '臣' else None
            if match['actor'] == '臣' and not quote:
                continue
            if not quote and match['event'] != narrative[slice(*match.span('event'))]:
                continue
            prior_clause = re.split(r'[。；！？\n]', narrative[start:match.start('actor')])[-1]
            if not quote and re.search(r'曰|云|称|传闻', prior_clause):
                continue
            fields = {}
            if frame['target'] and not re.search(topic, match['proposal']):
                anchor = _campaign_anchor(narrative[start:match.start('actor')], topic)
                if not anchor:
                    continue
                fields['action_context'] = A.excerpt(chunk, start + anchor[0], start + anchor[1])
            fields.update(event=A.excerpt(chunk, *match.span('event')),
                          action=A.excerpt(chunk, *match.span('proposal')),
                          detail=A.excerpt(chunk, *match.span('reason')))
            if quote:
                fields['speaker'] = quote
            return dict(slot='reason', kind='advocacy',
                        attribution='own_statement' if quote else 'narrator_explanation', **fields)
        # A speaker may state a proposal and spell out its benefits later in
        # the same statement. Require an exact repeated topic in the reason.
        for action in re.finditer(rf'(?:{_BOUNDARY})\s*(?P<proposal>{proposal})', text[start:end]):
            if frame['target'] or not frame['participants']:
                break
            action_start, action_end = (start + p for p in action.span('proposal'))
            reason = benefit.search(text, action_end, min(end, action_end + 145))
            if not reason or re.search(r'[「」『』：:]|(?:另|改|复)(?:请|议)|(?<!今)留(?:下)?(?:步|骑)', text[action_end:reason.start()]):
                continue
            speaker = _speaker(chunk, text, action_start, neighbors, names)
            if not speaker:
                continue
            return dict(slot='reason', kind='advocacy', attribution='own_statement', speaker=speaker,
                        event=A.excerpt(chunk, action_start, reason.end()),
                        action=A.excerpt(chunk, action_start, action_end),
                        detail=A.excerpt(chunk, *reason.span('reason')))
    return None
