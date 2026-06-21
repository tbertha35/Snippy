# Snippy Packaging

Platform-specific build scripts and config for Snippy. **The normal user never
sees this folder** — it's invoked by the GitHub Actions `.github/workflows/release.yml`
workflow on every release tag.

| File | What it does |
|------|--------------|
| `pyinstaller.spec` | Single cross-platform PyInstaller spec. Collects the `snippy/` package, the bundled `assets/`, and the PySide6 Qt plugins. Reads the entry point from `snippy/__main__.py`. |
| `inno_setup.iss` | Windows installer. Reads `/DSnippyVersion=<ver>` from the CLI to stamp the installer name + Add/Remove Programs entry. Includes a "Also delete my snippets" Yes/No prompt on uninstall. |
| `snippy.desktop` | Linux `.desktop` file. Used by the AppImage launcher and by the `.deb` postinst to register Snippy in the application menu. |
| `macos/entitlements.plist` | macOS hardened-runtime entitlements (clipboard, input monitoring, network client/server, JIT). Required for the `codesign` step in `build_macos.sh` when `CODESIGN_IDENTITY` is set. |
| `build_windows.bat` | Windows: PyInstaller → Inno Setup → portable `.zip`. The `.github/workflows/release.yml` `build-windows` job runs this. |
| `build_macos.sh` | macOS: PyInstaller → `Snippy.app` bundle → `.dmg` (via `create-dmg` or `hdiutil`). Optionally code-signs + notarizes if `CODESIGN_IDENTITY` and `NOTARYTOOL_PROFILE` env vars are set. |
| `build_linux.sh` | Linux: PyInstaller → AppImage (via `linuxdeploy` + the Qt + AppImage plugins) → `.deb` (via `fakeroot dpkg-deb`). |

## CI pipeline

`.github/workflows/release.yml` runs one matrix job per platform on every
push to `main` and every GitHub Release publication:

```
push to main
   └─► (matrix: windows-latest, macos-latest, ubuntu-latest)
        ├─► pip install -r requirements.txt
        ├─► pip install pyinstaller==6.*
        ├─► pytest -q               (sanity check on the runner)
        └─► packaging/build_*.{bat,sh}
              └─► upload-artifact (the installer + portable artifacts)

release: published
   └─► release job (ubuntu-latest, needs all builds)
        ├─► download-artifact (all 3)
        └─► softprops/action-gh-release@v2  (attach to the GitHub Release,
                                             body from CHANGELOG.md)
```

Result on a new tag like `v0.3.0`:
- `Snippy-Setup-0.3.0.exe` (Windows installer, ~70 MB)
- `Snippy-0.3.0-portable.zip` (Windows portable, no install)
- `Snippy-0.3.0.dmg` (macOS, ~80 MB)
- `Snippy-0.3.0-x86_64.AppImage` (Linux portable, ~90 MB)
- `snippy_0.3.0_amd64.deb` (Linux Debian/Ubuntu)

…all attached to the GitHub Release page automatically.

## Local build

If you want to build a binary on your own machine (faster iteration, no
CI round-trip), install the platform's tooling and run the script:

```bash
# Windows
pip install pyinstaller==6.*
packaging\build_windows.bat 0.3.0

# macOS
pip install pyinstaller==6.*
packaging/build_macos.sh 0.3.0
# To target both Apple Silicon + Intel:
#   ARCH=universal2 packaging/build_macos.sh 0.3.0
# (universal2 requires building twice and `lipo`ing the result; the current
#  script targets the host arch by default. Extend it if you need true
#  universal binaries.)

# Linux
sudo apt install -y libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0 libgl1 libegl1 fakeroot
pip install pyinstaller==6.*
packaging/build_linux.sh 0.3.0
```

## What the spec does (and doesn't do)

- ✅ **Bundles** the `snippy/` Python package, the `assets/` folder, and
  the PySide6 Qt platform plugins (so `xcb`/`cocoa`/`windows` is found at
  runtime without an env var).
- ✅ **Excludes** Qt modules Snippy never imports (WebEngine, Bluetooth,
  3D, etc.) to keep the binary small.
- ❌ **Does not code-sign** — that happens in the platform build scripts
  (Inno Setup on Windows, `codesign` on macOS, no signing on Linux). The
  first release is unsigned; `softprops/action-gh-release` will attach the
  files anyway, and the OS will show the standard "unidentified developer"
  warning. Add a code-signing cert to your GH Actions secrets when you're
  ready to distribute widely.
- ❌ **Does not produce auto-update metadata** — that's a v0.4+ concern.

## Things to wire up later (not in this drop)

- [ ] Real `assets/icon.png` (we currently draw the icon programmatically;
      add a 256×256 PNG and the .ico / .icns variants for the installers).
- [ ] Windows code-signing (Authenticode). Add `WINDOWS_CERT_BASE64` +
      `WINDOWS_CERT_PASSWORD` repo secrets and a `signtool` step in
      `build_windows.bat` after the ISCC run.
- [ ] macOS notarization profile (`xcrun notarytool store-credentials`).
      Set `CODESIGN_IDENTITY` + `NOTARYTOOL_PROFILE` env vars on the runner.
- [ ] Linux `.deb` polished package (man page, lintian-clean control file,
      AppStream metadata). Current `.deb` is a quick-and-dirty fakeroot build
      that works but doesn't pass `lintian` cleanly.
- [ ] Universal macOS build (one binary for Intel + Apple Silicon).