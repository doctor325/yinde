"""In-memory fixture with real trigram/bigram indexes; no corpus writes or temp files."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.pipeline.sqlite_store import SCHEMA, _fts_build
from search import engine as E, result_block as RB, zh

conn = sqlite3.connect(':memory:')
try:
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO books(book_id,book_dir,title,family,edition) VALUES('A','a','甲','tls','tls')")
    conn.execute("INSERT INTO books(book_id,book_dir,title,family,edition) VALUES('B','b','乙','sbck','sbck')")
    texts = ['內應', '内应', '齊桓公', '齐桓公', '齊桓公 内应', '齐桓公 內應',
             '齊桓公 管仲 内', '齐桓公 内', '齊桓公 管仲', '内应 齊桓公 管仲',
             '荆燕吳傳第五', '內應', '內%應', '內_應', '內\\應']
    for pid, text in enumerate(texts, 1):
        book = 'B' if pid == 12 else 'A'
        conn.execute("INSERT INTO files(file_id,book_id,file_name,file_no,sha256,origin_path) VALUES(?,?,?,?,?,?)",
                     (pid, book, f'{pid}.txt', pid, 'fixture', 'fixture'))
        conn.execute("INSERT INTO passages(passage_id,book_id,file_id,row_no,seq,kind,layer,status,text_orig,normalized_text) "
                     "VALUES(?,?,?,1,1,'passage','main','ok',?,?)", (pid, book, pid, text, text))
    conn.execute("INSERT INTO sections(book_id,file_id,label,first_row) VALUES('A',11,'荆燕吳傳第五',1)")
    _fts_build(conn, {})
    cur = conn.cursor()
    assert E.fts_tokenizer(cur) == 'trigram' and E.has_bigram_fts(cur)
    cases = [
        ('內應', [1,2,5,6,10,12], None),
        ('齊桓公', [3,4,5,6,7,8,9,10], None),
        ('齊桓公 內應', [5,6,10], None),
        ('齊桓公 内 管仲', [7,10], None),
        ('内应 管仲', [10], None),
        ('內應', [1,2,5,6,10], 'A'),
        ('內應', [12], 'B'),
        ('内%应', [13], None), ('内_应', [14], None), ('内\\应', [15], None),
        ('內應\u0085管仲', [10], None),
    ]
    payload_cases = []
    for q, expected, book in cases:
        r = E.run_search(cur, q, book=book, variants=True, page_size=100)
        assert sorted(x['passage_id'] for x in r['items']) == expected, (q, r)
        assert r['mode'] == 'like'
        h = RB._fetch_hits(cur, zh.to_traditional(q), book, None, True, q)
        assert sorted(x['passage_id'] for x in h) == expected, q
        b = RB.search_result_blocks(cur, q, book=book, variants=True)
        assert b['hit_total'] == len(expected) and b['exec_mode'] == 'like', q
        payload_cases.append(dict(q=q, expected=expected, book=book,
                                  result=r, blocks=b))
    assert E.run_search(cur, '內應', variants=False)['mode'] == 'bigram'
    assert E.run_search(cur, '齊桓公', variants=False)['mode'] == 'fts'
    assert E.run_search(cur, '內應', variants=False)['total'] == 3
    assert E.run_search(cur, '齊桓公', variants=False)['total'] == 5
    assert RB.search_result_blocks(cur, '荆燕吳傳第五', variants=True)['section_truncated'] is False
    idx = RB._section_index(cur)
    assert len(RB._section_hits(cur, zh.to_traditional('荆燕吳傳第五'), None, None, idx, True, '荆燕吳傳第五')[0]) == 1
    rows = [dict(r) for r in cur.execute('SELECT * FROM passages ORDER BY passage_id')]
    columns = list(rows[0])
    packed_rows = [[0 if k == 'kind' else r[k] for k in columns] for r in rows]
    payload = dict(packed=dict(format=1, columns=columns, dict_columns=['kind'],
                              dicts={'kind':['passage']}, rows=packed_rows,
                              bm25={'trigram':{}, 'bigram':{}}),
                   books=[dict(r) for r in cur.execute('SELECT * FROM books')],
                   files={str(r['file_id']):dict(r) for r in cur.execute('SELECT * FROM files')},
                   sections=[dict(r) for r in cur.execute('SELECT * FROM sections')],
                   cases=payload_cases, sectionQuery='荆燕吳傳第五')
    # Empty objects are truthy in JS, so both indexed branches would be selected without the fix.
    result = subprocess.run(['node', str(Path(__file__).with_suffix('.mjs'))],
        input=json.dumps(payload, ensure_ascii=False), encoding='utf-8', capture_output=True, cwd=ROOT)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stderr
    print(f'Python PASS: {len(cases)} keyword/block cases, literal wildcards, scopes, legacy indexed paths')

finally:
    conn.close()
