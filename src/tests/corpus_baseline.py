"""Versioned expected results, guarded by the complete source fingerprint."""
import hashlib
import json
from pathlib import Path


def load(conn):
    fixture=json.loads(Path(__file__).with_name('corpus_baseline_28.json').read_text(encoding='utf8'))
    sources=[list(row) for row in conn.execute(
        'SELECT book_id,origin_path,sha256 FROM files ORDER BY origin_path')]
    digest=hashlib.sha256(json.dumps(sources,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
    if digest != fixture['source_digest']:
        raise AssertionError('Corpus source version changed; audit before updating baseline')
    for word,expected in fixture['literal_counts'].items():
        from search import zh
        count=conn.execute("SELECT COUNT(*) FROM passages WHERE kind='passage' AND file_id<=1026 AND normalized_text LIKE ?",
                           ('%'+zh.to_traditional(word)+'%',)).fetchone()[0]
        if count != expected['legacy19']:
            raise AssertionError('Legacy recall changed: '+word)
    return fixture
