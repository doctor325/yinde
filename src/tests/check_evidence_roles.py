"""Frozen synthetic role contrasts: rank preference, not historical accuracy."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import types

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from search import evidence_pipeline as E, semantic_bridge as B, punctuated_search as S


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'src/data') or output.exists():raise ValueError('Invalid output')
    old=types.ModuleType('baseline_evidence')
    source=subprocess.check_output(['git','show','f0e2bd9:src/search/evidence_pipeline.py'],cwd=ROOT).decode('utf8')
    exec(compile(source,'baseline_evidence','exec'),old.__dict__)
    fixture=ROOT/'src/tests/semantic_role_pairs.json'
    rows=[]
    try:
        for i,case in enumerate(json.loads(fixture.read_text(encoding='utf8'))['cases'],1):
            with sqlite3.connect(':memory:') as db:
                db.executescript('''CREATE TABLE books(book_id TEXT,title TEXT);
                    INSERT INTO books VALUES('synthetic','合成语言测试');
                    CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,search_text TEXT,payload TEXT);
                    CREATE VIRTUAL TABLE chunk_fts USING fts5(search_text,tokenize='trigram');''')
                dense=[]
                # Distractor first; an unchanged order is never credited as success.
                for cid,label in [(1,'distractor'),(2,'relevant')]:
                    text=case[label]
                    c=dict(chunk_id=cid,page_id=cid,book_id='synthetic',book_title='合成语言测试',heading='',
                           revision_id=1,source_sha256='synthetic-'+str(cid),layer='body',text_orig=text,
                           parts=[dict(block_index=0,start=0,end=len(text))])
                    db.execute('INSERT INTO chunks VALUES(?,?,?,?)',(cid,c['book_id'],S.normalize(text),json.dumps(c)))
                    db.execute('INSERT INTO chunk_fts(rowid,search_text) VALUES(?,?)',(cid,S.normalize(text)))
                    dense.append(dict(chunk=c,semantic_score=.5))
                row=dict(index=i,relation_class=case['class'],query=case['query'])
                for label,engine in [('before',old),('after',E)]:
                    r=engine.evaluate(case['query'],db,dense,[])
                    row[label]=dict(preferred_relevant=r['hits'][0]['chunk']['chunk_id']==2,
                        hits=[dict(id=h['chunk']['chunk_id'],score=h['context_relevance_score'],audit=h['evidence_audit']) for h in r['hits']])
                rows.append(row)
                print(i,case['class'],row['before']['preferred_relevant'],'->',row['after']['preferred_relevant'],flush=True)
    finally:B.worker.close()
    output.write_text(json.dumps(dict(fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
        policy='Frozen synthetic contrast, not real history or answer accuracy',rows=rows),ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
