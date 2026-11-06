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
TOC_LINE = re.compile(r'^[#*]+\s*\[\[([^|\]]+)(?:\|[^\]]*)?\]\]\s*本紀第[一二三四五六七八九十百〇]+[:：]?\s*(\S+)\s*$')


def _title_key(text):
    return zh.to_simplified(text).replace('眞', '真')


def _toc_rows(book, text):
    for line in text.splitlines():
        match = TOC_LINE.fullmatch(line)
        if match:
            target, label = match.groups()
            yield _title_key(book+target if target.startswith('/') else target), _title_key(label), line


def _catalog(signature):
    manifest_path = Path(signature[0])
    try:
        books = json.loads(manifest_path.read_text(encoding='utf8'))['books']
    except (OSError, ValueError, KeyError):
        return {}
    archive = (Path(config.PROJECT_ROOT)/'corpus/wikisource').resolve()
    catalog = {}
    for book_id, book in books.items():
        if _title_key(book['title']) not in HOUSES:
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
        matches = [r for r in by_volume.get(number, ())
                   if labels.get(_title_key(r['title']), ('',))[0] == title+suffix]
        if len(matches) != 1:
            break
        row = matches[0]
        opening = _title_key((row['opening'] or '').strip())
        # If the reader has a numbered heading, it must agree with the catalog.
        if re.fullmatch(r'[\u3400-\u9fff][祖宗][一二三四五六七八九上下]',opening) and opening != title+suffix:
            break
        result.append(dict(page_id=row['page_id'],title=row['title'],label=title+suffix,
                           revision_id=row['revision_id'],source_url=row['source_url'],
                           toc_revision_id=revision,toc_source_url=source,
                           toc_line=labels[_title_key(row['title'])][1]))
    return result


def opening_aliases(book, text):
    house = HOUSES.get(zh.to_simplified(book))
    if not house:
        return []
    opening = re.sub(r'\s+', '', zh.to_simplified(text))
    match = re.match(r'([\u3400-\u9fff][祖宗])[^，。；]{0,45}?皇帝[，,]?讳([\u3400-\u9fff]{1,3})[，,。]', opening)
    if not match:
        return []
    dynasty, surname = house
    title, name = match.groups()
    return [dynasty + title, surname + name]


@lru_cache(maxsize=1)
def _entries(signature, catalog_signature):
    catalog = _catalog(catalog_signature)
    with punctuated.connect() as conn:
        rows = conn.execute("""
            SELECT c.page_id,c.title,c.book_id,b.title AS book_title,c.revision_id,c.source_url,c.sort_number,
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
            aliases = opening_aliases(row['book_title'], opening)
            if aliases:
                entries.append(dict(page_id=row['page_id'],title=row['title'],book_id=row['book_id'],
                                    book_title=row['book_title'],aliases=aliases,
                                    revision_id=row['revision_id'],source_url=row['source_url'],sort_number=row['sort_number'],
                                    quote=opening.strip().split('。')[0]+'。'))
        for entry in entries:
            book_id = entry['book_id']
            entry['continuations'] = _continuations(entry, chapters[book_id], catalog[book_id]) if book_id in catalog else []
        return entries


def lookup(query, book=None):
    # Bare ambiguous titles and questions do not select an identity.
    key = zh.to_simplified(query.strip())
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
    return [e for e in entries if key in e['aliases'] and (not book or e['book_id']==book)]


def resolved(query):
    entries = lookup(query)
    # Several editions may attest the same pair. Conflicting pairs stay separate.
    if len({tuple(e['aliases']) for e in entries}) == 1:
        return entries
    return []
