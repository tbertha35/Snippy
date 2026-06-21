"""Launch-at-login (autostart) for Snippy.

Tiny cross-platform wrapper around the OS-native autostart mechanism:
- **Windows:** `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` registry value
- **macOS:** `~/Library/LaunchAgents/com.snippy.client.plist` (deferred to actual macOS testing)
- **Linux:** `~/.config/autostart/snippy.desktop` XDG autostart file

Public API:
    is_enabled()           -> bool
    enable()                -> None
    disable()               -> None
    toggle()                -> bool  (returns the new state)
    autostart_path()        -> Optional[Path]  (None if the mechanism doesn't use a file)

Phase 2 WS9.
"""
from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path
from typing import Optional


LOGGER = logging.getLogger(__name__)

APP_NAME = "Snippy"


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def _windows_enable(executable: Path) -> None:
    import winreg  # type: ignore[import-not-found]  # only on Windows

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        # Quote the path in case it has spaces.
        cmd = f'"{executable}" --autostart'
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
    finally:
        winreg.CloseKey(key)
    LOGGER.info("Autostart enabled (Windows registry): %s", cmd)


def _windows_disable() -> None:
    import winreg  # type: ignore[import-not-found]

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
    except FileNotFoundError:
        return
    try:
        winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass
    finally:
        winreg.CloseKey(key)
    LOGGER.info("Autostart disabled (Windows registry)")


def _windows_is_enabled() -> bool:
    import winreg  # type: ignore[import-not-found]

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
    except FileNotFoundError:
        return False
    try:
        try:
            winreg.QueryValueEx(key, APP_NAME)
            return True
        except FileNotFoundError:
            return False
    finally:
        winreg.CloseKey(key)


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------


def _linux_autostart_file() -> Path:
    from platformdirs import user_config_dir

    return Path(user_config_dir("autostart", appauthor=False)) / "snippy.desktop"


def _is_pyinstaller_bundle() -> bool:
    """Return True if this process is running from a PyInstaller-frozen binary."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _linux_executable() -> str:
    """Return the correct Exec= line for the current Linux install channel."""
    if _is_pyinstaller_bundle():
        # AppImage: the running binary is the AppImage itself.
        return f'"{sys.executable}" --autostart'

    # .deb / system install: /usr/bin/snippy is the typical entry point.
    system_bin = Path("/usr/bin/snippy")
    if system_bin.is_file():
        return f'"{str(system_bin)}" --autostart'

    # Fallback: pip/venv source install.
    return f'"{sys.executable}" -m snippy --autostart'


def _macos_executable() -> str:
    """Return the correct path to the Snippy binary for LaunchAgent.

    When running from a PyInstaller .app bundle, sys.executable points to
    Snippy.app/Contents/MacOS/Snippy. We resolve it to an absolute path so
    the LaunchAgent can launch it at login regardless of the current
    working directory.
    """
    exe = Path(sys.executable).resolve()
    if _is_pyinstaller_bundle():
        if exe.name == "Snippy" and exe.parent.name == "MacOS":
            return str(exe)
    return str(exe)


def _linux_enable() -> None:
    path = _linux_autostart_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={_linux_executable()}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "NoDisplay=false\n"
        "Terminal=false\n"
        "Comment=Smart clipboard & snippet manager\n"
    )
    path.write_text(content, encoding="utf-8")
    LOGGER.info("Autostart enabled (Linux .desktop): %s", path)


def _linux_disable() -> None:
    path = _linux_autostart_file()
    try:
        path.unlink()
        LOGGER.info("Autostart disabled (Linux .desktop): %s", path)
    except FileNotFoundError:
        pass


def _linux_is_enabled() -> bool:
    return _linux_autostart_file().exists()


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


def _macos_autostart_file() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.snippy.client.plist"


_MACOS_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.snippy.client</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>--autostart</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""


def _macos_enable() -> None:
    path = _macos_autostart_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    exe = _macos_executable()
    path.write_text(_MACOS_PLIST_TEMPLATE.format(exe=exe), encoding="utf-8")
    LOGGER.info("Autostart enabled (macOS LaunchAgent): %s -> %s", path, exe)


def _macos_disable() -> None:
    path = _macos_autostart_file()
    try:
        path.unlink()
        LOGGER.info("Autostart disabled (macOS LaunchAgent): %s", path)
    except FileNotFoundError:
        pass


def _macos_is_enabled() -> bool:
    return _macos_autostart_file().exists()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _current_platform_branch() -> str:
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        return "windows"
    if sysname == "darwin":
        return "darwin"
    return "linux"


def is_enabled() -> bool:
    """Return whether Snippy is registered to launch at login."""
    branch = _current_platform_branch()
    if branch == "windows":
        return _windows_is_enabled()
    if branch == "darwin":
        return _macos_is_enabled()
    return _linux_is_enabled()


def enable() -> None:
    """Register Snippy to launch at login."""
    branch = _current_platform_branch()
    if branch == "windows":
        _windows_enable(_python_executable_path())
    elif branch == "darwin":
        _macos_enable()
    else:
        _linux_enable()


def disable() -> None:
    """Unregister Snippy from launching at login."""
    branch = _current_platform_branch()
    if branch == "windows":
        _windows_disable()
    elif branch == "darwin":
        _macos_disable()
    else:
        _linux_disable()


def toggle() -> bool:
    """Flip the current state. Returns the new state."""
    if is_enabled():
        disable()
        return False
    enable()
    return True


def autostart_path() -> Optional[Path]:
    """Return the file/registry path Snippy uses for autostart, or None on Windows.

    Provided for the Settings UI so it can show users *where* the toggle landed.
    """
    branch = _current_platform_branch()
    if branch == "darwin":
        return _macos_autostart_file()
    if branch == "linux":
        return _linux_autostart_file()
    return None  # Windows: registry — no single path


def _python_executable_path() -> Path:
    return Path(sys.executable)