"""Compare fixed historic search results with an offline memorial reader."""
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs

from search import colloquial_advocacy as C, evidence_pipeline as E
from search import event_attachment as A, evidence_context, punctuated, punctuated_search, zh
from tests.evidence_acceptance.run_search_evidence_review import read_holdout, write_json


DATA = Path(__file__).resolve().parents[2] / 'data'
_INTRO = re.compile(r'(?P<speaker>[\u3400-\u9fff]{2,5})(?:顾|谓)[^，。：「]{1,12}曰：「(?P<preface>[^「」]+)」'
                    r'(?:乃|遂)(?:上疏|上书|上表)(?:谏)?曰：')
_RETURN = re.compile(r'(?:^|\n)\s*(?:上|帝)得(?:疏|书|表)')
_REQUEST = re.compile(r'臣(?:愿|请|乞)[^。！？\n]{0,55}(?:罢|止|息|休)[^，。！？\n]{2,30}(?=[，。])')
_REASON = re.compile(r'(?:^|(?<=[。！？]))\s*(?P<reason>[^。！？\n]{0,100}'
                     r'今(?:无|非|未有)[^。！？\n]{1,20}[，,]而[^。！？\n]{3,70}(?:乎|邪|哉)[？?])'
                     r'\s*$')
_OTHER_VOICE = re.compile(r'(?:曰|云)[：:]')
_QUOTED = re.compile(r'\s*[「『]')


def attributed_memorial(window, plan, ruler_title):
    """Test the explicit handoff from a named speaker's remarks to his memorial.

    The ruler's identity is a separately checked source annotation in this
    experiment. The reader still has to bind it to the recipient of the speech.
    """
    frame = plan.get('colloquial_advocacy') or {}
    if frame.get('kind') != 'dissuade_attack' or not frame['target']:
        return None
    text = zh.to_simplified(window['text_orig'])
    if len(text) != len(window['text_orig']) or not evidence_context.quotes_balanced(text):
        return None
    heading = zh.to_simplified(window['heading'])
    if heading != frame['person'] or ruler_title not in C._recipient_forms(window, frame['recipient']):
        return None
    outside_quotes = A.narrative(text)
    for intro in _INTRO.finditer(text):
        if intro['speaker'] not in (heading, heading[1:]):
            continue
        if text[intro.start()] != outside_quotes[intro.start()]:
            continue
        target = re.escape(frame['target'])
        if not re.search(rf'(?:讨|征|伐|击){target}[^。！？]{{0,12}}(?:未止|未已)', intro['preface']):
            continue
        if not (end := _RETURN.search(text, intro.end())):
            continue
        original_body = text[intro.end():end.start()]
        body = A.narrative(original_body)
        if any(not _QUOTED.match(original_body, marker.end()) for marker in _OTHER_VOICE.finditer(body)):
            continue
        recipient = re.search(r'陛下', body)
        requests = list(_REQUEST.finditer(body))
        if not recipient or len(requests) != 1:
            continue
        action = requests[0]
        reason = _REASON.search(body[:action.start()])
        if not reason:
            continue
        detail_start, detail_end = reason.span('reason')
        if not re.search(rf'(?:使|若){target}', reason['reason']):
            # A demonstrative may explicitly refer to conditions in the previous sentence.
            if not re.match(r'今无是[二三四五六七八九]者', reason['reason']):
                continue
            conditions = re.search(rf'(?P<conditions>(?:使|若){target}[^。！？\n]+。)\s*$', body[:reason.start()])
            if not conditions:
                continue
            detail_start = conditions.start('conditions')

        def locate(span, base=0):
            return A.excerpt(window, base + span[0], base + span[1])

        detail = locate((detail_start, detail_end), intro.end())
        return dict(slot='reason', kind='advocacy', attribution='speaker_explanation',
                    support='dissuasion', event=detail, detail=detail,
                    action=locate(action.span(), intro.end()),
                    speaker=locate(intro.span('speaker')),
                    recipient=locate(recipient.span(), intro.end()))
    return None


def source_window(library, chunk, start, end):
    page = dict(library.execute('SELECT * FROM chapters WHERE page_id=?', (chunk['page_id'],)).fetchone())
    assert all(page[key] == chunk[key] for key in evidence_context.IDENTITY)
    part, = chunk['parts']
    block = part['block_index']
    original = json.loads(page['blocks_json'])[block]['text']
    assert original[part['start']:part['end']] == chunk['text_orig']
    assert 0 <= start < end <= len(original) and start < part['end'] and part['start'] < end
    return dict(chunk, text_orig=original[start:end], parts=[dict(block_index=block, start=start, end=end)])


def replay(result, chapter):
    command = ['node', str(Path(__file__).with_name('submit_search.mjs'))]
    process = subprocess.run(command, input=json.dumps(dict(query=result['query'], replay=result)),
                             capture_output=True, encoding='utf-8', timeout=20, check=True)
    rendered = json.loads(process.stdout)
    links, = rendered['evidenceLinks']
    assert [link['label'] for link in links] == ['发言人', '受议者', '所议行动', '理由']
    evidence = result['hits'][0]['evidence_audit']['attached_evidence'][0]
    for key, link in zip(('speaker', 'recipient', 'action', 'detail'), links, strict=True):
        route, params = html.unescape(link['href']).split('?', 1)
        params = {k: v[0] for k, v in parse_qs(params).items()}
        assert int(route.rsplit('/', 1)[1]) == chapter['page_id']
        assert int(params['rev']) == chapter['revision_id'] and params['sha'] == chapter['source_sha256']
        start, end, block = (int(params[k]) for k in ('start', 'end', 'block'))
        assert chapter['blocks'][block]['text'][start:end] == evidence[key]['quote']
    return rendered['rendered'][:5], links


def main():
    historic = DATA / 'speech-attribution-final-review'
    frozen = json.loads((historic / 'fang-xuanling-advises.json').read_text(encoding='utf-8'))
    baseline = json.loads((DATA / 'memorial-scope-baseline.json').read_text(encoding='utf-8'))
    holdout = DATA / 'memorial-scope-holdout.json'
    assert hashlib.sha256(holdout.read_bytes()).hexdigest() == (
        'c05fc03192f5b62b303b2af0831cb7569c7817f9204ea32cd65105cc73f324a0')
    result = frozen['result']
    hit = result['hits'][0]
    anchor = hit['chunk']
    report = {}
    with punctuated_search.connect() as index, punctuated.connect() as library:
        development, _ = read_holdout(index, DATA / 'speech-attribution-holdout.json')
        for scope in ('adjacent', 'memorial_with_speaker'):
            _, start, end = baseline[scope]['source_ranges'][0]
            window = source_window(library, anchor, start, end)
            if scope == 'memorial_with_speaker':
                identity_start = baseline['title_mentions'][-1]
                identity = source_window(library, anchor, identity_start, end)
                assert identity['text_orig'].startswith('太宗以燉煌公徇渭北')
            report[scope] = {}
            for case in development:
                plan = E.parse(case['query'])
                # Same ruler identity and source for the positive and reversed queries.
                attachment = attributed_memorial(window, plan, '太宗')
                audit = E.audit(window, plan, (), attachment=attachment)
                report[scope][case['id']] = dict(status=audit['status'], attachment=attachment)
                confirmed = scope == 'memorial_with_speaker' and case['policy'] == 'answerable'
                assert (audit['status'] == 'clues_present') == confirmed, report[scope]
                if confirmed:
                    assert '今無是三者' in attachment['detail']['quote'] and '所損大乎' in attachment['detail']['quote']
                    assert '罷應募之眾' in attachment['action']['quote']
                    hit.update(chunk=window, evidence_audit=audit, context_chunks=[])

        # Open the previously pinned cases only after evaluating the experiment on development.
        independent, _ = read_holdout(index, holdout)
        row = index.execute('SELECT payload FROM chunks WHERE chunk_id=?', (44067,)).fetchone()
        source = source_window(library, json.loads(row[0]), 175, 259)
        assert source['text_orig'].startswith('太祖將北討，慶之諫曰：')
        assert source['text_orig'].endswith('難以得志。」')
        report['holdout'] = {}
        for case in independent:
            plan = E.parse(case['query'])
            attachment = attributed_memorial(source, plan, '太祖')
            audit = E.audit(source, plan, (), attachment=attachment)
            report['holdout'][case['id']] = dict(status=audit['status'], attachment=attachment)
            if case['policy'] == 'reversed_roles':
                assert audit['status'] == 'insufficient'

    top5, links = replay(result, punctuated.chapter(anchor['page_id']))
    assert top5 == baseline['frozen_rank'][0]
    report.update(top5=top5, verified_links=links)
    path = DATA / 'memorial-scope-review.json'
    write_json(path, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
