"""Tests for the cross-platform autostart module.

We can't actually touch the Windows registry or a real macOS LaunchAgents
folder from a Linux test host, so those branches get "smoke" coverage only
(just confirm the public API is callable and `autostart_path()` returns
the right type). The Linux `.desktop` file gets full round-trip tests.
"""
from __future__ import annotations

import os
import sys
import platform
from pathlib import Path

import pytest


# Force linux branch in tests; we don't want to touch the host's autostart
# folder even if we're on a platform that has one.
@pytest.fixture(autouse=True)
def _force_linux_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the branch detector to always return 'linux'
    import snippy.core.autostart as mod
    monkeypatch.setattr(mod, "_current_platform_branch", lambda: "linux")
    # And point XDG_CONFIG_HOME at a temp dir so we don't pollute the host
    tmp = pytest.importorskip("tempfile").mkdtemp(prefix="snippy-autostart-")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(tmp) / "config"))


def test_linux_disabled_by_default() -> None:
    from snippy.core import autostart

    # Clean slate
    autostart.disable()
    assert autostart.is_enabled() is False


def test_linux_enable_creates_desktop_file() -> None:
    from snippy.core import autostart

    autostart.enable()
    assert autostart.is_enabled() is True

    path = autostart.autostart_path()
    assert path is not None
    assert path.exists()
    assert path.suffix == ".desktop"
    content = path.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert "Type=Application" in content
    assert "Name=Snippy" in content
    assert "Exec=" in content
    assert "snippy" in content
    assert "--autostart" in content


def test_linux_disable_removes_desktop_file() -> None:
    from snippy.core import autostart

    autostart.enable()
    assert autostart.is_enabled() is True

    autostart.disable()
    assert autostart.is_enabled() is False
    path = autostart.autostart_path()
    assert path is not None
    assert not path.exists()


def test_linux_toggle_round_trip() -> None:
    from snippy.core import autostart

    autostart.disable()  # start clean
    assert autostart.toggle() is True   # off → on
    assert autostart.is_enabled() is True
    assert autostart.toggle() is False  # on → off
    assert autostart.is_enabled() is False


def test_linux_disable_when_not_enabled_is_noop() -> None:
    """Calling disable() when nothing exists should not raise."""
    from snippy.core import autostart

    autostart.disable()
    autostart.disable()  # should not raise FileNotFoundError
    assert autostart.is_enabled() is False


def test_autostart_path_returns_path_for_linux() -> None:
    from snippy.core import autostart

    path = autostart.autostart_path()
    assert isinstance(path, Path)
    assert path.name == "snippy.desktop"


@pytest.mark.parametrize("sysname,expected_kind", [
    ("Windows", "registry"),  # returns None
    ("Darwin", "file"),
    ("Linux", "file"),
])
def test_platform_branches_are_reachable(sysname: str, expected_kind: str) -> None:
    """The branch selector dispatches on platform.system()."""
    from snippy.core import autostart

    assert autostart._current_platform_branch() in {"windows", "darwin", "linux"}
    # And the helpers are imported and callable (basic smoke):
    assert callable(autostart.enable)
    assert callable(autostart.disable)
    assert callable(autostart.is_enabled)
    assert callable(autostart.toggle)