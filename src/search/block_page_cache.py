"""One bounded assembly cache. Keys include on-disk database generation.

No connections, cursors, source rows or models are retained. Transactions and
in-memory databases bypass the cache. Each response gets its own mutable copy.
"""
from copy import deepcopy
from pathlib import Path
from threading import Lock

_lock=Lock()
_entry=None


def key(cur, query):
    conn=cur.connection
    if conn.in_transaction or conn.total_changes:return None
    rows=conn.execute('PRAGMA database_list').fetchall()
    path=next((r[2] for r in rows if r[1]=='main'),None)
    if not path:return None
    def stamp(p):
        try:
            s=Path(p).stat();return s.st_ino,s.st_size,s.st_mtime_ns
        except FileNotFoundError:return None
    return (path,stamp(path),stamp(path+'-wal'),*query)


def get(token, start=None, stop=None):
    if token is None:return None
    with _lock:
        if _entry is not None and _entry[0]==token:
            value=_entry[1]
            if start is None:return deepcopy(value)
            result={k:deepcopy(v) for k,v in value.items() if k!='results'}
            result['results']=deepcopy(value['results'][start:stop])
            return result


def put(token, value):
    global _entry
    if token is None:return
    blocks=value['results']
    estimated=sum(len(b['text'])*4+len(b['passage_ids'])*40+1200 for b in blocks)
    if estimated>64*1024*1024:return
    with _lock:_entry=(token,deepcopy(value))
