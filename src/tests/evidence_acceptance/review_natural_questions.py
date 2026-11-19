"""Capture four frozen questions with the unmodified desktop search path."""
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from desktop.runtime import DesktopServer
from search import punctuated, punctuated_search
from search.semantic_bridge import worker
from tests.evidence_acceptance.review_polity_evidence import DATA, FIXTURES, Sources
from tests.evidence_acceptance.review_speech_search import covered, ranges
from tests.evidence_acceptance.run_search_evidence_review import submit_form, write_json


def available(ref, chunks):
    parts = [span for chunk in chunks for span in ranges(chunk)
             if all(chunk[key] == ref[key] for key in ('page_id', 'revision_id', 'source_sha256'))]
    return all(covered(span, parts) for span in ref['ranges'])


def exact_link(link, ref):
    url = urlsplit(link['href'][1:])
    params = parse_qs(url.query)
    if (url.path != f"/punctuated/{ref['page_id']}"
            or params.get('rev') != [str(ref['revision_id'])]
            or params.get('sha') != [ref['source_sha256']]):
        return False
    parts = (json.loads(params['ranges'][0]) if 'ranges' in params else
             [[int(params[key][0]) for key in ('block', 'start', 'end')]])
    return all(covered(span, parts) for span in ref['ranges'])


def assess(case, result, form, passages, reason_ids):
    rows = result['evidence_pipeline']['candidate_diagnostics']
    assert form['rendered'] == [row['chunk_id'] for row in rows if row['displayed']]
    links = [link for group in form['evidenceLinks'] for link in group]
    visible = {}
    for rank, hit in enumerate(result['hits'], 1):
        chunk = hit['chunk']
        scope = ('book_id', 'page_id', 'revision_id', 'source_sha256', 'layer', 'heading')
        chunks = [chunk, *(item for item in hit['context_chunks']
                           if all(item[key] == chunk[key] for key in scope))]
        for name, refs in passages.items():
            present = [role for role in case['required'] if available(refs[role], chunks)]
            if present:
                visible.setdefault(rank, {})[name] = present
    sufficient = [
        rank for rank, editions in visible.items()
        if any(set(roles) == set(case['required']) for roles in editions.values())
    ]
    routes = {
        name: {role: [link['label'] for link in links if exact_link(link, refs[role])]
               for role in case['required']}
        for name, refs in passages.items()
    }
    return dict(
        top5=form['rendered'][:5], form_ms=round(form['form_ms']),
        parsed=result['query_plan'].get('colloquial_advocacy'),
        unsupported=result['query_plan'].get('unsupported'),
        recall_reason_ranks=[row['final_rank'] for row in rows if row['chunk_id'] in reason_ids],
        top5_coverage={rank: info for rank, info in visible.items() if rank <= 5},
        sufficient_ranks=sufficient, exact_evidence_links=routes,
        supported_ranks=[
            rank for rank, hit in enumerate(result['hits'], 1)
            if hit.get('polity_evidence', {}).get('status') == 'supported'
        ],
        clue_ranks=[
            rank for rank, hit in enumerate(result['hits'], 1)
            if hit.get('evidence_audit', {}).get('status') == 'clues_present'
        ],
    )


def main():
    raw = (FIXTURES / 'natural_polity_questions.json').read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    assert checksum == '4fae577d586b72b9d42c8ae147a914e85537bcd6a39cb6e6e96ae4a0d6149b97'
    frozen = json.loads(raw)
    out = DATA / 'polity-natural-1e997ea'
    out.mkdir(exist_ok=False)
    try:
        with DesktopServer(DATA.parent.parent) as server, punctuated_search.connect() as index, punctuated.connect() as library:
            sources = Sources(index, library, [])
            for pin in frozen['identity_and_chronology']:
                sources.pinned(pin)
            for case in frozen['cases']:
                response, form = submit_form(server.entry, case['query'])
                params = parse_qs(urlsplit(form['requestPath']).query)
                assert params['q'] == [case['query']] and params['strategy'] == ['evidence']
                editions = frozen['episodes'][case['episode']]['sources']
                passages = {
                    name: {role: sources.pinned(pin) for role, pin in frozen['evidence'][name].items()}
                    for name in editions
                }
                reason_ids = {frozen['evidence'][name]['reason'][0] for name in editions}
                summary = assess(case, response, form, passages, reason_ids)
                write_json(out / (case['id'] + '.json'), dict(result=response, form=form, summary=summary,
                                                            fixture_sha256=checksum))
                print(case['id'], json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        worker.close()


if __name__ == '__main__':
    main()
