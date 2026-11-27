"""Source-bound book and chapter labels from the acquisition table of contents.

The catalog is the edition's own TOC, not a hand-written name list. When the
local acquisition file or its archived TOC page is unavailable, callers get an
empty mapping and must fall back to unlabelled retrieval.
"""
import json
import re
import sqlite3
import unicodedata
from functools import lru_cache

from search import punctuated, zh

_SUB = re.compile(r'<sub>.*?</sub>', re.S)
_BRACE = re.compile(r'-\{|\}-')
_MENTION = re.compile(r'《([^》]{1,60})》')
_DOT = re.compile(r'[·・•‧]')
_BARE = re.compile(r'([㐀-鿿]{1,8})[·・•‧]([㐀-鿿]{2,20})')
_QUOTE_SPLIT = re.compile(r'[，,。；;：:！!？?、\s“”「」『』（）()《》〈〉]+')


def normalize(text):
    """Compare labels without spaces, wiki markup, case or punctuation."""
    text = _SUB.sub('', zh.to_simplified(text or ''))
    text = _BRACE.sub('', text)
    return ''.join(c for c in text.lower() if unicodedata.category(c)[0] in 'LN')


def _quote_text(text):
    # ``於`` and ``于`` are the same preposition but the simplifier keeps ``於``
    # for its surname reading; quotation matching must not split on that.
    return zh.to_simplified(text or '').replace('於', '于')


def quote_segments(text, minimum=4):
    """Split a quoted passage into punctuation-free segments worth matching."""
    segments = []
    for part in _QUOTE_SPLIT.split(_quote_text(text)):
        key = normalize(part)
        if len(key) >= minimum and key not in segments:
            segments.append(key)
    return segments


def quote_matches(text, quote, minimum=2):
    """Match a quote tolerantly against a source text.

    Questions paraphrase quotations, drop a character, or use a variant. Two
    distinct segments of the question's quotation must occur in the source, so
    a shared common phrase alone is not enough.
    """
    segments = quote_segments(quote)
    if not segments:
        return False
    haystack = normalize(_quote_text(text))
    return sum(segment in haystack for segment in segments) >= min(minimum, len(segments))


def _acquisition():
    path = punctuated.index_path().parent / '2026-10-25' / 'acquisition.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))['books']
    except (OSError, ValueError, KeyError, TypeError):
        return {}


@lru_cache(maxsize=1)
def _books():
    mapping = {}
    for book_id, entry in _acquisition().items():
        if not isinstance(entry, dict):
            continue
        for name in (entry.get('title'), entry.get('source_title')):
            if name:
                mapping.setdefault(normalize(name), set()).add(book_id)
    return {key: tuple(sorted(value)) for key, value in mapping.items()}


def books():
    """Map normalized edition title or upstream title to book ids."""
    return _books()


@lru_cache(maxsize=None)
def _chapters(book_id):
    from search.reading_grounding import _catalog_titles
    mapping = {}
    for label, titles in _catalog_titles(book_id).items():
        key = normalize(label)
        if not key:
            continue
        mapping.setdefault(key, set()).update(normalize(title) for title in titles)
    return {key: frozenset(value) for key, value in mapping.items()}


def chapters(book_id):
    """Map a normalized TOC label to normalized page titles."""
    return _chapters(book_id)


def book_ids(name):
    return books().get(normalize(name), ())


def catalogued_books():
    """Every book id that has an acquisition entry."""
    return tuple(sorted({book_id for ids in books().values() for book_id in ids}))


def mentions(text):
    """Return the book names and chapter labels named in a question.

    ``《史记·李将军列传》`` names both; ``《史记》`` only a book and
    ``《李斯列传》`` only a chapter. A label that is itself a known book title
    is never treated as a chapter.
    """
    text = zh.to_simplified(text or '')
    book_names, labels = [], []
    for inner in _MENTION.findall(text):
        parts = [part.strip() for part in _DOT.split(inner) if part.strip()]
        if not parts:
            continue
        if len(parts) >= 2:
            book_names.append(parts[0])
            labels.extend(parts[1:])
        elif book_ids(parts[0]):
            book_names.append(parts[0])
        else:
            labels.append(parts[0])
    for book, label in _BARE.findall(text):
        book_names.append(book)
        labels.append(label)
    return list(dict.fromkeys(book_names)), list(dict.fromkeys(labels))


def chapter_targets(book_id, label):
    """Resolve a chapter label through the edition's own TOC.

    Exact labels win. A longer TOC description may carry a division prefix or
    a list of persons after the chapter name, so a whole-label prefix/suffix is
    accepted next. Arbitrary substrings are never accepted.
    """
    key = normalize(label)
    if len(key) < 2:
        return frozenset()
    catalog = chapters(book_id)
    if key in catalog:
        return catalog[key]
    if len(key) < 3:
        return frozenset()
    found = set()
    for description, titles in catalog.items():
        if description.startswith(key) or description.endswith(key):
            found.update(titles)
    return frozenset(found)


def page_ids(conn, book_id, targets):
    """Find source pages by their title in the search index."""
    if not targets:
        return ()
    try:
        rows = conn.execute(
            "SELECT DISTINCT page_id, json_extract(payload,'$.title') AS title "
            "FROM chunks WHERE book_id=?", (book_id,)).fetchall()
    except sqlite3.OperationalError:
        return ()
    wanted = set(targets)
    found = []
    for page_id, title in rows:
        if title and normalize(title) in wanted:
            found.append(page_id)
    return tuple(dict.fromkeys(found))
