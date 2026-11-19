"""Read body chunks scoped to a named biography and one pinned source."""
import json
import re

from search import zh


def named_biographies(conn, name, subject, book=None):
    pages = {
        (chunk['book_id'], chunk['page_id'])
        for hit in subject
        for chunk in (hit['chunk'],)
        if zh.to_simplified(chunk['heading']).strip() == name
        and (not book or chunk['book_id'] == book)
    }
    if not pages:
        # Some biographies start with a short name and have no literal anchor.
        sql = "SELECT DISTINCT book_id,page_id FROM chunks WHERE json_extract(payload,'$.heading') IN (?,?)"
        args = (name, zh.to_traditional(name))
        if book:
            sql += " AND book_id=?"
            args += (book,)
        pages = {tuple(row) for row in conn.execute(sql, args)}

    opening = re.compile(r'\s*(?:' + re.escape(name) + '|' + re.escape(name[-1]) + r')[，,]?\s*字')
    for book_id, page_id in sorted(pages):
        versions = {}
        for row in conn.execute(
            'SELECT payload FROM chunks WHERE book_id=? AND page_id=? ORDER BY chunk_id',
            (book_id, page_id),
        ):
            chunk = json.loads(row[0])
            if chunk['layer'] != 'body' or zh.to_simplified(chunk['heading']).strip() != name:
                continue
            if chunk.get('revision_id') and chunk.get('source_sha256'):
                versions.setdefault((chunk['revision_id'], chunk['source_sha256']), {})[chunk['chunk_id']] = chunk
        for peers in versions.values():
            if opening.match(zh.to_simplified(next(iter(peers.values()))['text_orig'])):
                yield peers
