"""Question-independent source units and provenance-bound, resumable receipts."""
import json
from pathlib import Path
import uuid
from search import source_first_reader as S
from search.evidence_context import IDENTITY, quotes_balanced

VERSION = 'source-first-batches-v1'
ROOT = Path(__file__).resolve().parents[2]
BATCH_RULE = '\n本次只整理core_evidence编号中的事件；其余编号仅是相邻语境或身份依据，不另列事件。跨句的主语、对象和原因可引用邻句，但每个事件必须引用至少一个核心编号。核心事件不能确定时保留未知，不以邻句的其他事件替代。'


def contiguous(left, right):
    a, b = left['parts'][-1], right['parts'][0]
    return (a['block_index'] == b['block_index'] and a['end'] == b['start'] or
            a['block_index'] + 1 == b['block_index'] and b['start'] == 0)


def plan(evidence, source, max_chars=512):
    if not all(source.get(k) is not None and source.get(k) != '' for k in IDENTITY):
        raise ValueError('Missing source edition/revision identity')
    S.source_payload(evidence)  # Checks unique IDs/roles without accepting a query.
    events = [e for e in evidence if e['role'] == 'event_context']
    opening = [e for e in evidence if e['role'] == 'opening_context']
    identity = {k: source[k] for k in IDENTITY}
    tasks = []
    for i, core in enumerate(events):
        neighbours = []
        if i and contiguous(events[i-1], core):
            neighbours.append(events[i-1])
        if i + 1 < len(events) and contiguous(core, events[i+1]):
            neighbours.append(events[i+1])
        included = {e['id'] for e in [core, *neighbours, *opening]}
        selected = [e for e in evidence if e['id'] in included]
        blocked = []
        if any(not e['complete'] or not quotes_balanced(e['text']) for e in selected):
            blocked.append('incomplete_or_unbalanced_unit')
        if sum(len(e['text']) for e in selected) > max_chars:
            blocked.append('intact_unit_exceeds_budget')
        tasks.append(dict(version=VERSION, source=identity, parent_sha256=S.digest(evidence),
            core_ids=[core['id']], evidence=selected, blocked=blocked,
            omitted_ids=[e['id'] for e in evidence if e['id'] not in included],
            context_limit='one adjacent numbered unit each side; long-distance dependencies unresolved'))
    return tasks


def request(task):
    value = S.extraction_request(task['evidence'])
    value['messages'][0]['content'] += BATCH_RULE
    payload = S.source_payload(task['evidence'])
    payload['core_evidence'] = task['core_ids']
    value['messages'][1]['content'] = S.N.encode(payload)
    return value


def key(task, execution):
    return S.digest(dict(version=VERSION, task=task, execution=execution, request=request(task)))


def cache_path(folder, task, execution):
    folder = Path(folder).resolve()
    if not folder.is_relative_to(ROOT):
        raise ValueError('Batch cache must stay inside repository')
    path = folder / (key(task, execution) + '.json')
    if not path.resolve().is_relative_to(ROOT):
        raise ValueError('Batch receipt escaped repository')
    return path


def cached(folder, task, execution):
    path = cache_path(folder, task, execution)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding='utf8'))
    result = record['result']
    if (record['key'] != key(task, execution) or record['result_sha256'] != S.digest(result)
            or result['source_sha256'] != S.digest(task['evidence'])
            or result['request_sha256'] != S.digest(request(task))):
        raise ValueError('Batch receipt integrity failure; no automatic rerun')
    if result['completed'] and not S.N.conforms(result.get('output'), S.schema([e['id'] for e in task['evidence']])):
        raise ValueError('Invalid cached event ledger')
    return result  # Failed attempts are retained too; never silently retried.


def run_one(reader, task, execution, folder):
    if task['blocked']:
        raise ValueError('Blocked source unit; no extraction call')
    old = cached(folder, task, execution)
    if old is not None:
        return old, True
    result = S._extract_request(reader, task['evidence'], request(task))
    if result['completed'] and any(not set(e['references']).intersection(task['core_ids'])
                                    for e in result['output']['events']):
        result.update(completed=False, error=dict(type='CoreReferenceError',
            message='Neighbour-only event returned; not accepted as core extraction'))
    path = cache_path(folder, task, execution)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(dict(key=key(task, execution), result=result,
        result_sha256=S.digest(result)), ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    temporary.replace(path)
    return result, False


def progress(tasks, execution, folder):
    summary = {k: [] for k in ('response_complete', 'failed', 'blocked', 'pending')}
    for task in tasks:
        old = None if task['blocked'] else cached(folder, task, execution)
        state = ('blocked' if task['blocked'] else 'pending' if old is None else
                 'response_complete' if old['completed'] else 'failed')
        summary[state].extend(task['core_ids'])
    return dict(**summary, all_responses_complete=not any(summary[k] for k in ('failed','blocked','pending')),
                semantic_verified=False, note='Response completion is not event completeness or semantic accuracy')
