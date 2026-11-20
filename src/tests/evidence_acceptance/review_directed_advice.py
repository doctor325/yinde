"""Verify directed advice and an event-time constraint with fixed source passages."""
import argparse
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


def review_time_paraphrase():
    out = DATA / 'advice-speech-direction' / 'time-paraphrase-aa1f825'
    raw = (out / 'first-search.json').read_bytes()
    assert sha256(raw).hexdigest() == '5b0943d5e48b227776fad9fe658a76d3cffa66b002437217df5ad852a13649bb'
    first = json.loads(raw)
    criteria = json.loads((out / 'criteria.json').read_bytes())
    query = criteria['query']
    earlier = criteria['earlier_candidate']
    later = criteria['later_candidate']
    original = first['form']['rendered']
    assert first['result']['query'] == query and original[:2] == [earlier, later]

    # A different set of names rules out recognizing the wording by a name pair.
    background = polity_evidence._AFTER_DEFEAT.fullmatch('李乙战败之后')
    assert background
    match = polity_evidence._defeat_anchor
    assert match(background, '王甲既破李乙，今不可轻进。', '王甲出兵。')
    assert not match(background, '若王甲既破李乙，则可进兵。', '王甲出兵。')
    assert not match(background, '王甲既破李乙，今不可轻进。', '他军出兵。')
    assert not match(background, '王甲正攻李乙，今不可轻进。', '王甲出兵。')

    cases = (
        ('paraphrase', query, 'supported'),
        ('roles-reversed', query.replace('田丰为什么劝袁绍', '袁绍为什么劝田丰'), 'different_roles'),
        ('other-loser', query.replace('刘备', '孙权'), None),
        ('other-winner', query.replace('刘备兵败之后', '孙权打败刘备之后'), None),
    )
    report = {}
    with punctuated.connect() as library, punctuated_search.connect() as index:
        sources = Sources(index, library, [])
        expected = {'劝谏理由': sources.check(criteria['reason']), '战事先后依据': sources.check(criteria['timing'])}
        for case, text, status in cases:
            saved = json.loads(raw)['result']
            frame = evidence_pipeline.parse(text)['colloquial_advocacy']
            assert frame['kind'] == 'delay_attack' and frame['target'] is None
            assert frame['background'] == text.split('，', 1)[0]
            assert (frame['person'], frame['recipient']) == (
                ('袁绍', '田丰') if case == 'roles-reversed' else ('田丰', '袁绍')
            )
            polity_evidence.review_natural_hits(index, frame, saved['hits'])
            result, form = submit_form(None, text, replay=dict(saved, query=text))
            assert form['rendered'] == original
            hits = dict(zip(original, zip(result['hits'], form['hitCards'])))
            assert hits[earlier][1]['evidenceStatus'] is None and 'advice_evidence' not in hits[earlier][0]
            hit, card = hits[later]
            assert card['evidenceStatus'] == status
            supported = [row for row in form['hitCards'] if row['evidenceStatus'] == 'supported']
            if status:
                assert verify(sources, hit, card, status, expected)
                assert '田丰劝袁绍' in hit['advice_evidence']['message']
            assert len(supported) == (1 if status == 'supported' else 0)
            report[case] = dict(query=text, earlier_status=hits[earlier][1]['evidenceStatus'],
                                later_status=card['evidenceStatus'], later_saved_rank=original.index(later)+1,
                                citations=hit.get('advice_evidence', {}).get('citations', {}))
            write_json(out / (case + '-replay.json'), dict(result=result, form=form))
    write_json(out / 'replay-summary.json', report)
    for case, item in report.items():
        print(f'{case}: earlier={item["earlier_status"]}, later={item["later_status"]}')


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--time-paraphrase', action='store_true', help='Replay the saved event-time paraphrase')
    if parser.parse_args().time_paraphrase:
        review_time_paraphrase()
    else:
        main()
