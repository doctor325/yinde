import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from search import evidence_pipeline as E, local_rag


class Sources:
    """Minimal source witness with real slice verification."""

    def __init__(self, blocks, **page):
        self.blocks = blocks
        self.meta = dict(page_id=1, book_id='a', revision_id=1, source_sha256='source',
                         title='甲史/卷1', source_url='https://example.invalid', **page)

    def page(self, page_id):
        return dict(self.meta, page_id=page_id), self.blocks

    def check(self, ref):
        block, lo, hi = ref['ranges'][0]
        if self.blocks[block]['text'][lo:hi] != ref['quote']:
            raise ValueError('citation differs from source')
        return ref


class Lenient:
    def check(self, ref):
        return ref


def chunk(text, *, chunk_id=1, page_id=1, book_id='a', title='甲史/卷1',
          block=0, start=0, end=None, heading='甲'):
    return dict(chunk_id=chunk_id, page_id=page_id, book_id=book_id, book_title='甲史',
                revision_id=1, source_sha256='source', title=title,
                source_url='https://example.invalid', heading=heading, layer='body',
                text_orig=text, parts=[dict(block_index=block, start=start,
                                            end=len(text) if end is None else end)])


class LocalRag(unittest.TestCase):
    def test_long_unbalanced_quote_keeps_a_bounded_excerpt(self):
        text = '甲' * 300 + '「' + '乙' * 3000 + '。' + '丙' * 300
        focus = '乙' * 80
        start = text.index(focus, 1000)
        item = chunk(focus, start=start, end=start + len(focus))
        packet = local_rag.evidence_packet(dict(query='某问题', hits=[dict(chunk=item)]),
                                           Sources([dict(kind='body', text=text)]))
        self.assertEqual(len(packet['evidence']), 1)
        evidence = packet['evidence'][0]
        self.assertTrue(evidence.get('excerpt'))
        self.assertLessEqual(len(evidence['quote']), local_rag.MAX_EXCERPT_CHARS)
        self.assertIn(focus, evidence['quote'])
        self.assertTrue(evidence['truncated_before'])
        self.assertTrue(evidence['truncated_after'])

    def test_quantity_guard_rejects_an_unstated_count(self):
        quote = '廣出獵，見草中石，以爲虎而射之，中石沒鏃，視之石也。因復更射之，終不能復入石矣。'
        packet = dict(evidence=[dict(id='E1', quote=quote, ranges=[[0, 0, len(quote)]],
                                     page_id=1, book_id='a', revision_id=1,
                                     source_sha256='source', title='甲史/卷1',
                                     source_url='', heading='甲', layer='body')])
        source = Sources([dict(kind='body', text=quote)])
        raw = dict(status='answer', claims=[dict(text='李广一共用了两支箭。', evidence_ids=['E1'])])
        answer = local_rag.validate_answer(raw, packet, source)
        self.assertEqual(answer['status'], 'insufficient')
        self.assertEqual(answer['claims'], [])
        self.assertEqual(answer['rejected_claims'][0]['unsupported_quantities'], ['两支'])

        raw = dict(status='answer', claims=[dict(text='李广把草中石当成老虎射击。', evidence_ids=['E1'])])
        self.assertEqual(local_rag.validate_answer(raw, packet, source)['status'], 'answer')

    def test_quantity_guard_accepts_an_exact_original_count(self):
        quote = '夜擊阬秦軍二十餘萬人。'
        packet = dict(evidence=[dict(id='E1', quote=quote, ranges=[[0, 0, len(quote)]],
                                     page_id=1, book_id='a', revision_id=1,
                                     source_sha256='source', title='甲史/卷1',
                                     source_url='', heading='甲', layer='body')])
        source = Sources([dict(kind='body', text=quote)])
        raw = dict(status='answer', claims=[dict(text='坑杀秦军二十余万人。', evidence_ids=['E1'])])
        self.assertEqual(local_rag.validate_answer(raw, packet, source)['status'], 'answer')

    def test_quoted_page_uses_the_upstream_book_title(self):
        question = '《汉书》里“人固有一死，或重于泰山，或轻于鸿毛”是谁说的？'
        hit = dict(chunk=dict(book_id='KR2a0007', page_id=19688, book_title='前漢書',
                              text_orig='人固有一死，或重于泰山，或轻于鸿毛'))
        with patch('search.source_catalog.book_ids',
                   side_effect=lambda name: ('KR2a0007',) if name == '汉书' else ()):
            self.assertEqual(local_rag._quoted_page(question, [hit]), ('KR2a0007', 19688))

    def test_multi_chapter_claim_must_cite_both_chapters(self):
        packet = dict(evidence=[
            dict(id='E1', book_id='a', title='甲史/卷1', quote='甲', ranges=[[0, 0, 1]],
                 page_id=1, revision_id=1, source_sha256='source', source_url='', heading='甲', layer='body'),
            dict(id='E2', book_id='a', title='甲史/卷2', quote='乙', ranges=[[0, 0, 1]],
                 page_id=2, revision_id=1, source_sha256='source', source_url='', heading='乙', layer='body'),
        ])
        question = '《甲史》的《甲本纪》和《乙列传》都写到这件事。两篇是否一致？'
        scopes = {'甲本纪': frozenset({'甲史卷1'}), '乙列传': frozenset({'甲史卷2'})}
        patches = (
            patch('search.source_catalog.mentions', return_value=(['甲史'], ['甲本纪', '乙列传'])),
            patch('search.source_catalog.book_ids', return_value=('a',)),
            patch('search.source_catalog.chapter_targets',
                  side_effect=lambda book_id, label: scopes[label]),
        )
        with patches[0], patches[1], patches[2]:
            one = dict(status='answer', claims=[dict(text='两篇都记载同一件事。', evidence_ids=['E1'])])
            answer = local_rag.resolve_answer(one, question, packet, Lenient())
            self.assertEqual(answer['status'], 'insufficient')
            self.assertIn('乙列传', answer['rejected_claims'][0]['missing_chapters'])

            both = dict(status='answer', claims=[dict(text='两篇都记载同一件事。', evidence_ids=['E1', 'E2'])])
            answer = local_rag.resolve_answer(both, question, packet, Lenient())
            self.assertEqual(answer['status'], 'answer')
            self.assertEqual(answer['claims'][0]['evidence_ids'], ['E1', 'E2'])

    def test_named_chapter_channel_uses_pooled_and_fallback_chunks(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY, book_id TEXT, '
                   'page_id INTEGER, payload TEXT)')
        rows = [(1, 'a', 10, '甲史/卷1', '沙丘之谋出于此。'),
                (2, 'a', 11, '甲史/卷2', '沙丘之谋又见于此。'),
                (3, 'a', 12, '甲史/卷3', '无关文字。')]
        chunks = {}
        for cid, book_id, page_id, title, text in rows:
            payload = chunk(text, chunk_id=cid, page_id=page_id, book_id=book_id, title=title)
            chunks[cid] = payload
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)',
                       (cid, book_id, page_id, json.dumps(payload, ensure_ascii=False)))
        scopes = {'甲本纪': frozenset({'甲史卷1'}), '乙列传': frozenset({'甲史卷2'})}
        with patch('search.source_catalog.mentions', return_value=(['甲史'], ['甲本纪', '乙列传'])), \
             patch('search.source_catalog.book_ids', return_value=('a',)), \
             patch('search.source_catalog.chapter_targets', side_effect=lambda book_id, label: scopes[label]):
            plan = dict(clues=[dict(text='沙丘之谋', forms=[])])
            selected = E.named_chapter_candidates(
                db, '《甲史》的《甲本纪》和《乙列传》', plan, None,
                (('pool', [dict(chunk=chunks[3]), dict(chunk=chunks[1])]),))
        self.assertEqual([(h['named_chapter'], h['chunk']['chunk_id']) for h in selected],
                         [('甲本纪', 1), ('乙列传', 2)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
