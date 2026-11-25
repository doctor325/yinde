"""Recall source passages for colloquial questions about historical rewards."""
import json
import re

from search import event_attachment, punctuated_search as S, zh
from search.biography_scope import named_biographies


_NAME = r'[\u3400-\u9fff]{2,6}?'
_PLACE = r'[\u3400-\u9fff]{2,12}?'
_EVENT = re.compile(
    rf'(?:(?:(?P<victor>{_NAME})在)?(?P<anchor>{_PLACE})'
    r'(?P<trigger>打赢(?:了)?|打下来(?:了)?|攻下(?:了)?|报捷|告捷|传来捷报)'
    rf'|(?P<attacker>{_NAME})?(?P<capture>攻克|攻下|攻破|打下)(?P<site>{_PLACE}))'
    r'(?:之后|以后|后|那会儿)[，,]\s*(?P<question>.+)'
)
_OCCASION = re.compile(rf'(?P<anchor>{_PLACE})那(?:一)?(?:回|次|会儿)[，,]\s*(?P<question>.+)')
_GIFT = re.compile(
    rf'(?P<giver>{_NAME})(?:给|向)(?P<recipient>{_NAME})(?:赏赐|赏|赐)(?:了)?(?:什么(?:东西)?|哪些东西)'
)
_DIRECT_GIFT = re.compile(
    rf'(?P<giver>{_NAME})(?:赏赐|赏给|赐给|赏|赐|送给)(?:了)?(?P<recipient>{_NAME})(?:什么(?:东西)?|哪些东西)'
)
_PRAISE = re.compile(rf'(?P<giver>{_NAME})(?:是)?(?:怎么|如何)(?:看待|评价|看)(?P<recipient>{_NAME})的?')
_RECIPIENT = re.compile(r'(?:得到|受到|获得)赏赐的是谁|谁(?:得到|受到|获得)了?赏赐')
_DYNASTIC_TITLE = re.compile(r'[唐宋明晋汉隋][\u3400-\u9fff][祖宗]')
_UNNAMED = {'皇帝', '天子', '朝廷', '君主'}


def parse(text):
    event = _EVENT.fullmatch(text) or _OCCASION.fullmatch(text)
    if not event:
        return None
    event_parts = event.groupdict()
    question = event['question']
    if match := _GIFT.fullmatch(question) or _DIRECT_GIFT.fullmatch(question):
        slot = 'gift'
    elif 'trigger' not in event_parts:
        return None
    elif match := _PRAISE.fullmatch(question):
        slot = 'appraisal'
    elif _RECIPIENT.fullmatch(question):
        slot = 'recipient'
    else:
        return None
    giver, recipient = (match['giver'], match['recipient']) if match else (None, None)
    # Use a named participant as the retrieval anchor. Store the direction of
    # the question independently so the same passage can be tested both ways.
    victor = event_parts.get('victor') or event_parts.get('attacker')
    participants = (victor, recipient, giver)
    person = next((name for name in participants if name and name not in _UNNAMED
                   and not _DYNASTIC_TITLE.fullmatch(name)), None)
    return dict(kind='reward', anchor=event['anchor'] or event_parts.get('site'),
                trigger_action=event_parts.get('trigger') or event_parts.get('capture'), victor=victor,
                giver=giver, recipient=recipient, asked_role=slot, person=person)


_OUTCOME = re.compile(r'大破|击破|破贼|破敌|败之|拔(?:贼|敌)?|城破|攻克|大溃|平定')
_REPORT = re.compile(r'捷闻|捷奏入|捷至|捷报|奏捷|告捷|报捷|献捷|以捷闻')
_ACTION = {
    'gift': re.compile(r'(?:赐|赉)(?!死|自尽|自裁)[\u3400-\u9fff]{2,18}|赏(?:赐|赉)[\u3400-\u9fff]{1,16}'),
    'recipient': re.compile(r'赐(?:其)?(?:告捷者|报捷者)|(?:赉|赐)其使'),
    'appraisal': re.compile(r'赏[\u3400-\u9fff]{1,6}之知人|(?:上|帝)(?:嘉|称善)'),
}
_DELEGATED_GIFT = r'遣(?:使者|使)授(?:[\u3400-\u9fff]{1,6}?)?以[\u3400-\u9fff]{2,18}'
_LOCAL_GIFT = re.compile(rf'{_ACTION["gift"].pattern}|{_DELEGATED_GIFT}')
_EPISODE_BREAK = re.compile(r'(?:^|[。；\n])\s*(?:翌日|明年|其后|[一二三四五六七八九十]+年)[，,]')
_SENTENCE = re.compile(r'[^。！？；\n]+[。！？；]?')


def _action_context(chunk, preceding, overlap, place, frame, short_name):
    prefix = preceding[:-overlap] if overlap else ''
    original = prefix + chunk['text_orig']
    normalized = zh.to_simplified(original)
    if len(normalized) != len(original):
        return None
    prose = event_attachment.narrative(normalized)
    local_gift = frame['trigger_action'] is None
    action_pattern = _LOCAL_GIFT if local_gift else _ACTION[frame['asked_role']]
    for action in action_pattern.finditer(prose, len(prefix)):
        lo = max(0, action.start()-400)
        # Find the place in the lead-up to this action. A place that only
        # appears after the action may belong to a later campaign.
        anchor = prose.rfind(place, lo, action.start())
        if anchor < 0:
            continue
        start = max(lo, anchor-20)
        lead = prose[start:action.start()]
        episode = prose[anchor:action.start()]
        if _EPISODE_BREAK.search(episode):
            continue
        if local_gift and re.search(r'\n\s*\n', episode):
            continue
        if not local_gift and (not _OUTCOME.search(lead) or frame['asked_role'] == 'gift' and not _REPORT.search(lead)):
            continue
        person = frame['person']
        heading = zh.to_simplified(chunk['heading']).strip()
        names = (frame['giver'], frame['recipient']) if local_gift else (person,)
        if person and not any(name in normalized or name == heading for name in names) and not (short_name and short_name in lead):
            continue
        if frame['asked_role'] == 'appraisal' and frame['recipient'] not in normalized:
            continue
        sentence = next(s for s in _SENTENCE.finditer(prose) if s.start() <= action.start() < s.end())
        a, b = max(len(prefix), sentence.start()), sentence.end()
        span = dict(start=a-len(prefix), end=b-len(prefix), text=original[a:b], selected=True)
        # Preserve the original heading-scoped lead-up when the indexed
        # snippet begins after the battle's place name.
        context = original[start:a].strip()
        return span, context
    return None


def _unheaded_seeds(conn, person, subject, places):
    """Use an explicit biography opening to look past its first name mention."""
    opening = re.compile(rf'\s*{re.escape(person)}[，,]?\s*字')
    scopes = set()
    for hit in subject:
        chunk = hit['chunk']
        parts = chunk.get('parts', ())
        if (chunk['layer'] == 'body' and not chunk['heading'] and parts and
                parts[0]['block_index'] == parts[0]['start'] == 0 and
                opening.match(zh.to_simplified(chunk['text_orig']))):
            scopes.add(tuple(chunk[k] for k in event_attachment.SOURCE_SCOPE))
    for scope in sorted(scopes):
        where = ' OR '.join('search_text LIKE ?' for _ in places)
        rows = conn.execute(f'SELECT payload FROM chunks WHERE book_id=? AND page_id=? AND ({where}) '
                            'ORDER BY chunk_id LIMIT 60', (*scope[:2], *('%'+p+'%' for p in places)))
        for (payload,) in rows:
            chunk = json.loads(payload)
            if tuple(chunk[k] for k in event_attachment.SOURCE_SCOPE) == scope:
                yield chunk


def candidates(conn, frame, subject, book=None, *, place_forms=()):
    """Find action candidates in a bounded window after a place in the source."""
    places = tuple(dict.fromkeys((frame['anchor'], *place_forms)))
    seeds = {}
    for place in places:
        for hit in S.lexical(conn, place, book, page_size=60)['hits']:
            chunk = hit['chunk']
            seeds[chunk['chunk_id']] = chunk
    short_names = {}
    if person := frame['person']:
        for peers in named_biographies(conn, person, subject, book):
            seeds.update((cid, c) for cid, c in peers.items()
                         if any(place in zh.to_simplified(c['text_orig']) for place in places))
        if len(person) >= 3:
            for chunk in _unheaded_seeds(conn, person, subject, places):
                cid = chunk['chunk_id']
                seeds[cid] = chunk
                short_names[cid] = person[1:]
    selected = {}
    for cid, seed in seeds.items():
        if seed['layer'] != 'body' or S.normalize(seed['heading']) == '校勘记':
            continue
        anchor_forms = [place for place in places if place in zh.to_simplified(seed['text_orig'])]
        if not anchor_forms:
            continue
        peers = [(seed, '', 0)]
        if row := conn.execute('SELECT payload FROM chunks WHERE chunk_id=?', (cid+1,)).fetchone():
            following = json.loads(row[0])
            for _, preceding, _, overlap in event_attachment.previous_overlap(following, [seed]):
                peers.append((following, preceding, overlap))
        for chunk, preceding, overlap in peers:
            for place in anchor_forms:
                if match := _action_context(chunk, preceding, overlap, place, frame, short_names.get(cid)):
                    focus, context = match
                    target = chunk['chunk_id']
                    # Prefer candidates that carry their own link to the
                    # event over candidates that need an adjacent snippet.
                    direct = place in zh.to_simplified(chunk['text_orig'])
                    rank = (not direct, target)
                    if target not in selected or rank < selected[target][0]:
                        selected[target] = (rank, dict(chunk=chunk, reward_focus=focus, reward_context=context))
    return [hit for _, hit in sorted(selected.values(), key=lambda item: item[0])]
