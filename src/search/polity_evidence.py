"""Source-linked adjudication for reviewed military expeditions."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re

from search import colloquial_advocacy, event_attachment, evidence_context, punctuated, zh
from search.speech_turns import extract, match, quotes


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name('polity_sources.json').read_text(encoding='utf-8'))


def ranges(chunk):
    return [(part['block_index'], part['start'], part['end']) for part in chunk['parts']]


def covered(required, parts):
    block, start, end = required
    for item, lo, hi in sorted(parts):
        if item == block and lo <= start < hi:
            start = hi
            if start >= end:
                return True
    return False


_DIRECTION = re.compile(r'[东西南北][征伐讨]')
_YEAR = re.compile(r'(?P<era>[\u3400-\u9fff]{2,4}?)(?P<number>[元一二三四五六七八九十]{1,3})年')
_RAID = re.compile(
    r'(?:^|(?<=[。！？；\n]))\s*(?P<date>[\u3400-\u9fff]{3,7}年)[，,]\s*'
    r'(?:索?虏(?:主)?|敌(?:主)?|魏主)?(?P<opponent>[\u3400-\u9fff]{2,6}?)'
    r'(?:亲率众)?(?:[东西南北][侵进征伐]|来攻)[，,。]'
)
_WITHDRAWAL_REASON = re.compile(r'若[^。！？；\n]{2,24}[，,]则[^。！？；\n]{0,12}(?:散走|溃散|奔散)')
_OPPOSITION = re.compile(r'岂(?:有|可)[舍弃捨][^。！？；]{2,36}而(?:就|即)|(?:不可|未可)(?:弃城|退兵|遁走)')
_ADVANCE = re.compile(
    r'(?:^|(?<=[。，；\n]))\s*(?P<recipient>[\u3400-\u9fff]{2,5}?)(?:遂|欲|将|乃)?'
    r'(?:进逼|进攻|攻|伐|讨)[\u3400-\u9fff]{2,8}(?:[，,]求决胜负)?[。，；]\s*'
    r'(?P<intro>(?P<speaker>[\u3400-\u9fff]{2,5}?)(?:固|苦|进)?谏曰)[：:]\s*$'
)
_DELAY_REASON = re.compile(r'(?P<reason>[^。！？；\n]{4,70}?(?:非利|未可轻进|不可轻进))[。；]\s*不如[^。！？；\n]+')
_CAO_WEI_OPENING = re.compile(r'^\s*(?:太祖)?(?P<title>[\u3400-\u9fff])皇帝[^。]{0,20}?'
                             r'(?:姓(?P<surname>[\u3400-\u9fff])，)?讳(?P<name>[\u3400-\u9fff]{1,3})[，,]')
_CEREMONY_PROPOSAL = re.compile(r'(?:群臣|百官|公卿大臣)(?:上言|上表|并)?请(?:封禅|封[泰太]山)')
_CEREMONY_REASON = re.compile(
    r'[^。！？；\n]{2,32}则未可。(?:[^。！？；\n]{3,60}。)?[^。！？；\n]{4,36}何以能给[？?]'
)
_MESSAGES = {
    'supported': '发言、所议行动与对手身份均有原文依据。',
    'missing_event_opponent': '当前片段缺少能与这次劝谏相连的对手证据。',
    'missing_temporal_evidence': '缺少可核对的在位记载或同年战事依据。',
    'missing_reason': '这张卡缺少能归给同一发言人的理由。',
    'missing_ruler_identity': '原文点名了对手，其身份和所属政权尚待核实。',
    'different_roles': '原文中发言人和受议者的归属与问句不符。',
    'different_polity': '史料所载对手所属政权与问句不符。',
}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def source_ref(window, start, end):
    """Project an exact span back onto original edition block coordinates."""
    quote_text = window['text_orig'][start:end]
    if not quote_text or not 0 <= start < end <= len(window['text_orig']):
        raise ValueError('Empty or invalid source span')
    parts = []
    base = 0
    for block, lo, hi in ranges(window):
        overlap_start = max(0, start - base)
        overlap_end = min(hi - lo, end - base)
        if overlap_start < overlap_end:
            parts.append([block, lo + overlap_start, lo + overlap_end])
        base += hi - lo
    return {key: window[key] for key in (*evidence_context.IDENTITY, 'title', 'source_url', 'heading')} | {
        'ranges': parts, 'quote': quote_text,
    }


class Sources:
    def __init__(self, index, library, catalog):
        self.index = index
        self.library = library
        self.catalog = catalog
        self.pages = {}
        self.references = {}

    def chunk(self, cid):
        row = self.index.execute('SELECT payload FROM chunks WHERE chunk_id=?', (cid,)).fetchone()
        if row is None:
            raise ValueError(f'Pinned index chunk missing: {cid}')
        return json.loads(row[0])

    def page(self, pid):
        if pid not in self.pages:
            row = self.library.execute('SELECT * FROM chapters WHERE page_id=?', (pid,)).fetchone()
            if row is None:
                raise ValueError('Source page missing')
            self.pages[pid] = dict(row), json.loads(row['blocks_json'])
        return self.pages[pid]

    def check(self, ref):
        page, blocks = self.page(ref['page_id'])
        if any(page[key] != ref[key] for key in evidence_context.IDENTITY):
            raise ValueError('Source record refers to a different edition')
        parts = ref['ranges']
        if not parts or any(not (0 <= block < len(blocks) and 0 <= lo < hi <= len(blocks[block]['text']))
                            for block, lo, hi in parts):
            raise ValueError('Invalid original text range')
        restored = ''.join(blocks[block]['text'][lo:hi] for block, lo, hi in parts)
        if restored != ref['quote']:
            raise ValueError('Citation differs from original text')
        return ref

    def pinned(self, saved):
        key = tuple(saved)
        if key in self.references:
            return self.references[key]
        cid, revision, checksum, start, end, text_checksum = saved
        ref = source_ref(self.chunk(cid), start, end)
        if revision != ref['revision_id'] or checksum != ref['source_sha256'] or digest(ref['quote']) != text_checksum:
            raise ValueError(f'Pinned source changed: {cid}')
        self.references[key] = self.check(ref)
        return ref


def inside(container, passage):
    return (all(container[key] == passage[key] for key in evidence_context.IDENTITY)
            and all(covered(span, ranges(container)) for span in passage['ranges']))


def overlaps(left, right):
    return any(a == b and lo < end and start < hi
               for a, lo, hi in left for b, start, end in right)


def result(status, citations=(), *, message=None, **fields):
    return dict(status=status, message=message or _MESSAGES[status], citations=dict(citations), **fields)


def decide(sources, frame, chunk):
    try:
        context = evidence_context.assemble(sources.library, chunk, max_chars=1200)
    except ValueError:
        return result('missing_event_opponent')
    window = evidence_context.rule_view(chunk, context)
    for record in sources.catalog:
        try:
            event = {kind: sources.pinned(ref) for kind, ref in record['event'].items()}
        except ValueError:
            continue
        if not {'opponent', 'connection'}.issubset(event) or not all(inside(window, ref) for ref in event.values()):
            continue
        event_citations = [('出兵对手', event['opponent']), ('同次战事', event['connection'])]
        if 'date' in event:
            event_citations.append(('传记纪年', event['date']))
        if context['boundary_cut'] or context['unbalanced_quote']:
            return result('missing_reason', event_citations)
        names = {}
        role_sources = {}
        try:
            for alias, (name, ref) in record['identities'].items():
                role_sources[alias] = sources.pinned(ref)
                names[alias] = name
        except ValueError:
            return result('missing_reason', event_citations)
        directions = {word: word for word in _DIRECTION.findall(zh.to_simplified(window['text_orig']))}
        eligible = []
        for turn in extract(window, names, directions):
            speech = source_ref(window, turn['turn']['start'], turn['turn']['end'])
            if not overlaps(ranges(chunk), speech['ranges']):
                continue
            action = source_ref(window, turn['action']['start'], turn['action']['end'])
            if turn['target'] == record['ruler'] and overlaps(action['ranges'], event['opponent']['ranges']):
                eligible.append(turn)
            elif turn['target'] in directions and any(
                    zh.to_simplified(event['connection']['quote']).startswith('及' + turn['target'])
                    and k == b and end <= lo for k, _, end in action['ranges']
                    for b, lo, _ in event['connection']['ranges']):
                eligible.append(turn)
        if len(eligible) != 1:
            return result('missing_reason', event_citations)
        turn, = eligible
        if (turn['person'], turn['addressee']) != (frame['person'], frame['recipient']):
            return result('different_roles', event_citations)
        authority_labels = {
            'identity': '君主身份',
            'took_office': '即位记载',
            'reign_ended': '在位终点',
            'governance': '事发时在位',
            'political_name': '政权名称依据',
            'dated_event': '战事纪年',
            'date_synchronism': '同年战事互证',
        }
        try:
            term = record['reign']
            year = int(record['year'])
            if not term['start'] < year < term['end']:
                return result('missing_temporal_evidence', event_citations)
            authority = [(label, sources.pinned(record['authority'][kind]))
                         for kind, label in authority_labels.items()]
            if 'accession_year' in record['authority']:
                authority.append(('即位纪年', sources.pinned(record['authority']['accession_year'])))
        except (KeyError, ValueError):
            return result('missing_temporal_evidence', event_citations)
        citations = event_citations + authority
        if frame['target'] != record['polity']:
            return result('different_polity', citations, actual_polity=record['polity'])
        attachment = match(dict(colloquial_advocacy=frame), [dict(turn, target=record['polity'])])
        if not attachment:
            return result('missing_reason', event_citations)
        for label, role in [('劝谏理由', 'detail'), ('劝谏人', 'speaker'), ('所劝对象', 'recipient')]:
            ref = attachment[role]
            citations.append((label, sources.check(source_ref(window, ref['start'], ref['end']))))
            if role != 'detail':
                citations.append((label + '称谓依据', role_sources[zh.to_simplified(ref['quote'])]))
        return result('supported', citations, polity=record['polity'], ruler=record['ruler'], reign=term,
                      year=year, era=record['recorded_year'], parallel_era=record['comparison_year'])
    return result('missing_event_opponent')


def era_number(text):
    digits = '〇一二三四五六七八九'
    if text == '元':
        return 1
    if '十' not in text:
        return digits.index(text) if len(text) == 1 else None
    tens, _, units = text.partition('十')
    if len(tens) > 1 or len(units) > 1 or '十' in units:
        return None
    return (digits.index(tens) if tens else 1) * 10 + (digits.index(units) if units else 0)


def verify_ruler(sources, name, date, book_id):
    """Reuse only the general identity and term sources for a new engagement."""
    matching = [row for row in sources.catalog if name in (row['ruler'], *row.get('name_forms', ()))]
    if len(matching) != 1:
        return 'missing_ruler_identity', {}, []
    record, = matching
    observed = _YEAR.fullmatch(date)
    anchor = _YEAR.fullmatch(record['recorded_year'])
    try:
        anchor_ref = sources.pinned(record['authority']['dated_event'])
        if not observed or not anchor or observed['era'] != anchor['era'] or book_id != anchor_ref['book_id']:
            raise ValueError('Era is outside the dated source scope')
        observed_number, anchor_number = era_number(observed['number']), era_number(anchor['number'])
        year = int(record['year']) + observed_number - anchor_number
        term = record['reign']
        if not term['start'] < year < term['end']:
            raise ValueError('Year is outside the ruler term')
        refs = [(label, sources.pinned(record['authority'][key])) for key, label in (
            ('identity', '君主身份'), ('took_office', '即位记载'), ('reign_ended', '在位终点'),
            ('political_name', '政权名称依据'))]
        if 'accession_year' in record['authority']:
            refs.append(('即位纪年', sources.pinned(record['authority']['accession_year'])))
    except (KeyError, TypeError, ValueError):
        return 'missing_temporal_evidence', {}, []
    return 'supported', dict(polity=record['polity'], ruler=record['ruler'], reign=term, year=year, era=date), refs


def defence(sources, frame, chunk):
    """Bind a complete speech to the named commander and same local defence."""
    try:
        context = evidence_context.assemble(sources.library, chunk, max_chars=1200)
    except ValueError:
        return None
    if context['boundary_cut']:
        return None
    window = evidence_context.rule_view(chunk, context)
    text = zh.to_simplified(window['text_orig'])
    if len(text) != len(window['text_orig']):
        return None
    prose = event_attachment.narrative(text)
    recipient = frame['recipient']
    actor = (rf'(?:{re.escape(recipient)}|{re.escape(recipient[-2:])})'
             if recipient else r'[\u3400-\u9fff]{2,5}?')
    place = re.escape(frame['place']) if frame['place'] else r'[\u3400-\u9fff]{2,8}?'
    movement = r'(?:[东西南北]归)?' if frame['place'] else r'(?:[东西南北]归|而退|而走)'
    retreat = re.compile(rf'(?:^|(?<=[。，；\n]))\s*(?P<actor>{actor})'
                         rf'欲(?:弃|捨|舍)(?P<place>{place}){movement}(?=[。，；])')
    speaker = re.escape(frame['person'])
    spoken_by = re.compile(rf'(?:^|(?<=[。，；\n]))\s*'
                           rf'(?P<intro>(?:{speaker}|{re.escape(frame["person"][-1])})(?:固谏|谏)?曰)[：:]\s*$')
    _, blocks = sources.page(chunk['page_id'])
    start = chunk['parts'][0]['block_index']
    heading = next((i for i in range(start, -1, -1) if blocks[i]['kind'] == 'heading'), None)
    speaker_basis = []
    if heading is not None and zh.to_simplified(blocks[heading]['text']).strip() == frame['person']:
        heading_text = blocks[heading]['text']
        heading_window = dict(window, text_orig=heading_text, parts=[
            dict(block_index=heading, start=0, end=len(heading_text))])
        speaker_basis = [('劝谏人称谓依据', source_ref(heading_window, 0, len(heading_text)))]
    # A parallel account can introduce a short name by office and identify
    # the very same speaker by surname and office immediately after the turn.
    indirect = (not speaker_basis and not frame['target'] and not frame.get('comparison')
                and len(frame['person']) == 2)
    if not speaker_basis and not indirect:
        return None
    for proposal in retreat.finditer(prose):
        action = source_ref(window, proposal.start('actor'), proposal.end())
        before = prose[:proposal.start()]
        # When the question omits the addressee, resolve the short subject through
        # an earlier title and name in the same account of the defence.
        recipient_name = recipient
        if not recipient_name:
            titled = re.search(rf'([\u3400-\u9fff]{{2}}王{re.escape(proposal["actor"])})'
                               r'(?=统|总|镇|领|督)', before[-150:])
            recipient_name = titled[1] if titled else None
        if not recipient_name:
            continue
        recipient_at = before.rfind(recipient_name, max(0, len(before) - 150))
        if recipient_at < 0:
            continue
        for begin, end in complete_speeches(text):
            if not proposal.end() <= begin <= proposal.end() + 210:
                continue
            if text[proposal.end():begin] != prose[proposal.end():begin]:
                continue
            introduction = spoken_by.search(prose, proposal.end(), begin)
            if not introduction or not re.search(r'(?:集|召)[群羣](?:僚|臣).{0,6}(?:谋|议)', prose[proposal.end():begin]):
                continue
            speech = source_ref(window, begin, end + 1)
            if not overlaps(ranges(chunk), action['ranges'] + speech['ranges']):
                continue
            body = event_attachment.narrative(text[begin + 1:end])
            reason = _WITHDRAWAL_REASON.search(body)
            if not reason or not _OPPOSITION.search(body):
                continue
            bases = speaker_basis
            if indirect:
                surname, short = map(re.escape, frame['person'])
                offices = list(re.finditer(rf'(?<=[。，；\n]){short}为(?:[\u3400-\u9fff]{{0,4}})长史', before[-180:]))
                reply = re.match(rf'\s*[\u3400-\u9fff]{{2,4}}闻{short}议，谓{proposal["actor"]}曰[：:]'
                                 rf'\s*「{surname}长史言', text[end + 1:])
                if len(offices) != 1 or not reply:
                    continue
                office, = offices
                at = max(0, len(before) - 180)
                bases = [
                    ('劝谏人任职依据', source_ref(window, at + office.start(), at + office.end())),
                    ('劝谏人称谓依据', source_ref(window, end + 1 + reply.start(), end + 1 + reply.end())),
                ]
            citations = [
                ('劝谏人', source_ref(window, *introduction.span('intro'))),
                *bases,
                ('所劝对象', source_ref(window, recipient_at, recipient_at + len(recipient_name))),
                ('所议行动', action),
                ('劝谏理由', source_ref(window, begin + 1 + reason.start(), begin + 1 + reason.end())),
            ]
            sentence_start = max(prose.rfind(mark, 0, proposal.start()) for mark in '。！？；\n') + 1
            background = prose[sentence_start:proposal.start('actor')]
            if frame.get('comparison'):
                if not re.search(r'(?:军食|粮食|军粮)(?:不足|乏|少|尽)', background):
                    continue
                while text[sentence_start].isspace():
                    sentence_start += 1
                citations.append(('原文叙述的撤城背景', source_ref(window, sentence_start, proposal.end())))
            raids = [raid for raid in _RAID.finditer(before, max(0, len(before) - 180))
                     if proposal['place'] in before[raid.end():]
                     and not re.search(r'[元一二三四五六七八九十]+年', before[raid.end():])]
            if not frame['target']:
                fields = {}
                if len(raids) == 1:
                    raid, = raids
                    citations.append(('出兵对手', source_ref(window, raid.start('opponent'), raid.end())))
                    citations.append(('战事纪年', source_ref(window, *raid.span('date'))))
                    _, fields, refs = verify_ruler(sources, raid['opponent'], raid['date'], chunk['book_id'])
                    citations.extend(refs)
                message = f'{frame["person"]}劝{recipient_name}留守{proposal["place"]}。'
                if frame.get('comparison'):
                    message += '军粮不足是史书交代的撤城背景。下列劝谏理由出自留守一方的发言。'
                return result('supported', citations, message=message, **fields)
            if len(raids) != 1:
                return result('missing_event_opponent', citations)
            raid, = raids
            citations.extend([
                ('出兵对手', source_ref(window, raid.start('opponent'), raid.end())),
                ('战事纪年', source_ref(window, *raid.span('date'))),
            ])
            verdict, fields, refs = verify_ruler(sources, raid['opponent'], raid['date'], chunk['book_id'])
            citations.extend(refs)
            if verdict == 'supported' and frame['target'] != fields['polity']:
                return result('different_polity', citations, actual_polity=fields['polity'])
            return result(verdict, citations, **fields)
    return None


def complete_speeches(text):
    # Retain a complete earlier turn when an unrelated later turn ends at a source boundary.
    try:
        yield from quotes(text)
    except ValueError:
        return


def delayed_attack(sources, frame, chunk):
    try:
        context = evidence_context.assemble(sources.library, chunk, max_chars=1200)
    except ValueError:
        return None
    if context['boundary_cut']:
        return None
    window = evidence_context.rule_view(chunk, context)
    text = zh.to_simplified(window['text_orig'])
    if len(text) != len(window['text_orig']):
        return None
    prose = event_attachment.narrative(text)
    person, recipient, target = (frame[key] for key in ('person', 'recipient', 'target'))
    for begin, end in complete_speeches(text):
        debate = _ADVANCE.search(prose, max(0, begin - 85), begin)
        if not debate or debate['speaker'] not in (person, recipient):
            continue
        addressee = recipient if debate['speaker'] == person else person
        short = debate['recipient']
        if short == addressee:
            identity = None
        elif len(addressee) == len(short) + 1 and addressee.endswith(short):
            # Source ties this abbreviated name to a father of the cited surname.
            identity = re.search(rf'{re.escape(addressee[0])}[\u3400-\u9fff]{{2}}'
                                 rf'[^。！？；\n]{{0,20}}[，,]其(?:世子|子){re.escape(short)}',
                                 prose[:debate.start()])
            if not identity:
                continue
        else:
            continue
        response = re.match(rf'\s*{re.escape(short)}(?:不从|不纳|弗从)(?=[。，；])', prose[end + 1:])
        body = event_attachment.narrative(text[begin + 1:end])
        reason = _DELAY_REASON.match(body.lstrip())
        if not response or not reason or (target and not reason['reason'].startswith(target)):
            continue
        reason_start = begin + 1 + len(body) - len(body.lstrip())
        turn = source_ref(window, debate.start('recipient'), end + 1)
        if not overlaps(ranges(chunk), turn['ranges']):
            continue
        citations = [
            ('劝谏人', source_ref(window, *debate.span('intro'))),
            ('所议行动', source_ref(window, debate.start('recipient'), debate.start('intro'))),
            ('受议者回应', source_ref(window, end + 1, end + 1 + response.end())),
            ('劝谏理由', source_ref(window, reason_start, reason_start + reason.end('reason'))),
        ]
        if identity:
            citations.append(('所劝对象称谓依据', source_ref(window, *identity.span())))
        statement = f'原文记载{debate["speaker"]}劝{addressee}暂缓进兵。'
        if debate['speaker'] != person:
            return result('different_roles', citations, message=statement + '问句中的发言人与受议者对调了。')
        return result('supported', citations, message=statement)
    return None


def checked_window(sources, chunk):
    try:
        context = evidence_context.assemble(sources.library, chunk, max_chars=1200)
    except ValueError:
        return None
    if context['boundary_cut'] or context['unbalanced_quote']:
        return None
    window = evidence_context.rule_view(chunk, context)
    text = zh.to_simplified(window['text_orig'])
    if len(text) == len(window['text_orig']):
        return window, text, event_attachment.narrative(text)
    return None


def wei_ruler_name(sources, book_id, title):
    """Resolve the short imperial title against the founder and the same book's annals."""
    if book_id != 'KR2a0012':
        return None
    annals = {}
    surname = None
    for row in sources.library.execute(
        "SELECT page_id, sort_number FROM chapters WHERE book_id=? AND sort_number BETWEEN 1 AND 3",
        (book_id,),
    ):
        page, blocks = sources.page(row['page_id'])
        if not blocks or blocks[0]['kind'] != 'body':
            continue
        original = blocks[0]['text']
        text = zh.to_simplified(original)
        opening = _CAO_WEI_OPENING.match(text)
        if not opening or len(text) != len(original):
            continue
        window = dict(page, text_orig=original, heading='', parts=[
            dict(block_index=0, start=0, end=len(original))])
        sentence_end = text.find('。', opening.end())
        reference = source_ref(window, len(text) - len(text.lstrip()),
                               sentence_end + 1 if sentence_end >= 0 else opening.end())
        if row['sort_number'] == 1:
            surname = opening['surname'], reference
        key = opening['title'] + '帝'
        if key == title:
            annals[row['page_id']] = opening['name'], reference
    if not surname or not surname[0] or len(annals) != 1:
        return None
    name, reference = next(iter(annals.values()))
    return surname[0] + name, [('君主姓氏依据', surname[1]), ('所劝对象称谓依据', reference)]


def population_move(sources, frame, chunk):
    checked = checked_window(sources, chunk)
    if not checked:
        return None
    window, text, prose = checked
    heading = zh.to_simplified(chunk['heading']).strip()
    if heading not in (frame['person'], frame['recipient']):
        return None
    other = frame['recipient'] if heading == frame['person'] else frame['person']
    speaker = rf'(?:{re.escape(heading)}|{re.escape(heading[-1])})'
    proposal_pattern = re.compile(
        rf'(?:^|(?<=[。！？；\n]))\s*(?P<action>(?P<proposer>帝|{re.escape(other)})(?:欲|将)'
        + colloquial_advocacy._topic(frame) + r'[。，；])')
    introduction = re.compile(rf'(?:^|(?<=[。；」]))\s*(?P<speaker>{speaker}(?:谏|对)?曰)[：:]\s*$')
    reason_pattern = re.compile(r'(?:今|若)(?:徙|迁)[，,](?=[^。！？]{4,45}(?:民心|无以食))[^。！？]+[。！？]')
    reign = re.compile(r'(?P<title>[\u3400-\u9fff]帝)(?:践阼|即位|登极)')
    turns = list(complete_speeches(text))
    for proposal in proposal_pattern.finditer(prose):
        titles = list(reign.finditer(prose, max(0, proposal.start() - 240), proposal.start()))
        recipient_span = proposal.span('proposer')
        title_sources = []
        if proposal['proposer'] == '帝':
            if not titles:
                continue
            last_title = titles[-1]
            match = wei_ruler_name(sources, chunk['book_id'], last_title['title'])
            if not match or match[0] != other:
                continue
            recipient_span = last_title.span('title')
            title_sources = match[1]
        after = min(len(text), proposal.end() + 330)
        opening = None
        for begin, end in turns:
            if not proposal.end() < begin < after:
                continue
            intro = introduction.search(text, proposal.end(), begin)
            if not intro or intro['speaker'] != prose[slice(*intro.span('speaker'))]:
                continue
            body = event_attachment.narrative(text[begin + 1:end])
            if opening is None and re.match(r'陛下欲(?:徙|迁)', body):
                opening = intro, begin, end
            reason = reason_pattern.fullmatch(body)
            if not opening or not reason or not re.match(
                rf'\s*{re.escape(proposal["proposer"])}遂(?:徙|迁)', prose[end + 1:]):
                continue
            if re.search(r'(?:欲|将)(?:徙|迁)|(?:即位|践阼|薨|崩)|\n\s*\n',
                         prose[proposal.end():begin]):
                continue
            turn = source_ref(window, proposal.start(), end + 1)
            if not overlaps(ranges(chunk), turn['ranges']):
                continue
            first, _, first_end = opening
            citations = [
                ('劝谏人', source_ref(window, first.start('speaker'), first_end + 1)),
                ('所议行动', source_ref(window, *proposal.span('action'))),
                ('所劝对象', source_ref(window, *recipient_span)),
                ('劝谏理由', source_ref(window, intro.start('speaker'), end + 1)),
                *title_sources,
            ]
            message = f'原文记载{other}提出迁户，{heading}向其劝阻。'
            if heading != frame['person']:
                return result('different_roles', citations, message=message + '问句中的人物角色对调了。')
            return result('supported', citations, message=message)
    return None


def ceremony(sources, frame, chunk):
    checked = checked_window(sources, chunk)
    if not checked:
        return None
    window, text, prose = checked
    name = frame['person']
    # Historical editions sometimes use the interchangeable final name graph.
    short = '[征徵]' if name[-1] in '征徵' else re.escape(name[-1])
    speaker = re.escape(name[:-1]) + short
    intro_pattern = re.compile(rf'(?P<speaker>{speaker})(?:对|谏)?曰[：:]\s*$')
    for begin, end in complete_speeches(text):
        intro = intro_pattern.search(prose, max(0, begin - 14), begin)
        if not intro:
            continue
        body = event_attachment.narrative(text[begin + 1:end])
        reason = _CEREMONY_REASON.search(body)
        reply = re.match(rf'\s*(?P<ruler>[\u3400-\u9fff]{{2,4}}?)(?:深)?(?:嘉|善|纳)'
                         rf'(?:{speaker}|{short})言', prose[end + 1:])
        previous = prose[max(0, intro.start() - 460):intro.start()]
        proposals = list(_CEREMONY_PROPOSAL.finditer(previous))
        if not reason or not reply or len(proposals) != 1:
            continue
        proposal, = proposals
        proposal_start = max(0, intro.start() - 460) + proposal.start()
        proposal_end = proposal_start + len(proposal[0])
        if not re.match(rf'[。，；]\s*{re.escape(reply["ruler"])}曰[：:]', prose[proposal_end:]):
            continue
        if frame['place'] not in ('泰山', '太山') or not re.search(r'封[泰太]山', proposal[0]):
            continue
        if frame['recipient'] and reply['ruler'] not in colloquial_advocacy._recipient_forms(chunk, frame['recipient']):
            continue
        turn = source_ref(window, proposal_start, end + 1)
        if not overlaps(ranges(chunk), turn['ranges']):
            continue
        return result('supported', [
            ('劝谏人', source_ref(window, *intro.span('speaker'))),
            ('所议行动', source_ref(window, proposal_start, proposal_end)),
            ('所劝对象', source_ref(window, end + 1 + reply.start('ruler'), end + 1 + reply.end('ruler'))),
            ('劝谏理由', source_ref(window, begin + 1 + reason.start(), begin + 1 + reason.end())),
        ], message=f'群臣请封泰山，{name}向{reply["ruler"]}说明不宜此时举行。')
    return None


def review_natural_hits(index, frame, hits):
    if not frame:
        return
    kind = frame['kind']
    if kind == 'oppose_withdrawal' and not frame['target']:
        candidates = [
            hit for hit in hits if hit['chunk']['layer'] == 'body'
            and (zh.to_simplified(hit['chunk']['heading']).strip() == frame['person']
                 or (frame['place'] and any(frame['place'] in zh.to_simplified(part['text_orig'])
                     and '欲弃' in zh.to_simplified(part['text_orig'])
                     for part in [hit['chunk'], *hit['context_chunks']])))
        ]
        reader = defence
    elif kind == 'delay_attack':
        candidates = [
            hit for hit in hits if hit['chunk']['layer'] == 'body'
            and any((not frame['target'] or frame['target'] in zh.to_simplified(part['text_orig']))
                    and '谏曰' in zh.to_simplified(part['text_orig'])
                    for part in [hit['chunk'], *hit['context_chunks']])
        ]
        reader = delayed_attack
    elif kind == 'oppose_population_move':
        candidates = [hit for hit in hits if hit['chunk']['layer'] == 'body'
                      and zh.to_simplified(hit['chunk']['heading']).strip() in (frame['person'], frame['recipient'])]
        reader = population_move
    elif kind == 'oppose_ceremony':
        candidates = [hit for hit in hits if hit['chunk']['layer'] == 'body'
                      and any(_CEREMONY_PROPOSAL.search(zh.to_simplified(part['text_orig']))
                              for part in [hit['chunk'], *hit['context_chunks']])]
        reader = ceremony
    else:
        return
    if candidates:
        with punctuated.connect() as library:
            sources = Sources(index, library, catalog() if reader is defence else [])
            for hit in candidates:
                if evidence := reader(sources, frame, hit['chunk']):
                    hit['advice_evidence'] = evidence


def direction_without_opponent(sources, frame, chunk):
    try:
        context = evidence_context.assemble(sources.library, chunk, max_chars=1200)
    except ValueError:
        return None
    if context['boundary_cut'] or context['unbalanced_quote']:
        return None
    window = evidence_context.rule_view(chunk, context)
    text = zh.to_simplified(window['text_orig'])
    person, recipient = frame['person'], frame['recipient']
    names = {person: person, person[-1]: person, recipient: recipient}
    directions = {word: word for word in _DIRECTION.findall(text)}
    for turn in extract(window, names, directions):
        if (turn['person'], turn['addressee']) != (person, recipient) or turn['target'] not in directions:
            continue
        ref = source_ref(window, turn['turn']['start'], turn['turn']['end'])
        if not overlaps(ranges(chunk), ref['ranges']):
            continue
        tail = event_attachment.narrative(text[turn['turn']['end']:])
        if re.search(r'(?:虏|敌|贼)主', tail) or frame['target'] in tail:
            continue
        action, reason = turn['action'], turn['reasons'][0]
        return result('missing_event_opponent', [
            ('所议行动', source_ref(window, action['start'], action['end'])),
            ('劝谏理由', source_ref(window, reason['start'], reason['end'])),
        ])
    return None


def review_hits(index, frame, hits):
    """Attach verified sources to displayed cards after relevance ranking."""
    if not frame or not frame['target']:
        return
    if frame['kind'] == 'oppose_withdrawal':
        candidates = [hit for hit in hits if hit['chunk']['layer'] == 'body'
                      and zh.to_simplified(hit['chunk']['heading']).strip() == frame['person']]
        if candidates:
            with punctuated.connect() as library:
                sources = Sources(index, library, catalog())
                for hit in candidates:
                    if evidence := defence(sources, frame, hit['chunk']):
                        hit['polity_evidence'] = evidence
        return
    if frame['kind'] != 'dissuade_attack':
        return
    for record in catalog():
        participants = {name for name, _ in record['identities'].values()}
        if {frame['person'], frame['recipient']} != participants:
            continue
        cid, revision, checksum, *_ = record['event']['opponent']
        same_edition = [hit for hit in hits if hit['chunk']['layer'] == 'body'
                        and (hit['chunk']['revision_id'], hit['chunk']['source_sha256']) == (revision, checksum)]
        if not same_edition:
            continue
        with punctuated.connect() as library:
            sources = Sources(index, library, [record])
            try:
                episode = sources.chunk(cid)
            except ValueError:
                continue
            for hit in same_edition:
                chunk = hit['chunk']
                if chunk['heading'] == episode['heading'] and chunk['page_id'] == episode['page_id']:
                    hit['polity_evidence'] = decide(sources, frame, chunk)
    direction_cards = [
        hit for hit in hits
        if 'polity_evidence' not in hit and hit['chunk']['layer'] == 'body'
        and zh.to_simplified(hit['chunk']['heading']).strip() == frame['person']
        and any(_DIRECTION.search(part['text_orig']) for part in [hit['chunk'], *hit.get('context_chunks', ())])
    ]
    if direction_cards:
        with punctuated.connect() as library:
            sources = Sources(index, library, catalog())
            for hit in direction_cards:
                if evidence := direction_without_opponent(sources, frame, hit['chunk']):
                    hit['polity_evidence'] = evidence
