"""Measure the frozen polity questions through the actual desktop search form."""
import argparse
from contextlib import nullcontext
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from desktop.runtime import DesktopServer
from search import evidence_pipeline, polity_evidence, punctuated, punctuated_search
from search.semantic_bridge import worker
from tests.evidence_acceptance.review_polity_evidence import DATA, FIXTURES, REPLAY, Sources, decide
from tests.evidence_acceptance.review_speech_search import covered, ranges
from tests.evidence_acceptance.run_search_evidence_review import submit_form, write_json


def check_cards(result, form, anchor, expected, *, reusable=False):
    hits = [hit for hit in result['hits'] if 'polity_evidence' in hit]
    assert len(hits) == len(form['polityCards'])
    for hit, card in zip(hits, form['polityCards']):
        review = hit['polity_evidence']
        assert card['status'] == review['status']
        assert {link['label'] for link in card['links']} == set(review['citations'])
        for link in card['links']:
            ref = review['citations'][link['label']]
            route = urlsplit(link['href'][1:])
            params = parse_qs(route.query)
            block, start, end = ref['ranges'][0]
            assert route.path == f"/punctuated/{ref['page_id']}"
            assert params['rev'] == [str(ref['revision_id'])] and params['sha'] == [ref['source_sha256']]
            assert [int(params[key][0]) for key in ('block', 'start', 'end')] == [block, start, end]
            if len(ref['ranges']) > 1:
                assert json.loads(params['ranges'][0]) == ref['ranges']
        if hit['chunk']['chunk_id'] == anchor:
            assert review['status'] == expected
            if expected == 'supported':
                term = {'即位记载', '在位终点', '战事纪年'} if reusable else {'事发时在位'}
                assert {'劝谏理由', '出兵对手', '政权名称依据'} | term <= review['citations'].keys()
    assert any(hit['chunk']['chunk_id'] == anchor for hit in hits)
    if expected != 'supported':
        assert all(hit['polity_evidence']['status'] != 'supported' for hit in hits)


def check_shen(index):
    positive = json.loads((REPLAY / 'shen-qingzhi-northern-campaign.json').read_text(encoding='utf-8'))['result']
    reverse = json.loads((REPLAY / 'song-wendi-reversed.json').read_text(encoding='utf-8'))['result']
    evidence = {}
    for result in (positive, reverse):
        frame = evidence_pipeline.parse(result['query'])['colloquial_advocacy']
        polity_evidence.review_hits(index, frame, result['hits'])
        evidence[result['query']] = {hit['chunk']['chunk_id']: hit['polity_evidence']
                                     for hit in result['hits'] if 'polity_evidence' in hit}
    checked = evidence[positive['query']]
    assert checked[44068]['status'] == 'supported' and checked[44068]['polity'] == '北魏'
    assert checked[44074]['status'] == 'missing_event_opponent'
    assert evidence[reverse['query']][44068]['status'] == 'different_roles'
    return evidence


def review_reuse(*, replay=False):
    raw = (FIXTURES / 'reused_ruler_holdout.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '692aa63e6040deaff9b7b07a8a937d7af88281c7200359fc5305c3b6f1c84c00'
    frozen = json.loads(raw)
    captured = DATA / 'polity-ruler-reuse'
    out = DATA / 'polity-ruler-reuse-verified' if replay else captured
    out.mkdir(exist_ok=True)
    try:
        service = nullcontext() if replay else DesktopServer(DATA.parent.parent)
        with service as server, punctuated_search.connect() as index, punctuated.connect() as library:
            sources = Sources(index, library, polity_evidence.catalog())
            pins = {kind: sources.pinned([pin[key] for key in (
                'chunk_id', 'revision_id', 'source_sha256', 'start', 'end', 'text_sha256')])
                    for kind, pin in frozen['pins'].items()}
            reason = pins['reason']
            anchor = frozen['pins']['proposal']['chunk_id']
            verdicts = []
            for case in frozen['cases']:
                if replay:
                    original = json.loads((captured / (case['id'] + '.json')).read_text(encoding='utf-8'))
                    response = original['result']
                    for hit in response['hits']:
                        hit.pop('polity_evidence', None)
                    frame = evidence_pipeline.parse(case['query'])['colloquial_advocacy']
                    polity_evidence.review_hits(index, frame, response['hits'])
                    response, form = submit_form(None, case['query'], replay=response)
                    assert form['rendered'] == original['form']['rendered']
                    form_ms = original['form']['form_ms']
                else:
                    response, form = submit_form(server.entry, case['query'])
                    form_ms = form['form_ms']
                rows = response['evidence_pipeline']['candidate_diagnostics']
                assert form['rendered'] == [row['chunk_id'] for row in rows if row['displayed']]
                candidate = next((row for row in rows if row['chunk_id'] == anchor), None)
                found = [row['final_rank'] for row in rows if row['chunk_id'] in frozen['reason_chunks']]
                visible = []
                for rank, hit in enumerate(response['hits'][:5], 1):
                    for ref in (reason, pins['parallel_reason']):
                        pieces = [part for chunk in [hit['chunk'], *hit['context_chunks']]
                                  if chunk['page_id'] == ref['page_id']
                                  and chunk['source_sha256'] == ref['source_sha256'] for part in ranges(chunk)]
                        if all(covered(span, pieces) for span in ref['ranges']):
                            visible.append(rank)
                            break
                card = next((hit.get('polity_evidence') for hit in response['hits']
                             if hit['chunk']['chunk_id'] == anchor), None)
                summary = dict(anchor=candidate, reason_ranks=found, reason_in_top5=visible,
                               card=card, live_form_ms=round(form_ms), replay=replay)
                verdicts.append((case, response, form, summary))
                print(case['id'], summary['live_form_ms'], 'ms', 'top5=', form['rendered'][:5], 'gold ranks=', found,
                      'reason visible=', visible, 'card=', card and card['status'], flush=True)
                write_json(out / (case['id'] + '.json'), dict(result=response, form=form, summary=summary))
            write_json(out / 'shen-replay.json', check_shen(index))
            previous = json.loads((DATA / 'polity-search-validation' / 'guo-western-qin.json')
                                  .read_text(encoding='utf-8'))['result']
            guo = evidence_pipeline.parse(previous['query'])['colloquial_advocacy']
            polity_evidence.review_hits(index, guo, previous['hits'])
            assert next(hit['polity_evidence']['status'] for hit in previous['hits']
                        if hit['chunk']['chunk_id'] == 85786) == 'supported'
            for case, response, form, summary in verdicts:
                assert summary['anchor'] and summary['anchor']['final_rank'] <= 5 and summary['reason_in_top5'], summary
                check_cards(response, form, anchor, case['expected'], reusable=True)
                card = summary['card']
                ref = card['citations']['劝谏理由']
                assert ref['page_id'] == reason['page_id'] and reason['quote'] in ref['quote']
                assert reason['ranges'] == ref['ranges'] and card['citations']['战事纪年'] == pins['date']
                assert card.get('polity', card.get('actual_polity')) == '北魏'
                assert card['citations']['即位记载']['revision_id'] == 2318474
                assert card['citations']['在位终点']['revision_id'] == 2322627
            unresolved = polity_evidence.verify_ruler(sources, '托跋焘', '元嘉三十年', 'KR2a0016')
            assert unresolved[0] == 'missing_temporal_evidence'
    finally:
        worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', choices=('baseline', 'validation', 'reuse', 'reuse-replay'))
    args = parser.parse_args()
    if args.run in ('reuse', 'reuse-replay'):
        review_reuse(replay=args.run == 'reuse-replay')
        return
    raw = (FIXTURES / 'polity_event_holdout.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'bb68b4f43b7a7020d6a6be8c4314f3f34cd4622d648cc60bb58333cf861ad71f'
    frozen = json.loads(raw)
    catalog = polity_evidence.catalog()
    out = DATA / f'polity-search-{args.run}'
    out.mkdir(exist_ok=True)
    try:
        with DesktopServer(DATA.parent.parent) as server, punctuated_search.connect() as index, punctuated.connect() as library:
            sources = Sources(index, library, catalog)
            reason = sources.pinned(list(frozen['reason'][key] for key in (
                'chunk_id', 'revision_id', 'source_sha256', 'start', 'end', 'text_sha256')))
            for case in frozen['cases']:
                result, form = submit_form(server.entry, case['query'])
                rows = result['evidence_pipeline']['candidate_diagnostics']
                assert form['rendered'] == [row['chunk_id'] for row in rows if row['displayed']]
                anchor = next((row for row in rows if row['chunk_id'] == frozen['anchor']), None)
                visible = []
                for rank, hit in enumerate(result['hits'][:5], 1):
                    parts = [part for chunk in [hit['chunk'], *hit['context_chunks']]
                             if chunk['page_id'] == reason['page_id']
                             and chunk['source_sha256'] == reason['source_sha256'] for part in ranges(chunk)]
                    if all(covered(span, parts) for span in reason['ranges']):
                        visible.append(rank)
                outcome = decide(sources, case['query'], frozen['anchor']) if anchor else None
                summary = dict(id=case['id'], form_ms=round(form['form_ms']), top5=form['rendered'][:5],
                               reason_in_top5=visible, anchor=anchor, offline_judgment=outcome)
                write_json(out / (case['id'] + '.json'), dict(result=result, form=form, summary=summary))
                if args.run == 'validation':
                    baseline = json.loads((DATA / 'polity-search-baseline' / (case['id'] + '.json'))
                                          .read_text(encoding='utf-8'))
                    assert form['rendered'] == baseline['form']['rendered']
                    assert [row['chunk_id'] for row in rows] == [
                        row['chunk_id'] for row in baseline['result']['evidence_pipeline']['candidate_diagnostics']]
                    assert anchor and anchor['final_rank'] <= 5 and visible
                    check_cards(result, form, frozen['anchor'], case['expected'])
                print(case['id'], summary['form_ms'], 'ms', 'top5=', summary['top5'], 'reason=', visible,
                      'anchor_rank=', anchor['final_rank'] if anchor else None,
                      'judgment=', outcome['status'] if outcome else None, flush=True)
            if args.run == 'validation':
                write_json(out / 'shen-replay.json', check_shen(index))
                print('Shen replay: supported, reversed roles rejected, later expedition unconfirmed', flush=True)
    finally:
        worker.close()


if __name__ == '__main__':
    main()
