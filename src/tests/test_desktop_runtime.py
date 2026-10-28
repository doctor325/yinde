"""Desktop lifecycle/security tests with isolated in-repository fixtures."""
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import subprocess
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop.runtime import DesktopServer, InstanceLock, configure
from scripts.pipeline import config


class DesktopTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "data" / "desktop" / "test-fixtures"
        parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=parent))
        self.original = {k: getattr(config, k) for k in ("HISTORY_AI_DIR", "PROJECT_ROOT", "LIBRARY_DIR",
                         "DATA_DIR", "METADATA_DIR", "PROCESSED_DIR", "DATABASE_DIR", "LOG_DIR", "DB_PATH")}
        self.db = self.root / "src/data/database/history.db"
        self.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.db)
        for name in ("books", "files", "passages"):
            con.execute(f"CREATE TABLE {name}(id INTEGER)")
        con.commit()
        con.close()
        self.frontend = self.root / "src/frontend"
        self.frontend.mkdir()
        (self.frontend / "index.html").write_text('<script src="app.js"></script>', encoding="utf-8")
        self.before_hash = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.close()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), self.before_hash)
        for key, value in self.original.items():
            setattr(config, key, value)
        shutil.rmtree(self.root)

    def start(self):
        self.server = DesktopServer(self.root).start()
        return self.server

    def request(self, path="/api/desktop/health", auth=True, host=None, method="GET", body=None, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server.server_port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.server.server.server_port}"}
        if auth:
            headers["Cookie"] = f"yinde_desktop={self.server.token}"
        if origin:
            headers["Origin"] = origin
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_health_and_ephemeral_port(self):
        self.start()
        code, _, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["status"], "ready")
        self.assertNotEqual(self.server.server.server_port, 0)

    def test_no_cookie_denied(self):
        self.start()
        self.assertEqual(self.request(auth=False)[0], 403)

    def test_invalid_host_denied(self):
        self.start()
        self.assertEqual(self.request(host="evil.invalid")[0], 403)

    def test_bootstrap_cookie(self):
        self.start()
        code, headers, _ = self.request(f"/desktop-entry/{self.server.token}", auth=False)
        self.assertEqual(code, 302)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(headers["Location"], "/")

    def test_wrong_bootstrap_token(self):
        self.start()
        self.assertEqual(self.request("/desktop-entry/not-the-token", auth=False)[0], 403)

    def test_bootstrap_host_denied(self):
        self.start()
        self.assertEqual(self.request(f"/desktop-entry/{self.server.token}", auth=False, host="evil.invalid")[0], 403)

    def test_post_no_cookie_denied(self):
        self.start()
        self.assertEqual(self.request("/api/desktop/preferences", auth=False, method="POST", body="{}")[0], 403)

    def test_preferences_origin_denied(self):
        self.start()
        self.assertEqual(self.request("/api/desktop/preferences", method="POST", body="{}", origin="https://evil.invalid")[0], 403)

    def test_preferences_roundtrip(self):
        self.start()
        code, _, _ = self.request("/api/desktop/preferences", method="POST", body='{"readingMode":"orig"}', origin=self.server.origin)
        self.assertEqual(code, 200)
        _, _, page = self.request("/")
        self.assertIn(b'window.YindeDesktopMode="orig"', page)

    def test_invalid_preferences(self):
        self.start()
        for body in ('{"readingMode":"both"}', "null", "not-json"):
            self.assertEqual(self.request("/api/desktop/preferences", method="POST", body=body, origin=self.server.origin)[0], 400)

    def test_oversized_preferences(self):
        self.start()
        self.assertEqual(self.request("/api/desktop/preferences", method="POST", body="x" * 129, origin=self.server.origin)[0], 413)

    def test_corrupt_preferences_fallback(self):
        target = self.root / "src/data/desktop/preferences.json"
        target.parent.mkdir(parents=True)
        target.write_text("broken", encoding="utf-8")
        self.start()
        self.assertIn(b'window.YindeDesktopMode="simplified"', self.request("/")[2])

    def test_sibling_prefix_traversal_blocked(self):
        sibling = self.root / "src/frontend-private"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("secret", encoding="utf-8")
        self.start()
        self.assertEqual(self.request("/../frontend-private/secret.txt")[0], 404)

    def test_missing_static(self):
        self.start()
        self.assertEqual(self.request("/missing.js")[0], 404)

    def test_semantic_status_missing_index_is_explicit(self):
        self.start()
        code, _, body = self.request("/api/experimental/semantic/status")
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["ready"])

    def test_semantic_unready_does_not_return_keyword_success(self):
        self.start()
        code, _, body = self.request("/api/experimental/semantic/search?q=history")
        self.assertEqual(code, 503)
        self.assertIn("error", json.loads(body))
        self.assertNotIn("hits", json.loads(body))

    def test_semantic_empty_query_rejected(self):
        self.start()
        self.assertEqual(self.request("/api/experimental/semantic/search?q=")[0], 400)

    def test_semantic_wrong_strategy_rejected(self):
        self.start()
        self.assertEqual(self.request("/api/experimental/semantic/search?q=history&strategy=unknown")[0], 400)

    def test_stop_idempotent_and_socket_released(self):
        self.start()
        port = self.server.server.server_port
        self.server.close()
        self.server.close()
        self.assertFalse(self.server.thread.is_alive())
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    def test_start_twice_does_not_create_another_thread(self):
        self.start()
        thread = self.server.thread
        self.server.start()
        self.assertIs(thread, self.server.thread)

    def test_close_before_start(self):
        self.server = DesktopServer(self.root)
        self.server.close()
        with self.assertRaises(RuntimeError):
            self.server.start()

    def test_context_cleanup_on_exception(self):
        with self.assertRaisesRegex(ValueError, "test"):
            with DesktopServer(self.root) as server:
                raise ValueError("test")
        self.assertFalse(server.thread.is_alive())

    def test_lock_exclusion_and_recovery(self):
        first = InstanceLock(self.root / "instance.lock")
        second = InstanceLock(self.root / "instance.lock")
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.close()
            self.assertTrue(second.acquire())
        finally:
            first.close()
            second.close()

    def test_missing_database_not_created(self):
        missing = self.root / "missing-project"
        missing.mkdir()
        with self.assertRaises(FileNotFoundError):
            configure(missing)
        self.assertFalse((missing / "src/data/database/history.db").exists())

    def test_missing_frontend(self):
        (self.frontend / "index.html").unlink()
        with self.assertRaises(FileNotFoundError):
            configure(self.root)

    def test_bound_only_to_loopback(self):
        self.start()
        self.assertEqual(self.server.server.socket.getsockname()[0], "127.0.0.1")

    def test_separate_servers_use_distinct_tokens_and_ports(self):
        self.start()
        second = DesktopServer(self.root).start()
        try:
            self.assertNotEqual(second.token, self.server.token)
            self.assertNotEqual(second.server.server_port, self.server.server.server_port)
        finally:
            second.close()

    def test_crashed_process_releases_lock(self):
        signal = self.root / "child-ready"
        path = self.root / "crash.lock"
        source_dir = str(Path(__file__).resolve().parents[1])
        code = ("import sys,time; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from desktop.runtime import InstanceLock; lock=InstanceLock(Path(sys.argv[2])); "
                "assert lock.acquire(); Path(sys.argv[3]).write_text('ready'); time.sleep(60)")
        child = subprocess.Popen([sys.executable, "-B", "-c", code, source_dir, str(path), str(signal)],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        probe = InstanceLock(path)
        try:
            deadline = time.monotonic() + 5
            while not signal.exists() and time.monotonic() < deadline:
                if child.poll() is not None:
                    self.fail("lock fixture exited early")
                time.sleep(0.05)
            self.assertTrue(signal.exists())
            self.assertFalse(probe.acquire())
            child.terminate()
            child.wait(timeout=5)
            self.assertTrue(probe.acquire())
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            probe.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
