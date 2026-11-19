"""Attach a requested detail to a source-bound event in punctuated prose."""
import re

from search import annal_events, zh


_HAN = r'[\u3400-\u9fff]'
_BOUNDARY = r'(?:^|[。；！？：:，、\n])\s*'
_YEAR = rf'(?!在位|先是|是岁|是年|明年|其后|其後|年凡)(?:{_HAN}{{2}}|[秦汉魏晋周])[元一二三四五六七八九十百]+年'
_MONTH = r'[春夏秋冬]?(?:闰)?[正一二三四五六七八九十冬腊]+月'
_DATE_TAIL = rf'(?:{_MONTH})?(?:{annal_events.DATE_UNIT})?'
_DATE = re.compile(rf'{_BOUNDARY}(?P<year>{_YEAR})(?P<tail>{_DATE_TAIL})[，,]')
_DATED_HEADING = re.compile(_YEAR)
_PATIENT = r'(?:竟|遂|乃|寻|即)?(?:坐诛|伏诛|见诛|被诛|弃市|赐死|伏法|坐斩)'
_CAPTURE_QUERY = re.compile(
    rf'(?P<person>{_HAN}{{2,4}}?)(?:在哪里|在何处|哪里|何处)'
    rf'被(?P<captor>{_HAN}{{1,8}}?)?(?:俘虏|俘获|俘|擒获|擒)'
)
_CAPTORS = {'辽军': ('辽军', '辽兵', '辽人', '契丹'),
            '金军': ('金军', '金兵', '金人', '女真')}
SOURCE_SCOPE = ('book_id', 'page_id', 'revision_id', 'source_sha256', 'layer', 'heading')
_OPEN_QUOTE = '「『“‘'
_CLOSE_QUOTE = '」』”’'


def capture_query(text):
    match = _CAPTURE_QUERY.fullmatch(text.strip().rstrip('？?').rstrip())
    return match.groupdict() if match else None


def narrative(text):
    """Mask quoted speech while keeping the original character offsets."""
    chars = list(text)
    start = None
    depth = 0
    for i, char in enumerate(text):
        if char in _OPEN_QUOTE:
            if not depth:
                start = i
            depth += 1
        elif char in _CLOSE_QUOTE:
            depth = max(depth - 1, 0)
            if not depth:
                begin = start if start is not None else text.rfind('\n', 0, i) + 1
                chars[begin:i + 1] = ' ' * (i + 1 - begin)
                start = None
    if depth:
        chars[start:] = ' ' * (len(chars) - start)
    return ''.join(chars)


def excerpt(chunk, start, end):
    return dict(chunk_id=chunk['chunk_id'], start=start, end=end,
                quote=chunk['text_orig'][start:end])


def _execution_object(text, actor, place):
    place = re.escape(place)
    return re.search(
        rf'{_BOUNDARY}(?P<event>{actor}(?:遂|乃|复|又)?(?:攻破|攻克|攻取|攻陷|克|陷|拔|下)'
        rf'{place}城?[，,](?:即|遂|乃)?(?:诛夷|族诛|诛|斩|杀)'
        rf'(?P<value>(?!其|之|此|彼|降者|其余){_HAN}{{2,16}}))(?=[，。；！？,])',
        text,
    )


def _execution_date(text, person, actor, heading):
    end = rf'(?:于|於){_HAN}{{1,10}}(?=[，。；！？])|(?=[，。；！？])'
    dated_heading = _DATED_HEADING.fullmatch(heading)
    actions = '杀|诛|磔|处死|赐死' if dated_heading else '诛|磔|处死|赐死'
    executor = rf'(?:既而|遂|乃|即)?(?:{actions})'
    predicate = rf'(?:{executor}{re.escape(person)}(?:{end})|{actor}{_PATIENT}(?=[，。；！？]))'
    if match := re.search(rf'{_BOUNDARY}(?P<event>(?P<value>{_YEAR}{_DATE_TAIL})[，,]{predicate})', text):
        return match, None
    if dated_heading:
        match = re.search(rf'{_BOUNDARY}(?P<event>(?P<value>{_MONTH}(?:{annal_events.DATE_UNIT})?)'
                          rf'[，,]{predicate})', text)
        if match:
            return match, heading
    if heading != person:
        return None, None
    # The dated journey and the ensuing reported offense form the same episode.
    # Require the named biography's subject and an explicit consequence.
    for date in _DATE.finditer(text):
        episode = re.match(
            rf'(?:从驾|随驾|扈从)[^\n；「」]{{1,52}}?[。]{actor}以[^。；\n]{{1,40}}?'
            rf'[，,]为人所(?:奏|劾|告)[，,]{_PATIENT}(?=[，。；！？])',
            text[date.end():],
        )
        if not episode or re.search(r'年|翌日|明日|既而|已而|俄而|其后|其後|后[，,]|後[，,]', episode[0]):
            continue
        pattern = re.compile(rf'(?P<event>(?P<value>{_YEAR}{_DATE_TAIL})[，,]'
                             + re.escape(episode[0]) + ')')
        match = pattern.match(text, date.start('year'))
        return match, None
    return None, None


def _capture_place(text, actor, captor):
    forms = _CAPTORS.get(captor, (captor,)) if captor else ()
    captor_expr = '|'.join(map(re.escape, forms)) if forms else rf'{_HAN}{{1,8}}'
    passive = rf'(?:为(?:{captor_expr})所[擒禽俘])' if forms else rf'(?:被[擒禽俘]|为{captor_expr}所[擒禽俘])'
    # After reaching the place allow only a short sequence of local reactions
    # or injuries, with any explicitly named actor drawn from the same subject.
    local = rf'(?:望见无人|(?:即|乃)?拊膺{_HAN}{{0,4}}|(?:再|复)率{_HAN}{{1,10}}力战|身被{_HAN}{{1,8}}创|士卒殆尽|{actor}犹{_HAN}{{1,12}}|马重伤不能进)'
    movement = rf'(?:大?败[，,]走|败走|力战[，,]自{_HAN}至{_HAN}[，,]果|走|奔|行)?至'
    place = rf'(?!(?:天明|天晓|日暮|明日|翌日|来日|旦日|次日)[，,。]){_HAN}{{2,10}}'
    return re.search(
        rf'{_BOUNDARY}(?P<event>{actor}{movement}'
        rf'(?P<value>{place})[，,。](?:{local}[，,。]){{0,8}}'
        rf'(?:遂|乃|即|遇贼)?{passive})(?=[，。；！？]|$)', text)


def attach(chunk, plan, neighbors, *, subject_anchor=None):
    execution = plan.get('execution_event')
    capture = plan.get('capture_event')
    if not (execution or capture) or chunk['layer'] != 'body' or not all(chunk.get(k) for k in ('revision_id', 'source_sha256')):
        return None
    frame = execution or capture
    person = frame['person']
    heading = zh.to_simplified(chunk['heading']).strip()
    actor = re.escape(person)
    if heading == person:
        actor = rf'(?:{actor}|{re.escape(person[-1])})'
    elif capture and subject_anchor:
        actor = rf'(?:{actor}|{re.escape(subject_anchor["form"])})'
    text = zh.to_simplified(chunk['text_orig'])
    if len(text) != len(chunk['text_orig']):
        return None
    text = narrative(text)
    basis = None
    if capture:
        slot = 'place'
        match = _capture_place(text, actor, capture['captor'])
    else:
        slot = execution['answer_slot']
        prior = execution['preceding_event']
        if slot == 'object' and prior:
            match = _execution_object(text, actor, prior['object'])
        elif slot == 'time':
            match, basis = _execution_date(text, person, actor, heading)
        else:
            return None
    if not match:
        return None
    detail = excerpt(chunk, *match.span('value'))
    record = dict(slot=slot, detail=detail, event=excerpt(chunk, *match.span('event')))
    if subject_anchor:
        if subject_anchor['chunk_id'] == chunk['chunk_id'] and match.start('event') < subject_anchor['end']:
            return None
        record['subject_anchor'] = subject_anchor
    if basis:
        record['year_heading'] = chunk['heading']
    if slot == 'place' and match['value'] in ('谷口', '城门', '山下', '河口', '渡口', '关口'):
        # A relative place needs a uniquely named anchor from the same version.
        anchors = {}
        for previous in neighbors:
            if previous['chunk_id'] != chunk['chunk_id'] - 1 or any(previous.get(k) != chunk.get(k) for k in SOURCE_SCOPE):
                continue
            prefix = zh.to_simplified(previous['text_orig'])
            if len(prefix) != len(previous['text_orig']):
                continue
            for found in re.finditer(rf'(?:指|至|于|於)(?P<place>{_HAN}{{1,5}}{match["value"]})(?=[，。；！？]|曰)', prefix):
                anchors[found['place']] = excerpt(previous, *found.span('place'))
        if len(anchors) != 1:
            return None
        record['place_anchor'] = next(iter(anchors.values()))
        record['place_granularity'] = 'vicinity'
    return record
