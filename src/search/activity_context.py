"""Recall and focus narration of activity concurrent with an event."""
import re

from search import event_attachment, punctuated_relevance as R, punctuated_search as S, zh
from search.biography_scope import named_biographies


_PERSON = r'[\u3400-\u9fff]{2,6}'
_TRIGGER = r'[^。！？?!，,；;\n]{2,40}?'
_WHEN = r'(?:的时候|那会儿|那阵子|之际|当口|时)'
_ASK = r'(?:当时)?(?:正?在|正)?(?P<asked_activity>做什么|干什么|忙什么)'
_QUERY = re.compile(
    rf'(?P<person>{_PERSON}?)(?:在)?(?:接到|收到|听到|听闻|得知)'
    rf'(?P<trigger>{_TRIGGER}){_WHEN}[，,]?\s*{_ASK}'
)
_EVENT_FIRST = re.compile(rf'(?P<trigger>{_TRIGGER}){_WHEN}[，,]\s*(?P<person>{_PERSON}?){_ASK}')
_PERSON_FIRST = re.compile(rf'(?P<person>{_PERSON}?)(?:刚)?在(?P<trigger>{_TRIGGER}){_WHEN}[，,]?\s*{_ASK}')
_TRIGGER_ACTION = re.compile(r'(?P<anchor>[\u3400-\u9fff]{2,16}?)(?P<action>传来捷报|传来胜讯|告捷|报捷|打起来|开打|交战|起兵|举兵)')
_RECEIPT = re.compile(
    r'(?:驿书|军书|军报|捷报|战报|书报)(?:至|来|到|入)|'
    r'(?:得|接|览|闻)(?:军书|军报|捷报|战报)'
)
_UNREST = re.compile(r'(?:军|营)中[夜忽大]{0,2}(?:惊|乱|骚动)')
_VICTORY_REPORT = re.compile(r'(?:既破|已破|破之|大破|大捷|获胜)[^。！？；\n]{0,32}?(?:'+_RECEIPT.pattern+r')|报捷|献捷|告捷')
_CONFLICT = re.compile(r'战|攻|击|杀|拒')
_MUSTER = re.compile(r'起义兵|起兵|举义兵|举兵|募兵|率兵|发兵')
_SENTENCE = re.compile(r'[^。！？；\n]+[。！？；]?')


def parse(query):
    text = query.strip().rstrip('？?').rstrip()
    if match := _QUERY.fullmatch(text):
        trigger = match['trigger']
        if re.search(r'报[告书讯]|战报|军报|捷报|消息|来信', trigger):
            return dict(**match.groupdict(), kind='receipt')
        if re.search(r'(?:军|营)中.{0,6}(?:骚乱|惊乱|营啸|哗变)', trigger):
            return dict(**match.groupdict(), kind='unrest')
    match = _EVENT_FIRST.fullmatch(text) or _PERSON_FIRST.fullmatch(text)
    if match and (event := _TRIGGER_ACTION.fullmatch(match['trigger'])):
        action = event['action']
        if action in ('传来捷报', '传来胜讯', '告捷', '报捷'):
            kind = 'receipt'
        elif action in ('打起来', '开打', '交战'):
            kind = 'conflict'
        else:
            kind = 'muster'
        return dict(**match.groupdict(), kind=kind, anchor=event['anchor'], trigger_action=action)
    return None


def candidates(conn, frame, subject, identity, book=None):
    """Find body candidates that have both a person basis and a trigger clue."""
    person = frame['person']
    anchor = frame['anchor']
    kind = frame['kind']
    trigger = {'receipt': _VICTORY_REPORT, 'conflict': _CONFLICT, 'muster': _MUSTER}[kind]
    titles = {entry['aliases'][0] for entry in identity}
    scoped_titles = {title[1:]: R.DYNASTY_BOOKS.get(title[0], ()) for title in titles}
    pool = {hit['chunk']['chunk_id']: hit['chunk'] for hit in subject}
    for hit in S.lexical(conn, anchor, book, page_size=100)['hits']:
        chunk = hit['chunk']
        pool.setdefault(chunk['chunk_id'], chunk)
    scoped_ids = set()
    if kind == 'receipt' or any(zh.to_simplified(c['heading']).strip() == person for c in pool.values()):
        for peers in named_biographies(conn, person, subject, book):
            scoped_ids.update(peers)
            pool.update(peers)

    ranked = []
    for cid, chunk in pool.items():
        if chunk['layer'] != 'body' or S.normalize(chunk['heading']) == '校勘记':
            continue
        prose = event_attachment.narrative(zh.to_simplified(chunk['text_orig']))
        direct_name = person in prose or any(title in prose for title in titles)
        if not (direct_name or cid in scoped_ids or any(
            S.normalize(chunk['book_title']) in books and re.search(r'(?:^|[，。；\n]|是时|时)'+re.escape(title), prose)
            for title, books in scoped_titles.items()
        )):
            continue
        anchored = any(trigger.search(prose[max(0, start.start()-80):start.end()+80])
                       for start in re.finditer(re.escape(anchor), prose))
        # Relax the literal event anchor only within a pinned biography, and
        # only when a victory dispatch and the person's activity share a span.
        scoped_report = (kind == 'receipt' and cid in scoped_ids and trigger.search(prose)
                         and focus(chunk, frame))
        if anchored or scoped_report:
            ranked.append(((not anchored, not direct_name, cid), dict(chunk=chunk)))
    ranked.sort(key=lambda item: item[0])
    return [hit for _, hit in ranked]


def focus(chunk, frame):
    """Keep complete clauses and original offsets for the reranker's input."""
    trigger = {'receipt': _RECEIPT, 'unrest': _UNREST}.get(frame['kind'])
    if trigger is None or chunk['layer'] != 'body':
        return None
    original = chunk['text_orig']
    text = zh.to_simplified(original)
    if len(text) != len(original):
        return None
    narration = event_attachment.narrative(text)
    person = frame['person']
    forms = [person]
    heading = zh.to_simplified(chunk.get('heading', '')).split()
    short = person[1:]
    # A biography heading can suggest a local short form for ranking.
    if len(person) <= 3 and heading and heading[-1] in (person, short):
        forms.append(short)
    names = '|'.join(map(re.escape, forms))
    action = re.compile(rf'(?:^|[，,。])\s*(?:{names})(?=[\u3400-\u9fff])')
    sentences = list(_SENTENCE.finditer(narration))
    for i, sentence in enumerate(sentences):
        if 'anchor' in frame and not _VICTORY_REPORT.search(sentence.group()):
            continue
        for cue in trigger.finditer(sentence.group()):
            after = sentence.start() + cue.end()
            end = sentence.end()
            if not action.search(narration[after:end]):
                if i + 1 == len(sentences) or sentences[i + 1].start() != end:
                    continue
                end = sentences[i + 1].end()
                if not action.search(narration[after:min(end, after+48)]):
                    continue
            start = sentence.start()
            return dict(start=start, end=end, text=original[start:end],
                        selected=True, activity_context=True)
    return None
