"""Tests for `core.bundle` (Phase 3a)."""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path):
    from snippy.core.db import Database
    db_path = tmp_path / "t.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return Database(path=db_path)


def _seed(db, *snippets):
    for c in snippets:
        s = db.add_snippet(c, "text")
        assert s is not None
    return db


def _snip_path(tmp_path: Path, name: str = "test.snip") -> Path:
    return tmp_path / name


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_plain_text(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle, import_bundle
    db1 = _make_db(tmp_path / "a")
    _seed(db1, "hello world", "https://github.com/foo", "alpha bravo")
    out = _snip_path(tmp_path)
    manifest = export_bundle(out, db1)
    assert manifest.snippet_count == 3
    assert manifest.encrypted is False

    # Fresh DB, import
    db2 = _make_db(tmp_path / "b")
    result = import_bundle(out, db2, mode="merge")
    assert result.imported == 3
    assert result.skipped == 0
    assert result.errors == []
    assert db2.count() == 3


def test_round_trip_preserves_tags(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle, import_bundle
    db1 = _make_db(tmp_path / "a")
    s1 = db1.add_snippet("alpha", "text")
    s2 = db1.add_snippet("bravo", "text")
    db1.set_tags_for_snippet(s1.id, ["work", "important"])
    db1.set_tags_for_snippet(s2.id, ["personal"])

    out = _snip_path(tmp_path)
    export_bundle(out, db1)

    db2 = _make_db(tmp_path / "b")
    import_bundle(out, db2)
    assert set(db2.get_tags_for_snippet(1)) == {"work", "important"}
    assert db2.get_tags_for_snippet(2) == ["personal"]


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


def test_encryption_round_trip_with_passphrase(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle, import_bundle
    db1 = _make_db(tmp_path / "a")
    _seed(db1, "secret thing", "another secret")
    out = _snip_path(tmp_path)

    manifest = export_bundle(out, db1, passphrase="correct horse battery staple")
    assert manifest.encrypted is True
    assert manifest.encryption == "fernet-scrypt-2026"
    assert manifest.salt_b64

    # The zip must contain db.sqlite.enc (NOT db.sqlite)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "db.sqlite.enc" in names
        assert "db.sqlite" not in names

    db2 = _make_db(tmp_path / "b")
    result = import_bundle(out, db2, passphrase="correct horse battery staple")
    assert result.imported == 2
    assert result.errors == []
    assert db2.count() == 2


def test_wrong_passphrase_raises_decrypt_error(tmp_path: Path) -> None:
    from snippy.core.bundle import (
        BundleDecryptError,
        export_bundle,
        import_bundle,
    )
    db1 = _make_db(tmp_path / "a")
    _seed(db1, "secret")
    out = _snip_path(tmp_path)
    export_bundle(out, db1, passphrase="right one")

    db2 = _make_db(tmp_path / "b")
    result = import_bundle(out, db2, passphrase="wrong one")
    # The result is returned with the error recorded (host UI shows it)
    assert result.errors, "expected an error for wrong passphrase"
    assert db2.count() == 0, "no snippets should have been imported"


def test_encrypted_bundle_without_passphrase_returns_error(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle, import_bundle
    db1 = _make_db(tmp_path / "a")
    _seed(db1, "x")
    out = _snip_path(tmp_path)
    export_bundle(out, db1, passphrase="hunter2")
    db2 = _make_db(tmp_path / "b")
    result = import_bundle(out, db2)  # no passphrase
    assert result.errors
    assert "passphrase" in result.errors[0].lower()


# ---------------------------------------------------------------------------
# Merge semantics
# ---------------------------------------------------------------------------


def test_merge_skips_existing_by_hash(tmp_path: Path) -> None:
    """The default merge: same content_hash \u2192 skip silently."""
    from snippy.core.bundle import export_bundle, import_bundle
    db1 = _make_db(tmp_path / "a")
    _seed(db1, "shared content", "import-only-1", "import-only-2")
    out = _snip_path(tmp_path)
    export_bundle(out, db1)

    # db2 has the same "shared content" already
    db2 = _make_db(tmp_path / "b")
    existing = db2.add_snippet("shared content", "text")
    _seed(db2, "dest-only-1")

    result = import_bundle(out, db2, mode="merge")
    assert result.imported == 2  # the two import-only rows
    assert result.skipped == 1   # the shared one
    assert result.errors == []
    # Final state: 2 (existing: shared + dest-only) + 2 (imported) = 4
    assert db2.count() == 4


def test_replace_overwrites_existing_with_newer_updated_at(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle, import_bundle
    db1 = _make_db(tmp_path / "a")
    s = db1.add_snippet("dup", "text")
    # Force a NEWER updated_at by direct UPDATE
    db1._conn.execute(
        "UPDATE snippets SET updated_at = '2099-01-01T00:00:00+00:00', use_count = 99 WHERE id = ?",
        (s.id,),
    )
    db1._conn.commit()
    out = _snip_path(tmp_path)
    export_bundle(out, db1)

    db2 = _make_db(tmp_path / "b")
    s2 = db2.add_snippet("dup", "text")
    assert s2.use_count == 0
    result = import_bundle(out, db2, mode="replace")
    assert result.updated == 1
    refreshed = db2.get_by_id(s2.id)
    assert refreshed is not None
    assert refreshed.use_count == 99


def test_replace_skips_when_import_is_older(tmp_path: Path) -> None:
    from snippy.core.bundle import export_bundle, import_bundle
    db1 = _make_db(tmp_path / "a")
    s = db1.add_snippet("dup", "text")
    # Force an OLDER updated_at on the import
    db1._conn.execute(
        "UPDATE snippets SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (s.id,),
    )
    db1._conn.commit()
    out = _snip_path(tmp_path)
    export_bundle(out, db1)

    db2 = _make_db(tmp_path / "b")
    db2.add_snippet("dup", "text")
    result = import_bundle(out, db2, mode="replace")
    assert result.skipped == 1
    assert result.updated == 0


# ---------------------------------------------------------------------------
# Format errors
# ---------------------------------------------------------------------------


def test_missing_file_returns_error(tmp_path: Path) -> None:
    from snippy.core.bundle import BundleError, import_bundle
    db1 = _make_db(tmp_path / "a")
    with pytest.raises(BundleError):
        import_bundle(tmp_path / "nope.snip", db1)


def test_corrupt_zip_returns_error(tmp_path: Path) -> None:
    from snippy.core.bundle import import_bundle
    out = _snip_path(tmp_path)
    out.write_bytes(b"this is not a zip file at all")
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    assert result.errors
    assert db1.count() == 0


def test_zip_missing_manifest_returns_error(tmp_path: Path) -> None:
    from snippy.core.bundle import import_bundle
    out = _snip_path(tmp_path)
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("db.sqlite", b"junk")
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    assert result.errors
    assert "manifest" in result.errors[0].lower()


def test_manifest_json_validation(tmp_path: Path) -> None:
    """A manifest missing required fields raises BundleFormatError."""
    from snippy.core.bundle import BundleFormatError, Manifest
    with pytest.raises(BundleFormatError):
        Manifest.from_json('{"app_version": "x"}')


def test_inner_db_must_be_sqlite(tmp_path: Path) -> None:
    """A bundle whose 'db.sqlite' member is just text fails validation."""
    from snippy.core.bundle import import_bundle
    out = _snip_path(tmp_path)
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("manifest.json", '{"app_version":"x","exported_at":"x","schema_version":1,"snippet_count":0,"tag_count":0,"encrypted":false}')
        zf.writestr("db.sqlite", b"definitely not a sqlite file")
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    assert result.errors


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def test_manifest_round_trip_json() -> None:
    from snippy.core.bundle import Manifest
    m = Manifest(
        app_version="0.2.0b1",
        exported_at="2026-06-13T18:32:11+00:00",
        schema_version=1,
        snippet_count=5,
        tag_count=2,
        encrypted=False,
    )
    s = m.to_json()
    m2 = Manifest.from_json(s)
    assert m2 == m


# ---------------------------------------------------------------------------
# Manifest version validation (v0.3.0)
# ---------------------------------------------------------------------------


def _write_bundle_with_manifest(
    out: Path,
    *,
    app_version: str = "0.3.0",
    schema_version: int = 1,
) -> None:
    """Write a minimal valid bundle whose only "data" is a valid empty DB.

    Used by the version-validation tests so we can pretend the bundle was
    written by some other Snippy version. The inner DB is a real Snippy DB
    — we reuse the same export routine via `export_bundle` then patch the
    manifest in place.
    """
    from snippy.core.bundle import (
        DB_PLAIN_NAME,
        MANIFEST_NAME,
        export_bundle,
    )
    from snippy.core.db import Database
    # Write a real bundle first, then replace the manifest member
    db_path = out.parent / "tmp_inner.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path=db_path)
    db.add_snippet("placeholder", "text")
    export_bundle(out, db)
    # Now rewrite the manifest
    with zipfile.ZipFile(out, "r") as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members[MANIFEST_NAME] = (
        f'{{"app_version":"{app_version}",'
        f'"exported_at":"2026-06-13T18:00:00+00:00",'
        f'"schema_version":{schema_version},'
        f'"snippet_count":1,"tag_count":0,"encrypted":false}}'
    ).encode("utf-8")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    # `DB_PLAIN_NAME` is just imported so the test author can see what's
    # expected; the export routine always writes it.
    _ = DB_PLAIN_NAME


def test_rejects_future_schema_version(tmp_path: Path) -> None:
    """A bundle made by Snippy v9999.0 (schema v99) must be rejected with a
    user-friendly BundleFormatError — not a sqlite error or a crash."""
    from snippy.core.bundle import import_bundle
    out = _snip_path(tmp_path)
    _write_bundle_with_manifest(out, schema_version=99, app_version="9999.0")
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    assert result.imported == 0
    assert result.errors
    assert "newer" in result.errors[0].lower()
    assert "v99" in result.errors[0]


def test_rejects_legacy_schema_version(tmp_path: Path) -> None:
    """A bundle with schema_version < SUPPORTED must be rejected too."""
    from snippy.core.bundle import import_bundle
    out = _snip_path(tmp_path)
    _write_bundle_with_manifest(out, schema_version=0, app_version="0.1.0")
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    assert result.imported == 0
    assert result.errors
    assert "older" in result.errors[0].lower() or "schema" in result.errors[0].lower()


def test_rejects_legacy_app_version(tmp_path: Path) -> None:
    """A bundle with the right schema but a very old app version is
    rejected. (We'll never see this in practice for v0.3.0 since the only
    schema in the wild is 1, but the check is cheap and future-proofs us.)"""
    from snippy.core.bundle import (
        SUPPORTED_MIN_APP_VERSION,
        SUPPORTED_SCHEMA_VERSION,
        import_bundle,
    )
    # If SUPPORTED_MIN_APP_VERSION is ever changed, this test becomes
    # trivial; we want it to assert the *current* policy, so re-derive.
    assert SUPPORTED_SCHEMA_VERSION >= 1
    out = _snip_path(tmp_path)
    _write_bundle_with_manifest(
        out, schema_version=SUPPORTED_SCHEMA_VERSION, app_version="0.1.0",
    )
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    assert result.imported == 0
    assert result.errors
    assert "older than the minimum" in result.errors[0].lower() or "0.1.0" in result.errors[0]
    # And it must mention the floor
    assert SUPPORTED_MIN_APP_VERSION in result.errors[0]


def test_accepts_current_version(tmp_path: Path) -> None:
    """A bundle with the current app_version + schema_version imports cleanly."""
    from snippy import __version__
    from snippy.core.bundle import SUPPORTED_SCHEMA_VERSION, import_bundle
    out = _snip_path(tmp_path)
    _write_bundle_with_manifest(out, app_version=__version__, schema_version=SUPPORTED_SCHEMA_VERSION)
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    assert result.imported == 1
    assert result.errors == []


def test_legacy_message_is_user_friendly(tmp_path: Path) -> None:
    """The error message must guide the user to the next action."""
    from snippy.core.bundle import import_bundle
    out = _snip_path(tmp_path)
    _write_bundle_with_manifest(out, schema_version=99, app_version="9999.0")
    db1 = _make_db(tmp_path / "a")
    result = import_bundle(out, db1)
    msg = " ".join(result.errors).lower()
    # The user needs to know WHAT to do — at minimum, "update"
    assert "update" in msg or "newer" in msg
