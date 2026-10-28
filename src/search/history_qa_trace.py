"""Local bounded search diagnostics: no original passages, credentials or answers."""
from __future__ import annotations

import datetime
import json
from pathlib import Path
import threading
import uuid

_LOCK = threading.Lock()
ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "src" / "data" / "logs" / "history_qa"
MAX_LOG_BYTES = 10 * 1024 * 1024


def record_trace(data):
    trace_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    path = LOG_DIR / (now.strftime("%Y-%m-%d") + ".jsonl")
    try:
        if not path.resolve().is_relative_to(ROOT):
            return {"id": trace_id, "saved": False, "reason": "invalid_log_path"}
        body = json.dumps({"trace_id": trace_id, "at": now.isoformat(), **data}, ensure_ascii=False) + "\n"
        encoded = body.encode("utf-8")
        with _LOCK:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(encoded) > MAX_LOG_BYTES:
                return {"id": trace_id, "saved": False, "reason": "daily_log_limit"}
            with path.open("ab") as stream:
                stream.write(encoded)
        return {"id": trace_id, "saved": True,
                "file": path.relative_to(ROOT).as_posix()}
    except OSError:
        return {"id": trace_id, "saved": False, "reason": "log_write_failed"}
