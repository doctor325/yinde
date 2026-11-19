"""Recall explanations for an advocated action from a named biography."""
import re

from search import zh
from search.biography_scope import named_biographies


_QUESTION = re.compile(
    r'(?P<person>[\u3400-\u9fff]{2,4})(?:为什么|为何)'
    r'(?P<stance>坚持主张|坚持建议|主张|建议|坚持|提议|力主|力劝)'
    r'(?P<action>[\u3400-\u9fff]{2,20})'
)
_MILITARY = re.compile(r'(讨伐|征讨|征伐)([\u3400-\u9fff]{2,10})')
_RETAIN = re.compile(r'留下?(步兵|骑兵|士兵|部队|军队)([\u3400-\u9fff]{2,10})')
_FORCES = {
    '步兵': '步兵|步士|步卒|步军',
    '骑兵': '骑兵|骑士|骑卒|骑军',
    '士兵': '兵|卒|士', '部队': '兵|卒|军', '军队': '兵|卒|军',
}
_BOUNDARY = r'^|[。！？；，、：:「」\s]'
_STANCE = r'坚请|固请|力请|建请|复请|请|欲|议'
_SPEECH = re.compile(
    rf'(?:{_BOUNDARY})([\u3400-\u9fff]{{1,12}}?)(?:(?:上疏|疏|奏|对|谏)(?:曰[：:]?|[：:])|曰[：:])'
)
_REASON = re.compile(r'以|因|盖|恐|虑|惧|之(利|害)|有(利|害)|可免|可省')
TERMS = ('坚请', '固请', '建请', '上疏', '奏曰', '以为')


def parse(text):
    match = _QUESTION.fullmatch(text.strip().rstrip('？?').rstrip())
    if not match:
        return None
    person, stance, action = match.groups()
    topic = action.removeprefix('继续')
    predicate, target, participants = None, None, None
    if military := _MILITARY.fullmatch(topic):
        predicate, target = military.groups()
    elif retain := _RETAIN.fullmatch(topic):
        participants, topic = retain.groups()
    return dict(person=person, stance=stance, action=action, topic=topic,
                predicate=predicate, target=target, participants=participants)


def candidates(conn, frame, subject, book=None):
    name = frame['person']
    names = {name, name[-1:], name[-2:]}
    actor = '|'.join(map(re.escape, sorted(names, key=len, reverse=True)))
    mention = re.compile(rf'(?:{_BOUNDARY})(?:及|时)?(?:{actor})(?:以|因|谓|恐|虑)')
    topic = re.escape(frame['topic'])
    if frame['target']:
        target = re.escape(frame['target'])
        action = r'(?:征讨|征伐|讨伐|征|讨|伐)'
        topic = action + target
        # A dropped object is eligible only in a paragraph naming the campaign.
        proposal = rf'(?:{_STANCE})(?:{topic}|{action}(?=[。，；！？]|$))'
    elif frame['participants']:
        force = _FORCES[frame['participants']]
        topic = rf'留(?:下)?(?:{force})[^。！？；，]{{0,8}}{topic}'
        proposal = rf'(?:{_STANCE}|今){topic}'
    else:
        proposal = rf'(?:{_STANCE}){topic}'
    topic_pattern = re.compile(topic)
    proposal_pattern = re.compile(rf'(?<![不勿莫未坚固力建复])(?:{proposal})')
    ranked = []
    pages = scanned = 0
    for peers in named_biographies(conn, name, subject, book):
        pages += 1
        scanned += len(peers)
        texts = {cid: zh.to_simplified(chunk['text_orig']) for cid, chunk in peers.items()}
        for cid, text in texts.items():
            previous = texts.get(cid - 1, '')
            for paragraph in re.split(r'\n\s*\n', text):
                if not topic_pattern.search(paragraph) or not _REASON.search(paragraph):
                    continue
                proposal_hit = proposal_pattern.search(paragraph)
                if not proposal_hit:
                    continue
                speakers = list(_SPEECH.finditer(paragraph, 0, proposal_hit.start()))
                if not speakers and '臣' in paragraph:
                    speakers = list(_SPEECH.finditer(previous))
                    if speakers and '」' in previous[speakers[-1].end():]:
                        speakers = []
                if speakers:
                    if speakers[-1][1] not in names:
                        continue
                elif not mention.search(paragraph):
                    continue
                ranked.append(dict(chunk=peers[cid]))
                break
    return ranked, dict(pages=pages, scanned=scanned)
