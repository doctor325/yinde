"""Recall bounded source witnesses for selected, explicit question forms."""
import hashlib
import json
from pathlib import Path
import re

from scripts.pipeline import config
from search import punctuated, zh
from search.biography_lookup import TOC_LINK
from search.event_attachment import narrative
from search.polity_evidence import Sources, source_ref


HAN = r'[\u3400-\u9fff]'
CHAPTER = rf'{HAN}{{2,12}}?(?:本纪|列传|世家|年表)'
COMPARISON = re.compile(rf'《(?P<book>{HAN}{{2,12}})》(?:的)?(?P<first>{CHAPTER})'
                        rf'(?:和|与|及|、)(?P<second>{CHAPTER})对(?P<person>{HAN}{{2,8}})的'
                        r'(?:身世|出身|生父)(?:是)?(?:怎么写的|有何不同|有何记载)')
RELOCATION = re.compile(rf'(?P<person>{HAN}{{2,4}}?)被(?:迁往|迁到|迁至|流放至|流放到)'
                        rf'(?P<place>{HAN}{{1,7}}?海)牧羊[，,]?(?:是)?在'
                        rf'(?P<other>{HAN}{{2,4}}?)劝降(?P<order>之前|之后|以前|以后|前|后)吗')
ATTRIBUTION = re.compile(rf'《(?P<book>{HAN}{{2,12}})》(?:里|中)?[“「](?P<quote>[^”」]{{6,60}})[”」]'
                         r'(?:这句话)?是谁说的[，,]说的是谁')
NAME_LIST = re.compile(r'《[^》]{2,32}》(?:里|中)?[，,]?[^？?]{3,140}(?:分别叫|各叫|各自叫)什么(?:名字)?')
DEATH_COMPARISON = re.compile(rf'《(?P<book>{HAN}{{2,12}})》(?:的)?(?P<first>{CHAPTER})'
                              rf'(?:和|与|及|、)(?P<second>{CHAPTER})(?:分别)?(?:怎么写|如何记载)'
                              rf'(?P<person>{HAN}{{2,8}}?)之死')
PROPOSAL_ORDER = re.compile(rf'(?P<person>{HAN}{{2,8}}?)被困(?:于|在)?(?P<place>{HAN}{{2,8}}?)'
                            rf'[，,]?(?:是)?在他提出(?P<proposal>{HAN}{{3,12}}?)'
                            r'(?P<order>之前|之后|以前|以后|前|后)吗')
PURSUIT_DATE = re.compile(rf'《(?P<book>{HAN}{{2,12}})[·・](?P<chapter>{CHAPTER})》(?:里|中)?[，,]?'
                          rf'(?P<person>{HAN}{{2,4}}?)追(?:赶)?(?P<target>{HAN}{{2,4}}?)'
                          r'(?:那天|当天)(?:究竟|具体)?(?:是|为)?几月几日')
AUDIENCE = re.compile(rf'(?:^|[，。；\n])(?P<target>{HAN}{{1,4}}?)立(?P<speaker>{HAN}{{1,4}}帝)。'
                      r'帝[^。：「『\n]{1,60}?[，,]目(?P=target)曰[：:][『「“](?P<quote>[^』」”]+)[』」”]')


def frame(query):
    text = zh.to_simplified(query).strip().rstrip('？?。')
    for kind, pattern in (('parentage_comparison', COMPARISON), ('relocation_order', RELOCATION),
                          ('death_comparison', DEATH_COMPARISON), ('proposal_order', PROPOSAL_ORDER),
                          ('pursuit_date', PURSUIT_DATE),
                          ('quote_attribution', ATTRIBUTION), ('name_list', NAME_LIST)):
        if match := pattern.fullmatch(text):
            return dict(kind=kind, **match.groupdict())
    return None


def book_key(title):
    from search.punctuated_search import normalize
    return normalize(title).replace('後', '后')


def _catalog_titles(book_id):
    path = punctuated.index_path().parent/'2026-10-25'/'acquisition.json'
    try:
        toc = json.loads(path.read_bytes())['books'][book_id]['toc']
        archive = (Path(config.PROJECT_ROOT)/'corpus/wikisource').resolve()
        source = (archive/toc['path']).resolve()
        if not source.is_relative_to(archive):
            return {}
        saved = json.loads(source.read_bytes())
        text = saved['wikitext']
        if (saved['revision_id'] != toc['revision_id'] or saved['title'] != toc['title']
                or hashlib.sha256(text.encode()).hexdigest() != toc['text_sha256']):
            return {}
    except (OSError, KeyError, ValueError):
        return {}
    titles = {}
    for line in text.splitlines():
        if match := TOC_LINK.fullmatch(line):
            target, description = match.groups()
            if target.startswith('/'):
                target = toc['title']+target
            description = re.sub(r'第[一二三四五六七八九十百〇\d]+$', '', zh.to_simplified(description).strip())
            titles.setdefault(description, set()).add(zh.to_simplified(target))
    return titles


def _scoped_comparison(index, library, plan, book):
    book_ids = [row['book_id'] for row in library.execute('SELECT book_id,title FROM books')
                if book_key(row['title']) == book_key(plan['book']) and book in (None, '', row['book_id'])]
    if len(book_ids) != 1:
        return []
    book_id = book_ids[0]
    catalog = _catalog_titles(book_id)
    entries = [catalog.get(plan[key], set()) for key in ('first','second')]
    if any(len(titles) != 1 for titles in entries) or entries[0] == entries[1]:
        return []
    pages = {}
    for row in library.execute('SELECT page_id,title,revision_id,source_sha256 FROM chapters WHERE book_id=?',
                               (book_id,)):
        title = zh.to_simplified(row['title'])
        if any(title in titles for titles in entries):
            pages.setdefault(title, []).append(row)
    sources = Sources(index, library, {})
    selected = []
    personal_names = set()
    opening = re.compile(rf'{re.escape(plan["person"])}(?:帝)?者[，,]{HAN}{{2,8}}子也[。]')
    narrative_birth = re.compile(r'(?:知有身|匿有身)[^\n]{0,150}?生子[^。]{0,10}。')
    for key, titles in zip(('first', 'second'), entries, strict=True):
        versions = pages.get(next(iter(titles)), ())
        if len(versions) != 1:
            continue
        page = versions[0]
        pattern = opening if plan[key].endswith(('本纪', '世家')) else narrative_birth
        matches = {}
        for (payload,) in index.execute('SELECT payload FROM chunks WHERE book_id=? AND page_id=?',
                                         (book_id,page['page_id'])):
            chunk = json.loads(payload)
            if chunk['layer'] != 'body' or any(chunk[k] != page[k] for k in ('revision_id','source_sha256')):
                continue
            text = zh.to_simplified(chunk['text_orig'])
            if len(text) != len(chunk['text_orig']):
                continue
            for match in pattern.finditer(narrative(text)):
                # Expand through the introductory clause that identifies the mother.
                start = text.rfind('。', 0, match.start())+1 if pattern is narrative_birth else match.start()
                ref = sources.check(source_ref(chunk, start, match.end()))
                block, _, end = ref['ranges'][-1]
                key_range = block, end
                if key_range not in matches or len(ref['quote']) > len(matches[key_range][1]['quote']):
                    matches[key_range] = chunk, ref
        if len(matches) == 1:
            chunk, ref = next(iter(matches.values()))
            selected.append((pattern, dict(chunk=chunk, reading_support={plan[key]: ref})))
            if pattern is opening:
                text = zh.to_simplified(chunk['text_orig'])
                origin = opening.search(narrative(text))
                if origin and (name := re.search(rf'及生[，,]名(?:为|爲)(?P<name>{HAN}{{1,3}})[，,。]',
                                                 text[origin.end():origin.end()+100])):
                    personal_names.add(name['name'])
    # A pregnancy in the named biography can still concern another child.
    return [hit for pattern, hit in selected if pattern is opening or (
        len(personal_names) == 1 and re.search(
            rf'生子{re.escape(next(iter(personal_names)))}[，,。]',
            zh.to_simplified(next(iter(hit['reading_support'].values()))['quote'])))]


def _heir_scope(blocks, block, person):
    """Bind a bare given name to a father-and-son heading and opening."""
    if block < 3:
        return False
    parent_heading, parent, child_heading, body = blocks[block-3:block+1]
    if [x['kind'] for x in (parent_heading,parent,child_heading,body)] != ['heading','body','heading','body']:
        return False
    parent_name = zh.to_simplified(parent_heading['text']).strip()
    child = re.escape(person[-1])
    return (len(parent_name) == len(person) and parent_name[:-1] == person[:-1]
            and zh.to_simplified(parent['text']).lstrip().startswith(parent_name)
            and zh.to_simplified(child_heading['text']).replace(' ','') == f'{parent_name[-1]}子{person[-1]}'
            and re.match(rf'\s*{child}字{HAN}{{1,4}}[，,。]', zh.to_simplified(body['text'])) is not None)


def _relocation(index, library, plan, book):
    from search.punctuated_search import lexical
    sources = Sources(index, library, {})
    person = plan['person']
    name, other, place = (re.escape(plan[key]) for key in ('person','other','place'))
    short, agent = re.escape(person[-1]), re.escape(plan['other'][-1])
    migration = re.compile(rf'乃(?:徙|迁)(?:{name}|{short}){place}[^。\n]{{0,14}}?使牧[羊羝]')
    pursuit = re.compile(rf'初[，,](?:{name}|{short})与{other}[^\n]{{1,70}}?久之[，,]'
                         rf'{HAN}{{1,6}}?使{agent}至海上[，,][为爲]{short}置酒[^。\n]{{0,14}}?谓{short}曰')
    leads = lexical(index, f"{plan['place']} 牧", book, 1, page_size=50)['hits']
    proofs = {}
    for hit in leads:
        chunk = hit['chunk']
        if chunk['layer'] != 'body' or len(chunk['parts']) != 1:
            continue
        _, blocks = sources.page(chunk['page_id'])
        block = chunk['parts'][0]['block_index']
        if not _heir_scope(blocks, block, person):
            continue
        raw = blocks[block]['text']
        text = zh.to_simplified(raw)
        if len(raw) != len(text):
            continue
        prose = narrative(text)
        first = list(migration.finditer(prose))
        second = list(pursuit.finditer(prose))
        if len(first) != 1 or len(second) != 1 or first[0].end() >= second[0].start():
            continue
        first, second = first[0], second[0]
        intervening = prose[first.end():second.start()]
        if (len(intervening) > 280 or not re.search(rf'{short}既至海上', intervening)
                or re.search(r'(?:乃徙|又迁|他徙)', intervening)):
            continue
        speech = re.match(r'[:：]「([^」]+)」', text[second.end():])
        if not speech or not re.search(r'(?:欲|请|愿|肯|听.{0,3})降', speech[1]):
            continue
        found = []
        for label, match in ((f"先迁往{plan['place']}牧羊", first), ('后来海上劝降', second)):
            # Only use chunks from this source layer, heading and version.
            for (payload,) in index.execute('SELECT payload FROM chunks WHERE book_id=? AND page_id=? ORDER BY chunk_id',
                                             (chunk['book_id'],chunk['page_id'])):
                peer = json.loads(payload)
                if any(peer[k] != chunk[k] for k in ('revision_id','source_sha256','layer','heading')):
                    continue
                if len(peer['parts']) != 1:
                    continue
                part = peer['parts'][0]
                if part['block_index'] != block or not part['start'] <= match.start() < match.end() <= part['end']:
                    continue
                ref = source_ref(peer, match.start()-part['start'], match.end()-part['start'])
                found.append(dict(chunk=peer, reading_support={label: sources.check(ref)}))
                break
        if len(found) == 2:
            proofs[chunk['page_id'], block] = found
    return next(iter(proofs.values())) if len(proofs) == 1 else []


def quote_episode(original, quote):
    from search.punctuated_search import normalize
    text = zh.to_simplified(original)
    if len(text) != len(original):
        return None
    matches = [m for m in AUDIENCE.finditer(text) if normalize(m['quote']) == normalize(quote)]
    return matches[0] if len(matches) == 1 else None


def full_name_pattern(target, speaker):
    return re.compile(rf'大将军(?P<name>{HAN}{{1,3}}{re.escape(target)})'
                      rf'(?:与{HAN}{{2,8}})?立{re.escape(speaker)}(?=[，,。]|$)')


def _attribution(index, library, plan, book):
    from search.punctuated_search import lexical
    sources = Sources(index, library, {})
    attempts = lexical(index, plan['quote'], book, 1, page_size=30)['hits']
    matched = {}
    for hit in attempts:
        chunk = hit['chunk']
        if chunk['layer'] != 'body' or book_key(chunk['book_title']) != book_key(plan['book']):
            continue
        if episode := quote_episode(chunk['text_orig'], plan['quote']):
            ref = sources.check(source_ref(chunk, episode.start(), episode.end()))
            matched.setdefault((ref['page_id'], tuple(map(tuple, ref['ranges']))), (chunk, ref, episode))
    if len(matched) != 1:
        from search.reading_scope import named_speech
        return named_speech(sources, attempts, plan) if not matched else []
    chunk, ref, episode = next(iter(matched.values()))
    result = [dict(chunk=chunk, reading_support={'发言人与所指人物': ref})]
    target, speaker = episode['target'], episode['speaker']
    # A second passage must name the holder of the same office, taking the same action.
    prose = zh.to_simplified(chunk['text_orig'])
    if len(target) != 1 or not re.search(rf'拜{target}[为爲]大将军', prose[:episode.start()]):
        return result
    names = {}
    for hit in lexical(index, f'立{speaker} 大将军', chunk['book_id'], 1, page_size=30)['hits']:
        peer = hit['chunk']
        text = zh.to_simplified(peer['text_orig'])
        if peer['layer'] != 'body' or len(text) != len(peer['text_orig']):
            continue
        for match in full_name_pattern(target, speaker).finditer(narrative(text)):
            names.setdefault(match['name'], (peer, sources.check(source_ref(peer, *match.span()))))
    if len(names) == 1:
        peer, witness = next(iter(names.values()))
        result.append(dict(chunk=peer, reading_support={'所指人物全名': witness}))
    return result


def recall(index, query, book=None):
    plan = frame(query)
    if not plan or plan['kind'] == 'name_list':
        return []
    try:
        with punctuated.connect() as library:
            if plan['kind'] == 'parentage_comparison':
                return _scoped_comparison(index, library, plan, book)
            if plan['kind'] == 'quote_attribution':
                return _attribution(index, library, plan, book)
            if plan['kind'] in ('death_comparison', 'proposal_order', 'pursuit_date'):
                from search import reading_scope
                return reading_scope.recall(Sources(index, library, {}), plan, book)
            return _relocation(index, library, plan, book)
    except FileNotFoundError:
        return []
