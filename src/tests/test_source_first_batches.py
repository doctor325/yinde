import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from search import source_first_batches as B


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(page_id=1, book_id='book', revision_id=2, source_sha256='original')
        texts = ['甲出行。', '乙曰：「丙未至。且候之。」', '甲乃止。', '丁還。']
        at = 0
        self.evidence = []
        for i, text in enumerate(texts, 1):
            self.evidence.append(dict(id=f'e{i}', text=text, role='event_context', complete=True,
                parts=[dict(block_index=1, start=at, end=at+len(text), quote=text)]))
            at += len(text)
        self.evidence.append(dict(id='e5', text='甲字某。', role='opening_context', complete=True,
            parts=[dict(block_index=0, start=0, end=4, quote='甲字某。')]))
        self.tasks = B.plan(self.evidence, self.source)
        self.execution = dict(model='pinned', runtime='fixed', template='fixed')
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def reader(self, core='e1', reason='stop'):
        reader = Mock()
        value = dict(coverage='complete', events=[dict(actor='甲', action='出行', object='', reason='',
            state='occurred', attribution='narrator', references=[core])])
        reader.request.return_value = dict(choices=[dict(finish_reason=reason,
            message={'content': json.dumps(value)})])
        return reader

    def test_source_order_and_quotation_are_preserved_with_neighbours(self):
        self.assertEqual([t['core_ids'] for t in self.tasks], [['e1'], ['e2'], ['e3'], ['e4']])
        self.assertEqual([e['id'] for e in self.tasks[1]['evidence']], ['e1', 'e2', 'e3', 'e5'])
        self.assertEqual(self.tasks[1]['evidence'][1], self.evidence[1])
        payload = json.loads(B.request(self.tasks[1])['messages'][1]['content'])
        self.assertEqual(payload['core_evidence'], ['e2'])
        self.assertNotIn('question', payload)
        self.assertNotIn('gold', payload)

    def test_long_or_incomplete_quote_is_blocked_never_sliced(self):
        tasks = B.plan(self.evidence, self.source, max_chars=5)
        self.assertIn('intact_unit_exceeds_budget', tasks[1]['blocked'])
        self.assertEqual(tasks[1]['evidence'][1]['text'], self.evidence[1]['text'])
        altered = copy.deepcopy(self.evidence)
        altered[1]['complete'] = False
        self.assertTrue(B.plan(altered, self.source)[1]['blocked'])
        altered[1]['complete'] = True
        altered[1]['text'] = '乙曰：「未完'
        self.assertTrue(B.plan(altered, self.source)[1]['blocked'])

    def test_source_gap_prevents_neighbour_jump(self):
        evidence = copy.deepcopy(self.evidence)
        evidence[1]['parts'][0]['start'] += 1
        self.assertNotIn('e2', [e['id'] for e in B.plan(evidence, self.source)[0]['evidence']])

    def test_source_revision_text_runtime_and_prompt_have_distinct_keys(self):
        task = self.tasks[0]
        original = B.key(task, self.execution)
        changed = copy.deepcopy(task)
        changed['source']['revision_id'] += 1
        self.assertNotEqual(B.key(changed, self.execution), original)
        changed = copy.deepcopy(task)
        changed['evidence'][0]['text'] = '改文。'
        self.assertNotEqual(B.key(changed, self.execution), original)
        self.assertNotEqual(B.key(task, dict(self.execution, model='other')), original)
        self.assertNotEqual(B.key(task, dict(self.execution, template='other')), original)

    def test_success_reuses_cached_response_without_call_or_completion_claim(self):
        reader = self.reader()
        value, reused = B.run_one(reader, self.tasks[0], self.execution, self.folder)
        self.assertTrue(value['completed'])
        self.assertFalse(reused)
        saved, reused = B.run_one(reader, self.tasks[0], self.execution, self.folder)
        self.assertTrue(reused)
        self.assertEqual(saved, value)
        reader.request.assert_called_once()
        progress = B.progress(self.tasks, self.execution, self.folder)
        self.assertEqual(progress['response_complete'], ['e1'])
        self.assertEqual(progress['pending'], ['e2', 'e3', 'e4'])
        self.assertFalse(progress['all_responses_complete'])
        self.assertFalse(progress['semantic_verified'])

    def test_timeout_is_saved_without_automatic_retry(self):
        reader = Mock()
        reader.request.side_effect = TimeoutError('limit')
        result, _ = B.run_one(reader, self.tasks[0], self.execution, self.folder)
        self.assertFalse(result['completed'])
        saved, reused = B.run_one(reader, self.tasks[0], self.execution, self.folder)
        self.assertTrue(reused)
        self.assertEqual(saved, result)
        reader.request.assert_called_once()
        self.assertEqual(B.progress(self.tasks, self.execution, self.folder)['failed'], ['e1'])

    def test_neighbour_only_event_cannot_replace_core_event(self):
        reader = self.reader(core='e2')
        result, _ = B.run_one(reader, self.tasks[0], self.execution, self.folder)
        self.assertFalse(result['completed'])
        self.assertEqual(result['error']['type'], 'CoreReferenceError')

    def test_corrupt_receipt_fails_closed_and_blocked_unit_never_calls_model(self):
        reader = self.reader()
        B.run_one(reader, self.tasks[0], self.execution, self.folder)
        path = B.cache_path(self.folder, self.tasks[0], self.execution)
        record = json.loads(path.read_text(encoding='utf8'))
        record['result']['output']['events'][0]['actor'] = '他人'
        path.write_text(json.dumps(record), encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            B.run_one(reader, self.tasks[0], self.execution, self.folder)
        blocked = B.plan(self.evidence, self.source, max_chars=1)[0]
        with self.assertRaisesRegex(ValueError, 'Blocked'):
            B.run_one(reader, blocked, self.execution, self.folder)
        reader.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
