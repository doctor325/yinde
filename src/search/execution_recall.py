"""Parse and retrieve execution events without treating a lead as evidence."""
import json
import re

from search import punctuated_search as S, zh


_PERSON = r'[\u3400-\u9fff]{2,4}'
_DETAILS = {
    'time': ('什么时候', '什么时间', '哪一年', '哪年', '何年', '何时'),
    'place': ('在哪里', '在何处', '哪里', '何处'),
    'reason': ('为什么', '为何'),
}
_ASK = '|'.join(word for words in _DETAILS.values() for word in words)
_EXECUTION = r'处死|处决|诛杀|斩首|赐死|问斩'
_PATIENT = re.compile(
    rf'(?P<person>{_PERSON}?)(?:(?P<before>{_ASK})(?P<action>被(?:{_EXECUTION}))'
    rf'|(?P<post_action>被(?:{_EXECUTION}))的?(?P<after>{_ASK}))'
)
_AGENT = re.compile(
    rf'(?P<person>{_PERSON}?)(?:在?(?P<prior_action>攻破|攻克|攻取|攻陷)'
    r'(?P<place>[\u3400-\u9fff]{2,12}?)(?:之|以)?[后後][，,]?)?'
    rf'(?P<action>{_EXECUTION}|斩杀)了?(?:谁|何人|哪位[\u3400-\u9fff]{{0,5}})'
)
_TERMS = {
    'patient': ('坐诛', '伏诛', '见诛', '被诛', '诛死', '赐死', '弃市', '处死',
                '斩首', '磔', '伏法', '坐斩'),
    'agent': ('诛夷', '族诛', '夷三族', '诛', '斩', '杀', '弃市'),
}


def parse(text):
    text = text.strip().rstrip('？?').rstrip()
    if match := _PATIENT.fullmatch(text):
        ask = match['before'] or match['after']
        slot = next(detail for detail, words in _DETAILS.items() if ask in words)
        return dict(person=match['person'], role='patient',
                    action=match['action'] or match['post_action'], answer_slot=slot,
                    preceding_event=None)
    if match := _AGENT.fullmatch(text):
        preceding = (dict(action=match['prior_action'], object=match['place'])
                     if match['prior_action'] else None)
        return dict(person=match['person'], role='agent', action=match['action'],
                    answer_slot='object', preceding_event=preceding)
    return None


def terms(frame):
    return _TERMS[frame['role']]


def candidates(conn, frame, subject, book=None):
    """Scan the matching body of a pinned biography, including short-name passages."""
    name = frame['person']
    pages = set()
    for hit in subject:
        chunk = hit['chunk']
        if zh.to_simplified(chunk['heading']).strip() == name and (not book or chunk['book_id'] == book):
            pages.add((chunk['book_id'], chunk['page_id']))
    if not pages:
        # A biography can use the short name from its opening to its last line.
        headings = (name, zh.to_traditional(name))
        sql = "SELECT DISTINCT book_id,page_id FROM chunks WHERE json_extract(payload,'$.heading') IN (?,?)"
        args = headings
        if book:
            sql += " AND book_id=?"
            args += (book,)
        pages = {tuple(row) for row in conn.execute(sql, args)}

    opening = re.compile(r'\s*(?:' + re.escape(name) + '|' + re.escape(name[-1]) + r')[，,]?\s*字')
    preceding = frame['preceding_event']
    place = S.normalize(preceding['object']) if preceding else ''
    expressions = terms(frame)
    ranked = []
    scanned = 0
    biographies = 0
    for book_id, page_id in sorted(pages):
        versions = {}
        for row in conn.execute(
            'SELECT payload FROM chunks WHERE book_id=? AND page_id=? ORDER BY chunk_id',
            (book_id, page_id),
        ):
            chunk = json.loads(row[0])
            if chunk['layer'] != 'body' or zh.to_simplified(chunk['heading']).strip() != name:
                continue
            if chunk.get('revision_id') and chunk.get('source_sha256'):
                versions.setdefault((chunk['revision_id'], chunk['source_sha256']), {})[chunk['chunk_id']] = chunk
        for peers in versions.values():
            if not opening.match(zh.to_simplified(next(iter(peers.values()))['text_orig'])):
                continue
            biographies += 1
            scanned += len(peers)
            texts = {cid: S.normalize(c['text_orig']) for cid, c in peers.items()}
            for cid, text in texts.items():
                matched = [term for term in expressions if term in text]
                if not matched:
                    continue
                nearby = (texts.get(cid - 1, ''), texts.get(cid + 1, ''))
                if place and place not in text and not any(place in t for t in nearby):
                    continue
                ranked.append(((bool(place and place not in text), -max(map(len, matched)), cid), peers[cid]))
    ranked.sort(key=lambda item: item[0])
    return [dict(chunk=c) for _, c in ranked], dict(pages=biographies, scanned=scanned)
