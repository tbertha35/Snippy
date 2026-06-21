#!/usr/bin/env bash
# ============================================================================
# build_linux.sh — one-command Linux build for Snippy
# ============================================================================
#
# Produces:
#   dist/Snippy/                (the PyInstaller one-folder build)
#   dist/Snippy-X.Y.Z.AppImage  (the AppImage for end-user distribution)
#   dist/snippy_X.Y.Z_amd64.deb (a .deb for Debian/Ubuntu)
#
# Used by .github/workflows/release.yml on ubuntu-latest runners.
# Can also be run locally on a Debian-family distro with Python 3.10+.
#
# Usage:
#   packaging/build_linux.sh                 (defaults to current version)
#   packaging/build_linux.sh 0.3.0           (override the version)
#
# Requirements (all installed by the GitHub Actions workflow):
#   - python3, python3-pip
#   - libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0
#   - fakeroot dpkg (for the .deb)
#   - wget (to download linuxdeploy)
# ============================================================================

set -euo pipefail

cd "$(dirname "$0")/.."   # repo root

# --- 1. Resolve version --------------------------------------------------------
VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    VERSION="$(python3 -c 'import snippy; print(snippy.__version__)')"
fi
ARCH="$(uname -m)"   # x86_64 / aarch64
echo "=== Snippy Linux build (version $VERSION, arch $ARCH) ==="

# --- 2. Make sure pyinstaller is installed -----------------------------------
if ! python3 -m pip show pyinstaller >/dev/null 2>&1; then
    echo "Installing PyInstaller..."
    python3 -m pip install pyinstaller==6.*
fi

# --- 3. Clean previous build --------------------------------------------------
rm -rf dist build
rm -rf AppDir

# --- 4. Run PyInstaller --------------------------------------------------------
echo "[1/3] Running PyInstaller..."
pyinstaller packaging/pyinstaller.spec --clean --noconfirm

# --- 5. Build the AppImage ----------------------------------------------------
# linuxdeploy + the Qt + AppImage plugins turn the one-folder build into
# a single-file AppImage that runs on most distros without installing anything.
echo "[2/3] Building AppImage..."
LINUXDEPLOY_VERSION="continuous"
LINUXDEPLOY_URL="https://github.com/linuxdeploy/linuxdeploy/releases/download/${LINUXDEPLOY_VERSION}/linuxdeploy-${ARCH}.AppImage"
# --show-progress + set -e (from the script header) means a download failure
# (404, rate limit, etc.) will halt the build loudly instead of producing
# a half-installed linuxdeploy.AppImage that then fails cryptically later.
wget --show-progress "$LINUXDEPLOY_URL" -O linuxdeploy.AppImage || { echo "FATAL: failed to download linuxdeploy from $LINUXDEPLOY_URL"; exit 1; }
chmod +x linuxdeploy.AppImage

# Qt plugin (PySide6) + the AppImage plugin
wget --show-progress "https://github.com/linuxdeploy/linuxdeploy-plugin-qt/releases/download/${LINUXDEPLOY_VERSION}/linuxdeploy-plugin-qt-${ARCH}.AppImage" \
     -O linuxdeploy-plugin-qt.AppImage || { echo "FATAL: failed to download linuxdeploy-plugin-qt"; exit 1; }
chmod +x linuxdeploy-plugin-qt.AppImage
wget --show-progress "https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/${LINUXDEPLOY_VERSION}/linuxdeploy-plugin-appimage-${ARCH}.AppImage" \
     -O linuxdeploy-plugin-appimage.AppImage || { echo "FATAL: failed to download linuxdeploy-plugin-appimage"; exit 1; }
chmod +x linuxdeploy-plugin-appimage.AppImage

# Stage everything into AppDir/ using the standard linuxdeploy workflow
mkdir -p AppDir
cp -r dist/Snippy/* AppDir/
cp packaging/snippy.desktop AppDir/

# The user provided snippy/assets/appimage.png for the Linux/AppImage icon.
# linuxdeploy expects the icon file name to match the .desktop Icon= value.
# Copy it into place as snippy.png so Icon=snippy in snippy.desktop resolves.
ICON_SRC="snippy/assets/appimage.png"
ICON_DST="snippy/assets/snippy.png"
if [ -f "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$ICON_DST"
fi

# linuxdeploy-plugin-qt auto-discovers qmake on PATH. The release.yml workflow
# installs qt6-base-dev, which provides /usr/bin/qmake.
# (Exporting QMAKE here doesn't help because the extracted AppImage plugin
# doesn't inherit the caller's environment.)

# linuxdeploy uses --executable= to know which binary is the "entry point".
# NOTE: we intentionally do NOT use --plugin=qt. PyInstaller already
# bundles PySide6 + all required Qt platform plugins into dist/Snippy/.
# The qt plugin expects a traditionally-deployed Qt app and fails with
# "Could not find Qt modules to deploy" when Qt is already inside the
# PyInstaller one-folder build. Just let linuxdeploy bundle the folder.
# NOTE: appimage is an *output* plugin, invoked via --output=appimage,
# not --plugin=appimage. The latter fails with:
#   "Plugin appimage is an output plugin, please use like --output appimage"
./linuxdeploy.AppImage \
    --appdir=AppDir \
    --executable=AppDir/Snippy \
    --desktop-file=AppDir/snippy.desktop \
    --icon-file=snippy/assets/snippy.png \
    --output=appimage 2>&1 | tail -20
rm -f linuxdeploy*.AppImage

# Rename the produced AppImage with the version + arch
APPIMAGE_OUT="$(ls Snippy-*.AppImage 2>/dev/null | head -1)"
if [[ -z "$APPIMAGE_OUT" ]]; then
    echo "ERROR: linuxdeploy did not produce an AppImage"
    exit 1
fi
mv "$APPIMAGE_OUT" "dist/Snippy-${VERSION}-${ARCH}.AppImage"

# --- 6. Build the .deb --------------------------------------------------------
# Quick-and-dirty .deb using fakeroot. We use the AppDir tree as the payload.
echo "[3/3] Building .deb..."
DEB_DIR="dist/deb-staging"
rm -rf "$DEB_DIR"
mkdir -p "$DEB_DIR/DEBIAN" "$DEB_DIR/usr/bin" "$DEB_DIR/usr/lib/snippy"
cp -r AppDir/* "$DEB_DIR/usr/lib/snippy/"
ln -s /usr/lib/snippy/Snippy "$DEB_DIR/usr/bin/snippy"

cat > "$DEB_DIR/DEBIAN/control" <<EOF
Package: snippy
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: Snippy Contributors
Depends: libxcb-xinerama0, libxkbcommon-x11-0, libxcb-cursor0, libgl1, libegl1
Description: Smart clipboard & snippet manager for the system tray.
 Snippy watches your clipboard, stores snippets in a local SQLite
 database, and pops up a slick command palette (Ctrl+Space) when
 you need to recall something you copied. 100% local, MIT licensed,
 end-to-end AES-256 encrypted .snip bundles for cross-device sync.
EOF

cat > "$DEB_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
# Update the desktop + icon caches so the launcher shows up immediately
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
fi
EOF
chmod 755 "$DEB_DIR/DEBIAN/postinst"

mkdir -p "$DEB_DIR/usr/share/applications"
cp packaging/snippy.desktop "$DEB_DIR/usr/share/applications/"

fakeroot dpkg-deb --build "$DEB_DIR" "dist/snippy_${VERSION}_${ARCH}.deb"
rm -rf "$DEB_DIR"

echo ""
echo "=== Build complete ==="
echo "Output: dist/"
ls -lh dist