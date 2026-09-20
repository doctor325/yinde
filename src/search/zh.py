"""简体 ↔ 繁体 转换（Windows 系统映射，零第三方依赖）。

Kanripo 正文是繁体（國語/齊桓公…），用户大概率按简体输入（国语/齐桓公…）。
用 kernel32.LCMapStringEx 做整串映射；非 Windows 或调用失败时恒等回退
（不做字符级猜测——宁可少命中，不做错转换）。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

LCMAP_SIMPLIFIED_CHINESE = 0x02000000
LCMAP_TRADITIONAL_CHINESE = 0x04000000
LOCALE_ZH_CN = "zh-CN"  # 转换以简体中文本地为准，与系统输入法无关

_map_fn = None


def _get_map_fn():
    global _map_fn
    if _map_fn is not None:
        return _map_fn
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = k.LCMapStringEx
        fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR,
                       ctypes.c_int, wintypes.LPWSTR, ctypes.c_int,
                       wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
        fn.restype = ctypes.c_int

        def _impl(text: str, flag: int) -> str | None:
            if not text:
                return text
            # LCMapStringEx 数的是 **UTF-16 码元**，不是 Python 码点。传 len(text)
            # 会让含 BMP 外字（扩展区字形 𫝊 U+2B74A / 𤣥 U+248E5，文淵閣本
            # 篇题里就有）的串少读一截：每个非 BMP 字少算 1 个码元，API 只映射到
            # 第 n 个码元为止，**尾部字符被整段吞掉**。实测 'a𫝊b龍' → 'a𫝊b'（龍
            # 没了）、'漢書敘𫝊第七十下' → '汉书叙𫝊第七十'（下 没了）。单字时更
            # 隐蔽：'𫝊' → 一个孤立的代理项 '\ud86d'，长度还是 1，dual_text 的
            # 「长度不变」闸门根本拦不住，会把非法 Unicode 当成果发出去。
            n = len(text.encode("utf-16-le")) // 2
            buf = ctypes.create_unicode_buffer(2 * n + 16)
            r = fn(LOCALE_ZH_CN, flag, text, n, buf, 2 * n + 16, None, None, None)
            return buf.value[:r] if r else None

        _map_fn = _impl
    except (AttributeError, OSError):
        _map_fn = False  # 非 Windows / 无 LCMapStringEx
    return _map_fn


def to_traditional(text: str) -> str:
    """简体→繁体。例：齐桓公城濮 -> 齊桓公城濮。失败恒等回退。"""
    impl = _get_map_fn()
    if impl:
        out = impl(text, LCMAP_TRADITIONAL_CHINESE)
        if out is not None:
            return out
    return text


def to_simplified(text: str) -> str:
    """繁体→简体（书名等展示对照用）。失败恒等回退。"""
    impl = _get_map_fn()
    if impl:
        out = impl(text, LCMAP_SIMPLIFIED_CHINESE)
        if out is not None:
            return out
    return text
