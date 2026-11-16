"""Offline-only packet preparation. No credentials, transport or inference.

Requests contain only question/parse/numbered source. Gold and source mapping
stay in a separate local audit. Preparing packets does not authorize sending.
"""
import argparse
import hashlib
import json
from pathlib import Path

from search import brief_evidence_diagnostic as B, numbered_evidence as N
from search import event_context_window as W, evidence_context as C
from search import punctuated as P, punctuated_search as S
from tests.context_reader.run import cases, source_check
from tests.context_reader.run_windowed import source_mapping_check

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1]
SCOPE = HERE / 'strong_reader_scope.json'
DEST = SRC / 'data/evidence-reader/strong-reader-pilot-v1'
QUESTION_KEYS = {'actor', 'action', 'object', 'requested', 'mode', 'proposed_answer'}


def digest(value):
    return hashlib.sha256(N.encode(value).encode('utf8')).hexdigest()


def make_request(scope, question, parsed, evidence):
    if set(parsed) != QUESTION_KEYS or not all(isinstance(v, str) for v in parsed.values()):
        raise ValueError('Question contract changed')
    if parsed['mode'] not in ('open', 'verify'):
        raise ValueError('Unknown question mode')
    if parsed['mode'] == 'open' and parsed['proposed_answer']:
        raise ValueError('Open question cannot supply an answer')
    if parsed['mode'] == 'verify' and (not parsed['proposed_answer'] or parsed['proposed_answer'] not in question):
        raise ValueError('Proposed claim must come from the question')
    ids = [e['id'] for e in evidence]
    if not ids or len(set(ids)) != len(ids) or any(not e['complete'] for e in evidence):
        raise ValueError('Empty, duplicate or incomplete evidence')
    payload = dict(original_question=question, parsed_question=parsed,
                   **N.evidence_payload(evidence))
    # Do not pass a case, gold, source metadata, config, or local path here.
    return dict(model=scope['model'], **scope['generation'], system=B.SYSTEM,
                messages=[dict(role='user', content=N.encode(payload))])


def reservation_usd(scope):
    limits = scope['limits']
    reserved_input = (limits['counted_input_tokens_per_request'] * limits['input_reserve_factor']
                      + limits['input_reserve_extra_tokens'])
    return round(limits['message_requests'] * (
        reserved_input * limits['input_usd_per_million']
        + scope['generation']['max_tokens'] * limits['output_usd_per_million']) / 1_000_000, 6)


def check_baseline(baseline, case, payload):
    row = next(r for r in baseline['rows'] if r['id'] == case['id'])
    if any(row[k] != case[k] for k in ('query', 'expected', 'expected_answer')):
        raise ValueError('Baseline question/gold drift')
    if digest(row['payload']) != row['payload_sha256'] or payload != row['payload']:
        raise ValueError('Baseline question/parse/source payload drift')
    if baseline['diagnostic_prompt_sha256'] != digest(B.SYSTEM):
        raise ValueError('Baseline prose prompt drift')


def gold_covered(evidence, gold):
    """Cover an exact source range across adjacent IDs, never across a gap."""
    parts = sorted((p for e in evidence if e['role'] == 'event_context'
                    for p in e['parts'] if p['block_index'] == gold['block_index']),
                   key=lambda p: (p['start'], p['end']))
    cursor, text = gold['start'], []
    for part in parts:
        if part['end'] <= cursor:
            continue
        if part['start'] > cursor:
            break
        end = min(part['end'], gold['end'])
        text.append(part['quote'][cursor - part['start']:end - part['start']])
        cursor = end
        if cursor == gold['end']:
            return ''.join(text) == gold['quote']
    return False


def prepare(destination=DEST):
    scope = json.loads(SCOPE.read_text(encoding='utf8'))
    dataset, frozen = cases()
    if frozen['sha256'] != scope['frozen_cases_sha256']:
        raise ValueError('Frozen gold changed')
    selected = scope['cases']
    if len(selected) != 8 or len({c['id'] for c in selected}) != 8:
        raise ValueError('Keep all eight frozen cases, including failures')
    if reservation_usd(scope) > scope['limits']['cost_ceiling_usd']:
        raise ValueError('Requested budget insufficient')
    baseline_path = (SRC / scope['baseline']).resolve()
    if not baseline_path.is_relative_to(SRC.resolve()):
        raise ValueError('Baseline must stay inside repository')
    baseline = json.loads(baseline_path.read_text(encoding='utf8'))
    receipt = dict(version=scope['version'], status='prepared_not_authorized_not_run',
                   scope_sha256=digest(scope), frozen_cases_sha256=frozen['sha256'],
                   prompt_sha256=digest(B.SYSTEM), selector_version=W.VERSION,
                   baseline_sha256=digest(baseline), reserved_usd=reservation_usd(scope),
                   default_search_changed=False, network_requests=0, model_calls=0, rows=[])
    requests, audits, contexts = [], [], {}
    by_id = {c['id']: c for c in dataset}
    with P.connect() as lib, S.connect() as index:
        for item in selected:
            case = by_id[item['id']]
            source_check(lib, case)
            key = case['anchor_chunk_id']
            if key not in contexts:
                found = index.execute('SELECT payload FROM chunks WHERE chunk_id=?', (key,)).fetchone()
                if not found:
                    raise ValueError('Frozen candidate missing: ' + case['id'])
                chunk = json.loads(found[0])
                if any(chunk[k] != case['source'][k] for k in C.IDENTITY):
                    raise ValueError('Candidate source drift')
                context = W.assemble(lib, chunk)
                blocks = json.loads(lib.execute('SELECT blocks_json FROM chapters WHERE page_id=?',
                                                (chunk['page_id'],)).fetchone()[0])
                evidence = source_mapping_check(context, blocks) if context['input_ready'] else []
                contexts[key] = context, evidence
            context, evidence = contexts[key]
            gold = case['source']
            covered = gold_covered(evidence, gold)
            row = dict(id=case['id'], split=item['split'], query=case['query'],
                       anchor_chunk_id=key, source={k: gold[k] for k in C.IDENTITY},
                       source_chars=context['source_chars'], units=len(evidence),
                       context_sha256=digest(context), gold_quote_retained=covered,
                       input_ready=context['input_ready'], input_reasons=context['input_reasons'],
                       review='pending_manual_semantic_sufficiency')
            if context['input_ready'] and covered:
                if item['parse_origin'] == 'frozen_4b_baseline':
                    parsed = next(r['payload']['parsed_question'] for r in baseline['rows'] if r['id'] == case['id'])
                else:
                    if case['split'] != 'holdout':
                        raise ValueError('Held-out selection changed split')
                    parsed = item['parsed_question']
                body = make_request(scope, case['query'], parsed, evidence)
                if item['parse_origin'] == 'frozen_4b_baseline':
                    check_baseline(baseline, case, json.loads(body['messages'][0]['content']))
                row['request_sha256'] = digest(body)
                row['input_characters'] = len(body['system']) + len(body['messages'][0]['content'])
                requests.append(dict(id=case['id'], body=body, sha256=digest(body)))
            else:
                row['not_prepared'] = 'Source window not ready; retain case, do not replace or trim to gold'
            audits.append(dict(case=case, context=context, evidence=evidence,
                               model_response=None, semantic_review=None))
            receipt['rows'].append(row)
    receipt['requests_sha256'] = digest(requests)
    receipt['audit_sha256'] = digest(audits)
    receipt['status'] = ('prepared_pending_manual_review_and_authorization' if len(requests) == len(selected)
                         else 'preparation_incomplete_not_authorized_not_run')
    destination = Path(destination).resolve()
    if not destination.is_relative_to((SRC / 'data').resolve()):
        raise ValueError('Artifacts must stay inside repository data')
    destination.mkdir(parents=True, exist_ok=False)  # Never overwrite frozen packets.
    for name, data in (('requests.json', requests), ('local-audit.json', audits), ('receipt.json', receipt)):
        (destination / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return int(len(requests) != len(selected))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEST,
                        help='New repository data directory; existing packets are never overwritten')
    raise SystemExit(prepare(parser.parse_args().output))
