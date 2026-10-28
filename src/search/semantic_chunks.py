"""Deterministic source-bound chunks. No model imports or corpus writes."""
from bisect import bisect_right
import hashlib
import json
import re

VERSION = "source-block-v1"
MAX_CHARS = 280
OVERLAP_CHARS = 48
LAYERS = ("main", "commentary_candidate")
PROTECTED = re.compile(r"<pb:[^>]*>|&KR[A-Za-z0-9]+;")


def record_parts(row, maximum=MAX_CHARS):
    text = row["text_orig"] or ""
    start = 0
    protected = list(PROTECTED.finditer(text))
    while start < len(text):
        end = min(start + maximum, len(text))
        for match in protected:
            if match.start() < end < match.end():
                end = match.start() if match.start() > start else match.end()
                break
        yield {"passage_id": row["passage_id"], "row_no": row["row_no"], "seq": row["seq"],
               "start": start, "end": end, "text": text[start:end]}
        start = end


def iter_chunks(conn, maximum=MAX_CHARS, overlap=OVERLAP_CHARS):
    if maximum < 32 or not 0 <= overlap < maximum:
        raise ValueError("invalid chunk limits")
    files = {r["file_id"]: dict(r) for r in conn.execute(
        "SELECT f.file_id,f.book_id,f.file_name,f.origin_path,f.sha256,b.title AS book_title,b.edition "
        "FROM files f JOIN books b ON b.book_id=f.book_id")}
    sections = {}
    for r in conn.execute("SELECT section_id,file_id,label,first_row,last_row FROM sections "
                          "WHERE first_row IS NOT NULL ORDER BY file_id,first_row,section_id"):
        sections.setdefault(r["file_id"], []).append(dict(r))
    starts = {fid: [x["first_row"] for x in xs] for fid, xs in sections.items()}
    parts, key, metadata = [], None, None
    chunk_id = 0

    def pack():
        text = "".join(x["text"] for x in parts)
        return {**metadata, "chunk_id": chunk_id, "text_orig": text,
                "row_first": parts[0]["row_no"], "row_last": parts[-1]["row_no"],
                "parts": [{k: v for k, v in p.items() if k != "text"} for p in parts],
                "text_sha256": hashlib.sha256(text.encode()).hexdigest()}

    rows = conn.execute("SELECT passage_id,file_id,row_no,seq,kind,layer,juan,text_orig FROM passages "
                        "ORDER BY file_id,row_no,seq")
    for row in rows:
        # A non-passage or another layer is a real boundary, never deleted and bridged.
        valid = row["kind"] == "passage" and row["layer"] in LAYERS
        if not valid:
            if parts:
                chunk_id += 1
                yield pack()
                parts = []
            key = None
            continue
        fid = row["file_id"]
        i = bisect_right(starts.get(fid, []), row["row_no"]) - 1
        section = sections[fid][i] if i >= 0 else None
        if section and section["last_row"] is not None and row["row_no"] > section["last_row"]:
            section = None
        current_key = (fid, row["layer"], row["juan"], section["section_id"] if section else None)
        if current_key != key:
            if parts:
                chunk_id += 1
                yield pack()
            parts = []
            key = current_key
        metadata = {**files[fid], "layer": row["layer"], "juan": row["juan"],
                    "section": section["label"] if section else None,
                    "boundary_known": section is not None}
        for part in record_parts(row, maximum):
            if parts and sum(len(p["text"]) for p in parts) + len(part["text"]) > maximum:
                chunk_id += 1
                yield pack()
                tail = []
                for previous in reversed(parts):
                    if sum(len(p["text"]) for p in tail) >= overlap:
                        break
                    tail.insert(0, previous)
                while tail and sum(len(p["text"]) for p in tail) + len(part["text"]) > maximum:
                    tail.pop(0)
                parts = tail
            parts.append(part)
    if parts:
        chunk_id += 1
        yield pack()


def embedding_text(chunk):
    from search.zh import to_simplified
    text = re.sub(r"<pb:[^>]*>|¶|\s+", "", chunk["text_orig"])
    # Missing glyph codes are preserved in source but not interpreted as ordinary words.
    text = re.sub(r"&KR[A-Za-z0-9]+;", "□", text)
    return to_simplified(f"{chunk['book_title'][:32]} {str(chunk.get('section') or '')[:32]} {text}")
