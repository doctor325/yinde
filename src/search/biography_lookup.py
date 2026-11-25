"""Find imperial biography openings and verified continuation volumes."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from search import punctuated, zh
from scripts.pipeline import config

# Dynasty and ruling surname identify the book's royal house, not an individual.
HOUSES = {'宋史': ('宋', '赵'), '旧唐书': ('唐', '李'), '新唐书': ('唐', '李'),
          '明史': ('明', '朱'), '汉书': ('汉', '刘'), '后汉书': ('汉', '刘'),
          '晋书': ('晋', '司马'), '隋书': ('隋', '杨')}
TOC_LINK = re.compile(r'^[#*]+\s*\[\[([^|\]]+)(?:\|[^\]]*)?\]\]\s*(.*?)\s*$')
_ORDINAL = r'[一二三四五六七八九十百〇0-9]+'
_TOC_ANNAL = re.compile(rf'(?:本纪|帝纪)第{_ORDINAL}[:：]?\s*(\S+)')
_TOC_TITLED = re.compile(rf'(\S{{1,7}}帝)纪\s*第{_ORDINAL}([上下]?)')
_TITLED_LABEL = re.compile(r'([\u3400-\u9fff]{1,6})帝[上下]?')


def _title_key(text):
    return zh.to_simplified(text).replace('眞', '真')


def _house(book):
    return HOUSES.get(_title_key(book).replace('後', '后'))


def _toc_rows(book, text):
    for line in text.splitlines():
        if not (match := TOC_LINK.fullmatch(line)):
            continue
        target, description = match.groups()
        description = _title_key(description)
        row = _TOC_ANNAL.fullmatch(description) or _TOC_TITLED.fullmatch(description)
        if row:
            yield _title_key(book+target if target.startswith('/') else target), ''.join(row.groups()), line


def _catalog(signature):
    manifest_path = Path(signature[0])
    try:
        books = json.loads(manifest_path.read_text(encoding='utf8'))['books']
    except (OSError, ValueError, KeyError):
        return {}
    archive = (Path(config.PROJECT_ROOT)/'corpus/wikisource').resolve()
    catalog = {}
    for book_id, book in books.items():
        if not _house(book['title']):
            continue
        try:
            meta = book['toc']
            path = (archive/meta['path']).resolve()
            if not path.is_relative_to(archive):
                continue
            source = json.loads(path.read_text(encoding='utf8'))
            text = source['wikitext']
            if (source['revision_id'] != meta['revision_id'] or source['title'] != book['title']
                    or hashlib.sha256(text.encode()).hexdigest() != meta['text_sha256']):
                continue
        except (OSError, ValueError, KeyError):
            continue
        catalog[book_id] = (dict((target,(label,line)) for target,label,line in _toc_rows(book['title'],text)),
                            meta['revision_id'],meta['source_url'])
    return catalog


def _continuations(entry, chapters, toc):
    labels, revision, source = toc
    title = _title_key(entry['aliases'][0][1:])
    first = labels.get(_title_key(entry['title']))
    if not first or first[0] not in (title+'一', title+'上'):
        return []
    suffixes = '二三四五六七八九' if first[0].endswith('一') else '下'
    by_volume = {}
    for chapter in chapters:
        by_volume.setdefault(chapter['sort_number'], []).append(chapter)
    result = []
    for number, suffix in enumerate(suffixes, entry['sort_number']+1):
        volumes = by_volume.get(number, ())
        if suffix == '下':
            volumes = [*by_volume.get(entry['sort_number'], ()), *volumes]
        matches = [r for r in volumes
                   if labels.get(_title_key(r['title']), ('',))[0] == title+suffix]
        if len(matches) != 1:
            break
        row = matches[0]
        opening = _title_key((row['opening'] or '').strip())
        # If the reader has a numbered heading, it must agree with the catalog.
        if re.fullmatch(r'[\u3400-\u9fff]{1,7}[祖宗帝][一二三四五六七八九上下]',opening) and opening != title+suffix:
            break
        link = dict(page_id=row['page_id'],title=row['title'],label=title+suffix,
                           revision_id=row['revision_id'],source_url=row['source_url'],
                           toc_revision_id=revision,toc_source_url=source,
                           toc_line=labels[_title_key(row['title'])][1])
        if 'source_sha256' in row.keys():
            link['source_sha256'] = row['source_sha256']
        result.append(link)
    return result


def opening_aliases(book, text):
    house = _house(book)
    if not house:
        return []
    opening = re.sub(r'\s+', '', zh.to_simplified(text))
    match = re.match(r'([\u3400-\u9fff][祖宗])[^，。；]{0,45}?皇帝[，,]?讳([\u3400-\u9fff]{1,3})[，,。]', opening)
    if not match:
        return []
    dynasty, surname = house
    title, name = match.groups()
    return [dynasty + title, surname + name]


def _titled_opening(book, text, catalog_label):
    """A distinctive epithet must agree in the catalog and the opening body."""
    label = _TITLED_LABEL.fullmatch(catalog_label)
    if not label or not (house := _house(book)):
        return [], []
    epithet = label[1]
    opening = re.sub(r'\s+', '', _title_key(text))
    match = re.match(rf'(?:[\u3400-\u9fff][祖宗])?{re.escape(epithet)}皇帝[，,]?讳'
                     r'([\u3400-\u9fff]{1,3})[，,。]', opening)
    if not match:
        return [], []
    dynasty, surname = house
    title = epithet + '帝'
    return [dynasty + title, surname + match[1]], [title, *([epithet] if len(epithet) > 1 else [])]


@lru_cache(maxsize=1)
def _entries(signature, catalog_signature):
    catalog = _catalog(catalog_signature)
    with punctuated.connect() as conn:
        rows = conn.execute("""
            SELECT c.page_id,c.title,c.book_id,b.title AS book_title,c.revision_id,c.source_url,c.sort_number,
                   c.source_sha256,
                   json_extract(c.blocks_json,'$[0].kind') AS opening_kind,
                   json_extract(c.blocks_json,'$[0].text') AS opening
            FROM chapters c JOIN books b USING(book_id)
            WHERE c.status IN ('readable','partial_render','insufficient_punctuation')
        """)
        entries = []
        chapters = {}
        for row in rows:
            if row['book_id'] in catalog:
                chapters.setdefault(row['book_id'], []).append(row)
            opening = row['opening'] or ''
            toc = catalog.get(row['book_id'])
            found = toc[0].get(_title_key(row['title'])) if toc else None
            aliases, short_titles = [], []
            if found and _TITLED_LABEL.fullmatch(found[0]):
                body = opening
                if row['opening_kind'] != 'body':
                    first_body = conn.execute("""SELECT json_extract(value,'$.text') FROM chapters, json_each(blocks_json)
                        WHERE page_id=? AND json_extract(value,'$.kind')='body' ORDER BY key LIMIT 1""",
                        (row['page_id'],)).fetchone()
                    body = first_body[0] if first_body else ''
                aliases, short_titles = _titled_opening(row['book_title'], body, found[0])
                if aliases:
                    opening = body
            if not aliases:
                aliases = opening_aliases(row['book_title'], opening)
            if aliases:
                entry = dict(page_id=row['page_id'],title=row['title'],book_id=row['book_id'],
                             book_title=row['book_title'],aliases=aliases,source_sha256=row['source_sha256'],
                             revision_id=row['revision_id'],source_url=row['source_url'],sort_number=row['sort_number'],
                             quote=re.match(r'[^。]*。?',opening.strip())[0])
                if short_titles:
                    entry.update(short_titles=short_titles,toc_line=found[1],toc_revision_id=toc[1],toc_source_url=toc[2])
                entries.append(entry)
        for entry in entries:
            book_id = entry['book_id']
            entry['continuations'] = _continuations(entry, chapters[book_id], catalog[book_id]) if book_id in catalog else []
        return entries


def lookup(query, book=None):
    key = _title_key(query.strip())
    if not re.fullmatch(r'[\u3400-\u9fff]{2,5}',key):
        return []
    path = punctuated.index_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    manifest = path.parent/'2026-10-25'/'acquisition.json'
    try:
        catalog_mtime = manifest.stat().st_mtime_ns
    except FileNotFoundError:
        catalog_mtime = None
    entries = _entries((str(path), stat.st_size, stat.st_mtime_ns), (str(manifest), catalog_mtime))
    matches = [e for e in entries if key in e['aliases'] and (not book or e['book_id']==book)]
    if matches:
        return matches
    # Unqualified epithets need a catalog-backed source and a unique identity.
    short = [e for e in entries if key in e.get('short_titles', ()) and (not book or e['book_id']==book)]
    competing = {tuple(e['aliases']) for e in entries if key == e['aliases'][0][1:]
                 and (not book or e['book_id']==book)}
    return short if len(competing | {tuple(e['aliases']) for e in short}) == 1 else []


def resolved(query):
    entries = lookup(query)
    # Several editions may attest the same pair. Conflicting pairs stay separate.
    if len({tuple(e['aliases']) for e in entries}) == 1:
        return entries
    return []
