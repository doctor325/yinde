"""Search two migration questions and replay three saved natural-question results."""
import argparse
import json

from desktop.runtime import DesktopServer
from search import evidence_pipeline, polity_evidence, punctuated, punctuated_search
from search.semantic_bridge import worker
from tests.evidence_acceptance.review_natural_questions import exact_link
from tests.evidence_acceptance.review_polity_evidence import DATA, FIXTURES, Sources
from tests.evidence_acceptance.run_search_evidence_review import submit_form, write_json


def check_cards(result, form, status, reference_sets, expected_people):
    for rank, (hit, card) in enumerate(zip(result['hits'][:5], form['hitCards'][:5]), start=1):
        evidence = hit.get('advice_evidence', {})
        if card['evidenceStatus'] != status or not all(name in evidence['message'] for name in expected_people):
            continue
        if any(all(any(exact_link(link, ref) for link in card['evidenceLinks'])
                   for ref in refs) for refs in reference_sets):
            return dict(rank=rank, chunk_id=hit['chunk']['chunk_id'], status=status, message=evidence['message'])
    raise AssertionError((result['query'], status, [(row['evidenceStatus'], cid) for row, cid in
                                                    zip(form['hitCards'][:5], form['rendered'][:5])]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migration-only', action='store_true')
    args = parser.parse_args()
    first = DATA / 'polity-natural-blind6-839c743'
    out = DATA / 'natural-advice-roles'
    out.mkdir(exist_ok=True)
    frozen = json.loads((FIXTURES / 'natural_question_blind6.json').read_bytes())
    cases = {row['id']: row['query'] for row in frozen['cases']}
    with punctuated.connect() as library, punctuated_search.connect() as index:
        sources = Sources(index, library, [])
        zhang = [[sources.pinned(frozen['evidence'][edition][role]) for role in ('speaker', 'reason')]
                 for edition in ('song-46', 'nan-32')]
        wang = [[sources.pinned(frozen['evidence']['jin-122'][role]) for role in ('speaker', 'reason')]]
        # Alternate witness found and manually verified in the first blind run.
        wei = [[sources.check(dict(sources.chunk(184250), ranges=[[0, lo, hi]], quote=quote))
                for lo, hi, quote in (
                    (332, 334, '魏征'),
                    (353, 397, '養之則至仁，勞之則未可。升中之禮，須備千乘萬騎，供帳之費，動役數州。戶口蕭條，何以能給？'),
                )]]
        replays = (
            ('zhang-paraphrase', zhang, ('张畅', '义恭')),
            ('wang-paraphrase', wang, ('王穆', '张大豫')),
            ('wei-taishan', wei, ('魏征', '太宗')),
        ) if not args.migration_only else ()
        for case, refs, people in replays:
            saved = json.loads((first / (case + '.json')).read_bytes())['result']
            frame = evidence_pipeline.parse(cases[case])['colloquial_advocacy']
            polity_evidence.review_natural_hits(index, frame, saved['hits'])
            result, form = submit_form(None, cases[case], replay=saved)
            summary = check_cards(result, form, 'supported', refs, people)
            write_json(out / (case + '.json'), dict(result=result, form=form, summary=summary))
            print(case, summary, flush=True)
            if case == 'wei-taishan':
                # Present the same misleading material to the question about a palace.
                for hit in saved['hits']:
                    hit.pop('advice_evidence', None)
                mismatch = evidence_pipeline.parse(cases['wei-other-event']).get('colloquial_advocacy')
                polity_evidence.review_natural_hits(index, mismatch, saved['hits'])
                assert all(hit.get('advice_evidence', {}).get('status') != 'supported' for hit in saved['hits'])

        xin = [[sources.pinned(frozen['evidence']['sanguozhi-25'][role])
                for role in ('speaker', 'proposal', 'reason')]]
        try:
            with DesktopServer(DATA.parent.parent) as server:
                for case, status in (('xin-migration', 'supported'), ('xin-reversed', 'different_roles')):
                    result, form = submit_form(server.entry, cases[case])
                    write_json(out / (case + '.json'), dict(result=result, form=form))
                    ranks = result['evidence_pipeline']['candidate_diagnostics']
                    assert any(row['chunk_id'] in (65939, 65940) and 'biography_context' in row['channel_ranks']
                               for row in ranks), ranks
                    summary = check_cards(result, form, status, xin, ('辛毗', '曹丕'))
                    if status == 'different_roles':
                        assert not any(card['evidenceStatus'] == 'supported' for card in form['hitCards'])
                    write_json(out / (case + '.json'), dict(result=result, form=form, summary=summary))
                    print(case, summary, flush=True)
        finally:
            worker.close()


if __name__ == '__main__':
    main()
