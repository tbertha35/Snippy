#!/usr/bin/env bash
# ============================================================================
# build_macos.sh - one-command macOS build for Snippy
# ============================================================================
#
# Produces:
#   dist/Snippy.app          (the PyInstaller one-folder build, wrapped in .app)
#   dist/Snippy-X.Y.Z.dmg    (a drag-to-install disk image)
#
# The .dmg is styled like a standard Mac installer:
#   - Snippy.app on the left
#   - an alias to /Applications on the right
#   - a background graphic with "Drag to Applications" text
#
# Used by .github/workflows/release.yml on macos-latest runners.
# Can also be run locally on macOS with Python 3.10+.
# ============================================================================

set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (parent of packaging/)

# --- 1. Resolve version --------------------------------------------------------
VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    VERSION="$(python3 -c 'import snippy; print(snippy.__version__)')"
fi
echo "=== Snippy macOS build (version $VERSION) ==="

# --- 2. Pick the target architecture ------------------------------------------
ARCH="${ARCH:-$(uname -m)}"   # arm64 on Apple Silicon, x86_64 on Intel
echo "Target arch: $ARCH"

# --- 3. Make sure pyinstaller is installed -----------------------------------
if ! python3 -m pip show pyinstaller >/dev/null 2>&1; then
    echo "Installing PyInstaller..."
    python3 -m pip install pyinstaller==6.*
fi
# Pillow is required for PyInstaller's BUNDLE() icon conversion and for the
# drag-to-install DMG background graphic.
if ! python3 -m pip show pillow >/dev/null 2>&1; then
    echo "Installing Pillow (build-time icon + DMG background)..."
    python3 -m pip install pillow
fi

# --- 4. Clean previous build --------------------------------------------------
rm -rf dist build
rm -rf Snippy.app    # stale one-folder, if any

# --- 5. Run PyInstaller --------------------------------------------------------
echo "[1/4] Running PyInstaller..."
# NOTE: --target-arch is a pyi-makespec option, not a pyinstaller option when
# using a .spec file. PyInstaller on an Apple Silicon runner builds a native
# arm64 binary by default; universal2 is a separate concern handled elsewhere.
pyinstaller packaging/pyinstaller.spec --clean --noconfirm

# --- 6. PyInstaller BUNDLE() produces dist/Snippy.app -------------------------
# On macOS the spec uses PyInstaller's BUNDLE() to create a proper .app
# bundle with the correct Contents/MacOS + Contents/Frameworks layout.
# We just inject the real version number into the generated Info.plist.
echo "[2/4] Finalizing Snippy.app..."
sed -i '' "s|<string>0\\.3\\.0</string>|<string>$VERSION</string>|g" dist/Snippy.app/Contents/Info.plist

# Sanity check: CFBundleExecutable must be a real executable file.
if [[ ! -x dist/Snippy.app/Contents/MacOS/Snippy ]]; then
    echo "ERROR: CFBundleExecutable 'Snippy' is not an executable file." >&2
    file dist/Snippy.app/Contents/MacOS/Snippy || true
    exit 1
fi

# --- 7. Code-sign -------------------------------------------------------------
# Ad-hoc sign by default so the .app has a stable code identity.
# If a real Developer ID is provided via CODESIGN_IDENTITY, use it instead.
echo "[3/4] Code-signing..."
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
    codesign --force --options runtime \
             --sign "$CODESIGN_IDENTITY" \
             --entitlements packaging/macos/entitlements.plist \
             dist/Snippy.app/Contents/MacOS/Snippy
    codesign --force --sign "$CODESIGN_IDENTITY" dist/Snippy.app
    if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
        echo "[optional] Notarizing via notarytool profile: $NOTARYTOOL_PROFILE"
        ditto -c -k --sequesterRsrc --keepParent dist/Snippy.app dist/Snippy.zip
        xcrun notarytool submit dist/Snippy.zip --keychain-profile "$NOTARYTOOL_PROFILE" --wait
        xcrun stapler staple dist/Snippy.app
        rm -f dist/Snippy.zip
    fi
else
    codesign --force --options runtime \
             --sign - \
             --entitlements packaging/macos/entitlements.plist \
             dist/Snippy.app/Contents/MacOS/Snippy
    codesign --force --sign - dist/Snippy.app
fi

# Remove the Gatekeeper quarantine attribute so local testing doesn't
# require right-click > Open on every rebuild.
xattr -dr com.apple.quarantine dist/Snippy.app 2>/dev/null || true

# --- 8. Build the drag-to-install .dmg ----------------------------------------
echo "[4/4] Building drag-to-install .dmg..."
DMG_PATH="dist/Snippy-${VERSION}.dmg"
DMG_STAGING="dist/dmg_staging"
DMG_RW="dist/Snippy-${VERSION}-rw.dmg"
rm -rf "$DMG_STAGING" "$DMG_RW"
mkdir -p "$DMG_STAGING/.background"

# Copy the signed app and create the Applications alias.
cp -R dist/Snippy.app "$DMG_STAGING/Snippy.app"
ln -s /Applications "$DMG_STAGING/Applications"

# Generate a simple background graphic.
python3 - <<'PY'
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out = Path("dist/dmg_staging/.background/background.png")
W, H = 640, 400
img = Image.new("RGB", (W, H), "#f5f5f7")
draw = ImageDraw.Draw(img)

# Try to use a nice system font; fall back to the default Pillow bitmap font.
try:
    title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
    body_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
except Exception:
    title_font = ImageFont.load_default()
    body_font = title_font

# Title
title = "Install Snippy"
bbox = draw.textbbox((0, 0), title, font=title_font)
tw = bbox[2] - bbox[0]
draw.text(((W - tw) / 2, 30), title, fill="#1d1d1f", font=title_font)

# Arrow between the two icons (icon centers are roughly at these coords)
arrow_start = (200, 230)
arrow_end = (440, 230)
draw.line([arrow_start, arrow_end], fill="#86868b", width=4)
# arrowhead
draw.polygon([(arrow_end[0], arrow_end[1]-8), (arrow_end[0], arrow_end[1]+8),
              (arrow_end[0]+14, arrow_end[1])], fill="#86868b")

# Labels under the arrow
label1 = "Drag here"
label2 = "Applications"
bbox1 = draw.textbbox((0, 0), label1, font=body_font)
bbox2 = draw.textbbox((0, 0), label2, font=body_font)
draw.text(((arrow_start[0] - (bbox1[2]-bbox1[0])/2), 260), label1, fill="#86868b", font=body_font)
draw.text(((arrow_end[0] - (bbox2[2]-bbox2[0])/2), 260), label2, fill="#86868b", font=body_font)

# Bottom hint
hint = "Drag Snippy.app onto the Applications folder to install"
bbox = draw.textbbox((0, 0), hint, font=body_font)
hw = bbox[2] - bbox[0]
draw.text(((W - hw) / 2, 350), hint, fill="#86868b", font=body_font)

img.save(out, "PNG")
PY

# Create a temporary read/write DMG.
hdiutil create -volname "Snippy $VERSION" \
               -srcfolder "$DMG_STAGING" \
               -ov -format UDRW \
               -size 200m \
               "$DMG_RW"

# Mount the read/write DMG and use AppleScript to set the Finder layout.
echo "Configuring DMG layout (this may take a few seconds)..."
ATTACH_PLIST="/tmp/snippy_attach_$$.plist"
hdiutil attach -readwrite -noverify -noautoopen -plist "$DMG_RW" > "$ATTACH_PLIST"
MOUNT=$(python3 -c "
import plistlib, sys
p = plistlib.load(open('$ATTACH_PLIST', 'rb'))
for ent in p.get('system-entities', []):
    mp = ent.get('mount-point')
    if mp:
        print(mp)
        break
")
rm -f "$ATTACH_PLIST"

if [[ -z "$MOUNT" ]]; then
    echo "ERROR: Could not determine DMG mount point." >&2
    exit 1
fi

# Wait for Finder to see the volume.
sleep 2

osascript <<EOF
tell application "Finder"
    set dmgFolder to POSIX file "$MOUNT" as alias
    set bgImage to POSIX file "$MOUNT/.background/background.png" as alias
    open dmgFolder
    delay 0.5
    set w to front window
    set current view of w to icon view
    set toolbar visible of w to false
    set statusbar visible of w to false
    set bounds of w to {200, 120, 840, 520}
    set viewOptions to icon view options of w
    set arrangement of viewOptions to not arranged
    set icon size of viewOptions to 100
    set background picture of viewOptions to bgImage
    set position of item "Snippy.app" of w to {120, 210}
    set position of item "Applications" of w to {520, 210}
    close w
end tell
EOF

# Give Finder time to flush the .DS_Store before detaching.
sleep 2
hdiutil detach "$MOUNT" -force

# Convert the read/write DMG to a compressed read-only distribution DMG.
hdiutil convert "$DMG_RW" -format UDZO -ov -o "$DMG_PATH"

# Clean up staging.
rm -rf "$DMG_STAGING" "$DMG_RW"

echo ""
echo "=== Build complete ==="
echo "Output: dist/"
ls -lh dist