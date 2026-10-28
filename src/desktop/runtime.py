"""Owned, authenticated loopback service with deterministic cleanup."""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import sqlite3
import threading
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path


def configure(root: Path) -> Path:
    """Resolve explicit project paths; never use an inherited external data path."""
    from scripts.pipeline import config
    root = root.resolve(strict=True)
    data = root / "src" / "data"
    database = data / "database" / "history.db"
    frontend = root / "src" / "frontend"
    if not database.is_file():
        raise FileNotFoundError(f"未找到史料数据库：{database}")
    if not (frontend / "index.html").is_file():
        raise FileNotFoundError(f"未找到界面文件：{frontend}")
    # Opening read-only is essential: a missing DB must never become an empty DB.
    con = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        for table in ("books", "files", "passages"):
            con.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
    finally:
        con.close()
    config.HISTORY_AI_DIR = root / "src"
    config.PROJECT_ROOT = root
    config.LIBRARY_DIR = root / "corpus" / "kanripo"
    config.DATA_DIR = data
    config.METADATA_DIR = data / "metadata"
    config.PROCESSED_DIR = data / "processed"
    config.DATABASE_DIR = data / "database"
    config.LOG_DIR = data / "logs"
    config.DB_PATH = database
    return frontend


class DesktopServer:
    def __init__(self, root: Path):
        frontend = configure(root)
        from api.main import Handler
        self.token = secrets.token_urlsafe(32)
        token = self.token
        preferences = root / "src" / "data" / "desktop" / "preferences.json"

        def get_mode():
            try:
                mode = json.loads(preferences.read_text(encoding="utf-8")).get("readingMode")
                return mode if mode in ("orig", "simplified") else "simplified"
            except (OSError, ValueError, AttributeError):
                return "simplified"

        class LocalHandler(Handler):
            def authorized(handler):
                if handler.headers.get("Host") != f"127.0.0.1:{handler.server.server_port}":
                    handler._err("invalid host", 403)
                    return False
                cookies = SimpleCookie()
                try:
                    cookies.load(handler.headers.get("Cookie", ""))
                    value = cookies["yinde_desktop"].value if "yinde_desktop" in cookies else ""
                except Exception:
                    value = ""
                if not hmac.compare_digest(value, token):
                    handler._err("desktop session required", 403)
                    return False
                return True

            def do_GET(handler):
                if handler.path == f"/desktop-entry/{token}":
                    if handler.headers.get("Host") != f"127.0.0.1:{handler.server.server_port}":
                        return handler._err("invalid host", 403)
                    handler.send_response(302)
                    handler.send_header("Set-Cookie", f"yinde_desktop={token}; HttpOnly; SameSite=Strict; Path=/")
                    handler.send_header("Location", "/")
                    handler.send_header("Cache-Control", "no-store")
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                if not handler.authorized():
                    return
                if handler.path == "/api/desktop/health":
                    return handler._json({"status": "ready", "application": "yinde-desktop", "version": 1})
                return super(LocalHandler, handler).do_GET()

            def do_POST(handler):
                if not handler.authorized():
                    return
                if handler.path == "/api/desktop/preferences":
                    if handler.headers.get("Origin") != f"http://127.0.0.1:{handler.server.server_port}":
                        return handler._err("invalid origin", 403)
                    try:
                        length = int(handler.headers.get("Content-Length", "0"))
                        if not 0 < length <= 128:
                            return handler._err("invalid length", 413)
                        handler.connection.settimeout(5)
                        body = json.loads(handler.rfile.read(length))
                        if not isinstance(body, dict) or body.get("readingMode") not in ("orig", "simplified"):
                            return handler._err("invalid mode", 400)
                        preferences.parent.mkdir(parents=True, exist_ok=True)
                        # Serialize concurrent saves; replace avoids half-written JSON after a crash.
                        with handler.server.preferences_lock:
                            staging = preferences.with_suffix(".tmp")
                            staging.write_text(json.dumps({"readingMode": body["readingMode"]}), encoding="utf-8")
                            staging.replace(preferences)
                    except (ValueError, OSError):
                        return handler._err("preferences could not be saved", 400)
                    return handler._json({"saved": True})
                return super(LocalHandler, handler).do_POST()

            def _static(handler, rel):
                target = (frontend / (rel or "index.html")).resolve()
                if not target.is_relative_to(frontend.resolve()) or not target.is_file():
                    return handler._err("not found", 404)
                content_types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
                body = target.read_bytes()
                if target == frontend / "index.html":
                    script = f'<script>window.YindeDesktopMode={json.dumps(get_mode())};</script>'
                    body = body.replace(b'<script src="app.js">', script.encode() + b'<script src="app.js">')
                handler._send(200, body, content_types.get(target.suffix, "application/octet-stream"))

            def log_message(handler, fmt, *args):
                # URL may contain a bootstrap token or a user's historical question.
                logging.getLogger("desktop.http").debug("HTTP request handled")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        self.server.preferences_lock = threading.Lock()
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="yinde-http", daemon=True)
        self.started = False
        self.closed = False

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def entry(self):
        return f"{self.origin}/desktop-entry/{self.token}"

    def start(self):
        if self.closed:
            raise RuntimeError("server already closed")
        if not self.started:
            self.thread.start()
            self.started = True
        return self

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.started:
            self.server.shutdown()
        self.server.server_close()
        if self.started:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("检索服务未能及时停止")

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


class InstanceLock:
    """OS releases the file lock even after a crash; stale PID files cannot block startup."""
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def acquire(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            self.file = None
            return False
        return True

    def close(self):
        if self.file:
            self.file.close()
            self.file = None
