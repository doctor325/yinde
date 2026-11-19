"""Local source-first review of who opposed a proposed expedition, and why."""
import hashlib
import json
from pathlib import Path

from search import evidence_pipeline as E, event_attachment as A, punctuated, punctuated_search
from search.speech_turns import extract, match
from tests.evidence_acceptance.review_memorial_scope import DATA, source_window
from tests.evidence_acceptance.run_search_evidence_review import write_json


def chunk_at(index, cid):
    row = index.execute('SELECT payload FROM chunks WHERE chunk_id=?', (cid,)).fetchone()
    return json.loads(row[0])


def reason_quotes(index, episode, window):
    """Read frozen gold from the local index using offsets and hashes only."""
    quotes = []
    for reference in episode['reason_sources']:
        chunk = chunk_at(index, reference['chunk_id'])
        assert all(chunk[key] == window[key] for key in A.SOURCE_SCOPE)
        start, end = reference['offset']
        quote = chunk['text_orig'][start:end]
        assert quote in window['text_orig']
        assert hashlib.sha256(quote.encode()).hexdigest() == reference['text_sha256']
        quotes.append(quote)
    return quotes


def main():
    manifest = Path(__file__).with_name('speech_turn_cases.json')
    episodes = json.loads(manifest.read_text(encoding='utf-8'))
    report = []
    with punctuated_search.connect() as index, punctuated.connect() as library:
        for episode in episodes:
            chunk = chunk_at(index, episode['anchor'])
            assert all(chunk[key] == episode[key] for key in ('page_id', 'revision_id', 'source_sha256'))
            window = source_window(library, chunk, *episode['scope'])
            raw = window['text_orig']
            assert hashlib.sha256(raw.encode()).hexdigest() == episode['window_sha256']
            gold = reason_quotes(index, episode, window)
            turns = extract(window, episode['names'], episode['targets'])
            for turn in turns:
                quote = turn['turn']
                for ref in [turn['action'], turn['speaker'], turn['recipient'], quote, *turn['reasons']]:
                    assert raw[ref['start']:ref['end']] == ref['quote']
                assert all(quote['start'] < ref['start'] < ref['end'] <= quote['end'] for ref in turn['reasons'])
            for case in episode['cases']:
                plan = E.parse(case['query'])
                baseline = E.audit(window, plan, ())
                attachment = match(plan, turns)
                expected_reason = bool(attachment and any(phrase in attachment['detail']['quote'] for phrase in gold))
                positive = case['policy'] == 'answerable'
                report.append(dict(id=case['id'], expected=case['policy'], baseline=baseline['status'],
                                   pass_=bool(expected_reason if positive else not attachment),
                                   attachment=attachment,
                                   turns=[{k: turn[k] for k in ('person', 'addressee', 'target')} for turn in turns]))
    write_json(DATA / 'speech-turn-review.json', report)
    for row in report:
        print(row['id'], row['baseline'], row['pass_'], row['turns'],
              row['attachment']['detail']['quote'] if row['attachment'] else '')
    assert report[0]['pass_'], 'Development positive needs the pinned reason'
    assert all(row['pass_'] for row in report if row['expected'] != 'answerable'), 'False confirmation'


if __name__ == '__main__':
    main()
