"""Recall and focus narration of activity concurrent with an event."""
import json
import re

from search import annal_events, event_attachment, punctuated_relevance as R, punctuated_search as S, reward_recall, zh
from search.biography_scope import named_biographies
from search.polity_evidence import source_ref


_PERSON = r'[\u3400-\u9fff]{2,6}'
_TRIGGER = r'[^。！？?!，,；;\n]{2,40}?'
_WHEN = r'(?:的时候|那会儿|那阵子|之际|当口|时)'
_ASK = r'(?:当时)?(?:正?在|正)?(?P<asked_activity>做什么|干什么|忙什么)'
_QUERY = re.compile(
    rf'(?P<person>{_PERSON}?)(?:在)?(?:接到|收到|听到|听闻|得知)'
    rf'(?P<trigger>{_TRIGGER}){_WHEN}[，,]?\s*{_ASK}'
)
_EVENT_FIRST = re.compile(rf'(?P<trigger>{_TRIGGER}){_WHEN}[，,]\s*(?P<person>{_PERSON}?){_ASK}')
_PERSON_FIRST = re.compile(rf'(?P<person>{_PERSON}?)(?:刚)?在(?P<trigger>{_TRIGGER}){_WHEN}[，,]?\s*{_ASK}')
_TRIGGER_ACTION = re.compile(r'(?P<anchor>[\u3400-\u9fff]{2,16}?)(?P<action>传来捷报|传来胜讯|告捷|报捷|打起来|开打|交战|被围|起兵|举兵)')
_RECEIPT = re.compile(
    r'(?:驿书|军书|军报|捷报|战报|书报)(?:至|来|到|入)|'
    r'(?:得|接|览|闻)(?:军书|军报|捷报|战报)'
)
_UNREST = re.compile(r'(?:军|营)中[夜忽大]{0,2}(?:惊|乱|骚动)')
_VICTORY_REPORT = re.compile(r'(?:既破|已破|破之|大破|大捷|获胜)[^。！？；\n]{0,32}?(?:'+_RECEIPT.pattern+r')|报捷|献捷|告捷')
_CONFLICT = re.compile(r'战|攻|击|杀|拒')
_SIEGE = re.compile(r'围|城下|城北|城中')
_MUSTER = re.compile(r'起义兵|起兵|举义兵|举兵|募兵|率兵|发兵')
_SENTENCE = re.compile(r'[^。！？；\n]+[。！？；]?')
_CLAUSE = re.compile(r'[^，,。！？；\n]+')
_SIEGE_END = re.compile(r'解围|围解|(?:贼|寇|敌)退|(?:明年|翌月|其后)[，,]|\n\s*\n')
_DEFENCE = re.compile(r'留守|守城|守御|御敌|拒守|募兵|征兵|赴难|入卫|赴援|救援|救兵|救.{1,6}围')
_APPROACH = re.compile(r'(?:军|兵)[^，。！？；\n]{0,10}(?:且至|将至)(?:城下|城北|城南|城东|城西)')
_ARRIVAL = re.compile(r'(?:军|兵)[^，。！？；\n]{0,10}(?:已至|到|至)(?:城下|城北|城南|城东|城西)')


def parse(query):
    text = query.strip().rstrip('？?').rstrip()
    if match := _QUERY.fullmatch(text):
        trigger = match['trigger']
        if re.search(r'报[告书讯]|战报|军报|捷报|消息|来信', trigger):
            return dict(**match.groupdict(), kind='receipt')
        if re.search(r'(?:军|营)中.{0,6}(?:骚乱|惊乱|营啸|哗变)', trigger):
            return dict(**match.groupdict(), kind='unrest')
    match = _EVENT_FIRST.fullmatch(text) or _PERSON_FIRST.fullmatch(text)
    if match and (event := _TRIGGER_ACTION.fullmatch(match['trigger'])):
        action = event['action']
        if action in ('传来捷报', '传来胜讯', '告捷', '报捷'):
            kind = 'receipt'
        elif action in ('打起来', '开打', '交战'):
            kind = 'conflict'
        elif action == '被围':
            kind = 'siege'
        else:
            kind = 'muster'
        return dict(**match.groupdict(), kind=kind, anchor=event['anchor'], trigger_action=action)
    return reward_recall.parse(text)


def _annal_candidates(conn, identity, anchor, book):
    """Search anchored chunks in the exact editions that support the identity."""
    volumes = {(entry['book_id'], volume['page_id'], volume['revision_id'], volume['source_sha256'])
               for entry in identity if not book or entry['book_id'] == book
               for volume in (entry, *entry.get('continuations', ())) if volume.get('source_sha256')}
    for book_id, page_id, revision, checksum in sorted(volumes):
        for (payload,) in conn.execute('SELECT payload FROM chunks WHERE book_id=? AND page_id=? '
                                       'AND search_text LIKE ? ORDER BY chunk_id LIMIT 100',
                                       (book_id, page_id, '%'+S.normalize(anchor)+'%')):
            chunk = json.loads(payload)
            if chunk['revision_id'] == revision and chunk['source_sha256'] == checksum:
                yield chunk


def _siege_prose(chunk):
    original = chunk['text_orig']
    text = zh.to_simplified(original)
    return event_attachment.narrative(text) if len(text) == len(original) else ''


def _siege_setting(conn, chunk, anchor, prose):
    place = re.escape(anchor)
    # A place and a siege may be recorded in successive clauses of one entry.
    local = re.compile(rf'(?<!解)围(?:攻)?(?:于|於)?{place}城?(?=[，,。；！？]|$)|'
                       rf'(?:(?:至|幸|入|次|保){place})[，。][^；\n]{{0,28}}?围城')
    for m in local.finditer(prose):
        onset = m.start() + m[0].rfind('围')
        lead = re.split(r'[，,。；\n]', prose[max(0, onset - 16):onset])[-1]
        if not re.search(r'欲|未|不|谋|将|拟|请|议', lead):
            start = max(prose.rfind(mark, 0, m.start()) for mark in '，,。；\n') + 1
            return m.end(), {'围城记载': source_ref(chunk, start, m.end())}

    # Some editions split the account at interleaved annotations. Use the
    # preceding body location and subsequent actual arrival only within the
    # same edition/heading, with the approach explicit in the action fragment.
    if not (approach := _APPROACH.search(prose)):
        return None
    cid = chunk['chunk_id']
    before = None
    for (payload,) in conn.execute('SELECT payload FROM chunks WHERE chunk_id BETWEEN ? AND ? ORDER BY chunk_id',
                                   (cid - 4, cid + 4)):
        peer = json.loads(payload)
        if not all(peer.get(k) == chunk.get(k) for k in event_attachment.SOURCE_SCOPE):
            continue
        text = _siege_prose(peer)
        if peer['chunk_id'] < cid:
            if at := re.search(rf'(?:入|至|保|守){place}(?=[，,。；])', text):
                before = (peer, at)
            if _SIEGE_END.search(text[at.end():] if at else text):
                before = None
        elif peer['chunk_id'] > cid and before and (arrival := _ARRIVAL.search(text)):
            if _SIEGE_END.search(text[:arrival.start()]):
                break
            peer_before, at = before
            return approach.end(), {'围城地点': source_ref(peer_before, *at.span()),
                                    '兵临城下': source_ref(peer, *arrival.span())}
    return None


def siege_evidence(conn, chunk, activity, identity):
    """Require an anchored siege and the attested ruler's own defensive act."""
    if chunk['layer'] != 'body' or not identity or not chunk.get('parts'):
        return None
    if S.normalize(chunk['heading']) in ('校勘记', '论', '赞'):
        return None
    entry = next((e for e in identity if chunk['book_id'] == e['book_id'] and any(
        all(chunk.get(k) and chunk[k] == volume.get(k) for k in ('page_id', 'revision_id', 'source_sha256'))
        for volume in (e, *e.get('continuations', ()))
    )), None)
    if not entry:
        return None
    prose = _siege_prose(chunk)
    if not (setting := _siege_setting(conn, chunk, activity['anchor'], prose)):
        return None
    at, citations = setting
    tail = prose[at:at + 180]
    tail = _SIEGE_END.split(tail, maxsplit=1)[0]
    names = [*entry['aliases'], *entry.get('short_titles', ()), entry['aliases'][0][1:], '帝', '上']
    actor = '|'.join(map(re.escape, sorted(set(names), key=len, reverse=True)))
    explicit = re.compile(rf'(?:{actor})(?:乃|遂|即|复|亲)?(?:下诏|诏|命|遣|令|使|发|召).+')
    for clause in _CLAUSE.finditer(tail):
        text = clause[0].lstrip()
        match = explicit.fullmatch(text)
        # An edict without a written subject is attributable only at the
        # start of a dated entry, or a sentence in that same pinned annal.
        start = at + clause.start() + len(clause[0]) - len(text)
        previous = prose[:start].rstrip().rstrip('，,')
        prefix = re.split(r'[。！？；\n]', previous)[-1].strip()
        implicit = (text.startswith(('诏', '下诏')) and annal_events.DATE_PREFIX.fullmatch(prefix)
                    and bool(previous) and (prefix or previous.endswith(('。', '！', '？'))))
        if not _DEFENCE.search(text) or re.search(r'^(?:诏书|上书|帝欲|上欲)', text):
            continue
        if match or implicit:
            if implicit and prefix:
                start = previous.rfind(prefix)
            end = at + clause.end()
            if end >= len(prose) or prose[end] not in '，,。；！？':
                continue
            citations['当时行动'] = source_ref(chunk, start, end)
            return dict(person=activity['person'], citations=citations,
                        identity={key: entry[key] for key in ('quote', 'source_url') if key in entry})
    return None


def candidates(conn, frame, subject, identity, book=None):
    """Find body candidates that have both a person basis and a trigger clue."""
    person = frame['person']
    anchor = frame['anchor']
    kind = frame['kind']
    trigger = {'receipt': _VICTORY_REPORT, 'conflict': _CONFLICT, 'muster': _MUSTER, 'siege': _SIEGE}[kind]
    titles = {entry['aliases'][0] for entry in identity}
    scoped_titles = {title[1:]: R.DYNASTY_BOOKS.get(title[0], ()) for title in titles}
    pool = {hit['chunk']['chunk_id']: hit['chunk'] for hit in subject}
    for hit in S.lexical(conn, anchor, book, page_size=100)['hits']:
        chunk = hit['chunk']
        pool.setdefault(chunk['chunk_id'], chunk)
    scoped_ids = set()
    if kind == 'receipt' or any(zh.to_simplified(c['heading']).strip() == person for c in pool.values()):
        for peers in named_biographies(conn, person, subject, book):
            scoped_ids.update(peers)
            pool.update(peers)

    annal_ids = set()
    if kind == 'siege':
        for chunk in _annal_candidates(conn, identity, anchor, book):
            annal_ids.add(chunk['chunk_id'])
            pool[chunk['chunk_id']] = chunk
    direct_forms = {person, *titles}
    if kind == 'siege' and any(person in entry.get('short_titles', ()) for entry in identity):
        direct_forms.discard(person)
        direct_forms.update(entry['aliases'][1] for entry in identity)
    forward = 100 if kind == 'siege' else 80

    ranked = []
    for cid, chunk in pool.items():
        if chunk['layer'] != 'body' or S.normalize(chunk['heading']) == '校勘记':
            continue
        text = zh.to_simplified(chunk['text_orig'])
        prose = event_attachment.narrative(text)
        direct_name = any(form in prose for form in direct_forms)
        if not (direct_name or cid in scoped_ids or cid in annal_ids or any(
            S.normalize(chunk['book_title']) in books and re.search(r'(?:^|[，。；\n]|是时|时)'+re.escape(title), prose)
            for title, books in scoped_titles.items()
        )):
            continue
        # A place named in a speech may locate a siege in the annal. The siege
        # clue must still come from narration, and this does not confirm a role.
        anchor_text = text if cid in annal_ids else prose
        anchored = any(trigger.search(prose[max(0, start.start()-80):start.end()+forward])
                       for start in re.finditer(re.escape(anchor), anchor_text))
        # Relax the literal event anchor only within a pinned biography, and
        # only when a victory dispatch and the person's activity share a span.
        scoped_report = (kind == 'receipt' and cid in scoped_ids and trigger.search(prose)
                         and focus(chunk, frame))
        if anchored or scoped_report:
            ranked.append(((not anchored, cid not in annal_ids, not direct_name, cid), dict(chunk=chunk)))
    ranked.sort(key=lambda item: item[0])
    return [hit for _, hit in ranked]


def focus(chunk, frame):
    """Keep complete clauses and original offsets for the reranker's input."""
    trigger = {'receipt': _RECEIPT, 'unrest': _UNREST}.get(frame['kind'])
    if trigger is None or chunk['layer'] != 'body':
        return None
    original = chunk['text_orig']
    text = zh.to_simplified(original)
    if len(text) != len(original):
        return None
    narration = event_attachment.narrative(text)
    person = frame['person']
    forms = [person]
    heading = zh.to_simplified(chunk.get('heading', '')).split()
    short = person[1:]
    # A biography heading can suggest a local short form for ranking.
    if len(person) <= 3 and heading and heading[-1] in (person, short):
        forms.append(short)
    names = '|'.join(map(re.escape, forms))
    action = re.compile(rf'(?:^|[，,。])\s*(?:{names})(?=[\u3400-\u9fff])')
    sentences = list(_SENTENCE.finditer(narration))
    for i, sentence in enumerate(sentences):
        if 'anchor' in frame and not _VICTORY_REPORT.search(sentence.group()):
            continue
        for cue in trigger.finditer(sentence.group()):
            after = sentence.start() + cue.end()
            end = sentence.end()
            if not action.search(narration[after:end]):
                if i + 1 == len(sentences) or sentences[i + 1].start() != end:
                    continue
                end = sentences[i + 1].end()
                if not action.search(narration[after:min(end, after+48)]):
                    continue
            start = sentence.start()
            return dict(start=start, end=end, text=original[start:end],
                        selected=True, activity_context=True)
    return None
