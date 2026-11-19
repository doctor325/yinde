"""Replay fixed candidates against provenance-checked opponent and reign evidence."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, quote, urlsplit

from search import evidence_context, evidence_pipeline, punctuated, punctuated_search, zh
from tests.evidence_acceptance.review_memorial_scope import DATA
from tests.evidence_acceptance.review_speech_search import ranges, covered
from tests.evidence_acceptance.review_speech_turns import chunk_at, extract, match
from tests.evidence_acceptance.run_search_evidence_review import write_json


FIXTURES = Path(__file__).parent
REPLAY = DATA / 'speech-search-directional'
_DIRECTION = re.compile(r'[东西南北][征伐讨]')
_MESSAGES = {
    'supported': '发言、所议行动与对手身份均有原文依据。',
    'missing_event_opponent': '当前片段缺少能与这次劝谏相连的对手证据。',
    'missing_temporal_evidence': '缺少可核对的在位记载或同年战事依据。',
    'missing_reason': '这张卡缺少能归给同一发言人的理由。',
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

    def check(self, ref):
        pid = ref['page_id']
        if pid not in self.pages:
            row = self.library.execute('SELECT * FROM chapters WHERE page_id=?', (pid,)).fetchone()
            if row is None:
                raise ValueError('Source page missing')
            self.pages[pid] = dict(row), json.loads(row['blocks_json'])
        page, blocks = self.pages[pid]
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
        cid, revision, checksum, start, end, text_checksum = saved
        try:
            chunk = chunk_at(self.index, cid)
        except TypeError:
            raise ValueError(f'Pinned index chunk missing: {cid}') from None
        ref = source_ref(chunk, start, end)
        if revision != ref['revision_id'] or checksum != ref['source_sha256'] or digest(ref['quote']) != text_checksum:
            raise ValueError(f'Pinned source changed: {cid}')
        return self.check(ref)


def inside(container, passage):
    return (all(container[key] == passage[key] for key in evidence_context.IDENTITY)
            and all(covered(span, ranges(container)) for span in passage['ranges']))


def overlaps(left, right):
    return any(a == b and lo < end and start < hi
               for a, lo, hi in left for b, start, end in right)


def decide(sources, query, cid):
    def result(status, citations=(), **fields):
        return dict(status=status, message=_MESSAGES[status], citations=dict(citations), **fields)

    chunk = chunk_at(sources.index, cid)
    context = evidence_context.assemble(sources.library, chunk, max_chars=1200)
    window = evidence_context.rule_view(chunk, context)
    frame = evidence_pipeline.parse(query)['colloquial_advocacy']
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
        for label, role in [('劝谏理由', 'detail'), ('劝谏人', 'speaker'), ('所劝对象', 'recipient')]:
            ref = attachment[role]
            citations.append((label, sources.check(source_ref(window, ref['start'], ref['end']))))
            if role != 'detail':
                citations.append((label + '称谓依据', role_sources[zh.to_simplified(ref['quote'])]))
        return result('supported', citations, polity=record['polity'], ruler=record['ruler'], reign=term,
                      year=year, era=record['recorded_year'], parallel_era=record['comparison_year'])
    return result('missing_event_opponent')


def link(ref):
    url = urlsplit(ref['source_url'])
    if (url.scheme != 'https' or url.hostname != 'zh.wikisource.org'
            or parse_qs(url.query).get('oldid') != [str(ref['revision_id'])]):
        raise ValueError('Expected a fixed source revision')
    # Keep punctuation within the text directive and rely on oldid for the full page.
    return url.geturl() + '#:~:text=' + quote(ref['quote'], safe='').replace('-', '%2D')


def replay(filename, checksum):
    path = REPLAY / (filename + '.json')
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == checksum
    saved = json.loads(raw)
    rows = saved['result']['evidence_pipeline']['candidate_diagnostics']
    ranks = {item['chunk_id']: item['final_rank'] for item in rows}
    assert [item['chunk_id'] for item in rows if item['displayed']] == saved['form']['rendered']
    return saved['result']['query'], ranks


def main():
    holdout_raw = (FIXTURES / 'polity_event_holdout.json').read_bytes()
    assert hashlib.sha256(holdout_raw).hexdigest() == 'bb68b4f43b7a7020d6a6be8c4314f3f34cd4622d648cc60bb58333cf861ad71f'
    frozen = json.loads(holdout_raw)
    catalog = json.loads((FIXTURES / 'polity_sources.json').read_text(encoding='utf-8'))
    shen, ranks = replay('shen-qingzhi-northern-campaign',
                         'c9d3683e19615584d03932821d3c4460c00a0ae5be15d261904266817cdde678')
    reversed_query, reversed_ranks = replay('song-wendi-reversed',
                                           'd0426464f04018ce9cf1f4789e1e1081c5e28e2cd2526ca392befa370775a798')
    assert [ranks[44068], ranks[44074], reversed_ranks[44068]] == [5, 1, 6]
    cases = [
        ('shen-northern-wei', shen, 44068, ranks[44068], 'supported'),
        ('shen-other-polity', shen.replace('北魏', '柔然'), 44068, ranks[44068], 'different_polity'),
        ('song-wendi-reversed', reversed_query, 44068, reversed_ranks[44068], 'different_roles'),
        ('shen-later-expedition', shen, 44074, ranks[44074], 'missing_event_opponent'),
        *((c['id'], c['query'], frozen['anchor'], None, c['expected']) for c in frozen['cases']),
    ]
    report = []
    with punctuated_search.connect() as index, punctuated.connect() as library:
        sources = Sources(index, library, catalog)
        for name, query, cid, rank, expected in cases:
            outcome = decide(sources, query, cid)
            print(name, rank, outcome['status'], flush=True)
            assert outcome['status'] == expected, (name, outcome)
            report.append(dict(id=name, rank=rank, query=query, chunk_id=cid, **outcome))
        for key in ('event', 'reason', 'outcome', 'chronicle', 'campaign_date', 'reign_at_event', 'political_name'):
            pin = frozen[key]
            ref = sources.pinned([pin[name] for name in (
                'chunk_id', 'revision_id', 'source_sha256', 'start', 'end', 'text_sha256')])
            assert ref['page_id'] == pin['page_id']
            if key == 'event':
                assert report[-2]['citations']['出兵对手']['quote'] in ref['quote']
            if key == 'reason':
                assert ref['quote'] in report[-2]['citations']['劝谏理由']['quote']

        first = catalog[0]
        for name, incomplete, expected in [
            ('missing-opponent-citation', dict(first, event={k: v for k, v in first['event'].items() if k != 'opponent'}),
             'missing_event_opponent'),
            ('missing-reign-citation', dict(first, authority={
                k: v for k, v in first['authority'].items() if k != 'governance'}), 'missing_temporal_evidence'),
            ('outside-the-attested-reign', dict(first, year=453), 'missing_temporal_evidence'),
        ]:
            gap = decide(Sources(index, library, [incomplete]), shen, 44068)
            assert gap['status'] == expected
            report.append(dict(id=name, rank=5, query=shen, chunk_id=44068, **gap))

    lines = ['# 政权归属离线验收', '']
    for case in report:
        label = '有原文依据' if case['status'] == 'supported' else '尚未确认'
        lines.extend([f"## {case['query']}", '', f"**{label}**。{case['message']}", ''])
        if term := case.get('reign'):
            lines.extend([f"{case['ruler']}在位区间为 {term['start']}–{term['end']} 年。战事为 {case['era']}。", ''])
        for label, ref in case['citations'].items():
            lines.append(f"- {label}。{ref['quote']}。[{ref['title']}（修订 {ref['revision_id']}）]({link(ref)})")
        lines.append('')
    write_json(DATA / 'polity-evidence-review.json', report)
    (DATA / 'polity-evidence-review.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
