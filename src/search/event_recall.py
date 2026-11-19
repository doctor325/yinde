"""Admit dated executions and capture clauses with local name attribution."""
import json
import re

from search import event_attachment as A, zh


_CAPTURE_WINDOW = 8
_HAN = r'[\u3400-\u9fff]'
_INTRODUCTION = r'(?:^|[，。；\n]|[送遣迎召奉从助与王帝君])'
_NEXT_ACTION = r'(?=于|於|以|乃|遂|即|卽|欲|率|领|出|入|进|拜|授|归|败|战)'


def _capture_records(conn, plan, subject):
    name = plan['capture_event']['person']
    short = re.escape(name[-1])
    # Keep deployment data and personal name lists out of the rule. A full
    # name followed by a subject using its short form links the two locally.
    transition = re.compile(rf'{_INTRODUCTION}(?P<full>{_HAN}{{{len(name)-1}}}{short})'
                            rf'{_HAN}{{0,6}}。[ \t　]*{short}{_NEXT_ACTION}')
    mention = re.compile(rf'(?:^|[。，；！？：、\n]|[攻拒与说送迎])\s*{short}')
    for hit in subject:
        anchor = hit['chunk']
        if anchor['layer'] != 'body' or not (anchor.get('revision_id') and anchor.get('source_sha256')):
            continue
        original = anchor['text_orig']
        text = zh.to_simplified(original)
        if len(text) != len(original):
            continue
        links = list(transition.finditer(A.narrative(text)))
        link = next((m for m in links if m['full'] == name), None)
        if not link or any(m['full'] != name for m in links):
            continue
        proof = dict(A.excerpt(anchor, link.start('full'), link.end()), form=name[-1])
        cid = anchor['chunk_id']
        scope = tuple(anchor[key] for key in A.SOURCE_SCOPE)
        rows = conn.execute('SELECT payload FROM chunks WHERE book_id=? AND page_id=? '
                            'AND chunk_id BETWEEN ? AND ? ORDER BY chunk_id',
                            (anchor['book_id'], anchor['page_id'], cid + 1, cid + _CAPTURE_WINDOW))
        nearby = [anchor, *(json.loads(row[0]) for row in rows)]
        records = []
        gaps = 0
        previous = None
        for offset, chunk in enumerate(nearby):
            if chunk['chunk_id'] != cid + offset or tuple(chunk[key] for key in A.SOURCE_SCOPE) != scope:
                break
            narrative = A.narrative(zh.to_simplified(chunk['text_orig']))
            if any(m['full'] != name for m in transition.finditer(narrative)):
                break
            if offset and not mention.search(narrative):
                gaps += 1
                if gaps > 1:
                    break
            else:
                gaps = 0
                record = A.attach(chunk, plan, [previous] if previous else [], subject_anchor=proof)
                if record:
                    records.append((chunk, record))
            previous = chunk
        yield from records


def candidates(conn, plan, subject):
    execution = plan.get('execution_event', {})
    if plan.get('capture_event'):
        records = _capture_records(conn, plan, subject)
    elif execution.get('role') == 'patient' and execution['answer_slot'] == 'time':
        records = ((hit['chunk'], A.attach(hit['chunk'], plan, [])) for hit in subject)
    else:
        return [], {}
    hits, evidence = [], {}
    for chunk, record in records:
        cid = chunk['chunk_id']
        if record and cid not in evidence:
            evidence[cid] = record
            hits.append(dict(chunk=chunk))
    return hits, evidence
