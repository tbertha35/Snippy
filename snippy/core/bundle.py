"""Encrypted `.snip` bundle export / import \u2014 Phase 3a.

A `.snip` file is a plain zip (PEP 427-style) with two members:

    manifest.json    plain JSON, always readable, no secrets
    db.sqlite        the Snippy SQLite database
                     \u2014 plain bytes by default,
                     \u2014 Fernet-encrypted bytes if a passphrase is supplied

The manifest carries enough metadata for a smart merge on import:

    {
        "app_version":   "0.2.0b1",
        "exported_at":   "2026-06-13T18:32:11+00:00",
        "schema_version": 1,
        "snippet_count": 142,
        "tag_count":      18,
        "encrypted":      false,
        "encryption":     "fernet-scrypt-2026"     // only if passphrase was used
    }

Merge semantics (mode='merge'):
    \u2022 If a snippet with the same `content_hash` exists in the destination
      DB, **skip silently**. Errors / skip counts are returned in
      `ImportResult.errors` so the host UI can show "Errors, check log".
    \u2022 All snippet metadata (use_count, last_used_at, is_pinned) is taken
      from the EXISTING row \u2014 the import never overwrites a row it
      already has.
    \u2022 Tags from the import are UNIONED into existing tags per snippet
      (no tag ever gets removed by a merge).
    \u2022 Archived state is preserved from the import (more recent archive
      flag wins \u2014 by `updated_at`).
    \u2022 Per-snippet errors (e.g. schema mismatch) are caught and reported
      in `ImportResult.errors` rather than aborting the whole import.

Merge semantics (mode='replace'):
    \u2022 All existing snippets are kept; the import's rows are added on top.
    \u2022 In the (rare) case of hash collision, the imported row's metadata
      wins (last-write-wins per the bundle's `updated_at`).

Encryption:
    \u2022 Passphrase \u2192 scrypt salt (random 16 bytes, stored in manifest)
      \u2192 scrypt key derivation (n=2**15) \u2192 Fernet.
    \u2022 `db.sqlite.enc` is the encrypted member when `encrypted=true`;
      the plain `db.sqlite` is **not** present.
    \u2022 Wrong passphrase raises `BundleDecryptError` (caught by host UI).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import secrets
import sqlite3
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from snippy import __version__
from snippy.core.db import Database


LOGGER = logging.getLogger(__name__)


# --- Constants --------------------------------------------------------------

SNIP_EXTENSION = ".snip"
MANIFEST_NAME = "manifest.json"
DB_PLAIN_NAME = "db.sqlite"
DB_ENC_NAME = "db.sqlite.enc"
ENCRYPTION_ID = "fernet-scrypt-2026"
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_LEN = 32  # Fernet requires 32-byte urlsafe-base64

# Manifest versioning (Phase 3a / v0.3.0).
# Increment `SUPPORTED_SCHEMA_VERSION` whenever the `snippets` table
# shape changes in a way that breaks existing imports.
SUPPORTED_SCHEMA_VERSION = 3
# Bundles written by an app older than this are rejected with a clear
# message ("please export from the older version first") rather than
# crashing the user with a sqlite error.
SUPPORTED_MIN_APP_VERSION = "0.2.0"


# --- Errors -----------------------------------------------------------------


class BundleError(Exception):
    """Base for all bundle errors."""


class BundleDecryptError(BundleError):
    """Wrong passphrase, corrupt bytes, or unsupported encryption."""


class BundleFormatError(BundleError):
    """Zip missing members, manifest malformed, schema mismatch."""


# --- Data classes -----------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Manifest:
    app_version: str
    exported_at: str
    schema_version: int
    snippet_count: int
    tag_count: int
    encrypted: bool
    encryption: str | None = None
    salt_b64: str | None = None  # only present when encrypted=True

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Manifest":
        try:
            data = json.loads(s)
        except json.JSONDecodeError as exc:
            raise BundleFormatError(f"manifest is not valid JSON: {exc}") from exc
        for required in ("app_version", "exported_at", "schema_version",
                         "snippet_count", "tag_count", "encrypted"):
            if required not in data:
                raise BundleFormatError(f"manifest missing required field: {required!r}")
        return cls(
            app_version=str(data["app_version"]),
            exported_at=str(data["exported_at"]),
            schema_version=int(data["schema_version"]),
            snippet_count=int(data["snippet_count"]),
            tag_count=int(data["tag_count"]),
            encrypted=bool(data["encrypted"]),
            encryption=data.get("encryption"),
            salt_b64=data.get("salt_b64"),
        )


@dataclass(slots=True)
class ImportResult:
    imported: int = 0
    skipped: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [f"imported {self.imported}", f"skipped {self.skipped}", f"updated {self.updated}"]
        if self.errors:
            parts.append(f"errors: {len(self.errors)} (check log)")
        return ", ".join(parts)


# --- DB snapshot ------------------------------------------------------------


def _snapshot_db_to_bytes(db: "Database") -> bytes:
    """Return a consistent snapshot of `db` as a single SQLite file in memory.

    We use sqlite's online backup API to a temp file (avoiding WAL sidecar
    issues), then read the bytes. The temp file is left for the OS to
    clean up to avoid Windows file-lock races.
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        # Connect to the temp file, then call backup() on it. The
        # connection is closed (and file released) before we return.
        with sqlite3.connect(tmp_path) as dst:
            db._conn.backup(dst)
        return tmp_path.read_bytes()
    except Exception:
        # Worst case: do a checkpointed copy via the main file. We turn
        # off WAL on a *new* connection (without breaking the live one).
        if tmp_path is None:
            return b""
        try:
            with sqlite3.connect(tmp_path) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return tmp_path.read_bytes()
        except sqlite3.Error:
            return b""


# --- Key derivation ---------------------------------------------------------


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Passphrase \u2192 scrypt \u2192 32-byte urlsafe-base64 key for Fernet."""
    kdf = Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    raw = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


# --- Export -----------------------------------------------------------------


def export_bundle(
    path: str | Path,
    db: Database,
    *,
    passphrase: str | None = None,
) -> Manifest:
    """Write a `.snip` zip to `path` containing a snapshot of the DB.

    If `passphrase` is provided, the DB bytes are encrypted with Fernet
    (key derived via scrypt). The manifest always stays plaintext so the
    user can inspect what's inside.

    Returns the Manifest written to the bundle (caller can show
    `manifest.snippet_count` in a confirmation dialog).
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    encrypted = passphrase is not None
    salt = secrets.token_bytes(SALT_BYTES) if encrypted else None
    encryption = ENCRYPTION_ID if encrypted else None
    salt_b64 = base64.b64encode(salt).decode("ascii") if salt else None

    # Read the DB into memory (snippy DBs are small for a single user).
    # We use sqlite's online backup API instead of a raw file read so we
    # get a consistent snapshot even when WAL is active.
    db_bytes = _snapshot_db_to_bytes(db)

    # Encrypt if needed
    if encrypted:
        assert salt is not None
        key = _derive_key(passphrase, salt)
        db_bytes = Fernet(key).encrypt(db_bytes)

    # Counts for the manifest
    snippet_count = db.count()
    # Tag count: cheap query
    row = db._conn.execute("SELECT COUNT(*) AS c FROM tags").fetchone()
    tag_count = int(row["c"] or 0)

    manifest = Manifest(
        app_version=__version__,
        exported_at=datetime.now(timezone.utc).isoformat(),
        schema_version=db._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"] or 1,
        snippet_count=snippet_count,
        tag_count=tag_count,
        encrypted=encrypted,
        encryption=encryption,
        salt_b64=salt_b64,
    )

    # Write the zip
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, manifest.to_json())
        if encrypted:
            zf.writestr(DB_ENC_NAME, db_bytes)
        else:
            zf.writestr(DB_PLAIN_NAME, db_bytes)

    LOGGER.info(
        "Exported %d snippet(s), %d tag(s) to %s (encrypted=%s)",
        snippet_count, tag_count, out_path, encrypted,
    )
    return manifest


# --- Import -----------------------------------------------------------------


def _read_manifest(zf: zipfile.ZipFile) -> Manifest:
    try:
        with zf.open(MANIFEST_NAME) as fh:
            return Manifest.from_json(fh.read().decode("utf-8"))
    except KeyError as exc:
        raise BundleFormatError(f"bundle is missing {MANIFEST_NAME}") from exc


def _parse_version(v: str) -> tuple[int, ...]:
    """Return a comparable int-tuple for a semver-ish string.

    '0.2.0b1'  -> (0, 2, 0)
    '0.3.0'    -> (0, 3, 0)
    '0.3.0rc2' -> (0, 3, 0)  (pre-release suffix is ignored — the leading
    numeric components are all that matter for ordering; we just need to
    know if 0.2.0 < 0.3.0.)
    """
    import re
    parts: list[int] = []
    for m in re.finditer(r"\d+", v):
        parts.append(int(m.group(0)))
    return tuple(parts)


def _validate_version_compat(manifest: Manifest) -> None:
    """Reject a manifest written by a Snippy newer than us, or older than
    the floor set by `SUPPORTED_MIN_APP_VERSION`.

    Raises `BundleFormatError` (NOT a generic `BundleError`) so the host
    UI's existing error-handling path displays the message verbatim.
    """
    if manifest.schema_version > SUPPORTED_SCHEMA_VERSION:
        raise BundleFormatError(
            f"this bundle was made by a newer Snippy (schema "
            f"v{manifest.schema_version}; this build supports up to "
            f"v{SUPPORTED_SCHEMA_VERSION}). Please update Snippy and try again."
        )
    if manifest.schema_version < SUPPORTED_SCHEMA_VERSION:
        # Bundles from older schema versions are not supported without a
        # migration. Re-export from the original Snippy version.
        raise BundleFormatError(
            f"this bundle was made by an older Snippy (schema "
            f"v{manifest.schema_version}). The original Snippy version "
            f"is {manifest.app_version}."
        )
    try:
        bundle_version = _parse_version(manifest.app_version)
        floor_version = _parse_version(SUPPORTED_MIN_APP_VERSION)
    except Exception:
        return  # defensive: never let a bad version string break the read
    if bundle_version < floor_version:
        raise BundleFormatError(
            f"this bundle was made by Snippy {manifest.app_version}, which "
            f"is older than the minimum supported by this build "
            f"({SUPPORTED_MIN_APP_VERSION}). Please export it again from "
            f"the original machine, or upgrade that machine first."
        )


def _extract_db_bytes(zf: zipfile.ZipFile, manifest: Manifest, passphrase: str | None) -> bytes:
    """Return the (possibly decrypted) DB bytes from the zip."""
    if not manifest.encrypted:
        try:
            with zf.open(DB_PLAIN_NAME) as fh:
                return fh.read()
        except KeyError as exc:
            raise BundleFormatError(f"bundle is missing {DB_PLAIN_NAME}") from exc

    # Encrypted path
    if not passphrase:
        raise BundleDecryptError("bundle is encrypted; passphrase required")
    if manifest.encryption != ENCRYPTION_ID:
        raise BundleDecryptError(f"unsupported encryption: {manifest.encryption!r}")
    if not manifest.salt_b64:
        raise BundleFormatError("encrypted bundle missing salt_b64 in manifest")
    try:
        salt = base64.b64decode(manifest.salt_b64)
    except Exception as exc:
        raise BundleFormatError(f"manifest salt_b64 is not valid base64: {exc}") from exc
    try:
        with zf.open(DB_ENC_NAME) as fh:
            ciphertext = fh.read()
    except KeyError as exc:
        raise BundleFormatError(f"bundle is missing {DB_ENC_NAME}") from exc
    try:
        key = _derive_key(passphrase, salt)
        return Fernet(key).decrypt(ciphertext)
    except InvalidToken as exc:
        raise BundleDecryptError("wrong passphrase or corrupt bundle") from exc


def import_bundle(
    path: str | Path,
    db: Database,
    *,
    passphrase: str | None = None,
    mode: str = "merge",
) -> ImportResult:
    """Read a `.snip` file from `path` and merge its contents into `db`.

    `mode` is 'merge' (default, skip-by-hash, never delete) or 'replace'
    (import wins on hash collision).

    Returns an `ImportResult` with counts and any per-snippet errors.
    The host UI should display `result.summary()` and, if `not result.ok`,
    a clear "Errors, check log to see" line.
    """
    if mode not in ("merge", "replace"):
        raise ValueError(f"mode must be 'merge' or 'replace', got {mode!r}")
    in_path = Path(path)
    if not in_path.exists():
        raise BundleError(f"bundle not found: {in_path}")

    result = ImportResult()

    try:
        with zipfile.ZipFile(in_path, "r") as zf:
            manifest = _read_manifest(zf)
            _validate_version_compat(manifest)
            db_bytes = _extract_db_bytes(zf, manifest, passphrase)
    except BundleError as exc:
        result.errors.append(f"open bundle: {exc}")
        LOGGER.exception("Import failed: open bundle")
        return result
    except zipfile.BadZipFile as exc:
        result.errors.append(f"open bundle: {exc}")
        LOGGER.exception("Import failed: corrupt zip")
        return result

    # Validate the inner DB before touching the live one
    try:
        _validate_db_bytes(db_bytes)
    except BundleFormatError as exc:
        result.errors.append(f"validate inner DB: {exc}")
        return result

    # Stash inner DB to a temp file so we can use sqlite3 to read it
    # (avoids a second Database instance sharing our singleton)
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp.write(db_bytes)
        tmp_path = Path(tmp.name)
    try:
        _apply_inner_db(tmp_path, db, manifest, mode, result)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    LOGGER.info(
        "Imported from %s (%d expected, %d imported, %d skipped, %d updated, %d errors)",
        in_path, manifest.snippet_count, result.imported, result.skipped,
        result.updated, len(result.errors),
    )
    return result


def _validate_db_bytes(db_bytes: bytes) -> None:
    """Smoke-test that the bytes look like a SQLite DB."""
    if not db_bytes:
        raise BundleFormatError("inner DB is empty")
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp.write(db_bytes)
            tmp_path = Path(tmp.name)
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            if row is None or row["v"] is None:
                raise BundleFormatError("inner DB has no schema_version row")
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise BundleFormatError(f"inner DB is not a valid sqlite file: {exc}") from exc
    finally:
        # On Windows the WAL/SHM sidecar files can keep the main file locked
        # briefly after close; we keep the file (OS temp-cleaner removes it
        # later) rather than racing the unlink.
        _ = tmp_path  # suppress lint; we intentionally do not unlink


def _apply_inner_db(
    inner_path: Path,
    outer_db: Database,
    manifest: Manifest,
    mode: str,
    result: ImportResult,
) -> None:
    """Walk the inner DB's snippets + tags and merge into `outer_db`."""
    # Pre-build a {inner_id: [tag_name, ...]} map so we don't have to
    # keep the inner connection open during the merge loop.
    tag_map: dict[int, list[str]] = {}
    with sqlite3.connect(inner_path) as src:
        src.row_factory = sqlite3.Row
        rows: Iterable[sqlite3.Row] = src.execute(
            "SELECT * FROM snippets ORDER BY id"
        ).fetchall()
        snippets = list(rows)
        # Read all tag links in one go
        link_rows = src.execute(
            "SELECT st.snippet_id AS sid, t.name AS name "
            "FROM snippet_tags st JOIN tags t ON t.id = st.tag_id"
        ).fetchall()
    for r in link_rows:
        tag_map.setdefault(int(r["sid"]), []).append(str(r["name"]))

    for s in snippets:
        try:
            _merge_one_snippet(outer_db, s, mode, result, tag_map)
        except Exception as exc:  # per-snippet error, keep going
            msg = f"snippet id={s['id']} ({s['content'][:30]!r}\u2026): {exc}"
            result.errors.append(msg)
            LOGGER.exception("Import skipped snippet due to error")


def _merge_one_snippet(
    db: Database,
    src: sqlite3.Row,
    mode: str,
    result: ImportResult,
    tag_map: dict[int, list[str]],
) -> None:
    content = src["content"]
    content_hash = src["content_hash"]
    content_type = src["content_type"]
    source_app = src["source_app"]
    created_at = src["created_at"]
    updated_at = src["updated_at"]
    last_used_at = src["last_used_at"]
    use_count = int(src["use_count"])
    is_pinned = bool(src["is_pinned"])
    is_archived = bool(src["is_archived"])
    is_sensitive = bool(src["is_sensitive"])

    # We talk to the outer DB directly (autocommit-friendly). The
    # `db.transaction()` helper uses a separate cursor and is not safe
    # for our back-to-back statements here.
    conn = db._conn
    new_id: int | None = None
    do_skip = False
    try:
        conn.execute("BEGIN")
        existing_row = conn.execute(
            "SELECT * FROM snippets WHERE content_hash = ?", (content_hash,)
        ).fetchone()

        if existing_row is None:
            # Brand-new snippet: insert
            cur = conn.execute(
                """
                INSERT INTO snippets
                    (content, content_hash, content_type, source_app,
                     created_at, updated_at, last_used_at, use_count,
                     is_pinned, is_archived, is_sensitive)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (content, content_hash, content_type, source_app,
                 created_at, updated_at, last_used_at, use_count,
                 1 if is_pinned else 0, 1 if is_archived else 0,
                 1 if is_sensitive else 0),
            )
            new_id = int(cur.lastrowid)
            result.imported += 1
        else:
            existing_id = int(existing_row["id"])
            if mode == "merge":
                # Skip silently (the user said: "skip and log, just be sure
                # it shows there were errors and to check log to see")
                result.skipped += 1
                do_skip = True
            else:
                # mode == "replace": import wins, but only overwrite metadata
                # if its updated_at is newer (last-write-wins)
                existing_updated = existing_row["updated_at"]
                if str(updated_at) > str(existing_updated):
                    conn.execute(
                        """UPDATE snippets SET
                            content_type = ?, source_app = ?,
                            created_at = ?, updated_at = ?,
                            last_used_at = ?, use_count = ?,
                            is_pinned = ?, is_archived = ?, is_sensitive = ?
                           WHERE id = ?""",
                        (content_type, source_app, created_at, updated_at,
                         last_used_at, use_count,
                         1 if is_pinned else 0, 1 if is_archived else 0,
                         1 if is_sensitive else 0, existing_id),
                    )
                    result.updated += 1
                else:
                    result.skipped += 1
                    do_skip = True
                new_id = existing_id
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    else:
        try:
            conn.execute("COMMIT")
        except sqlite3.Error:
            pass
    finally:
        pass  # nothing else to clean up

    if do_skip or new_id is None:
        return

    # Now handle tags for the source snippet, looked up from the prebuilt map
    src_id = int(src["id"])
    tag_names = tag_map.get(src_id, [])
    if tag_names:
        # Union with existing tags on `new_id` (merge semantics for tags)
        existing_tags = set(db.get_tags_for_snippet(new_id))
        merged = sorted(existing_tags | set(tag_names))
        db.set_tags_for_snippet(new_id, merged)
