import errno
import json
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError, URLError

from search import local_evidence_reader


class LocalReaderHealthTests(unittest.TestCase):
    def setUp(self):
        self.reader = local_evidence_reader.LocalReader()
        self.reader.process = Mock()
        self.reader.process.poll.return_value = None
        self.reader.request = Mock()
        self.clock = Mock()
        clock_patch = patch.object(local_evidence_reader, 'time', self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def test_closes_503_and_retries_until_healthy(self):
        response = Mock()
        unavailable = HTTPError('http://localhost/health', 503, 'Unavailable', None, response)

        def probe(path, timeout):
            if self.reader.request.call_count == 1:
                raise unavailable
            response.close.assert_called_once_with()
            return {'status': 'ok'}

        self.clock.perf_counter.side_effect = [100, 100, 100.25]
        self.reader.request.side_effect = probe

        self.reader._wait_ready(started=100)

        response.close.assert_called_once_with()
        self.assertEqual(self.reader.request.call_args_list, [call('/health', timeout=1)] * 2)
        self.assertEqual(self.reader.startup_diagnostics['attempts'], 2)
        self.assertEqual(self.reader.startup_diagnostics['last_error']['code'], 503)
        self.clock.sleep.assert_called_once_with(0.25)

    def test_permission_denial_stops_after_first_probe(self):
        denied = PermissionError(errno.EACCES, self.reader.key)
        denied.winerror = 10013
        self.reader.request.side_effect = URLError(denied)
        self.clock.perf_counter.return_value = 100

        with self.assertRaises(PermissionError) as raised:
            self.reader._wait_ready(started=100)

        self.reader.request.assert_called_once_with('/health', timeout=1)
        self.clock.sleep.assert_not_called()
        self.assertEqual(self.reader.startup_diagnostics, {
            'attempts': 1,
            'last_error': {'type': 'URLError', 'errno': errno.EACCES, 'winerror': 10013},
        })
        self.assertNotIn(self.reader.key, str(raised.exception))

    def test_timeout_preserves_sanitized_error_and_caps_probe_timeout(self):
        self.reader.request.side_effect = OSError(errno.ECONNREFUSED, self.reader.key)
        self.clock.perf_counter.side_effect = [159.5, 159.875, 160]

        with self.assertRaises(TimeoutError) as raised:
            self.reader._wait_ready(started=100)

        self.reader.request.assert_called_once_with('/health', timeout=0.5)
        self.clock.sleep.assert_called_once_with(0.125)
        expected = {
            'attempts': 1,
            'last_error': {'type': 'ConnectionRefusedError', 'errno': errno.ECONNREFUSED,
                           'winerror': None},
        }
        self.assertEqual(self.reader.startup_diagnostics, expected)
        self.assertIn(json.dumps(expected), str(raised.exception))
        self.assertNotIn(self.reader.key, str(raised.exception))

    def test_exited_process_is_detected_before_health_probe(self):
        self.reader.process.poll.return_value = 1
        self.clock.perf_counter.return_value = 100

        with self.assertRaisesRegex(RuntimeError, 'exited during startup'):
            self.reader._wait_ready(started=100)

        self.reader.request.assert_not_called()
        self.clock.sleep.assert_not_called()
        self.assertEqual(self.reader.startup_diagnostics['attempts'], 0)
