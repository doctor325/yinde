"""Parse spoken questions about policy choices and recall their source episodes."""
import re

from search import event_attachment as A, zh
from search.biography_scope import named_biographies


_HAN = r'[\u3400-\u9fff]'
_BACKGROUND = re.compile(rf'(?P<background>{_HAN}{{2,24}}?(?:[后後]|之际|时))[，,]\s*')
_QUESTION = re.compile(rf'(?P<person>{_HAN}{{2,5}}?)(?:为什么|为何)(?P<action>.+)')
_ATTACK = r'(?:出兵|派兵)?(?:攻打|征讨|征伐|讨伐|进攻|打)'
_DISSUADE = re.compile(rf'劝(?P<recipient>{_HAN}{{2,5}}?)(?:别|不要|不必|暂缓){_ATTACK}(?P<target>{_HAN}{{2,10}})')
_EXPEDITE = re.compile(
    rf'(?:觉得应该|想|建议|主张)(?:趁早|尽早|尽快){_ATTACK}(?P<target>{_HAN}{{2,10}})'
    rf'(?:[，,](?:不能|别|不该)(?:再)?(?:拖到|等到)(?P<deadline>{_HAN}{{2,8}}))?'
)
_MOVE = re.compile(
    r'(?:不愿(?:意)?|反对|不赞成)(?:把)?(?:朝廷|京城|都城)'
    r'(?:搬到|迁到|迁往)(?P<target>南方|南边|北方|北边)'
)
_TAX = re.compile(
    rf'(?:觉得|认为)(?:{_HAN}{{1,6}}?原来(?:的)?)?'
    r'(?:旧的)?(?:收税|征税)(?:办法|法子|制度)(?:得|该|需要|应该)改'
)
_FAMILY_ACTION = r'(?:不让儿子当官|反对儿子受荐|拒绝让儿子当官)'
_HYPOTHESIS = re.compile(rf'(?P<person>{_HAN}{{2,5}}?)(?P<action>{_FAMILY_ACTION})[，,]\s*'
                         rf'(?:是因为(?P<reason>{_HAN}{{2,40}})吗|是不是因为(?P<check>{_HAN}{{2,40}}))')
_MILITARY_SOURCE = r'出兵|发兵|兴兵|动兵|击|伐|攻|讨|征'
_TAX_TOPIC = r'赋税|课税|租庸|新旧税|税法|赋敛|科敛|赋役'
_SOURCE_ACTIONS = {
    'dissuade_attack': (r'谏|难[于於]动兵|从其言而止|不可.{0,6}(?:出兵|兴师)',
                        r'边郡困乏|难[于於]动兵|无名|侵边|犯[于於]边境|不宜|不可'),
    'expedite_attack': (r'请|可.{0,5}(?:伐|讨)|若今中止|纵敌|待明年|须[后後]年',
                        r'明年|[后後]年|大计|若今|恐|纵敌|形颇露'),
    'oppose_relocation': (r'反对|不从|所斥|不可|可斩|守议', r'根本|一动则|不可|守议'),
    'reform_tax': (r'请.{0,5}(?:税法|税制)|税法.{0,4}以一|更定.{0,5}(?:赋|税)',
                   r'弊|敝|重敛|苛|残瘁|仍积'),
    'decline_family_appointment': (r'荐.{0,5}(?:其子|臣子|子)|以子.{0,5}功',
                                   r'不敢|不受|辞|私恩|公议'),
}


def parse(text):
    text = text.strip().rstrip('？?').rstrip()
    background = _BACKGROUND.match(text)
    if background:
        text = text[background.end():]
    context = background['background'] if background else None
    if hypothesis := _HYPOTHESIS.fullmatch(text):
        return dict(person=hypothesis['person'], action=hypothesis['action'], kind='decline_family_appointment',
                    stance='拒绝', predicate='受荐任职', target='儿子', recipient=None,
                    background=context, deadline=None,
                    reason_hypothesis=hypothesis['reason'] or hypothesis['check'])
    if not (question := _QUESTION.fullmatch(text)):
        return None
    person, action = question.groups()
    frame = dict(person=person, action=action, recipient=None, deadline=None,
                 reason_hypothesis=None, background=context)
    if match := _DISSUADE.fullmatch(action):
        return dict(frame, kind='dissuade_attack', stance='劝止', predicate='出兵',
                    recipient=match['recipient'], target=match['target'])
    if match := _EXPEDITE.fullmatch(action):
        return dict(frame, kind='expedite_attack', stance='主张及早', predicate='出兵',
                    target=match['target'], deadline=match['deadline'])
    if match := _MOVE.fullmatch(action):
        return dict(frame, kind='oppose_relocation', stance='反对', predicate='迁都', target=match['target'])
    if _TAX.fullmatch(action):
        return dict(frame, kind='reform_tax', stance='主张', predicate='改革', target='税制')
    return None


def _topic(frame):
    kind, target = frame['kind'], frame['target']
    if kind in ('dissuade_attack', 'expedite_attack'):
        return r'东吴|吴' if target == '东吴' else re.escape(target)
    if kind == 'oppose_relocation':
        direction = target[0]
        return rf'{direction}迁|迁都.{0,4}{direction}|迁[于於]?.{{0,5}}{direction}'
    if kind == 'reform_tax':
        return _TAX_TOPIC
    return r'荐.{0,5}(?:其子|臣子|子)|以子.{0,5}功'


def terms(frame):
    """Literal terms for ordinary lexical features, independent of evidence."""
    return {
        'dissuade_attack': ('谏', '兴兵', '动兵', '出兵'),
        'expedite_attack': ('伐', '讨', '出兵', '兴兵'),
        'oppose_relocation': ('迁', '不可', '守议'),
        'reform_tax': ('赋敛', '科敛', '税法'),
        'decline_family_appointment': ('荐', '辞', '私恩'),
    }[frame['kind']]


def candidates(conn, frame, subject, book=None):
    topic = re.compile(_topic(frame))
    action, reason = map(re.compile, _SOURCE_ACTIONS[frame['kind']])
    military = frame['predicate'] == '出兵'
    ranked = []
    pages = scanned = 0
    for peers in named_biographies(conn, frame['person'], subject, book):
        pages += 1
        scanned += len(peers)
        texts = {cid: zh.to_simplified(chunk['text_orig']) for cid, chunk in peers.items()}
        for cid, text in texts.items():
            if not (action.search(text) and (not military or re.search(_MILITARY_SOURCE, text))):
                continue
            local_topic = bool(topic.search(text))
            if not local_topic and not any(topic.search(texts.get(n, '')) for n in (cid - 1, cid + 1)):
                continue
            ranked.append(((not bool(reason.search(text)), not local_topic, cid), dict(chunk=peers[cid])))
    ranked.sort(key=lambda item: item[0])
    return [hit for _, hit in ranked], dict(pages=pages, scanned=scanned)


def evidence(chunk, frame):
    """Attach an explicit narrator-stated defect and its corresponding reform."""
    if (frame['kind'] != 'reform_tax' or chunk['layer'] != 'body'
            or not (chunk.get('revision_id') and chunk.get('source_sha256'))):
        return None
    text = zh.to_simplified(chunk['text_orig'])
    if len(text) != len(chunk['text_orig']):
        return None
    name = frame['person']
    names = [re.escape(name)]
    if zh.to_simplified(chunk['heading']).strip() == name:
        names += [re.escape(name[-2:]), re.escape(name[-1:])]
    actor = '|'.join(dict.fromkeys(names))
    pattern = re.compile(
        rf'(?P<detail>[^。！？；\n「」]{{3,55}}(?:困乏|凋敝|残瘁|仍积)[^。！？；\n「」]{{0,36}})。'
        rf'\s*(?:{actor})(?:疾|患|忧)其(?:敝|弊)[，,](?:乃|遂)'
        r'(?P<action>请(?:为|作|行)[「『]?(?:[\u3400-\u9fff]{1,6})税(?:制|法)[」』]?以一其(?:制|名))'
    )
    match = pattern.search(text)
    if not match:
        return None
    # Embedded book-title quotes may appear in the request itself.
    prefix = slice(match.start(), match.start('action'))
    if text[prefix] != A.narrative(text)[prefix]:
        return None
    return dict(slot='reason', kind='advocacy', attribution='narrator_explanation',
                event=A.excerpt(chunk, *match.span()), action=A.excerpt(chunk, *match.span('action')),
                detail=A.excerpt(chunk, *match.span('detail')))
