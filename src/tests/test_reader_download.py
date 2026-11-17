import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.setup_evidence_reader import download


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'model.gguf'
        self.part = self.path.with_suffix('.download.partial')
        self.asset = dict(name=self.path.name, url='https://example.invalid/pinned', bytes=6,
                          sha256=hashlib.sha256(b'abcdef').hexdigest())

    def response(self, status, data, content_range=None):
        result = io.BytesIO(data)
        result.status = status
        result.headers = {'Content-Range': content_range}
        return result

    def test_resume_preserves_old_fragment_and_verifies_complete_hash(self):
        old = self.path.with_suffix('.gguf.partial')
        old.write_bytes(b'abc')
        with patch('urllib.request.urlopen', return_value=self.response(206, b'def', 'bytes 3-5/6')) as request:
            download(self.asset, self.path)
        self.assertEqual(request.call_args.args[0].get_header('Range'), 'bytes=3-')
        self.assertEqual(self.path.read_bytes(), b'abcdef')
        self.assertEqual(old.read_bytes(), b'abc')

    def test_ignored_range_restarts_instead_of_appending(self):
        self.part.write_bytes(b'abc')
        with patch('urllib.request.urlopen', return_value=self.response(200, b'abcdef')):
            download(self.asset, self.path)
        self.assertEqual(self.path.read_bytes(), b'abcdef')

    def test_wrong_range_cannot_change_fragment(self):
        self.part.write_bytes(b'abc')
        with patch('urllib.request.urlopen', return_value=self.response(206, b'xxx', 'bytes 0-2/6')):
            with self.assertRaisesRegex(ValueError, 'range mismatch'):
                download(self.asset, self.path)
        self.assertEqual(self.part.read_bytes(), b'abc')
        self.assertFalse(self.path.exists())

    def test_corrupt_complete_file_never_promoted_or_requested(self):
        self.part.write_bytes(b'abcxxx')
        with patch('urllib.request.urlopen') as request:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                download(self.asset, self.path)
            request.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_short_response_remains_resumable(self):
        with patch('urllib.request.urlopen', return_value=self.response(200, b'abc')):
            with self.assertRaisesRegex(ValueError, 'size/hash'):
                download(self.asset, self.path)
        self.assertEqual(self.part.read_bytes(), b'abc')
        self.assertFalse(self.path.exists())

    def test_complete_fragment_verifies_without_network(self):
        self.part.write_bytes(b'abcdef')
        with patch('urllib.request.urlopen') as request:
            download(self.asset, self.path)
            request.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b'abcdef')


if __name__ == '__main__':
    unittest.main()
