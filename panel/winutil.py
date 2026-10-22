"""Win32 窗口工具：TOOLWINDOW、Codex 定位、工作区、单实例互斥。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Any

MUTEX_NAME = r"Local\MoeWu.PrinterPanel"
_ERROR_ALREADY_EXISTS = 183
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_SPI_GETWORKAREA = 0x0030


class NamedMutex:
    """同名互斥体；已存在则返回 False，随后静默退出。"""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle: Any | None = None
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, self._name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "创建互斥体失败")
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def enable_dpi_awareness() -> None:
    """避免高 DPI 下坐标与字号被系统拉伸。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def work_area() -> tuple[int, int, int, int]:
    """主显示器工作区 (left, top, right, bottom)，已排除任务栏。"""
    user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
    rect = wintypes.RECT()
    if user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    return 0, 0, w, h


def clamp_position(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    left, top, right, bottom = work_area()
    x = min(max(int(x), left), max(left, right - w))
    y = min(max(int(y), top), max(top, bottom - h))
    return x, y


def find_codex_rect() -> tuple[int, int, int, int] | None:
    """返回 CodexUsageBar 可见窗口的 (x, y, w, h)；未找到返回 None。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    result: list[tuple[int, int, int, int] | None] = [None]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lp) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                if "CodexUsageBar" in buf.value:
                    rect = wintypes.RECT()
                    if user32.IsWindowVisible(hwnd) and user32.GetWindowRect(
                        hwnd, ctypes.byref(rect)
                    ):
                        result[0] = (
                            rect.left, rect.top,
                            rect.right - rect.left, rect.bottom - rect.top,
                        )
                        return False
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(callback, 0)
    return result[0]


def set_toolwindow(root: Any) -> None:
    """让 Tk 窗口不进任务栏/Alt-Tab。"""
    import tkinter as tk

    try:
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 0)
    except (ValueError, tk.TclError):
        return
    try:
        user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        set_long(hwnd, _GWL_EXSTYLE, ex_style | _WS_EX_TOOLWINDOW)
    except Exception:
        pass


def dock_taskbar_size(w: int, h: int) -> tuple[int, int]:
    """任务栏条定位：仅当 Codex 是底部矮条时贴其上方，否则回退右下角。"""
    left, top, right, bottom = work_area()
    codex = find_codex_rect()
    if codex is not None:
        cx, cy, cw, ch = codex
        is_strip = ch <= 60 and (cy + ch) > (top + bottom) // 2
        if is_strip:
            x = cx + cw - w
            y = cy - h - 4
            if y >= top:
                return clamp_position(x, y, w, h)
    return clamp_position(right - w - 8, bottom - h - 8, w, h)
