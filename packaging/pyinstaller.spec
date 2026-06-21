# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Snippy — works on Windows, macOS, and Linux.
#
# Built per platform by the matching `build_*.{bat,sh}` script. The
# `release.yml` GitHub Actions workflow runs those scripts on each
# platform runner (windows-latest / macos-latest / ubuntu-latest) and
# uploads the resulting bundle as a build artifact.
#
# Why a single spec? PyInstaller is the same binary on all platforms;
# only the wrapping (Inno Setup on Windows, .app bundle on macOS,
# AppImage on Linux) differs, and that's done by the post-pyinstaller
# build scripts, not PyInstaller.
#
# Run directly with:  pyinstaller packaging/pyinstaller.spec --clean --noconfirm

from pathlib import Path
import os
import sys

block_cipher = None  # no PyInstaller bytecode encryption; we ship plain .pyc

# Detect project root (this file lives in `packaging/`, so .. is the root).
PROJECT_ROOT = Path(SPECPATH).resolve().parent
DIST = PROJECT_ROOT / 'dist'
BUILD = PROJECT_ROOT / 'build'

# ---------------------------------------------------------------------------
# What to bundle
# ---------------------------------------------------------------------------

# Hidden imports: PyInstaller's static analyzer sometimes misses dynamically
# imported submodules. These are the ones we *know* are imported by string
# in the app.
hidden = [
    'cryptography.hazmat.primitives.kdf.scrypt',
    'cryptography.hazmat.primitives.ciphers.aead',
    'rapidfuzz.process_cpp',
    'rapidfuzz.fuzz_cpp',
    'pynput',
    'pynput.keyboard',
    'pynput.keyboard._base',
    'pynput.keyboard._darwin',
    'pynput.keyboard._xorg',
    'pynput.keyboard._win32',
    'pynput.mouse',
    'six',
]

# Collect PySide6 plugins. PyInstaller 6 has a builtin hook for PySide6 but
# it sometimes misses the platform plugins dir on Linux. Being explicit
# costs nothing.  We deliberately skip QML/QtQuick data because Snippy uses
# Qt Widgets and those bundles can contain nested .app bundles that break
# macOS code signing.
from PyInstaller.utils.hooks import collect_data_files
pyside6_data = collect_data_files('PySide6', includes=['plugins/platforms/*',
                                                       'plugins/imageformats/*',
                                                       'plugins/iconengines/*'])

# Bundled assets (icon, ding.wav, themes README).
datas = [
    (str(PROJECT_ROOT / 'snippy' / 'assets'),  'snippy/assets'),
]

# pynput's platform-specific backends (e.g. pynput.keyboard._darwin) are
# covered by hiddenimports; we no longer copy the whole pynput/six package
# trees because that drags in macOS test bundles, dSYM bundles, and
# nested .app bundles that break macOS code signing.

a = Analysis(
    [str(PROJECT_ROOT / 'snippy' / '__main__.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas + pyside6_data,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim some heavyweight stdlib modules the app never imports.
        'tkinter', 'test', 'unittest', 'pydoc_data',
        # NOTE: QtMultimedia is REQUIRED — snippy/ui/feedback.py imports
        # QSoundEffect for the copy/paste audio feedback. Do not exclude it.
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.Qt3DCore', 'PySide6.QtBluetooth',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,        # one-folder for the Windows installer; .app / AppImage wrap the folder
    name='Snippy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,                   # leave symbols so debug info is available
    upx=False,                     # don't compress (slower startup, false AV positives)
    console=False,                 # GUI app, no console window
    disable_windowed_traceback=False,
    target_arch=None,              # None = native arch; macOS job passes --target-arch=universal2
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / 'snippy' / 'assets' / 'snippy.ico'),
                                   # multi-resolution .ico embedded into the Windows .exe
                                   # (16/24/32/48/64/128/256). Regenerate from
                                   # `python snippy/assets/generate_ico.py` when the
                                   # `appimage.png` design changes. macOS ignores this
                                   # (uses the .icns in macOS/Info.plist).
    version=None,                  # add a Windows VersionInfo resource here when we code-sign
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Snippy',
)

# ---------------------------------------------------------------------------
# macOS .app bundle
# ---------------------------------------------------------------------------
# On macOS, use PyInstaller's BUNDLE() to create a proper .app bundle.
# This produces a layout that LaunchServices recognizes, places libraries
# in Contents/Frameworks, and is compatible with ad-hoc / real code-signing.
# Windows and Linux keep the one-folder COLLECT output and wrap it later.
if sys.platform == 'darwin':
    from PyInstaller.building.osx import BUNDLE
    app = BUNDLE(
        exe,
        coll,
        name='Snippy.app',
        icon=str(PROJECT_ROOT / 'snippy' / 'assets' / 'appimage.png'),
        bundle_identifier='app.tbertha35.snippy',
        info_plist={
            'CFBundleName': 'Snippy',
            'CFBundleDisplayName': 'Snippy',
            'CFBundleIdentifier': 'app.tbertha35.snippy',
            'CFBundleInfoDictionaryVersion': '6.0',
            'CFBundleVersion': '0.3.0',
            'CFBundleShortVersionString': '0.3.0',
            'LSMinimumSystemVersion': '11.0',
            'NSHighResolutionCapable': True,
            'LSUIElement': True,
            'NSHumanReadableCopyright': 'MIT License',
        },
    )
