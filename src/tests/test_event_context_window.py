import json
import sqlite3
import unittest

from search import event_context_window as W, numbered_evidence as N


class EventContextWindow(unittest.TestCase):
    def source(self, *items):
        library = sqlite3.connect(':memory:')
        self.addCleanup(library.close)
        library.row_factory = sqlite3.Row
        library.execute('''CREATE TABLE chapters (
            page_id INTEGER, book_id TEXT, revision_id TEXT, source_sha256 TEXT,
            title TEXT, source_url TEXT, license_url TEXT, attribution TEXT, blocks_json TEXT
        )''')
        blocks = [dict(kind='heading', text='本傳')]
        for item in items:
            kind, text = ('body', item) if isinstance(item, str) else item
            blocks.append(dict(kind=kind, text=text))
        library.execute('INSERT INTO chapters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (1, 'book', 'rev', 'sha', '史書', '/source', '/license', '記載',
                         json.dumps(blocks)))
        return library, blocks

    def chunk(self, block_index, text, anchor):
        start = text.index(anchor)
        return dict(page_id=1, book_id='book', revision_id='rev', source_sha256='sha',
                    chunk_id=7, heading='本傳', layer='body', text_orig=anchor,
                    parts=[dict(block_index=block_index, start=start, end=start + len(anchor))])

    def selected_text(self, context, role='event_context'):
        return ''.join(segment['text'] for segment in context['segments'] if segment['role'] == role)

    def test_anchor_neighbors_and_negation_after_semicolon(self):
        text = '遠前。左句。\n\n受命求仕；終不赴任。右句。遠後。'
        library, _ = self.source(text)
        chunk = self.chunk(1, text, '求仕')
        context = W.assemble(library, chunk)
        self.assertEqual(context['version'], W.VERSION)
        self.assertTrue(context['input_ready'])
        self.assertEqual(context['selection']['anchor_parts'], chunk['parts'])
        self.assertEqual(self.selected_text(context), '左句。\n\n受命求仕；終不赴任。右句。')
        self.assertEqual([s['relation'] for s in context['selection']['sentences']],
                         ['left_neighbor', 'anchor', 'right_neighbor'])

    def test_nested_quote_and_anchor_across_blocks(self):
        left, right = '伊曰：「聞『母老。', '不忍遠行』；吾不仕。」遂歸。右句。'
        library, _ = self.source(left, right)
        chunk = self.chunk(1, left, '母老。')
        chunk['parts'].append(dict(block_index=2, start=0, end=2))
        chunk['text_orig'] += right[:2]
        context = W.assemble(library, chunk, neighbors=0)
        self.assertTrue(context['input_ready'])
        self.assertEqual(context['selection']['anchor_parts'], chunk['parts'])
        unit, = N.numbered(context)
        self.assertTrue(unit['complete'])
        self.assertEqual(unit['text'], left + '不忍遠行』；吾不仕。」遂歸。')
        self.assertEqual([part['block_index'] for part in unit['parts']], [1, 2])

    def test_source_partition_matches_every_original_coordinate(self):
        items = ('舊記。\n\n前句。', '又載。當日入', '城。其後歸。餘文。')
        library, blocks = self.source(*items)
        context = W.assemble(library, self.chunk(2, items[1], '當日'))
        self.assertEqual(context['scope_chars'], sum(map(len, items)))
        self.assertTrue(context['scope_truncated'])
        self.assertEqual(len(context['omissions']), 2)
        covered = []
        for part in context['segments'] + context['omissions']:
            block, start, end = (part[k] for k in ('block_index', 'start', 'end'))
            if 'role' in part:
                self.assertEqual(part['text'], blocks[block]['text'][start:end])
            else:
                self.assertEqual(set(part), {'block_index', 'start', 'end', 'reason'})
            covered.extend((block, i) for i in range(start, end))
        self.assertEqual(sorted(covered), [(block, i) for block, text in enumerate(items, 1)
                                           for i in range(len(text))])

    def test_opening_identity_is_separate_or_already_in_event(self):
        text = '韓生者，東里人也。其家貧。旁人將行。王乃遣使。使至。'
        library, _ = self.source(text)
        separate = W.assemble(library, self.chunk(1, text, '遣使'))
        self.assertEqual(self.selected_text(separate, 'opening_context'), '韓生者，東里人也。')
        self.assertEqual(self.selected_text(separate), '旁人將行。王乃遣使。使至。')
        self.assertEqual(separate['selection']['identity']['status'], 'opening_context')
        overlapping = W.assemble(library, self.chunk(1, text, '其家貧'))
        self.assertEqual(overlapping['selection']['identity']['status'], 'already_in_event')
        self.assertEqual(self.selected_text(overlapping, 'opening_context'), '')
        second = '始載其事。韓生者，東里人也。'
        library, _ = self.source(second)
        context = W.assemble(library, self.chunk(1, second, '始載'), neighbors=0)
        self.assertEqual(self.selected_text(context), '始載其事。')
        self.assertEqual(self.selected_text(context, 'opening_context'), '韓生者，東里人也。')
        self.assertFalse(context['scope_truncated'])

    def test_budgets_report_overflow_without_slicing(self):
        text = '韓生者，東里人也。間事。左句。是日乃至。右句。'
        library, _ = self.source(text)
        chunk = self.chunk(1, text, '乃至')
        context = W.assemble(library, chunk, event_budget=3, identity_budget=3)
        self.assertFalse(context['input_ready'])
        self.assertFalse(context['ready_for_reader'])
        self.assertFalse(context['boundary_cut'])
        self.assertEqual(context['input_reasons'], ['event_budget_exceeded', 'identity_budget_exceeded'])
        self.assertEqual(self.selected_text(context), '左句。是日乃至。右句。')
        self.assertEqual(self.selected_text(context, 'opening_context'), '韓生者，東里人也。')

    def test_rejects_source_mismatches_unbalanced_quotes_and_unproven_boundaries(self):
        library, _ = self.source('前句。甲曰：「語未完。', ('annotation', '注文。'),
                                 ('heading', '後傳'), '後句無句讀')
        chunk = self.chunk(1, '前句。甲曰：「語未完。', '前句')
        for change in (dict(source_sha256='other'), dict(heading='後傳'),
                       dict(layer='annotation'), dict(text_orig='其他')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                W.assemble(library, dict(chunk, **change))
        context = W.assemble(library, chunk, neighbors=0)
        self.assertFalse(context['input_ready'])
        self.assertTrue(context['unbalanced_quote'])
        self.assertEqual(context['input_reasons'], ['unbalanced_source_quotes'])
        self.assertEqual(context['segments'], [])
        self.assertEqual(context['omissions'][0]['end'], context['scope_chars'])
        tail = dict(self.chunk(4, '後句無句讀', '後句'), heading='後傳')
        context = W.assemble(library, tail)
        self.assertFalse(context['input_ready'])
        self.assertEqual(context['input_reasons'], ['event_sentence_boundary_unconfirmed'])
