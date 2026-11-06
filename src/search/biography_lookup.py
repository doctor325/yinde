"""Find source-attested imperial biography openings for exact name searches."""
from functools import lru_cache
import re
from search import punctuated, zh

# Dynasty and ruling surname identify the book's royal house, not an individual.
HOUSES = {'宋史': ('宋', '赵'), '旧唐书': ('唐', '李'), '新唐书': ('唐', '李'),
          '明史': ('明', '朱'), '汉书': ('汉', '刘'), '后汉书': ('汉', '刘'),
          '晋书': ('晋', '司马'), '隋书': ('隋', '杨')}


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
def _entries(signature):
    with punctuated.connect() as conn:
        rows = conn.execute("""
            SELECT c.page_id,c.title,b.title AS book_title,
                   json_extract(c.blocks_json,'$[0].text') AS opening
            FROM chapters c JOIN books b USING(book_id)
            WHERE c.status IN ('readable','partial_render','insufficient_punctuation')
        """)
        entries = []
        for row in rows:
            opening = row['opening'] or ''
            aliases = opening_aliases(row['book_title'], opening)
            if aliases:
                entries.append(dict(page_id=row['page_id'],title=row['title'],
                                    book_title=row['book_title'],aliases=aliases,
                                    quote=opening.strip().split('。')[0]+'。'))
        return entries


def lookup(query, book=None):
    # Bare ambiguous titles and questions do not select an identity.
    key = zh.to_simplified(query.strip())
    path = punctuated.index_path()
    stat = path.stat()
    entries = _entries((str(path), stat.st_size, stat.st_mtime_ns))
    if book:
        with punctuated.connect() as conn:
            row = conn.execute('SELECT title FROM books WHERE book_id=?',(book,)).fetchone()
        return [e for e in entries if key in e['aliases'] and row and e['book_title']==row['title']]
    return [e for e in entries if key in e['aliases']]
