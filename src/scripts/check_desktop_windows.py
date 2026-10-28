"""Regression for SW_HIDE startup and reactivating an existing minimized instance."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop.windows import window_state


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise TimeoutError("Windows desktop regression timed out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--semantic", action="store_true")
    parser.add_argument("--punctuated", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    exe = args.executable.resolve(strict=True)
    if not exe.is_relative_to(root):
        raise ValueError("executable must be inside repository")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # Precisely reproduce the previous hidden-window bug.
    command = [str(exe), "--smoke-test"] + (["--require-semantic"] if args.semantic else [])
    command += ["--require-punctuated"] if args.punctuated else []
    proc = subprocess.Popen(command, cwd=root, startupinfo=startup)
    result = {"pid": proc.pid}
    try:
        visible = wait_for(lambda: (s if (s := window_state(proc.pid)) and s["visible"] else None), 35)
        result["hidden_start_becomes_visible"] = visible["visible"]
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.ShowWindow(visible["hwnd"], 6)
        wait_for(lambda: (s if (s := window_state(proc.pid)) and s["minimized"] else None), 5)
        duplicate = subprocess.Popen([str(exe)], cwd=root, startupinfo=startup)
        result["second_launch_exit"] = duplicate.wait(timeout=25)
        restored = wait_for(lambda: (s if (s := window_state(proc.pid)) and s["visible"] and not s["minimized"] else None), 5)
        result["second_launch_restores_window"] = bool(restored)
        result["main_exit"] = proc.wait(timeout=180)
        smoke = json.loads((root / "src/data/desktop/window-smoke.json").read_text(encoding="utf-8"))
        result["full_window_checks_completed"] = smoke.get("completed") is True
        result["semantic_required"] = args.semantic
        result["semantic_ready_and_tested"] = (smoke.get("checks", {}).get("semantic_index_ready") is True
                                               and smoke.get("checks", {}).get("semantic_ui") is True)
        result["semantic_worker_cleanup"] = smoke.get("semantic_worker_stopped") is True
        result["expanded_query_tested"] = smoke.get("checks", {}).get("semantic_expanded") is True
        result["indexed_query_tested"] = smoke.get("checks", {}).get("semantic_indexed") is True
        result["contextual_query_tested"] = smoke.get("checks", {}).get("semantic_contextual") is True
        result["punctuated_reader_tested"] = (smoke.get("checks", {}).get("punctuated_ready") is True
                                              and smoke.get("checks", {}).get("punctuated_reader") is True)
        result["passed"] = (result["hidden_start_becomes_visible"] and result["second_launch_restores_window"]
                            and result["second_launch_exit"] == 0 and result["main_exit"] == 0
                            and result["full_window_checks_completed"]
                            and (not args.punctuated or result["punctuated_reader_tested"])
                            and (not args.semantic or (result["semantic_ready_and_tested"] and result["semantic_worker_cleanup"] and result["expanded_query_tested"] and result["indexed_query_tested"] and result["contextual_query_tested"])))
    finally:
        if proc.poll() is None:
            # Only the process owned by this test may be terminated.
            proc.terminate()
            proc.wait(timeout=10)
        (root / "src/data/desktop/window-activation-regression.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
