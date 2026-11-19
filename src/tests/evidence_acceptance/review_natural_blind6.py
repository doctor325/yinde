"""Record first submissions of six frozen natural questions against desktop 839c743."""
from hashlib import file_digest, sha256
import json
from urllib.parse import parse_qs, urlsplit

from desktop.runtime import DesktopServer
from search import punctuated, punctuated_search
from search.semantic_bridge import worker
from tests.evidence_acceptance.review_natural_questions import available, exact_link
from tests.evidence_acceptance.review_polity_evidence import DATA, FIXTURES, Sources
from tests.evidence_acceptance.run_search_evidence_review import submit_form, write_json


FIXTURE_SHA = '8a049138fbea71c1268b77a0dae4eee1b157eda2bcfad1bab639242e00cd2211'
DESKTOP_SHA = '8109d646287c735a3c57434a096c8b6555c918408ea30f92012c0018e44f59b5'
SCOPE = ('book_id', 'page_id', 'revision_id', 'source_sha256', 'layer', 'heading')


def verify_desktop_version(root):
    with (root / 'desktop-dist/Yinde/Yinde.exe').open('rb') as executable:
        assert file_digest(executable, 'sha256').hexdigest() == DESKTOP_SHA
    settings = json.loads((root / 'desktop-dist/Yinde/yinde-project.json').read_bytes())
    assert (root / 'desktop-dist/Yinde' / settings['relative_root']).resolve() == root
    receipt = json.loads((DATA / 'polity-natural-validated/desktop-receipt.json').read_bytes())
    assert receipt['executable_sha256'] == DESKTOP_SHA and receipt['polity_sources_match']
    for entry in receipt['modules']:
        source = DATA.parent / (entry['module'].replace('.', '/') + '.py')
        assert entry['embedded_code_matches']
        assert sha256(source.read_bytes()).hexdigest() == entry['source_sha256'], source


def compare(result, form, edition_refs, sources):
    pipeline = result['evidence_pipeline']
    rows = pipeline['candidate_diagnostics']
    assert form['rendered'] == [row['chunk_id'] for row in rows if row['displayed']]
    pool = [(row['final_rank'], sources.chunk(row['chunk_id'])) for row in rows]
    coverage, links, pool_ranks = {}, {}, {}
    for name, refs in edition_refs.items():
        pool_ranks[name] = {
            role: [rank for rank, chunk in pool if available(ref, [chunk])]
            for role, ref in refs.items()
        }
        for rank, (hit, card) in enumerate(zip(result['hits'], form['hitCards']), 1):
            chunk = hit['chunk']
            neighbors = [item for item in hit['context_chunks']
                         if all(item[key] == chunk[key] for key in SCOPE)]
            covered_roles = [role for role, ref in refs.items() if available(ref, [chunk, *neighbors])]
            if covered_roles:
                coverage.setdefault(name, {})[rank] = covered_roles
            linked_roles = {
                role: [item['label'] for item in card['evidenceLinks'] if exact_link(item, ref)]
                for role, ref in refs.items()
            }
            if linked := {role: labels for role, labels in linked_roles.items() if labels}:
                links.setdefault(name, {})[rank] = linked

    claims = [
        dict(rank=rank, chunk_id=hit['chunk']['chunk_id'], status=card['evidenceStatus'],
             audit=hit['evidence_audit']['status'],
             message=(hit.get('advice_evidence') or hit.get('polity_evidence') or {}).get('message'))
        for rank, (hit, card) in enumerate(zip(result['hits'], form['hitCards']), 1)
        if card['evidenceStatus'] or hit['evidence_audit']['status'] == 'clues_present'
    ]
    enough = {edition: [rank for rank, roles in matched.items() if set(roles) == set(edition_refs[edition])]
              for edition, matched in coverage.items()}
    top5_evidence = {
        edition: sorted({role for rank, roles in matched.items() if rank <= 5 for role in roles})
        for edition, matched in coverage.items()
    }
    return dict(top5=form['rendered'][:5], fully_readable_ranks=enough, top5_evidence=top5_evidence,
                exact_links=links, candidate_ranks=pool_ranks, scored_candidates=len(rows),
                claims=claims, ms=round(form['form_ms']),
                parsed=result.get('query_plan', {}).get('colloquial_advocacy'),
                clues=result.get('query_plan', {}).get('clues', []))


def main():
    root = DATA.parent.parent
    verify_desktop_version(root)
    raw = (FIXTURES / 'natural_question_blind6.json').read_bytes()
    assert sha256(raw.replace(b'\r\n', b'\n')).hexdigest() == FIXTURE_SHA
    raw_checksum = sha256(raw).hexdigest()
    frozen = json.loads(raw)
    out = DATA / 'polity-natural-blind6-839c743'
    results = {}
    try:
        with punctuated_search.connect() as index, punctuated.connect() as library:
            sources = Sources(index, library, [])
            episodes = {
                key: {
                    edition: {role: sources.pinned(frozen['evidence'][edition][role])
                              for role in episode['required']}
                    for edition in episode['sources']
                }
                for key, episode in frozen['episodes'].items()
            }
            # Validate separately frozen context and alternate explanations before searching.
            for pins in frozen['evidence'].values():
                for pin in pins.values():
                    sources.pinned(pin)
            out.mkdir(exist_ok=True)
            if any(out.iterdir()):
                raise FileExistsError(f'Preserve the first run in {out}')
            with DesktopServer(root) as server:
                for case in frozen['cases']:
                    case_id = case['id']
                    print(case_id, flush=True)
                    result, form = submit_form(server.entry, case['query'])
                    write_json(out / (case_id + '.json'),
                               dict(result=result, form=form, fixture_sha256=raw_checksum))
                    params = parse_qs(urlsplit(form['requestPath']).query)
                    assert params['q'] == [case['query']] and params['strategy'] == ['evidence']
                    refs = dict(episodes[case['episode']])
                    if other := case.get('other_episode'):
                        refs.update(episodes[other])
                    results[case_id] = compare(result, form, refs, sources)
                    write_json(out / 'measurements.json', results)
    finally:
        worker.close()


if __name__ == '__main__':
    main()
