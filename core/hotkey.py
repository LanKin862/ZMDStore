from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication


user32 = ctypes.windll.user32
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


SPECIAL_KEYS = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "SPACE": 0x20, "TAB": 0x09,
}


def parse_hotkey(sequence: str) -> tuple[int, int]:
    parts = [part.strip().upper() for part in sequence.split("+") if part.strip()]
    modifiers = 0
    vk = None
    for part in parts:
        if part == "CTRL":
            modifiers |= MOD_CONTROL
        elif part == "ALT":
            modifiers |= MOD_ALT
        elif part == "SHIFT":
            modifiers |= MOD_SHIFT
        elif part in ("WIN", "META"):
            modifiers |= MOD_WIN
        elif part in SPECIAL_KEYS:
            vk = SPECIAL_KEYS[part]
        elif len(part) == 1:
            vk = ord(part)
    if vk is None:
        raise ValueError("无法识别快捷键")
    return modifiers, vk


class HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = MSG.from_address(int(message))
        if msg.message == WM_HOTKEY:
            self.callback()
            return True, 0
        return False, 0


class GlobalHotkeyManager(QObject):
    activated = Signal()

    def __init__(self):
        super().__init__()
        self.hotkey_id = 1
        self.event_filter = HotkeyEventFilter(self.activated.emit)
        self.registered = False
        QApplication.instance().installNativeEventFilter(self.event_filter)

    def register(self, sequence: str):
        self.unregister()
        modifiers, vk = parse_hotkey(sequence)
        if not user32.RegisterHotKey(None, self.hotkey_id, modifiers, vk):
            raise RuntimeError("快捷键注册失败，可能已被其他程序占用")
        self.registered = True

    def unregister(self):
        if self.registered:
            user32.UnregisterHotKey(None, self.hotkey_id)
            self.registered = False
