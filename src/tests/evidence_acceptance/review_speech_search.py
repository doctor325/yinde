"""Search frozen questions through the desktop form and assess source-grounded dialogue."""
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import time

from search import evidence_context, evidence_pipeline, punctuated, punctuated_search, zh
from tests.evidence_acceptance.review_memorial_scope import DATA
from tests.evidence_acceptance.review_speech_turns import chunk_at, extract, match, reason_quotes
from tests.evidence_acceptance.run_search_evidence_review import submit_form, write_json


MANIFEST = Path(__file__).with_name('speech_turn_cases.json')
_IMPERIAL_OPENING = re.compile(
    r'\s*(?P<title>[\u3400-\u9fff][祖宗])(?P<posthumous>[\u3400-\u9fff]{1,20})皇帝[，,]?(?:姓[^，。]+[，,])?讳'
)


def collect(episodes, out):
    from desktop.runtime import DesktopServer
    from search.semantic_bridge import worker

    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'inputs.json', dict(
        fixture_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        questions=[case['query'] for episode in episodes for case in episode['cases']],
    ))
    try:
        with DesktopServer(DATA.parent.parent) as server:
            for episode in episodes:
                for case in episode['cases']:
                    result, form = submit_form(server.entry, case['query'])
                    ranks = result['evidence_pipeline']['candidate_diagnostics']
                    assert form['rendered'] == [row['chunk_id'] for row in ranks if row['displayed']]
                    write_json(out / (case['id'] + '.json'), dict(result=result, form=form))
                    print(case['id'], 'ms=', round(form['form_ms']), 'top5=', form['rendered'][:5], flush=True)
    finally:
        worker.close()


def citation(block_index, start, end, blocks):
    return dict(block_index=block_index, start=start, end=end, quote=blocks[block_index]['text'][start:end])


def opening_names(chapter, heading, before):
    if not re.fullmatch(r'[\u3400-\u9fff]{2,5}', heading):
        return {}, {}
    blocks = chapter['blocks']
    for i in range(before, -1, -1):
        if blocks[i]['kind'] == 'heading':
            heading_block = i
            break
    else:
        return {}, {}
    if zh.to_simplified(blocks[heading_block]['text']).strip() != heading:
        return {}, {}
    body_index = heading_block + 1
    if body_index >= len(blocks) or blocks[body_index]['kind'] != 'body':
        return {}, {}
    body = zh.to_simplified(blocks[body_index]['text'])
    alternatives = '|'.join(re.escape(heading[start:]) for start in range(len(heading)))
    opening = re.match(rf'\s*(?P<name>{alternatives})[，,]?\s*字', body)
    if not opening or len(body) != len(blocks[body_index]['text']):
        return {}, {}
    spelling = opening['name']
    ref = citation(body_index, *opening.span('name'), blocks)
    alias = spelling
    if spelling == heading and len(heading) >= 3:
        # Verify a separate narrative occurrence before shortening a full name.
        # In particular, a four-character name does not imply a one-character surname.
        suffix = heading[-2:]
        mention = re.search(rf'(?<=[。！？\n])\s*(?P<name>{re.escape(suffix)})(?=[\u3400-\u9fff])',
                            body[opening.end():opening.end() + 160])
        if mention:
            alias = suffix
            lo, hi = mention.span('name')
            ref = citation(body_index, opening.end() + lo, opening.end() + hi, blocks)
    return {alias: heading, heading: heading}, {alias: [ref, citation(
        heading_block, 0, len(blocks[heading_block]['text']), blocks)]}


def imperial_names(library, book_id, book_title, requested):
    dynasty = re.fullmatch(r'([\u3400-\u9fff])(?:书|史)', zh.to_simplified(book_title))
    if not dynasty or not any(name.startswith(dynasty[1]) for name in requested):
        return {}, {}
    names, bases = {}, {}
    rows = library.execute(
        "SELECT page_id,revision_id,source_sha256,source_url,json_extract(blocks_json,'$[0].text') AS opening "
        "FROM chapters WHERE book_id=? AND sort_number<=30", (book_id,))
    for row in rows:
        original = row['opening'] or ''
        text = zh.to_simplified(original)
        opening = _IMPERIAL_OPENING.match(text)
        if not opening or len(text) != len(original):
            continue
        alias = opening['title']
        titles = [dynasty[1] + alias]
        if len(opening['posthumous']) == 1:
            titles.append(dynasty[1] + opening['posthumous'] + '帝')
        matches = requested.intersection(titles)
        if len(matches) == 1:
            names.setdefault(alias, set()).update(matches)
            bases.setdefault(alias, []).append(dict(
                page_id=row['page_id'], revision_id=row['revision_id'], source_sha256=row['source_sha256'],
                source_url=row['source_url'], block_index=0, start=opening.start('title'), end=opening.end(),
                quote=original[opening.start('title'):opening.end()]))
    unique = {alias: next(iter(values)) for alias, values in names.items()
              if len(values) == 1 and len(bases[alias]) == 1}
    return unique, {alias: bases[alias] for alias in unique}


def automatic_evidence(index, library, result):
    plan = evidence_pipeline.parse(result['query'])
    frame = plan.get('colloquial_advocacy') or {}
    requested = {name for name in (frame.get('person'), frame.get('recipient')) if name}
    if not requested:
        return []
    get_chapter = lru_cache(maxsize=32)(punctuated.chapter)
    lookup = lru_cache(maxsize=32)(lambda book, title: imperial_names(library, book, title, requested))
    annotated = []
    for rank in result['evidence_pipeline']['candidate_diagnostics']:
        chunk = chunk_at(index, rank['chunk_id'])
        if chunk['layer'] != 'body':
            continue
        item = dict(rank=rank['final_rank'], chunk=chunk, window=None, turns=[], evidence=None)
        annotated.append(item)
        try:
            context = evidence_context.assemble(library, chunk, max_chars=1200)
        except ValueError as error:
            item['context_error'] = str(error)
            continue
        item['window'] = window = evidence_context.rule_view(chunk, context)
        if context['boundary_cut'] or context['unbalanced_quote']:
            item['context_error'] = 'incomplete_or_unbalanced'
            continue
        chapter = get_chapter(chunk['page_id'])
        names, bases = opening_names(chapter, zh.to_simplified(chunk['heading']).strip(),
                                     chunk['parts'][0]['block_index'])
        rulers, ruler_bases = lookup(chunk['book_id'], chunk['book_title'])
        for alias, name in rulers.items():
            if alias not in names:
                names[alias], bases[alias] = name, ruler_bases[alias]
        text = zh.to_simplified(window['text_orig'])
        names.update({name: name for name in requested if name in text})
        item['turns'] = extract(window, names, {})
        item['evidence'] = match(plan, item['turns'])
        if item['evidence']:
            for role in ('speaker', 'recipient', 'action', 'detail'):
                ref = item['evidence'][role]
                assert window['text_orig'][ref['start']:ref['end']] == ref['quote']
                if role in ('speaker', 'recipient'):
                    ref['name_sources'] = bases.get(zh.to_simplified(ref['quote']), [])
    return annotated


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


def score(index, episode, case, response, annotated):
    result, form = response['result'], response['form']
    anchor = chunk_at(index, episode['anchor'])
    block = anchor['parts'][0]['block_index']
    scope = (block, *episode['scope'])
    gold = reason_quotes(index, episode, anchor | dict(text_orig=punctuated.chapter(
        anchor['page_id'])['blocks'][block]['text']))
    expected = []
    for reference in episode['reason_sources']:
        chunk = chunk_at(index, reference['chunk_id'])
        (k, base, _), = ranges(chunk)
        expected.append((k, base + reference['offset'][0], base + reference['offset'][1]))

    def same_source(chunk):
        return all(chunk[key] == anchor[key] for key in evidence_context.IDENTITY)

    def reason_in(parts):
        return any(covered(span, parts) for span in expected)

    episode_ranks, direct, expanded, complete, context_errors = [], [], [], [], []
    confirmations = []
    for item in annotated:
        rank, chunk, window = item['rank'], item['chunk'], item['window']
        if same_source(chunk):
            if any(k == block and lo < scope[2] and scope[1] < hi for k, lo, hi in ranges(chunk)):
                episode_ranks.append(rank)
                if item.get('context_error'):
                    context_errors.append(dict(rank=rank, chunk_id=chunk['chunk_id'], error=item['context_error']))
            if reason_in(ranges(chunk)):
                direct.append(rank)
            if window and not item.get('context_error'):
                if reason_in(ranges(window)):
                    expanded.append(rank)
                if covered(scope, ranges(window)):
                    complete.append(rank)
        if evidence := item['evidence']:
            confirmations.append(dict(rank=rank, chunk_id=chunk['chunk_id'], evidence=evidence,
                                      window_parts=window['parts'], page_id=window['page_id'],
                                      revision_id=window['revision_id'], source_sha256=window['source_sha256'],
                                      source_url=window['source_url'],
                                      matches_gold=same_source(chunk) and any(
                                          quote in evidence['detail']['quote'] for quote in gold)))

    visible = []
    for rank, hit in enumerate(result['hits'], 1):
        chunks = [hit['chunk'], *hit['context_chunks']]
        if any(same_source(chunk) for chunk in chunks) and reason_in(
                [span for chunk in chunks if same_source(chunk) for span in ranges(chunk)]):
            visible.append(rank)
    return dict(id=case['id'], policy=case['policy'], query=case['query'],
                form_ms=form['form_ms'], backend_ms=result['elapsed_ms'], top5=form['rendered'][:5],
                in_pool_episode=episode_ranks, direct_reason=direct, current_card_reason=visible,
                expanded_reason=expanded, full_dialogue=complete, confirmations=confirmations,
                episode_context_errors=context_errors,
                current_flags=[dict(rank=i, chunk_id=hit['chunk']['chunk_id'])
                               for i, hit in enumerate(result['hits'], 1)
                               if hit['evidence_audit']['status'] == 'clues_present'])


def assess(episodes, out):
    report = []
    with punctuated_search.connect() as index, punctuated.connect() as library:
        for episode in episodes:
            for case in episode['cases']:
                response = json.loads((out / (case['id'] + '.json')).read_text(encoding='utf-8'))
                assert response['result']['query'] == case['query']
                started = time.perf_counter()
                annotated = automatic_evidence(index, library, response['result'])
                offline_ms = round((time.perf_counter() - started) * 1000)
                row = score(index, episode, case, response, annotated)
                row['offline_ms'] = offline_ms
                report.append(row)
                print(row['id'], round(row['form_ms']), 'ms',
                      {key: (row[key] or [None])[0] for key in (
                          'in_pool_episode', 'direct_reason', 'current_card_reason', 'expanded_reason', 'full_dialogue')},
                      'flags=', [(item['rank'], item['matches_gold']) for item in row['confirmations']], flush=True)
    write_json(out / 'summary.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('collect', 'assess'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(DATA) or out == DATA:
        parser.error('--out must be a directory under src/data')
    episodes = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if args.action == 'collect':
        collect(episodes, out)
    else:
        assess(episodes, out)


if __name__ == '__main__':
    main()
