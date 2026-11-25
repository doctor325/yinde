"""Offline cross-chapter verification of reactions to a reported victory."""
from dataclasses import dataclass
import re

from search import activity_context, biography_lookup, evidence_context, event_attachment, zh
from search import punctuated_search as search
from search.polity_evidence import inside, source_ref


_HAN = r'[\u3400-\u9fff]'
_START = r'(?:^|[，,。；！？\n])[ \t]*'
_END = r'(?=[，,。；！？：\n]|$)'
_NUMBER = r'[元一二三四五六七八九十]+'
_DAY = r'[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]'
_YEAR = rf'(?:(?P<era>{_HAN}{{2}}))?(?P<year>{_NUMBER})年'
_YEAR_HEADING = re.compile(_YEAR)
_MONTH = rf'闰?(?:正|冬|腊|{_NUMBER})月'
_DATE = re.compile(rf'{_START}(?:{_YEAR}(?:[春夏秋冬]?(?P<month>{_MONTH}))?'
                   rf'|(?:其年)?[春夏秋冬]?(?P<relative_month>{_MONTH}))'
                   rf'(?:{_DAY}(?:朔)?)?{_END}')
_REACTION = (r'(?P<action>(?:大悦|大喜|喜甚)[，,](?:谓[^，。；！？：\n]{1,8}曰|诏[^，。；！？：\n]{1,16}))')
_APPROACH = re.compile(rf'{_START}(?:师|军)(?:进)?至(?P<place>{_HAN}{{2,8}}){_END}')


@dataclass(frozen=True)
class Date:
    era: str | None
    year: str
    month: str | None
    sources: tuple


def _date_before(text, offset, heading=''):
    """Read the calendar state at the event, resetting the month on a new year."""
    heading_date = _YEAR_HEADING.fullmatch(heading.strip())
    era, year = heading_date.groups() if heading_date else (None, None)
    year_span = era_span = month_span = None
    month = None
    for marker in _DATE.finditer(text, 0, offset):
        if marker['year']:
            year, month = marker['year'], None
            year_span, month_span = (marker.start('year'), marker.end('year') + 1), None
            if marker['era']:
                era, era_span = marker['era'], (marker.start('era'), year_span[1])
        current_month = marker['month'] or marker['relative_month']
        if current_month:
            month = current_month.replace('正月', '一月').replace('冬月', '十一月').replace('腊月', '十二月')
            month_span = marker.span('month' if marker['month'] else 'relative_month')
    if era_span and year_span and era_span[1] == year_span[1]:
        year_span = None
    spans = tuple(span for span in (era_span, year_span, month_span) if span)
    return Date(era, year.replace('元', '一'), month, spans) if year else None


def _prose(view):
    original = view['text_orig']
    normalized = zh.to_simplified(original)
    return event_attachment.narrative(normalized) if len(original) == len(normalized) else ''


def _body_view(sources, chunk):
    if chunk['layer'] != 'body' or search.normalize(chunk['heading']) in ('校勘记', '史评', '赞'):
        return None
    context = evidence_context.assemble(sources.library, chunk, max_chars=1600)
    if context['boundary_cut'] or context['unbalanced_quote']:
        return None
    return evidence_context.rule_view(chunk, context)


def _block_quote(sources, page_id, block_id, start, end):
    page, blocks = sources.page(page_id)
    return sources.check({key: page[key] for key in (*evidence_context.IDENTITY, 'title', 'source_url')} |
                         {'heading': '', 'ranges': [[block_id, start, end]], 'quote': blocks[block_id]['text'][start:end]})


def _ruler_quote(sources, entry):
    page, blocks = sources.page(entry['page_id'])
    if any(page[key] != entry[key] for key in evidence_context.IDENTITY):
        return None
    for i, block in enumerate(blocks):
        if block['kind'] == 'body' and block['text'].lstrip().startswith(entry['quote']):
            start = len(block['text']) - len(block['text'].lstrip())
            return _block_quote(sources, entry['page_id'], i, start, start + len(entry['quote']))
    return None


def _victor(sources, view):
    name = zh.to_simplified(view['heading']).strip()
    if not re.fullmatch(rf'{_HAN}{{2,4}}', name):
        return None
    _, blocks = sources.page(view['page_id'])
    end = view['parts'][0]['block_index']
    for i in range(end, 0, -1):
        if blocks[i - 1]['kind'] == 'heading':
            text = zh.to_simplified(blocks[i]['text'])
            match = re.match(rf'\s*{re.escape(name)}[，,](?:字|本名){_HAN}{{1,8}}{_END}', text)
            if match and len(text) == len(blocks[i]['text']):
                return name, _block_quote(sources, view['page_id'], i, *match.span())
            return None
    return None


def _episode(sources, chunk, receiver, ruler_ref):
    title, full_name = receiver
    book = biography_lookup.HOUSES.get(search.normalize(chunk['book_title']))
    if chunk['layer'] != 'body' or not book or book[0] != title[0]:
        return None
    names = '|'.join(map(re.escape, (title, full_name, title[1:])))
    notice = re.compile(rf'{_START}(?P<recipient>{names})(?:初|既|始|乃)?闻'
                        rf'(?P<winner>{_HAN}{{1,4}}?)(?:又|已|大)?破(?P<loser>{_HAN}{{2,5}})'
                        rf'[，,]{_REACTION}{_END}')
    if not notice.search(zh.to_simplified(chunk['text_orig'])) or not (view := _body_view(sources, chunk)):
        return None
    if not (victor := _victor(sources, view)):
        return None
    winner, biography_ref = victor
    text = _prose(view)
    for receipt in notice.finditer(text):
        action_ref = source_ref(view, receipt.start('recipient'), receipt.end())
        if receipt['winner'] not in (winner, winner[-1]) or not inside(chunk, action_ref):
            continue
        loser = receipt['loser']
        # Require approach, the named opponent's retreat and the victor's act
        # within the same dated narrative, in that order before the reaction.
        flight = rf'{_START}{re.escape(loser)}(?:可汗|王)?[^，。；\n]{{0,10}}?(?:先走|远遁|奔走|退走){_END}'
        victory = rf'{_START}(?:{re.escape(winner)}|{re.escape(winner[-1])})(?:乃|遂)?(?:斩|俘|大破)'
        approaches = list(_APPROACH.finditer(text, max(0, receipt.start() - 250), receipt.start()))
        if len(approaches) != 1:
            continue
        approach = approaches[0]
        continuation = text[approach.end():receipt.start()]
        if '\n\n' in continuation or not (retreat := re.search(flight, continuation)):
            continue
        if not (combat := re.search(victory + rf'{_HAN}{{1,12}}{_END}', continuation[retreat.end():])):
            continue
        date = _date_before(text, approach.start(), zh.to_simplified(view['heading']))
        if not date or not date.era or not date.month or date != _date_before(text, receipt.start(), zh.to_simplified(view['heading'])):
            continue
        references = {'闻捷者身份': ruler_ref, '胜方身份': biography_ref, '闻捷与行动': action_ref,
                      '传记战场与对手': source_ref(view, approach.start(), approach.end()+retreat.end()+combat.end())}
        return (winner, loser, approach['place'], date, references,
                [source_ref(view, *span) for span in date.sources])
    return None


def _witnesses(sources, home, winner, loser, place, date):
    # The query uses participants and a place recovered from the action source.
    # A polity can stand for the opposing force when the same record names its
    # fleeing commander. A second account can corroborate the same dated fight.
    pattern = re.compile(rf'{_START}(?:(?P<day>{_DAY})[，,])?{re.escape(winner)}'
                         rf'(?:(?:又|遂|乃)?(?:大)?破(?P<enemy>{_HAN}{{2,8}}?)(?:于|於){re.escape(place)}'
                         rf'|(?:与|及)(?P<force>{_HAN}{{2,8}}?)战(?:于|於){re.escape(place)}[，,]败之){_END}')
    routed = re.compile(rf'[，,]{re.escape(loser)}(?:可汗|王|主)?[^，。；\n]{{0,8}}?(?:远遁|退走|奔走|先走){_END}')
    primary, corroborating = [], []
    for hit in search.lexical(sources.index, f'{place} {winner}', page_size=16)['hits']:
        chunk = hit['chunk']
        if chunk['page_id'] == home['page_id'] or not (view := _body_view(sources, chunk)):
            continue
        # Restrict the calendar comparison to the same historical house.
        if biography_lookup.HOUSES.get(search.normalize(chunk['book_title'])) != biography_lookup.HOUSES.get(search.normalize(home['book_title'])):
            continue
        text = _prose(view)
        heading = zh.to_simplified(view['heading'])
        for battle in pattern.finditer(text):
            when = _date_before(text, battle.start(), heading)
            if not when or when.era != date.era or (when.year, when.month) != (date.year, date.month):
                continue
            retreat = routed.match(text, battle.end())
            end = retreat.end() if retreat else battle.end()
            battle_ref = source_ref(view, battle.start(), end)
            refs = [source_ref(view, *span) for span in when.sources]
            if _YEAR_HEADING.fullmatch(heading):
                _, blocks = sources.page(view['page_id'])
                preceding = view['parts'][0]['block_index'] - 1
                if preceding >= 0 and blocks[preceding]['kind'] == 'heading' and blocks[preceding]['text'] == view['heading']:
                    refs.insert(0, _block_quote(sources, view['page_id'], preceding, 0, len(view['heading'])))
            witness = dict(battle=battle_ref, calendar=refs, day=battle['day'], opponent=battle['enemy'] or battle['force'])
            if retreat or battle['enemy'] == loser:
                primary.append(witness)
            elif battle['day']:
                corroborating.append(witness)
    return primary + [w for w in corroborating if any(
        (p['opponent'], p['day']) == (w['opponent'], w['day']) and p['battle']['page_id'] != w['battle']['page_id']
        for p in primary)]


def compare(sources, query, order):
    """Reorder an existing pool for inspection, with no effect on desktop search."""
    frame = activity_context.parse(zh.to_simplified(query))
    if not frame or frame['kind'] != 'receipt' or not frame.get('anchor'):
        raise ValueError('Expected a victory report question with a place')
    identity = biography_lookup.resolved(frame['person'])
    if not identity or not (ruler_ref := _ruler_quote(sources, identity[0])):
        return dict(order=list(order), evidence={})
    evidence, cache = {}, {}
    for cid in order:
        chunk = sources.chunk(cid)
        episode = _episode(sources, chunk, identity[0]['aliases'], ruler_ref)
        if not episode:
            continue
        victor, opponent, place, date, refs, calendar = episode
        key = (chunk['page_id'], victor, opponent, place, date.era, date.year, date.month)
        if key not in cache:
            cache[key] = _witnesses(sources, chunk, victor, opponent, place, date)
        witnesses = cache[key]
        if not witnesses:
            continue
        for ref in [*refs.values(), *calendar, *(r for w in witnesses for r in [w['battle'], *w['calendar']])]:
            sources.check(ref)
        evidence[cid] = dict(status='supported' if place == frame['anchor'] else 'different_battle',
                             recipient=identity[0]['aliases'][1], victor=victor, opponent=opponent,
                             place=place, era=date.era, year=date.year, month=date.month,
                             references=refs, calendar=calendar, witnesses=witnesses)
    return dict(order=sorted(order, key=lambda cid: evidence.get(cid, {}).get('status') != 'supported'), evidence=evidence)
