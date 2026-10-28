"""Narrow Win32 window activation for this application's verified owner process."""
import ctypes
from ctypes import wintypes
from pathlib import Path

TITLE = "引得 · 史料检索"


def process_matches(pid, executable):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        value = ctypes.create_unicode_buffer(size.value)
        return (bool(kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)))
                and Path(value.value).resolve() == Path(executable).resolve())
    finally:
        kernel.CloseHandle(handle)


def window_state(pid):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    found = []

    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, len(title))
            if title.value == TITLE:
                found.append({"pid": pid, "hwnd": int(hwnd), "visible": bool(user.IsWindowVisible(hwnd)),
                              "minimized": bool(user.IsIconic(hwnd))})
        return True

    user.EnumWindows(callback(collect), 0)
    return found[0] if found else None


def activate(pid, executable):
    if not isinstance(pid, int) or pid <= 0 or not process_matches(pid, executable):
        return None
    state = window_state(pid)
    if not state:
        return None
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.ShowWindowAsync(state["hwnd"], 9)  # SW_RESTORE also shows a hidden window.
    user.SetForegroundWindow(state["hwnd"])
    return state
