"""Parse spoken questions about policy choices and recall their source episodes."""
import re

from search import event_attachment as A, zh
from search.biography_scope import named_biographies


_HAN = r'[\u3400-\u9fff]'
_BACKGROUND = re.compile(rf'(?P<background>{_HAN}{{2,24}}?(?:[后後]|之际|时))[，,]\s*')
_QUESTION = re.compile(rf'(?P<person>{_HAN}{{2,5}}?)(?:为什么|为何)(?P<action>.+)')
_ATTACK = r'(?:出兵|派兵)?(?:攻打|征讨|征伐|讨伐|进攻|打)'
_DISSUADE = re.compile(rf'劝(?P<recipient>{_HAN}{{2,5}}?)(?:别|不要|不必|暂缓)(?:再)?'
                       rf'(?:{_ATTACK}(?P<target>{_HAN}{{2,10}})|出兵|派兵)')
_HOLD_CITY = re.compile(
    rf'劝(?P<recipient>{_HAN}{{2,10}}?)(?:别|不要)在(?P<target>{_HAN}{{2,10}}?)'
    rf'(?:来攻|进攻)时(?:撤出|撤离|放弃)(?P<place>{_HAN}{{2,10}})'
)
_KEEP_CITY = re.compile(rf'(?:坚持|仍要|执意)?(?:留守|坚守|守住|守)(?P<place>{_HAN}{{2,8}})')
_BOTH_SIDES = re.compile(
    rf'(?P<person>{_HAN}{{2,5}}?)(?:和|与)(?:主张|想|要)(?:撤走|撤退|撤离)的人'
    r'[，,]?(?:各自|分别)(?:在)?(?:担心|顾虑)什么'
)
_DELAY_ATTACK = re.compile(
    rf'劝(?P<recipient>{_HAN}{{2,8}}?)(?:别急着|别急于|不要急于|不要贸然|先别)'
    rf'(?:{_ATTACK}(?P<target>{_HAN}{{2,10}})|进兵|出兵|派兵|进攻|动兵)'
)
_WITHDRAWAL_QUESTION = re.compile(
    rf'(?P<place>{_HAN}{{2,8}}?)(?:当时|那时)?(?:为什么|为何)(?:不能|不宜|不该)'
    rf'(?:撤离|撤退|撤走|撤)[？?，,。]\s*(?P<person>{_HAN}{{2,5}}?)(?:当时)?(?:是)?怎么说(?:的)?'
)
_RUSH_QUESTION = re.compile(
    rf'(?P<person>{_HAN}{{2,5}}?)(?:觉得|认为)(?P<recipient>{_HAN}{{2,5}}?)'
    r'(?:急着|急于|仓促)(?:动手|出兵|进兵|进攻)(?:有)?什么(?:问题|不妥|坏处)'
)
_MOVE_PEOPLE = re.compile(
    rf'(?P<recipient>{_HAN}{{2,6}}?)(?:想|要|打算)(?:把|将)(?P<origin>{_HAN}{{2,8}}?)'
    rf'(?:的)?(?:百姓|居民|民户|士家|人|民)(?:迁到|迁往|搬到)(?P<target>{_HAN}{{2,8}})'
    rf'[，,]\s*(?P<person>{_HAN}{{2,6}}?)(?:为什么|为何)(?:要)?(?:拦着|拦阻|反对|劝阻)'
)
_CEREMONY = re.compile(
    rf'(?:大家|群臣|百官)(?:都)?(?:建议|请)(?P<recipient>{_HAN}{{2,6}}?)'
    rf'(?:去|到)(?P<place>{_HAN}{{2,8}}?)封禅[，,]\s*(?P<person>{_HAN}{{2,6}}?)'
    r'(?:为什么|为何)(?:觉得|认为)?(?:还不是时候|时机未到|不该封禅|反对)'
)
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
_DIRECTIONAL = re.compile(
    rf'(?:^|(?<=[。；，,！？\n]))\s*(?P<proposal>{_HAN}{{1,8}}?(?:将|欲)(?P<action>[东西南北][征伐讨]))'
    rf'[。；，,]\s*(?P<speaker>{_HAN}{{1,5}}?)(?:固谏|进谏|谏)曰[：:]\s*(?=[「『])'
)
_NEW_EXPEDITION = re.compile(r'(?:[一二三四五六七八九十]+年|复更|更议|改议|又欲|复欲)')
_TAX_TOPIC = r'赋税|课税|租庸|新旧税|税法|赋敛|科敛|赋役'
_SOURCE_ACTIONS = {
    'dissuade_attack': (r'谏|难[于於]动兵|从其言而止|不可.{0,6}(?:出兵|兴师)',
                        r'边郡困乏|难[于於]动兵|无名|侵边|犯[于於]边境|不宜|不可'),
    'oppose_withdrawal': (r'欲[弃舍]|去留|(?:曰|谏曰)[：:]',
                          r'各自散走|士卒必散|去留|危亡|守城'),
    'delay_attack': (r'谏曰|不从', r'非利|不如|未可|不可轻'),
    'oppose_population_move': (r'(?:欲|将)(?:徙|迁)|曰[：:]', r'失民心|无以食|不宜徙|不可徙'),
    'oppose_ceremony': (r'封禅|曰[：:]', r'劳之|未可|无以供|未足以供|何以能给'),
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
    if pair := _BOTH_SIDES.fullmatch(text):
        return dict(person=pair['person'], action=text, kind='oppose_withdrawal',
                    stance='比较', predicate='弃城', target=None, recipient=None, place=None,
                    comparison=True, background=context, deadline=None, reason_hypothesis=None)
    common = dict(background=context, deadline=None, reason_hypothesis=None, stance='劝止')
    if question := _WITHDRAWAL_QUESTION.fullmatch(text):
        return dict(common, **question.groupdict(), action='撤城', kind='oppose_withdrawal',
                    predicate='弃城', target=None, recipient=None)
    if question := _RUSH_QUESTION.fullmatch(text):
        return dict(common, **question.groupdict(), action='仓促进兵', kind='delay_attack',
                    predicate='出兵', target=None)
    if question := _MOVE_PEOPLE.fullmatch(text):
        return dict(common, **question.groupdict(), action='迁徙人口', kind='oppose_population_move',
                    predicate='徙民')
    if question := _CEREMONY.fullmatch(text):
        roles = question.groupdict()
        if roles['recipient'] in ('皇帝', '天子'):
            roles['recipient'] = None
        return dict(common, **roles, action='封禅', kind='oppose_ceremony', predicate='封禅', target=None)
    if not (question := _QUESTION.fullmatch(text)):
        return None
    person, action = question.groups()
    frame = dict(person=person, action=action, recipient=None, deadline=None,
                 reason_hypothesis=None, background=context)
    if match := _DISSUADE.fullmatch(action):
        return dict(frame, kind='dissuade_attack', stance='劝止', predicate='出兵',
                    recipient=match['recipient'], target=match['target'])
    if match := _HOLD_CITY.fullmatch(action):
        return dict(frame, kind='oppose_withdrawal', stance='劝止', predicate='弃城',
                    recipient=match['recipient'], target=match['target'], place=match['place'])
    if match := _KEEP_CITY.fullmatch(action):
        return dict(frame, kind='oppose_withdrawal', stance='劝止', predicate='弃城',
                    target=None, place=match['place'])
    if match := _DELAY_ATTACK.fullmatch(action):
        return dict(frame, kind='delay_attack', stance='主张暂缓', predicate='出兵',
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
    if kind == 'oppose_withdrawal':
        return re.escape(frame['place']) if frame['place'] else r'欲[弃舍]\S{2,8}(?:归|走|退)'
    if kind in ('dissuade_attack', 'expedite_attack', 'delay_attack'):
        return (r'东吴|吴' if target == '东吴' else re.escape(target)) if target else _MILITARY_SOURCE
    if kind == 'oppose_population_move':
        return (rf'(?:徙|迁){re.escape(frame["origin"])}[^。！？；\n]{{0,24}}'
                rf'(?:实|于|於|至|入){re.escape(target)}')
    if kind == 'oppose_ceremony':
        return r'封禅|封(?:泰|太)山|东封'
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
        'oppose_withdrawal': ('弃城', '南归', '去留', '散走'),
        'delay_attack': ('谏', '进逼', '不如', '非利'),
        'oppose_population_move': ('徙', '迁', '士家', '民心'),
        'oppose_ceremony': ('封禅', '升中', '东封'),
        'expedite_attack': ('伐', '讨', '出兵', '兴兵'),
        'oppose_relocation': ('迁', '不可', '守议'),
        'reform_tax': ('赋敛', '科敛', '税法'),
        'decline_family_appointment': ('荐', '辞', '私恩'),
    }[frame['kind']]


def candidates(conn, frame, subject, book=None, *, semantic=()):
    topic = re.compile(_topic(frame))
    action, reason = map(re.compile, _SOURCE_ACTIONS[frame['kind']])
    military = frame['predicate'] == '出兵'
    ranked = []
    pages = scanned = 0
    direction_recall = frame['kind'] == 'dissuade_attack' and bool(frame['target'])
    sources = [(frame['person'], subject, False)]
    # If the semantic results already locate the other participant's biography,
    # let its remonstrance enter as context even for a question reversing the roles.
    recipient = frame.get('recipient')
    if frame['kind'] == 'oppose_population_move' and recipient != frame['person']:
        sources.append((recipient, semantic, False))
    elif direction_recall and recipient != frame['person'] and any(
        zh.to_simplified(hit['chunk']['heading']).strip() == recipient for hit in semantic
    ):
        sources.append((recipient, semantic, True))
    for name, anchors, directional_only in sources:
        for peers in named_biographies(conn, name, anchors, book):
            pages += 1
            scanned += len(peers)
            texts = {cid: zh.to_simplified(chunk['text_orig']) for cid, chunk in peers.items()}
            for cid, text in texts.items():
                local_topic = bool(topic.search(text))
                if (not directional_only and action.search(text)
                        and (not military or re.search(_MILITARY_SOURCE, text))
                        and (local_topic or any(topic.search(texts.get(n, '')) for n in (cid - 1, cid + 1)))):
                    ranked.append(((False, not bool(reason.search(text)), not local_topic, cid), dict(chunk=peers[cid])))
                elif direction_recall and (context := directional_context(peers, cid, name, topic)):
                    ranked.append(((True, False, True, cid), dict(chunk=peers[cid], campaign_context=context)))
    ranked.sort(key=lambda item: item[0])
    return [hit for _, hit in ranked], dict(pages=pages, scanned=scanned)


def directional_context(peers, cid, name, topic):
    """Recall one card for a directional proposal with a named opponent in its resumption."""
    previous, current, following = (peers.get(i) for i in (cid - 1, cid, cid + 1))
    if not previous or not following:
        return None
    prefix = next(A.previous_overlap(current, (previous,)), None)
    tail = next(A.previous_overlap(following, (current,)), None)
    if not prefix or not tail:
        return None
    if previous['parts'][-1]['block_index'] != following['parts'][0]['block_index']:
        return None
    before = previous['text_orig'][:-prefix[3]]
    original = before + current['text_orig'] + following['text_orig'][tail[3]:]
    text = zh.to_simplified(original)
    if len(text) != len(original):
        return None
    prose = A.narrative(text)
    for proposal in _DIRECTIONAL.finditer(text):
        if prose[proposal.start():proposal.end()] != proposal[0]:
            continue
        if proposal['speaker'] not in (name, name[-2:], name[-1:]):
            continue
        if not (proposal.start() < len(before) and proposal.end() >= len(before) - 40
                and text[proposal.end():].startswith(('「', '『'))):
            continue
        action = proposal['action']
        resumes = re.search(rf'(?:^|(?<=[。；\n]))\s*(?P<start>及{action}[，,])',
                            prose[proposal.end():])
        if not resumes:
            continue
        start = proposal.end() + resumes.start('start')
        if _NEW_EXPEDITION.search(prose[proposal.end():start]):
            continue
        end = min(len(text), start + 160)
        narrative = prose[start:end]
        boundary = _NEW_EXPEDITION.search(narrative)
        if boundary:
            narrative = narrative[:boundary.start()]
        # A direction alone cannot verify the target in the question.
        # Require an actual opponent in the continued narration for recall.
        opponent = re.search(rf'(?:虏主|敌主|贼主){_HAN}{{2,6}}?(?=率|遣|将|攻|围|南|北)', narrative)
        explicit = topic.search(narrative)
        if not opponent and not explicit:
            continue
        marker = opponent or explicit
        head = original[proposal.start('proposal'):proposal.end()]
        return head + '\n' + original[start:start + marker.end()]
    return None


_CLAUSE = r'[^，,。！？；：「」『』“”‘’\n]'
_SPEECH_END = re.compile(r'[「」『』“”‘’]|(?:曰|云)[：:]|\n\s*\n')
_REIGN_CHANGE = re.compile(r'崩|薨|嗣位|即位|践阼|登极|监国|尊为|立为')
_DISCOURAGE = re.compile(
    rf'(?:^|(?<=[。；，,]))\s*(?P<reason>{_CLAUSE}{{0,16}}(?:困乏|困敝|疲敝|疲弊|粮乏|乏食)'
    rf'{_CLAUSE}{{0,18}}(?:[，,]{_CLAUSE}{{2,20}}){{0,4}}[，,]'
    r'(?:难[于於]|不宜|不可|未可)(?:动兵|出兵|兴师|发兵|进兵))(?=[，,。；！？]|$)'
)


def _recipient_forms(chunk, name):
    book = zh.to_simplified(chunk.get('book_title', ''))
    if re.fullmatch(r'[汉唐宋晋明][\u3400-\u9fff]{1,3}(?:帝|祖|宗)', name):
        qualifier = '前?' if name.startswith('汉') else r'(?:新|旧)?'
        if re.fullmatch(qualifier + name[0] + r'(?:书|史)', book):
            return (name, name[1:])
    return (name,)


def _dissuasion(chunk, frame, neighbors, actor):
    if not frame['target']:
        return None
    previous = next(A.previous_overlap(chunk, neighbors), None)
    prefix = previous[1][:-previous[3]] if previous else ''
    source = prefix + chunk['text_orig']
    text = zh.to_simplified(source)
    if len(text) != len(source):
        return None
    target = '(?:' + _topic(frame) + ')'
    names = '|'.join(map(re.escape, _recipient_forms(chunk, frame['recipient'])))
    action = (
        rf'(?:欲|将)(?:(?:出兵|发兵|兴师)?(?:击|攻|讨|伐){target}'
        rf'|因{target}{_CLAUSE}{{1,10}}[，,](?:出兵|发兵|兴兵)(?:击|攻|讨|伐)其{_HAN}{{1,10}})'
    )
    proposal = re.compile(
        rf'(?:^|(?<=[。！？；\n，,]))\s*(?P<recipient>{names}|上|帝)'
        rf'(?:与{_CLAUSE}{{2,32}}?议[，,])?(?P<action>{action})'
        rf'(?:(?:[，,](?:使|以)){_CLAUSE}{{2,24}})?[。；]\s*'
        rf'(?P<speaker>(?:{actor})(?:上书谏|上疏谏|进谏|谏|奏谏)曰)[：:]\s*[「『“‘]'
    )
    narrative = A.narrative(text)

    def locate(span):
        start, end = span
        if start >= len(prefix):
            return A.excerpt(chunk, start - len(prefix), end - len(prefix))
        if previous and end <= len(previous[1]):
            return A.excerpt(previous[0], start, end)
        return None

    for match in proposal.finditer(text):
        if text[match.start('recipient'):match.end('speaker')] != narrative[match.start('recipient'):match.end('speaker')]:
            continue
        recipient_span = match.span('recipient')
        if match['recipient'] in ('上', '帝'):
            start = match.start('recipient')
            title = list(re.finditer(rf'(?:^|(?<=[。；\n]))\s*(?P<title>{names})(?=[\u3400-\u9fff])',
                                     narrative[:start]))
            if not title or start - title[-1].end() > 130 or _REIGN_CHANGE.search(narrative[title[-1].end():start]):
                continue
            recipient_span = title[-1].span('title')
        end = _SPEECH_END.search(text, match.end())
        reason = _DISCOURAGE.search(text, match.end(), end.start() if end else len(text))
        if not reason or reason.start('reason') < len(prefix):
            continue
        recipient = locate(recipient_span)
        speaker = locate(match.span('speaker'))
        action_ref = locate(match.span('action'))
        if recipient and speaker and action_ref:
            detail = locate(reason.span('reason'))
            return dict(slot='reason', kind='advocacy', attribution='speaker_explanation',
                        support='dissuasion', event=detail, detail=detail, action=action_ref,
                        speaker=speaker, recipient=recipient)
    return None


def evidence(chunk, frame, neighbors=()):
    """Bind quoted advice or an explicit narrator-stated reform rationale."""
    if (frame['kind'] not in ('reform_tax', 'dissuade_attack') or chunk['layer'] != 'body'
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
    if frame['kind'] == 'dissuade_attack':
        return _dissuasion(chunk, frame, neighbors, actor)
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
