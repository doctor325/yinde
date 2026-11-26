"""Resolve requested editions and attach clauses to the event they describe."""
import json
import re

from search import zh
from search.event_attachment import narrative
from search.reading_grounding import HAN, _catalog_titles, book_key


def _chapter(sources, book, label, catalog):
    canonical = catalog.get(zh.to_simplified(label), set())
    if len(canonical) != 1:
        return None
    named = zh.to_simplified(f'{book["title"]}/{label}')
    pages = {name: [] for name in (*canonical, named)}
    for row in sources.library.execute('SELECT page_id,title FROM chapters WHERE book_id=?', (book['book_id'],)):
        if (title := zh.to_simplified(row['title'])) in pages:
            pages[title].append(row['page_id'])
    choices = pages[next(iter(canonical))] or pages[named]
    return choices[0] if len(choices) == 1 else None


class PageWitnesses:
    def __init__(self, sources, page_id):
        self.sources = sources
        self.page, self.blocks = sources.page(page_id)
        self.chunks = [chunk for (payload,) in sources.index.execute(
            'SELECT payload FROM chunks WHERE book_id=? AND page_id=? ORDER BY chunk_id',
            (self.page['book_id'], page_id))
            if (chunk := json.loads(payload))['layer'] == 'body'
            and all(chunk[key] == self.page[key] for key in ('revision_id', 'source_sha256'))]

    def body(self):
        heading = ''
        for block, unit in enumerate(self.blocks):
            if unit['kind'] == 'heading':
                heading = zh.to_simplified(unit['text']).strip()
            if unit['kind'] != 'body':
                continue
            raw = unit['text']
            text = zh.to_simplified(raw)
            if len(raw) == len(text):
                yield block, heading, text

    def hit(self, block, start, end, label, *, focus=None):
        original = self.blocks[block]['text']
        start += len(original[start:end]) - len(original[start:end].lstrip())
        if start >= end:
            return None
        lo, hi = focus or (start, end)

        def overlap(chunk):
            return sum(max(0, min(hi, part['end'])-max(lo, part['start']))
                       for part in chunk['parts'] if part['block_index'] == block)

        chunk = max(self.chunks, key=overlap, default=None)
        if chunk is None or not overlap(chunk):
            return None
        ref = {k: chunk[k] for k in ('page_id', 'book_id', 'revision_id', 'source_sha256',
                                     'title', 'source_url', 'heading')}
        ref.update(ranges=[[block, start, end]], quote=original[start:end])
        return dict(chunk=chunk, reading_support={label: self.sources.check(ref)})

    def matches(self, pattern, label, *, mask=True, heading=None):
        found = []
        for block, scope, text in self.body():
            if heading is not None and scope != heading:
                continue
            for match in pattern.finditer(narrative(text) if mask else text):
                if hit := self.hit(block, *match.span(), label):
                    found.append(hit)
        return found


def _book(sources, title, book_id):
    rows = [row for row in sources.library.execute('SELECT book_id,title FROM books')
            if book_key(row['title']) == book_key(title) and book_id in (None, '', row['book_id'])]
    return rows[0] if len(rows) == 1 else None


def _death(sources, plan, book):
    catalog = _catalog_titles(book['book_id'])
    labels = (plan['first'], plan['second'])
    pages = [_chapter(sources, book, label, catalog) for label in labels]
    if pages[0] == pages[1]:
        return []
    biography = next((i for i, label in enumerate(labels) if label.endswith('列传')), None)
    person = plan['person']
    names = {person}
    identity_confirmed = False
    if biography is not None and pages[biography] is not None:
        record = PageWitnesses(sources, pages[biography])
        for _, heading, text in record.body():
            if heading != person:
                continue
            opening = re.match(rf'\s*(?P<title>{HAN}{{2,5}}?)者[，,][^。！？]{{1,60}}名'
                               rf'{re.escape(person[-1])}[，,。]', text)
            if opening:
                names.add(opening['title'])
                identity_confirmed = True
    actor = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    # Table cells give a short direct notice. Narratives can distinguish a
    # killing and a later desecration of the body in consecutive sentences.
    notice = re.compile(rf'(?:{actor})(?:反[，,])?死(?:于|於)?{HAN}{{1,12}}[。]')
    killing = re.compile(rf'{HAN}{{1,8}}(?:发兵)?攻(?:{actor})[，,]杀之(?:于|於){HAN}{{1,12}}[。]'
                         rf'(?:{HAN}{{1,8}}车裂(?:{actor})以徇[，,。])?')
    selected = []
    for label, page in zip(labels, pages, strict=True):
        if page is None:
            continue
        witness = PageWitnesses(sources, page)
        hits = witness.matches(notice if label.endswith('年表') else killing, label)
        if len(hits) == 1:
            selected.extend(hits)
    if identity_confirmed and biography is not None and pages[biography] is not None:
        aliases = names-{person}
        if len(aliases) == 1:
            title = re.escape(next(iter(aliases)))
            identity = re.compile(rf'{title}者[，,][^。！？]{{1,60}}名{re.escape(person[-1])}[，,。]')
            selected.extend(PageWitnesses(sources, pages[biography]).matches(identity, '人物身份', heading=person))
    return selected


def _pursuit(sources, plan, book):
    page = _chapter(sources, book, plan['chapter'], _catalog_titles(book['book_id']))
    if page is None:
        return []
    witness = PageWitnesses(sources, page)
    pursuer, target = plan['person'], plan['target']
    target_name = re.compile(rf'{re.escape(target)}者[，,]{HAN}{{1,12}}人也')
    actual = witness.matches(target_name, '被追者身份')
    if len(actual) != 1 or pursuer[-1] == target[-1]:
        return []
    short, other = re.escape(pursuer[-1]), re.escape(target[-1])
    event = re.compile(rf'{other}数与{re.escape(pursuer)}语[，,][^\n]{{1,130}}?'
                       rf'{short}闻{other}亡[，,][^\n。]{{1,18}}?自追之[。]')
    events = witness.matches(event, '追赶原文')
    return [*events, *actual] if len(events) == 1 else []


def _proposal(sources, plan, book_id):
    from search.punctuated_search import lexical
    name, place, proposal = (re.escape(plan[key]) for key in ('person', 'place', 'proposal'))
    speech = re.compile(rf'王曰[：:][「『][^」』\n]{{0,280}}?(?P<proposal>今吾(?:将|欲){proposal})'
                        r'[^」』\n]{0,30}[」』]')
    alias = re.compile(rf'(?:{name}|{re.escape(plan["person"][1:])})自号[为爲](?P<title>{HAN}{{2,4}})[。]')
    pages = {chunk['page_id'] for hit in lexical(sources.index, plan['proposal'], book_id, 1, page_size=24)['hits']
             if (chunk := hit['chunk'])['layer'] == 'body'
             and zh.to_simplified(chunk['heading']).strip() == plan['person']}
    results = []
    for page in pages:
        witness = PageWitnesses(sources, page)
        for block, heading, text in witness.body():
            if heading != plan['person']:
                continue
            proposed = list(speech.finditer(text))
            identities = list(alias.finditer(narrative(text)))
            if len(proposed) != 1:
                continue
            first = witness.hit(block, *proposed[0].span(), '提出主张的原文',
                                focus=proposed[0].span('proposal'))
            if not first:
                continue
            linked = [first]
            if len(identities) == 1 and proposed[0].end() < identities[0].start():
                identity = identities[0]
                short = re.escape(identity['title'])
                besieged = re.compile(rf'(?:乃|遂|因)围{short}[。][^\n]{{0,65}}?{short}欲出不得'
                                      rf'[^。\n]{{0,50}}?{place}(?:宫)?[。]')
                first_ref = witness.hit(block, *identity.span(), '人物别称')
                later = [h for h in witness.matches(besieged, '被围原文')
                         if h['reading_support']['被围原文']['ranges'][0][0] > block]
                if first_ref and len(later) == 1:
                    linked.extend((first_ref, *later))
            results.append(linked)
    return results[0] if len(results) == 1 else []


def named_speech(sources, candidates, plan):
    from search.punctuated_search import normalize
    quote = normalize(plan['quote'])
    pages = {chunk['page_id'] for hit in candidates if (chunk := hit['chunk'])['layer'] == 'body'
             and book_key(chunk['book_title']) == book_key(plan['book'])}
    results = []
    for page in pages:
        witness = PageWitnesses(sources, page)
        for block, heading, text in witness.body():
            if not re.fullmatch(rf'{HAN}{{2,8}}', heading) or normalize(plan['quote']) not in normalize(text):
                continue
            speaker = re.match(rf'\s*{re.escape(heading)}{HAN}{{2,4}}者[，,]', text)
            talks = re.compile(rf'(?:^|[。\n])\s*{re.escape(heading)}[^。！？「』\n]{{1,40}}?'
                               r'曰[：:][「『](?P<speech>[^」』\n]+)[」』]')
            for talk in talks.finditer(text):
                if not speaker or talk.start() <= speaker.end() or quote not in normalize(talk['speech']):
                    continue
                if not narrative(text)[talk.start():talk.start('speech')].strip('。\n '):
                    continue
                mentions = list(re.finditer(rf'(?P<family>{HAN})先生以(?P<rest>[^。！？]{{2,48}})', talk['speech']))
                family = [m['family'] for m in mentions if normalize(m['rest']) == quote]
                if len(family) != 1:
                    continue
                actors = {}
                prefix = narrative(text[:talk.start()])
                left = max(0, len(prefix)-240)
                for actor in re.finditer(rf'(?P<name>{re.escape(family[0])}{HAN}{{1,2}}?)(?=左手|右手|奉|按剑)',
                                         prefix[left:]):
                    actors[actor['name']] = actor
                if len(actors) != 1:
                    continue
                actor = next(iter(actors.values()))
                linked = [witness.hit(block, *speaker.span(), '说话者身份'),
                          witness.hit(block, *talk.span(), '发言原文'),
                          witness.hit(block, left+actor.start(), left+actor.end(), '被称赞者身份')]
                if all(linked):
                    results.append(linked)
    return _merge(results[0]) if len(results) == 1 else []


def recall(sources, plan, book_id):
    if plan['kind'] == 'proposal_order':
        hits = _proposal(sources, plan, book_id)
    else:
        book = _book(sources, plan['book'], book_id)
        if book is None:
            return []
        hits = _death(sources, plan, book) if plan['kind'] == 'death_comparison' else _pursuit(sources, plan, book)
    return _merge(hits)


def _merge(hits):
    merged = {}
    for hit in hits:
        current = merged.setdefault(hit['chunk']['chunk_id'], dict(chunk=hit['chunk'], reading_support={}))
        current['reading_support'].update(hit['reading_support'])
    return list(merged.values())
