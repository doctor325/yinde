"""Verify directed advice and an event-time constraint with fixed source passages."""
from hashlib import sha256
import json

from search import evidence_pipeline, polity_evidence, punctuated, punctuated_search
from tests.evidence_acceptance.review_natural_questions import exact_link
from tests.evidence_acceptance.review_polity_evidence import DATA, FIXTURES, Sources
from tests.evidence_acceptance.run_search_evidence_review import submit_form, write_json


def fixed_hit(index, sources, chunk_id):
    chunk = sources.chunk(chunk_id)
    return dict(chunk=chunk, context_chunks=evidence_pipeline.context(index, chunk))


def verify(sources, hit, card, status, expected):
    if card['evidenceStatus'] != status:
        return False
    for label, reference in expected.items():
        attached = sources.check(hit['advice_evidence']['citations'][label])
        links = [link for link in card['evidenceLinks'] if link['label'] == label]
        assert any(exact_link(link, attached) for link in links)
        assert any(exact_link(link, reference) for link in links), (label, reference, attached)
    return True


def main():
    baseline = DATA / 'natural-advice-holdout-200106f'
    out = DATA / 'advice-speech-direction'
    raw = (out / 'holdout-frozen.json').read_bytes()
    assert sha256(raw).hexdigest() == '70ecafd07e492bf51ca5205194c437b57cfca4a00d6add68f2db70d77b998d82'
    holdout = json.loads(raw)
    original = json.loads((baseline / 'frozen.json').read_bytes())
    established = DATA / 'natural-advice-live-200106f'
    report = {}
    with punctuated.connect() as library, punctuated_search.connect() as index:
        sources = Sources(index, library, [])
        expected = original['evidence']['houhanshu']
        for case in original['cases']:
            saved = json.loads((baseline / (case['id'] + '-live.json')).read_bytes())['result']
            query = case['query']
            frame = evidence_pipeline.parse(query)['colloquial_advocacy']
            assert frame['background'] == '曹操打败刘备后'
            polity_evidence.review_natural_hits(index, frame, saved['hits'])
            replay, form = submit_form(None, query, replay=saved)
            hits = dict(zip(form['rendered'], zip(replay['hits'], form['hitCards'])))
            verified, card = hits[73734]
            citations = verified['advice_evidence']['citations']
            intro = expected['speaker']
            recipient = dict(intro, ranges=[[2, 11061, 11062]], quote='紹')
            sources.check(recipient)
            time = dict(expected['reason'], ranges=[[2, 11065, 11071]], quote='曹操既破劉備')
            sources.check(time)
            assert verify(sources, verified, card, case['expected'], {
                '所议行动': expected['event'], '所劝对象': recipient,
                '劝谏理由': expected['reason'], '战事先后依据': time,
            })
            for label in ('劝谏人', '所劝对象称谓依据', '完整引语'):
                reference = sources.check(citations[label])
                assert any(link['label'] == label and exact_link(link, reference) for link in card['evidenceLinks'])
            assert citations['劝谏人']['quote'] == '田豐' and '袁紹' in citations['所劝对象称谓依据']['quote']
            earlier = fixed_hit(index, sources, 73733)
            polity_evidence.review_natural_hits(index, frame, [earlier])
            assert 'advice_evidence' not in earlier, earlier
            if case['expected'] == 'different_roles':
                assert all(item['evidenceStatus'] != 'supported' for item in form['hitCards'])
            report[case['id']] = dict(rank=form['rendered'].index(73734) + 1, status=card['evidenceStatus'],
                                      reason=citations['劝谏理由']['quote'], earlier_accepted=False)
            write_json(out / (case['id'] + '-replay.json'), dict(result=replay, form=form))

        previous = json.loads((established / 'wang-paraphrase.json').read_bytes())['result']
        hit = fixed_hit(index, sources, 87211)
        frame = evidence_pipeline.parse(previous['query'])['colloquial_advocacy']
        polity_evidence.review_natural_hits(index, frame, [hit])
        wang = json.loads((FIXTURES / 'natural_question_blind6.json').read_bytes())['evidence']['jin-122']
        assert hit['advice_evidence']['status'] == 'supported'
        assert (sources.check(hit['advice_evidence']['citations']['劝谏理由'])['quote']
                == sources.pinned(wang['reason'])['quote'])

        for case in holdout['cases']:
            query = case['query']
            hit = fixed_hit(index, sources, holdout['chunk_id'])
            frame = evidence_pipeline.parse(query)['colloquial_advocacy']
            polity_evidence.review_natural_hits(index, frame, [hit])
            replay, form = submit_form(None, query, replay=dict(previous, hits=[hit], query=query))
            refs = holdout['citations']
            card = form['hitCards'][0]
            assert verify(sources, hit, card, case['status'], {
                '劝谏人': refs['speaker'], '所劝对象': refs['recipient'],
                '所议行动': refs['action'], '劝谏理由': refs['reason'],
            }), (query, card)
            evidence = hit['advice_evidence']
            assert '张欣泰劝崔慧景' in evidence['message']
            report[case['status']] = dict(query=query, status=card['evidenceStatus'],
                                          reason=evidence['citations']['劝谏理由']['quote'])
            write_json(out / ('holdout-' + case['status'] + '.json'), dict(result=replay, form=form))
    write_json(out / 'summary.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
