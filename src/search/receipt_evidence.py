"""Ground a ruler's reaction to a victory report in the original edition."""
import json
import re

from search import biography_lookup, evidence_context, event_attachment, punctuated, zh
from search.polity_evidence import Sources, inside, source_ref


_HAN = r'[\u3400-\u9fff]'
_REPORT = r'捷奏入|至是告捷|捷闻'
_REACTION = re.compile(
    rf'(?<![^，,。；！？\s])(?:(?P<report>{_REPORT})[，,]\s*(?:京师相贺[，,]\s*)?)?'
    rf'(?P<actor>{_HAN}{{1,6}}?)(?:(?:大悦|大喜)[，,]\s*)?'
    rf'(?P<action>赐(?P<envoy>其告捷者){_HAN}{{2,16}}'
    r'|(?:尤|益)?赏(?P<name>[\u3400-\u9fff]{1,3})之知人'
    r'|召归[，,]宴劳[\u3400-\u9fff]{0,8}[，,]赐[\u3400-\u9fff]{2,16}(?:[，,]遣还镇)?)'
    r'(?=[。！？；])'
)
_BREAK = re.compile(r'\n\s*\n|(?:^|[。；])\s*(?:明年|翌日|其后|[一二三四五六七八九十]+年)[，,]')
_OTHER_REPORT = re.compile(_REPORT)
_OTHER_FIGHT = re.compile(r'大破|败之|大溃|(?:再|又|复|遂)(?:攻|破|击|拔|战)')
_GIFT = re.compile(rf'捷闻[，,]\s*(?P<actor>帝|上)曰[：:]\s{{1,90}}'
                   rf'(?P<action>赐(?!死|自尽|自裁)(?P<gift>{_HAN}{{2,16}}))(?=[。；！？])')
_DELEGATION = re.compile(rf'(?:其明日|明日|翌日|次日)[，,](?P<action>遣(?:使者|使)授以'
                         rf'(?P<gift>{_HAN}{{2,16}}))(?=[。；！？])')
_LOCAL_GRANT = re.compile(r'赐|赉|遣(?:使者|使)授')


def _opening_reference(sources, entry):
    _, blocks = sources.page(entry['page_id'])
    i = entry['opening_block']
    if not 0 <= i < len(blocks) or blocks[i]['kind'] != 'body':
        raise ValueError('Ruler opening no longer points to a body block')
    block = blocks[i]['text']
    lo = len(block) - len(block.lstrip())
    return sources.check({
        **{k: entry[k] for k in (*evidence_context.IDENTITY, 'title', 'source_url', 'quote')},
        'heading': entry.get('scope_heading', ''),
        'ranges': [[i, lo, lo + len(entry['quote'])]],
    })


def _annal_scope(chunk, entry):
    if all(chunk[k] == entry.get(k) for k in evidence_context.IDENTITY):
        heading = entry.get('scope_heading')
        return not heading or zh.to_simplified(chunk.get('heading', '')).strip() == heading
    return any(chunk['book_id'] == entry['book_id'] and all(
        chunk[k] == v.get(k) for k in ('page_id', 'revision_id', 'source_sha256')
    ) for v in entry['continuations'])


def _battle(lead, places, reported):
    direct = re.compile(rf'(?:大破{_HAN}{{1,12}}?[于於]|拔(?:贼)?)(?:{places})(?=[，,。])')
    for battle in reversed(list(direct.finditer(lead))):
        prefix = re.split(r'[，,。；\n]', lead[:battle.start()])[-1]
        if re.search(r'欲|未|不|请|议|谋|拟|将$', prefix):
            continue
        tail = lead[battle.end():]
        # Without a written report, require the actor to follow the victory
        # in the same sentence and the recipient to be a victory messenger.
        if not reported and re.fullmatch(r'[，,]\s*', tail):
            return {'所问战事': battle.span()}
        if reported and not (_BREAK.search(tail) or _OTHER_REPORT.search(tail)):
            if re.fullmatch(r'[，,。]\s*', tail) or '奏曰：' in tail and not _OTHER_FIGHT.search(tail):
                return {'所问战事': battle.span()}
    # The location may be given at the approach, but an actual rout must
    # bridge the approach and the report in the same source paragraph.
    if reported and (approach := re.search(rf'去(?:{places}){_HAN}{{1,6}}而军', lead)):
        tail = lead[approach.end():]
        if (rout := re.search(r'敌遂大溃', tail)) and not (_BREAK.search(tail) or _OTHER_REPORT.search(tail)
                                                         or _OTHER_FIGHT.search(tail[rout.end():])):
            return {'所问战事': approach.span(),
                    '战果': (approach.end()+rout.start(), approach.end()+rout.end())}
    return None


def _original_view(sources, chunk, pattern):
    if chunk['layer'] != 'body' or not pattern.search(event_attachment.narrative(zh.to_simplified(chunk['text_orig']))):
        return None
    body = evidence_context.assemble(sources.library, chunk, max_chars=1600)
    if body['boundary_cut'] or body['unbalanced_quote']:
        return None
    view = evidence_context.rule_view(chunk, body)
    normalized = zh.to_simplified(view['text_orig'])
    if len(normalized) != len(view['text_orig']):
        return None
    return view, event_attachment.narrative(normalized)


def _review(sources, chunk, identity, places, frame=None):
    if not (original := _original_view(sources, chunk, _REACTION)):
        return None
    view, prose = original
    same_book = [entry for entry in identity if entry['book_id'] == chunk['book_id']]
    for reaction in _REACTION.finditer(prose):
        start = reaction.start()
        actor, report = reaction['actor'], reaction['report']
        if not report and not reaction['envoy']:
            continue
        matching = [e for e in same_book if actor in (*e['aliases'], e['aliases'][0][1:])
                    or actor in ('上', '帝') and _annal_scope(chunk, e)]
        if len({tuple(e['aliases']) for e in matching}) != 1:
            continue
        entry = matching[0]
        if frame and (frame['asked_role'] == 'recipient' and not reaction['envoy']
                      or frame['asked_role'] == 'appraisal' and not reaction['name']):
            continue
        action = source_ref(view, reaction.start('action'), reaction.end('action'))
        if not inside(chunk, action):
            continue
        lo = max(0, start-420)
        lead = prose[lo:start]
        if not (battle := _battle(lead, places, bool(report))):
            continue
        if frame and frame['victor']:
            a = lo + battle['所问战事'][0]
            victor = frame['victor']
            if not prose[:a].endswith(victor):
                continue
            battle['胜方'] = (a-lo-len(victor), a-lo)
        refs = {
            **{label: source_ref(view, lo+a, lo+b) for label, (a, b) in battle.items()},
            '行动者': source_ref(view, *reaction.span('actor')),
            '当时行动': action,
        }
        if report:
            refs['报捷原句'] = source_ref(view, *reaction.span('report'))
        if reaction['envoy']:
            refs['赏赐对象'] = source_ref(view, *reaction.span('envoy'))
            description = '赏赐报捷者，原句未记使者姓名'
        elif (short := reaction['name']):
            candidates = list(re.finditer(rf'(?<=[。；，\n])\s*(?P<full>{_HAN}{{2,4}})使还[，,]\s*奏曰[：:]', lead))
            if len(candidates) != 1 or not candidates[0]['full'].endswith(short) or candidates[0]['full'] == short:
                continue
            person = candidates[0]
            if frame and person['full'] != frame['recipient']:
                continue
            refs['赞赏对象'] = source_ref(view, reaction.start('name'), reaction.end('action'))
            refs['对象全名'] = source_ref(view, lo+person.start('full'), lo+person.end('full'))
            description = f"赞赏{person['full']}识人"
        else:
            description = '召回犒劳并赏赐，赏赐句省称受赏者'
        refs['本纪身份'] = _opening_reference(sources, entry)
        corroboration = next((e for e in identity if e['book_id'] != entry['book_id']
                              and e['aliases'] == entry['aliases']), None)
        if corroboration:
            refs['本纪身份互证'] = _opening_reference(sources, corroboration)
        return dict(person=entry['aliases'][0], summary=description,
                    citations={label: sources.check(ref) for label, ref in refs.items()})
    return None


def _recipient_opening(sources, chunk, name):
    page, blocks = sources.page(chunk['page_id'])
    if chunk['heading'] or any(page[k] != chunk[k] for k in evidence_context.IDENTITY):
        return None
    block = blocks[0]
    original = block['text']
    text = zh.to_simplified(original)
    match = re.match(rf'\s*({re.escape(name)}[，,]?字[^。！？]+。)', text)
    if block['kind'] != 'body' or not match or len(text) != len(original):
        return None
    opening = {**chunk, 'text_orig': original, 'parts': [dict(block_index=0, start=0, end=len(original))]}
    return source_ref(opening, *match.span(1))


def _annal_victory(sources, entry, victor, opponent):
    """Resolve 帝 in a biography via the named victor and opponent's fate."""
    # A different name for the place is acceptable here only when both names,
    # the storming of the place and the opponent's fate agree in one sentence.
    event = re.compile(rf'{re.escape(victor)}拔{_HAN}{{2,10}}[，,]'
                       rf'{re.escape(opponent)}自焚(?:死)?(?=[，,。])')
    pages = [volume['page_id'] for volume in (entry, *entry['continuations'])]
    placeholders = ','.join('?' for _ in pages)
    # The existing index is keyed by book, so query the book once for every
    # attested volume. Keep the per-row revision and heading check below.
    rows = sources.index.execute(
        f'SELECT payload FROM chunks WHERE book_id=? AND page_id IN ({placeholders}) '
        'AND search_text LIKE ? ORDER BY chunk_id',
        (entry['book_id'], *pages, '%'+victor+'%'))
    matches = {}
    for (payload,) in rows:
        chunk = json.loads(payload)
        if chunk['layer'] != 'body' or not _annal_scope(chunk, entry):
            continue
        original = chunk['text_orig']
        text = zh.to_simplified(original)
        if len(original) != len(text):
            continue
        for battle in event.finditer(event_attachment.narrative(text)):
            ref = source_ref(chunk, *battle.span())
            key = (ref['page_id'], tuple(map(tuple, ref['ranges'])))
            matches[key] = ref
    return next(iter(matches.values())) if len(matches) == 1 else None


def _gift_evidence(sources, chunk, name, places, entry):
    if len(name) < 3:
        return None
    if not (original := _original_view(sources, chunk, _GIFT)):
        return None
    if not (opening := _recipient_opening(sources, chunk, name)):
        return None
    view, prose = original
    # Establish the participant from a command in the battle paragraph and
    # keep the narration in that participant's biography through the report.
    short_name = re.escape(name[1:])
    revolt = re.compile(rf'(?:{places})(?P<enemy>{_HAN}{{2,4}})反(?=[，,。])')
    for gift in _GIFT.finditer(prose):
        action = source_ref(view, *gift.span('action'))
        if not inside(chunk, action):
            continue
        lo = max(0, gift.start()-400)
        lead = prose[lo:gift.start()]
        battle = revolt.search(lead)
        if not battle:
            continue
        opponent = battle['enemy']
        dispatch = re.search(rf'以{short_name}为[\u3400-\u9fff、]{{2,24}}使(?=[，,。])', lead[battle.end():])
        result = re.search(rf'城破[，,](?:{re.escape(opponent)}|{re.escape(opponent[1:])}){_HAN}{{0,8}}?自焚', lead)
        if not dispatch or not result or battle.end()+dispatch.end() > result.start():
            continue
        tail = lead[result.end():]
        if _OTHER_REPORT.search(tail) or _OTHER_FIGHT.search(tail) or not re.search(
                rf'\n\s*\n{short_name}[^。！？\n]{{1,18}}[，,]', tail):
            continue
        witness = _annal_victory(sources, entry, name, opponent)
        if not witness:
            continue
        lower = lo+battle.end()
        refs = {
            '所问战事': source_ref(view, lo+battle.start(), lo+battle.end()),
            '受赏者身份': opening,
            '受赏者在战事中的记载': source_ref(view, lower+dispatch.start(), lower+dispatch.end()),
            '报捷与赏赐原句': source_ref(view, gift.start(), gift.end()),
            '赏赐者': source_ref(view, *gift.span('actor')),
            '当时行动': action,
            '本纪战事互证': witness,
            '本纪身份': _opening_reference(sources, entry),
        }
        gift_text = zh.to_simplified(view['text_orig'][slice(*gift.span('gift'))])
        return dict(person=entry['aliases'][0], recipient=name, summary=f'赐{name}{gift_text}',
                    citations={label: sources.check(ref) for label, ref in refs.items()})
    return None


def _biography_opening(sources, chunk, name):
    if zh.to_simplified(chunk['heading']).strip() != name:
        return None
    _, blocks = sources.page(chunk['page_id'])
    first = chunk['parts'][0]['block_index']
    heading = next((i for i in range(first-1, -1, -1) if blocks[i]['kind'] == 'heading'), None)
    if heading is None or blocks[heading]['text'] != chunk['heading']:
        return None
    i = heading+1
    body = blocks[i]
    text = zh.to_simplified(body['text'])
    match = re.match(rf'\s*(?P<name>{re.escape(name)}[，,]?字{_HAN}{{1,4}})(?=[，,。])', text)
    if body['kind'] == 'body' and match and len(text) == len(body['text']):
        a, b = match.span('name')
        return {**{k: chunk[k] for k in (*evidence_context.IDENTITY, 'title', 'source_url', 'heading')},
                'ranges': [[i, a, b]], 'quote': body['text'][a:b]}
    return None


def _named_participant(sources, chunk, event, person):
    """Find a full name beside the same confrontation wording in this work."""
    rows = sources.index.execute(
        'SELECT payload FROM chunks WHERE book_id=? AND page_id<>? AND search_text LIKE ? AND search_text LIKE ?',
        (chunk['book_id'], chunk['page_id'], f'%{event}%', f'%{person}%'))
    witness = re.compile(rf'{re.escape(event)}[^\n]{{0,140}}?{re.escape(person)}[^\n。！？]{{0,28}}[。！？][」』]?')
    matches = {}
    for (payload,) in rows:
        peer = json.loads(payload)
        if zh.to_simplified(peer['heading']).strip() != person:
            continue
        original = peer['text_orig']
        text = zh.to_simplified(original)
        if len(text) != len(original):
            continue
        for match in witness.finditer(text):
            ref = source_ref(peer, *match.span())
            matches[ref['page_id'], tuple(map(tuple, ref['ranges']))] = ref
    return next(iter(matches.values())) if len(matches) == 1 else None


def _delegated_roles(sources, chunk, view, lead, grant, places, giver, recipient):
    # Require the order to the biographical subject, their reply, and release
    # from the audience before attaching an unnamed next-day delegation.
    opening = _biography_opening(sources, chunk, recipient)
    if not opening or giver[-1] == recipient[-1]:
        return None
    actor, target = map(re.escape, (giver[-1], recipient[-1]))
    audience = re.compile(
        rf'(?:^|[。；\n])(?P<order>(?P<actor>{re.escape(giver)}|{actor})'
        rf'(?:自)?(?:行酒)?(?:到|至|召|引)(?:{re.escape(recipient)}|{target})(?:前)?[，,]'
        rf'(?:命|令)(?:{re.escape(recipient)}|{target}){_HAN}{{1,10}}'
        rf'(?:[，,](?P=actor){_HAN}{{1,14}}){{0,2}}(?:[，,](?:问|谕|慰){_HAN}{{1,8}})?[。])'
        rf'(?P<reply>(?:{re.escape(recipient)}|{target}){_HAN}{{1,20}}以对[，,](?:既)?毕[，,]'
        rf'(?:使|令)(?:复服|退|就坐|归席)(?:[，,](?:欢[宴讌燕]|宴饮){_HAN}{{0,6}})?[。])$')
    if not (turn := audience.search(lead)):
        return None
    if re.search(r'未|弗|拒|不许|欲|将遣', turn['order'] + turn['reply']):
        return None
    event = re.compile(rf'(?:^|[。；\n])\s*(?P<fight>{_HAN}{{2,5}}?(?:出|攻|犯|寇)(?:{places}))(?=[，,。])')
    prior = lead[:turn.start()]
    battles = list(event.finditer(prior))
    if len(battles) != 1:
        return None
    battle = battles[0]
    if _BREAK.search(prior[battle.end():]) or re.search(r'欲|未|拟|将', battle['fight']):
        return None
    refs = {'受赏者身份': opening}
    if turn['actor'] != giver:
        witness = _named_participant(sources, chunk, battle['fight'], giver)
        if not witness:
            return None
        refs['赏赐者全名旁证'] = witness
    base = grant.start() - len(lead)
    return {**refs,
            '所问战事': source_ref(view, *(base+i for i in battle.span('fight'))),
            '赏赐者与受赏者': source_ref(view, *(base+i for i in turn.span('order'))),
            '受赏者答话': source_ref(view, *(base+i for i in turn.span('reply')))}


def _local_gift_evidence(sources, chunk, places, frame):
    giver, recipient = frame['giver'], frame['recipient']
    if not giver or giver == recipient or not (original := _original_view(sources, chunk, _LOCAL_GRANT)):
        return None
    view, prose = original
    explicit = re.compile(rf'(?:^|[。；！？\n])\s*(?P<action>{re.escape(giver)}'
                          rf'(?:(?:赐|赉){re.escape(recipient)}|遣(?:使者|使)授{re.escape(recipient)}以)'
                          rf'(?!死|自尽|自裁)(?P<gift>{_HAN}{{2,16}}))(?=[。；！？])')
    grants = [(match, False) for match in explicit.finditer(prose)]
    grants.extend((match, True) for match in _DELEGATION.finditer(prose))
    for grant, delegated in grants:
        action = source_ref(view, *grant.span('action'))
        if not inside(chunk, action):
            continue
        start = grant.start() if delegated else grant.start('action')
        lo = max(0, start-400)
        lead = prose[lo:start]
        if paragraph := list(re.finditer(r'\n\s*\n', lead)):
            lo += paragraph[-1].end()
            lead = prose[lo:start]
        if delegated:
            refs = _delegated_roles(sources, chunk, view, lead, grant, places, giver, recipient)
            if not refs:
                continue
        else:
            locations = list(re.finditer(places, lead))
            if not locations or _BREAK.search(lead[locations[-1].end():]):
                continue
            place = locations[-1]
            refs = {'事发地点': source_ref(view, lo+place.start(), lo+place.end())}
        refs['当时行动'] = action
        gift = grant['gift']
        return dict(person=giver, recipient=recipient, summary=f'赐{recipient}{gift}',
                    citations={label: sources.check(ref) for label, ref in refs.items()})
    return None


def _review_reward(sources, chunk, entries, places, frame):
    if frame['trigger_action'] is None:
        return _local_gift_evidence(sources, chunk, places, frame)
    if frame['asked_role'] != 'gift':
        giver = frame['giver']
        if giver not in (None, '皇帝', '天子', '君主'):
            entries = [entry for entry in entries if giver in entry['aliases']]
        return _review(sources, chunk, entries, places, frame)
    for entry in entries:
        if entry['book_id'] != chunk['book_id']:
            continue
        # Check the same source when either named side is the attested ruler.
        # Match the question's direction only after reconstructing the gift.
        if frame['giver'] in entry['aliases']:
            name = frame['recipient']
        elif frame['recipient'] in entry['aliases']:
            name = frame['giver']
        else:
            continue
        if linked := _gift_evidence(sources, chunk, name, places, entry):
            return linked if frame['giver'] in entry['aliases'] else None
    return None


def review_hits(index, activity, hits, *, place_forms=()):
    """Attach verified citations to the relevant candidates."""
    kind = activity.get('kind')
    if kind not in ('receipt', 'reward') or not activity.get('anchor'):
        return
    forms = '|'.join(re.escape(form) for form in dict.fromkeys((activity['anchor'], *place_forms)))
    try:
        local_gift = kind == 'reward' and activity['trigger_action'] is None
        if local_gift:
            identity = ()
        elif kind == 'reward':
            identity = biography_lookup.indexed_entries()
        else:
            identity = biography_lookup.resolved(activity['person'], include_shared=True)
        if not identity and not local_gift:
            return
        with punctuated.connect() as library:
            sources = Sources(index, library, {})
            for hit in hits:
                try:
                    linked = (_review_reward(sources, hit['chunk'], identity, forms, activity) if kind == 'reward'
                              else _review(sources, hit['chunk'], identity, forms))
                except ValueError:
                    continue
                if linked:
                    hit['evidence_audit'].update(status='clues_present', missing=[], activity_evidence=linked,
                        answer_slot_evidence={'activity': [linked['citations']['当时行动']['quote']]})
    except FileNotFoundError:
        return
