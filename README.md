# ✂️ Snippy

> **Smart clipboard & snippet manager that lives in your system tray.**
> Find anything you've ever copied. Sync across your devices with encrypted bundles.

> 🆕 **Easy install & uninstall** — installers for Windows / macOS / Linux, no Python required. No CLI needed for backup/restore — it’s all in the app. You’ll always *see* Snippy catch a copy with a subtle toast + tray icon flash.

[![Status](https://img.shields.io/badge/status-v0.3.0--shipped-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![Qt](https://img.shields.io/badge/Qt-PySide6-green)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-purple)]()


Snippy watches your clipboard, stores everything in a local SQLite database, and pops up a slick command-palette (`Ctrl+Space`) whenever you need to recall something you copied. It's local-first, private, and extensible.

---

## 📑 Table of Contents
- [Why Snippy?](#-why-snippy)
- [Features](#-features)
- [Install (Easy Way)](#-install-easy-way)
- [Uninstall](#-uninstall)
- [Usage](#-usage)
- [Hotkeys](#-hotkeys)
- [Backup & Restore](#-backup--restore)
- [License](#-license)

---

## 🤔 Why Snippy?

There are dozens of clipboard managers. Snippy is different because it:

- **Respects your privacy** — 100% local, no telemetry, no accounts, open source.
- **Talks to your other apps** — local HTTP API makes it trivial to wire into your custom voice assistant or anything else.
- **Syncs without a server** — encrypted `.snip` bundles you can drop on a USB stick, into Google Drive, or anywhere.
- **Stays out of your way** — lives in the tray, one hotkey away, never gets in front of your work.
- **Plays well with your stack** — Python + PySide6, easy to hack on, easy to extend.

---

## ✨ Features

See **[FEATURES.md](FEATURES.md)** for the full list (~100 features tracked). Highlights:

- 🔍 **Fuzzy live search** with `rapidfuzz` — typo-tolerant, instant
- 🏷️ **Auto-detects** URLs, emails, phone numbers, code, file paths
- 📌 **Pin** important snippets so they always sit at the top
- ⌨️ **Keyboard-first** command palette — never reach for the mouse
- 🎨 **Themes** — Light, Dark, plus custom `.qss` themes
- 🔐 **Encrypted `.snip` bundles** — AES-256 via Fernet, passphrase-protected
- 🛡️ **No telemetry, no cloud, no lock-in**

---

### Search operators cheat sheet (v0.2.0)

| Operator | Example | Effect |
|----------|---------|--------|
| `tag:` | `tag:work` | Only snippets with this tag |
| `type:` | `type:url` or `type:code` | Only snippets of this content type |
| `pin:` | `pin:yes` / `pin:no` | Pinned or unpinned only |
| `before:` | `before:2026-01-01` | Created before this date |
| `after:` | `after:2026-05-01` | Created after this date |
| `archive:` | `archive:no` | Include or exclude archived |
| Free text | `github token` | Fuzzy-matched against content (v0.1.0 behavior) |

Operators compose: `tag:work type:url pin:yes github` returns work-tagged pinned URLs fuzzy-matching “github”. The free-text portion is always fuzzy-matched, so typos are forgiven.

---

## 📦 Install (Easy Way)

> **No Python, no terminal, no `pip` required.** Download, double-click, done.

Pick the one for your platform:

| Platform | Installer | Portable? |
|----------|-----------|-----------|
| **Windows** | `Snippy-Setup-0.3.0.exe` (latest) — Start Menu + Add/Remove Programs | `Snippy-0.3.0-portable.zip` (latest) — extract & run, no install |
| **macOS** | `Snippy-0.3.0.dmg` (latest) — drag to Applications | n/a |
| **Linux** | `Snippy-0.3.0.AppImage` (latest) — `chmod +x` and run | `.deb` package available for Debian/Ubuntu: `sudo dpkg -i snippy_0.3.0.deb` |

> All downloads come from the [Releases page](https://github.com/tbertha35/snippy/releases/latest). GitHub Actions builds the installer for every release tag (see `.github/workflows/release.yml`).

### macOS install notes

1. Double-click `Snippy-0.3.0.dmg` to mount it.
2. A Finder window opens with **Snippy.app** on the left and an **Applications** shortcut on the right.
3. Drag **Snippy.app** onto the **Applications** shortcut.
4. Open **Applications → Snippy.app**.
   - The first time you launch, macOS Gatekeeper will warn you because the app is not signed with a paid Apple Developer ID. Right-click Snippy.app → **Open**, then click **Open** in the dialog.
5. Snippy appears in your system tray (menu bar). Click the tray icon or right-click it for options.

> **macOS hotkeys:** Snippy does **not** register global hotkeys on macOS (Accessibility permission is unreliable for ad-hoc signed apps). Open Snippy by clicking the tray icon or using the tray menu. Windows and Linux still use `Ctrl+Space`.

> **Launch at login:** Open Snippy Settings, check **“Launch Snippy at login”**, then **log out and back in** (or restart) for the LaunchAgent to take effect.

> **Screen recording:** The first time you use **Capture screen region…** on macOS, macOS asks for **Screen Recording** permission. Allow it so Snippy can take the screenshot.

### Windows / Linux install notes

After install, Snippy:
1. Launches in the **system tray** (look near your clock).
2. Starts watching your clipboard immediately.
3. Registers the **`Ctrl+Space`** hotkey to summon the palette.


## 🗑️ Uninstall

We made sure there’s **no leftover junk** and no orphan files.

| Platform | How | What stays behind |
|----------|-----|-------------------|
| **Windows** | **Settings → Apps → Installed apps → Snippy → Uninstall** | Your snippets in `%APPDATA%\Snippy\` (kept on purpose — you can re-install and pick up where you left off). Tick "Also delete my snippets" to remove. |
| **Windows portable** | Just delete the folder | Nothing |
| **macOS** | Open **Applications**, drag **Snippy.app** to the **Trash** | Your snippets stay in `~/Library/Application Support/Snippy/` |

### macOS uninstall details

1. Quit Snippy (right-click the tray icon → **Quit**).
2. Open **Finder → Applications**, drag **Snippy.app** to the **Trash**.
3. (Optional) Remove your snippets and settings:
   - Open Finder, hold **Option** and click **Go → Library**.
   - Delete `~/Library/Application Support/Snippy/`.
   - Delete `~/Library/Logs/Snippy/`.
4. (Optional) If you enabled “Launch at login”, the LaunchAgent file at `~/Library/LaunchAgents/com.snippy.client.plist` is removed when you toggle it off in Settings. If you already deleted the app first, you can delete that file manually.

### Windows / Linux uninstall details

The installer registers an uninstaller (Windows) / uninstalls cleanly (macOS drag-to-Trash, Linux apt) and removes all program files.

---

## 🚀 Usage

### First run
1. Launch Snippy — it starts in the system tray (look for the icon near your clock on Windows/Linux, or in the menu bar on macOS).
2. Copy anything — text, a URL, an email.
3. Open the Snippy History window:
   - **Windows / Linux:** press **`Ctrl+Space`** (default).
   - **macOS:** click the tray icon or choose **Open Snippy…** from the tray menu.
4. Start typing — results filter live.
5. Press **`Enter`** to copy the selected snippet to your clipboard.
6. Paste it anywhere.

### Tray menu
Right-click the tray icon for:
- **History** — browse all snippets
- **Export…** — save your snippets to a `.snip` file (optionally encrypted)
- **Import…** — load a `.snip` file (merge or replace)
- **Pause capture** — temporarily stop capturing
- **Settings** — open settings dialog
- **Quit** — exit Snippy

---

## ⌨️ Hotkeys

| Action | Default | Configurable | Status |
|--------|---------|--------------|--------|
| Open / close history | `Ctrl+Space` (Win/Linux) / tray click (macOS) |
| Navigate results | `↑` / `↓` |
| Copy selected | `Enter` |
| Close history| `Esc` |
| Toggle pin | `Ctrl+P` |
| Show details | `Ctrl+D` |
| Delete snippet | `Ctrl+Del` |

> **macOS note:** Global hotkeys are disabled on macOS. In-app shortcuts (arrow keys, `Ctrl+P`, etc.) still work while the History window is focused. Open History via the tray icon or tray menu.

---

## 🔐 Backup & Restore

**No CLI required.** All backup and restore is done in the app — through the tray menu or the Settings window.

### Export (backup)
Two ways to start an export, pick whichever is easiest:

1. **Tray menu** → **Export…** → pick a location, optionally set a passphrase → **Save**
2. **Settings → Backup & Restore** → big **Open Backup dialog…** button → same flow

The dialog asks:
- **Where to save** (default: your `Documents` folder)
- **Encrypt with passphrase?** (recommended for cloud/USB backups) — uses AES-256 (Fernet, scrypt KDF)
- **Include archived snippets?** (off by default — archives are typically noise in backups)

### Import (restore)
1. **Tray menu** → **Import…** → pick a `.snip` file
2. Enter the passphrase if prompted
3. Choose **Merge** (smart-merge with your existing snippets) or **Replace** (wipe and load)
4. Done. A confirmation toast shows how many snippets were added/skipped.


### Format
A `.snip` file is a zip archive containing:
- `manifest.json` — Snippy version, created-at, snippet count
- `db.sqlite` — your full Snippy database
- (optionally) — the whole archive is encrypted with Fernet (AES-128 in CBC mode + HMAC, derived from your passphrase via PBKDF2)

You can drop `.snip` files in Google Drive, OneDrive, iCloud, a USB stick, an email attachment, anywhere. Snippy has no opinion on transport.

---

## 📜 License

[MIT](LICENSE) — do whatever you want, just don't blame me.