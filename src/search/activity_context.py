"""Focus ranking on narration of what someone was doing when an event occurred."""
import re

from search import event_attachment, zh


_QUERY = re.compile(
    r'(?P<person>[\u3400-\u9fff]{2,6}?)(?:在)?(?:接到|收到|听到|听闻|得知)'
    r'(?P<trigger>[^。！？？，,；;\n]{2,40}?)(?:的时候|之际|时)[，,]?'
    r'(?:当时)?(?:正?在)?(?:做什么|干什么)[？?]?'
)
_RECEIPT = re.compile(
    r'(?:驿书|军书|军报|捷报|战报|书报)(?:至|来|到|入)|'
    r'(?:得|接|览|闻)(?:军书|军报|捷报|战报)'
)
_UNREST = re.compile(r'(?:军|营)中[夜忽大]{0,2}(?:惊|乱|骚动)')
_SENTENCE = re.compile(r'[^。！？；\n]+[。！？；]?')


def parse(query):
    match = _QUERY.fullmatch(query.strip())
    if not match:
        return None
    result = match.groupdict()
    trigger = result['trigger']
    if re.search(r'报[告书讯]|战报|军报|捷报|消息|来信', trigger):
        result['kind'] = 'receipt'
    elif re.search(r'(?:军|营)中.{0,6}(?:骚乱|惊乱|营啸|哗变)', trigger):
        result['kind'] = 'unrest'
    else:
        return None
    return result


def focus(chunk, frame):
    """Keep complete clauses and original offsets for the reranker's input."""
    if chunk['layer'] != 'body':
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
    trigger = _RECEIPT if frame['kind'] == 'receipt' else _UNREST
    sentences = list(_SENTENCE.finditer(narration))
    for i, sentence in enumerate(sentences):
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
