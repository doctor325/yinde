"""Replay fixed candidates against provenance-checked opponent and reign evidence."""
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from search import evidence_pipeline, polity_evidence, punctuated, punctuated_search
from search.polity_evidence import Sources
from tests.evidence_acceptance.review_memorial_scope import DATA
from tests.evidence_acceptance.review_speech_turns import chunk_at
from tests.evidence_acceptance.run_search_evidence_review import write_json


FIXTURES = Path(__file__).parent
REPLAY = DATA / 'speech-search-directional'
def decide(sources, query, cid):
    frame = evidence_pipeline.parse(query)['colloquial_advocacy']
    return polity_evidence.decide(sources, frame, chunk_at(sources.index, cid))


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
    catalog = polity_evidence.catalog()
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
