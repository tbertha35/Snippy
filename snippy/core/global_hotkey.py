"""System-wide global hotkey for Snippy.

Cross-platform implementation:
- **Windows:** native Win32 `RegisterHotKey` + message-only window on a
  dedicated thread.
- **Linux / macOS:** `pynput` global hotkey listener. Works on X11 Linux
  and macOS with Accessibility permission. Falls back to an in-app
  `QShortcut` when registration fails.

The callback is always marshalled back onto the Qt GUI thread via the
owner's `trigger` Signal + `Qt.QueuedConnection`.
"""
from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal


LOGGER = logging.getLogger(__name__)


def _debug_log_path() -> Path:
    from platformdirs import user_log_dir
    from snippy import __app_name__

    log_dir = Path(user_log_dir(__app_name__, appauthor=__app_name__))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return log_dir / "hotkey.log"


def _debug_log(message: str) -> None:
    try:
        from datetime import datetime, timezone

        line = f"[{datetime.now(timezone.utc).isoformat()}] {message}\n"
        path = _debug_log_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _headless_or_test() -> bool:
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance()
        if app is None:
            return False
        platform_name = app.platformName().lower() if hasattr(app, "platformName") else ""
        return platform_name in ("offscreen", "minimal", "vnc")
    except Exception:
        return False


class GlobalHotkey(QObject):
    """Register and unregister a system-wide hotkey."""

    trigger = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._callback: Callable[[], None] | None = None
        self._sequence: str | None = None
        self._backend: object | None = None
        self._last_trigger_at: datetime | None = None

    @property
    def last_trigger_at(self) -> datetime | None:
        return self._last_trigger_at

    def register(self, sequence: str, callback: Callable[[], None]) -> bool:
        self.unregister()
        _debug_log(f"register() called with sequence={sequence!r}")

        if sys.platform == "win32":
            self._backend = _Win32Backend(self)
        else:
            self._backend = _PynputBackend(self)

        if not self._backend.register(sequence, callback):
            self._backend = None
            return False

        self.trigger.connect(callback, Qt.ConnectionType.QueuedConnection)

        self._callback = callback
        self._sequence = sequence
        return True

    def unregister(self) -> None:
        if self._backend is not None:
            self._backend.unregister()
            self._backend = None
        if self._callback is not None:
            try:
                self.trigger.disconnect(self._callback)
            except (TypeError, RuntimeError):
                pass
        self._callback = None
        self._sequence = None


# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------


import ctypes  # noqa: E402

_WIN32_HWND_MESSAGE = -3
_WIN32_WM_HOTKEY = 0x0312
_WIN32_WM_QUIT = 0x0012

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x0000


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [
        ctypes.c_int,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    user32.DestroyWindow.restype = ctypes.c_bool
    user32.RegisterHotKey.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
    ]
    user32.RegisterHotKey.restype = ctypes.c_bool
    user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.UnregisterHotKey.restype = ctypes.c_bool
    user32.PostThreadMessageW.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.PostThreadMessageW.restype = ctypes.c_bool

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_void_p),
            ("lParam", ctypes.c_void_p),
            ("time", ctypes.c_uint),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
            ("lPrivate", ctypes.c_uint),
        ]

    user32.GetMessageW.argtypes = [
        ctypes.POINTER(_MSG),
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
    user32.DispatchMessageW.restype = ctypes.c_void_p
    user32.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
    user32.TranslateMessage.restype = ctypes.c_bool
    user32.DefWindowProcW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.DefWindowProcW.restype = ctypes.c_void_p


def _parse_win32_sequence(sequence: str) -> tuple[int, int | None]:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence

    parts = [p.strip().lower() for p in sequence.replace("+", " ").split() if p.strip()]
    if not parts:
        return 0, None

    mod_flags = 0
    key_name: str | None = None
    for part in parts:
        if part in ("ctrl", "control"):
            mod_flags |= _MOD_CONTROL
        elif part == "alt":
            mod_flags |= _MOD_ALT
        elif part == "shift":
            mod_flags |= _MOD_SHIFT
        elif part in ("win", "meta"):
            mod_flags |= _MOD_WIN
        else:
            key_name = part

    if key_name is None:
        return 0, None

    try:
        qks = QKeySequence(key_name)
    except Exception:
        return 0, None
    if qks.isEmpty():
        return 0, None

    key = qks[0].key()
    key_value = key.value if hasattr(key, "value") else int(key)
    if key_value == 0:
        return 0, None

    vk = _qt_key_to_vk(key)
    if vk is None or vk == 0:
        LOGGER.warning("No Windows VK mapping for key %r", key_name)
        return 0, None

    return mod_flags, vk


def _qt_key_to_vk(key: Qt.Key) -> int | None:
    import re
    from PySide6.QtCore import Qt

    name = key.name if hasattr(key, "name") else str(key)
    m = re.match(r"Key_([A-Z0-9])$", name)
    if m:
        ch = m.group(1)
        if "A" <= ch <= "Z":
            return 0x41 + (ord(ch) - ord("A"))
        if "0" <= ch <= "9":
            return 0x30 + (ord(ch) - ord("0"))

    raw_mapping = {
        "Key_Space": 0x20,
        "Key_Escape": 0x1B,
        "Key_Tab": 0x09,
        "Key_Backspace": 0x08,
        "Key_Return": 0x0D,
        "Key_Enter": 0x0D,
        "Key_Insert": 0x2D,
        "Key_Delete": 0x2E,
        "Key_Pause": 0x13,
        "Key_Print": 0x2C,
        "Key_Home": 0x24,
        "Key_End": 0x23,
        "Key_Left": 0x25,
        "Key_Up": 0x26,
        "Key_Right": 0x27,
        "Key_Down": 0x28,
        "Key_PageUp": 0x21,
        "Key_PageDown": 0x22,
        "Key_Shift": 0x10,
        "Key_Control": 0x11,
        "Key_Meta": 0x5B,
        "Key_Alt": 0x12,
        "Key_CapsLock": 0x14,
        "Key_NumLock": 0x90,
        "Key_ScrollLock": 0x91,
        "Key_Menu": 0x5D,
        "Key_Help": 0x2F,
    }
    mapping = {}
    for kname, vk in raw_mapping.items():
        qt_key = getattr(Qt.Key, kname, None)
        if qt_key is not None:
            mapping[qt_key] = vk
    return mapping.get(key)


class _Win32HotkeyThread(threading.Thread):
    def __init__(
        self,
        sequence: str,
        callback: Callable[[], None],
        owner: "GlobalHotkey",
    ) -> None:
        super().__init__(daemon=True, name="snippy-hotkey")
        self._sequence = sequence
        self._callback = callback
        self._owner = owner
        self.hwnd: int | None = None
        self.hotkey_id: int | None = None
        self.ready_event = threading.Event()
        self._stop_event = threading.Event()

    def run(self) -> None:
        _debug_log(f"win32 thread run() entered (sequence={self._sequence!r})")
        if sys.platform != "win32":
            self.ready_event.set()
            return

        try:
            mod_flags, vk = _parse_win32_sequence(self._sequence)
            if vk is None:
                _debug_log("vk is None; aborting (parse failed)")
                self.ready_event.set()
                return

            register_mods = mod_flags | _MOD_NOREPEAT
            hinst = kernel32.GetModuleHandleW(None)

            hwnd = user32.CreateWindowExW(
                0,
                "STATIC",
                "SnippyHotkeyWindow",
                0,
                0,
                0,
                0,
                0,
                _WIN32_HWND_MESSAGE,
                None,
                hinst,
                None,
            )
            if not hwnd:
                err = ctypes.get_last_error()
                LOGGER.warning("CreateWindowExW failed (error=%d)", err)
                _debug_log(f"CreateWindowExW FAILED (error={err})")
                self.ready_event.set()
                return

            hotkey_id = (id(self._owner) & 0x7FFF) or 1
            if not user32.RegisterHotKey(hwnd, hotkey_id, register_mods, vk):
                err = ctypes.get_last_error()
                LOGGER.warning(
                    "RegisterHotKey failed (error=%d, mods=0x%X, vk=0x%X)",
                    err,
                    register_mods,
                    vk,
                )
                _debug_log(
                    f"RegisterHotKey FAILED (error={err}, mods=0x{register_mods:X}, vk=0x{vk:X})"
                )
                user32.DestroyWindow(hwnd)
                self.ready_event.set()
                return

            self.hwnd = hwnd
            self.hotkey_id = hotkey_id
            self.ready_event.set()

            msg = _MSG()
            last_heartbeat = kernel32.GetTickCount64()
            while not self._stop_event.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                if msg.message == _WIN32_WM_HOTKEY and msg.wParam == hotkey_id:
                    self._owner._last_trigger_at = datetime.now(timezone.utc)
                    try:
                        self._owner.trigger.emit()
                    except Exception as exc:
                        LOGGER.warning("Failed to dispatch hotkey callback: %s", exc)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
                now = kernel32.GetTickCount64()
                if now - last_heartbeat >= 5_000:
                    last_heartbeat = now

            try:
                user32.UnregisterHotKey(hwnd, hotkey_id)
                user32.DestroyWindow(hwnd)
            except Exception as exc:
                LOGGER.debug("Cleanup failed: %s", exc)
        except Exception as exc:
            _debug_log(f"win32 thread EXCEPTION: {exc}")
            self.ready_event.set()
        finally:
            _debug_log("win32 thread exiting")

    def stop(self) -> None:
        self._stop_event.set()
        if self.ident is not None:
            try:
                user32.PostThreadMessageW(self.ident, _WIN32_WM_QUIT, None, None)
            except Exception:
                pass


class _Win32Backend:
    def __init__(self, owner: "GlobalHotkey") -> None:
        self._owner = owner
        self._thread: _Win32HotkeyThread | None = None

    def register(self, sequence: str, callback: Callable[[], None]) -> bool:
        if sys.platform != "win32":
            return False
        if _headless_or_test():
            return False
        thread = _Win32HotkeyThread(sequence, callback, self._owner)
        thread.start()
        if not thread.ready_event.wait(timeout=2.0):
            thread.stop()
            return False
        if thread.hwnd is None:
            return False
        self._thread = thread
        return True

    def unregister(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            try:
                thread.stop()
                thread.join(timeout=2.0)
            except Exception as exc:
                LOGGER.debug("Failed to stop hotkey thread: %s", exc)


# ---------------------------------------------------------------------------
# pynput backend (Linux / macOS)
# ---------------------------------------------------------------------------


def _sequence_to_pynput(sequence: str) -> str | None:
    """Convert 'Ctrl+Shift+Space' style string into a pynput hotkey string."""
    parts = [p.strip().lower() for p in sequence.replace("+", " ").split() if p.strip()]
    if not parts:
        return None

    modifiers: list[str] = []
    key_name: str | None = None
    for part in parts:
        if part in ("ctrl", "control"):
            modifiers.append("ctrl")
        elif part == "alt":
            modifiers.append("alt")
        elif part == "shift":
            modifiers.append("shift")
        elif part in ("win", "meta", "cmd", "command"):
            modifiers.append("cmd")
        else:
            key_name = part

    if key_name is None:
        return None

    special_keys = {
        "space": "space",
        "return": "enter",
        "enter": "enter",
        "escape": "esc",
        "esc": "esc",
        "tab": "tab",
        "backspace": "backspace",
        "delete": "delete",
        "del": "delete",
        "insert": "insert",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
        "left": "left",
        "right": "right",
        "up": "up",
        "down": "down",
        "print": "print",
        "pause": "pause",
        "capslock": "capslock",
        "numlock": "numlock",
        "scrolllock": "scrolllock",
        "f1": "f1",
        "f2": "f2",
        "f3": "f3",
        "f4": "f4",
        "f5": "f5",
        "f6": "f6",
        "f7": "f7",
        "f8": "f8",
        "f9": "f9",
        "f10": "f10",
        "f11": "f11",
        "f12": "f12",
    }

    normalized = special_keys.get(key_name, key_name)
    if len(normalized) == 1 and normalized.isalnum():
        key_part = normalized
    else:
        key_part = f"<{normalized}>"

    if modifiers:
        return "+".join(f"<{m}>" for m in modifiers) + "+" + key_part
    return key_part


class _PynputBackend:
    """Global hotkey backend using pynput (Linux X11 / macOS)."""

    def __init__(self, owner: "GlobalHotkey"):
        self._owner = owner
        self._listener: object | None = None

    def register(self, sequence: str, callback: Callable[[], None]) -> bool:
        if sys.platform == "win32":
            return False
        if _headless_or_test():
            _debug_log("headless Qt platform; skipping pynput global hotkey")
            return False

        try:
            from pynput import keyboard
        except Exception as exc:
            LOGGER.warning("pynput not available; global hotkey disabled: %s", exc)
            return False

        pynput_sequence = _sequence_to_pynput(sequence)
        if pynput_sequence is None:
            LOGGER.warning("Could not parse hotkey %r for pynput", sequence)
            return False

        _debug_log(
            f"pynput backend: registering {sequence!r} as {pynput_sequence!r}"
        )

        # On macOS, verify Accessibility trust and warn if missing. pynput's
        # event tap will silently fail when the app is not trusted, so we
        # surface this to the user before trying to start the listener.
        macos_trusted = True
        if sys.platform == "darwin":
            macos_trusted = self._macos_is_accessibility_trusted()
            if not macos_trusted:
                LOGGER.warning(
                    "Snippy is not trusted for Accessibility. Global hotkey will "
                    "not work until you enable it in System Settings → Privacy & "
                    "Security → Accessibility."
                )

        try:
            hotkey = keyboard.HotKey(
                keyboard.HotKey.parse(pynput_sequence),
                self._on_activate,
            )
            self._listener = keyboard.Listener(
                on_press=hotkey.press,
                on_release=hotkey.release,
                suppress=False,
            )
            self._listener.start()
            # Give the listener a moment to initialize its platform tap.
            import time
            time.sleep(0.1)
            alive = getattr(self._listener, "is_alive", lambda: False)()
            running = getattr(self._listener, "running", False)
            _debug_log(f"pynput listener state alive={alive} running={running}")
            if not alive:
                LOGGER.warning(
                    "Global hotkey listener died immediately (Accessibility/Screen Recording permission may be missing). Falling back to in-app shortcut."
                )
                return False
            _debug_log("pynput listener started")
            return True
        except Exception as exc:
            LOGGER.warning("Failed to start pynput hotkey listener: %s", exc)
            _debug_log(f"pynput register EXCEPTION: {exc}")
            return False

    def _macos_is_accessibility_trusted(self) -> bool:
        """Return True if this process is trusted for Accessibility on macOS."""
        try:
            from ctypes import c_void_p, c_bool, cdll

            ax = cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            ax.AXIsProcessTrustedWithOptions.restype = c_bool
            ax.AXIsProcessTrustedWithOptions.argtypes = [c_void_p]
            # Passing None checks without prompting; we surface the dialog via
            # Qt so we don't risk crashing in CoreFoundation ctypes.
            trusted = ax.AXIsProcessTrustedWithOptions(None)
            _debug_log(f"macOS accessibility trusted={trusted}")
            return bool(trusted)
        except Exception as exc:
            _debug_log(f"macOS accessibility check failed: {exc}")
            return True

    def _on_activate(self) -> None:
        _debug_log("pynput hotkey activated")
        self._owner._last_trigger_at = datetime.now(timezone.utc)
        try:
            self._owner.trigger.emit()
        except Exception as exc:
            LOGGER.warning("Failed to dispatch pynput hotkey callback: %s", exc)

    def unregister(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception as exc:
                LOGGER.debug("Failed to stop pynput listener: %s", exc)
