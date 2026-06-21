"""SQLite storage for Snippy.

Single-file SQLite database at the OS-appropriate user data directory.

Phase 1 schema (raw SQL migrations, no ORM):

    snippets:
        id INTEGER PRIMARY KEY AUTOINCREMENT
        content TEXT NOT NULL
        content_hash TEXT NOT NULL UNIQUE   -- sha256 of content for dedup
        content_type TEXT NOT NULL          -- 'url' | 'email' | 'phone' | 'code' | 'path' | 'text'
        source_app TEXT                     -- best-effort: which app the copy came from
        created_at TEXT NOT NULL            -- ISO-8601 UTC
        updated_at TEXT NOT NULL
        last_used_at TEXT
        use_count INTEGER NOT NULL DEFAULT 0
        pin_order INTEGER NOT NULL DEFAULT 0
        is_pinned INTEGER NOT NULL DEFAULT 0
        is_archived INTEGER NOT NULL DEFAULT 0
        is_sensitive INTEGER NOT NULL DEFAULT 0

The module is designed so that all DB access is funneled through a
`Database` instance. Connection is opened in WAL mode for better
concurrency, and `row_factory` is set to `sqlite3.Row` for dict-like access.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from PySide6.QtCore import QObject, Signal
from platformdirs import user_data_dir

from snippy import __app_name__


LOGGER = logging.getLogger(__name__)

DB_FILENAME = "snippy.db"
SCHEMA_VERSION = 3


def _data_dir() -> Path:
    path = Path(user_data_dir(__app_name__, appauthor=__app_name__))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return _data_dir() / DB_FILENAME


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

# Each migration is a single SQL string. We track the current applied
# version in `schema_version` (single-row table). Migrations are applied
# in order; each is wrapped in a transaction.
_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS snippets (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        content         TEXT    NOT NULL,
        content_hash    TEXT    NOT NULL UNIQUE,
        content_type    TEXT    NOT NULL,
        source_app      TEXT,
        created_at      TEXT    NOT NULL,
        updated_at      TEXT    NOT NULL,
        last_used_at    TEXT,
        use_count       INTEGER NOT NULL DEFAULT 0,
        is_pinned       INTEGER NOT NULL DEFAULT 0,
        is_archived     INTEGER NOT NULL DEFAULT 0,
        is_sensitive    INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_snippets_created_at  ON snippets(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_snippets_pinned      ON snippets(is_pinned, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_snippets_archived    ON snippets(is_archived);

    CREATE TABLE IF NOT EXISTS tags (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name  TEXT    NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS snippet_tags (
        snippet_id  INTEGER NOT NULL,
        tag_id      INTEGER NOT NULL,
        PRIMARY KEY (snippet_id, tag_id),
        FOREIGN KEY (snippet_id) REFERENCES snippets(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id)     REFERENCES tags(id)     ON DELETE CASCADE
    );
    """,
    2: """
    CREATE INDEX IF NOT EXISTS idx_snippets_content_type ON snippets(content_type);
    """,
    3: """
    ALTER TABLE snippets ADD COLUMN pin_order INTEGER NOT NULL DEFAULT 0;
    UPDATE snippets SET pin_order = id WHERE pin_order = 0;
    CREATE INDEX IF NOT EXISTS idx_snippets_pin_order ON snippets(pin_order);
    """,
}


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT    NOT NULL
        )
        """
    )
    conn.commit()


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0) if row else 0


def _apply_migrations(conn: sqlite3.Connection) -> None:
    _ensure_migrations_table(conn)
    current = _current_version(conn)
    for version in sorted(_MIGRATIONS.keys()):
        if version <= current:
            continue
        LOGGER.info("Applying migration v%d", version)
        with closing(conn.cursor()) as cur:
            cur.executescript(_MIGRATIONS[version])
            cur.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()
    if _current_version(conn) != SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema is at v{_current_version(conn)} but code expects v{SCHEMA_VERSION}"
        )


# ---------------------------------------------------------------------------
# Data class for a snippet row
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Snippet:
    id: int
    content: str
    content_hash: str
    content_type: str
    source_app: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None
    use_count: int
    pin_order: int
    is_pinned: bool
    is_archived: bool
    is_sensitive: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Snippet":
        return cls(
            id=row["id"],
            content=row["content"],
            content_hash=row["content_hash"],
            content_type=row["content_type"],
            source_app=row["source_app"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
            use_count=row["use_count"],
            pin_order=row["pin_order"],
            is_pinned=bool(row["is_pinned"]),
            is_archived=bool(row["is_archived"]),
            is_sensitive=bool(row["is_sensitive"]),
        )


def hash_content(content: str) -> str:
    """Stable sha256 hex digest of content (used for dedup)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database(QObject):
    """A small wrapper around a sqlite3 connection with Snippy-specific helpers.

    Not thread-safe — designed to be created on the GUI thread and used
    from there. (Phase 1 has no worker threads; the voice API in Phase 4
    will use a separate Database instance per worker.)
    """

    snippets_changed = Signal()

    def __init__(self, path: Path | None = None, parent: QObject | None = None) -> None:
        QObject.__init__(self, parent)
        self._path = path or db_path()
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        _apply_migrations(self._conn)
        LOGGER.info("Database ready at %s", self._path)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._conn.cursor()) as cur:
            cur.execute("BEGIN")
            try:
                yield self._conn
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    # -- inserts / updates -----------------------------------------------

    def add_snippet(
        self,
        content: str,
        content_type: str,
        *,
        source_app: str | None = None,
        is_sensitive: bool = False,
    ) -> Snippet | None:
        """Insert a new snippet, or return the existing one if `content` is already stored.

        Returns `None` only on hard errors. If a duplicate exists, the existing
        row is returned and `last_used_at` is bumped.
        """
        content_hash = hash_content(content)
        now = datetime.now(timezone.utc).isoformat()

        existing = self.get_by_hash(content_hash)
        if existing is not None:
            self.bump_used(existing.id, now=now)
            self.snippets_changed.emit()
            return self.get_by_hash(content_hash)

        with self.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO snippets
                    (content, content_hash, content_type, source_app,
                     created_at, updated_at, last_used_at, use_count,
                     is_pinned, is_archived, is_sensitive)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?)
                """,
                (content, content_hash, content_type, source_app, now, now, now, int(is_sensitive)),
            )
            new_id = cur.lastrowid

        result = self.get_by_id(new_id)
        assert result is not None
        self.snippets_changed.emit()
        return result

    def add_image_snippet(self, image_path: str | Path) -> Snippet | None:
        """Store a captured screenshot path as a snippet with content_type='image'."""
        path = Path(image_path).resolve()
        return self.add_snippet(
            content=str(path),
            content_type="image",
            source_app="snippy-screen-capture",
        )

    def bump_used(self, snippet_id: int, *, now: str | None = None) -> None:
        """Increment use_count and update last_used_at for a snippet."""
        now = now or datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE snippets SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
            (now, snippet_id),
        )
        self._conn.commit()
        self.snippets_changed.emit()

    # -- WS5 / WS2 mutators (Phase 2) ----------------------------------

    def set_pinned(self, snippet_id: int, pinned: bool) -> None:
        """Set the `is_pinned` flag on a snippet.

        When pinning, assign the snippet a high pin_order so it appears at
        the bottom of the pinned list. When unpinning, reset its order to 0.
        """
        if pinned:
            max_order = self._conn.execute(
                "SELECT COALESCE(MAX(pin_order), 0) AS m FROM snippets WHERE is_pinned = 1"
            ).fetchone()["m"] or 0
            new_order = int(max_order) + 1
            self._conn.execute(
                "UPDATE snippets SET is_pinned = 1, pin_order = ? WHERE id = ?",
                (new_order, snippet_id),
            )
        else:
            self._conn.execute(
                "UPDATE snippets SET is_pinned = 0, pin_order = 0 WHERE id = ?",
                (snippet_id,),
            )
        self._conn.commit()
        self.snippets_changed.emit()

    def set_pin_order(self, snippet_id: int, pin_order: int) -> None:
        """Set the explicit pin_order for a pinned snippet."""
        self._conn.execute(
            "UPDATE snippets SET pin_order = ? WHERE id = ?",
            (pin_order, snippet_id),
        )
        self._conn.commit()
        self.snippets_changed.emit()

    def update_snippet_content(self, snippet_id: int, new_content: str) -> Snippet | None:
        """Replace a snippet's content. Re-hashes and returns the updated row.

        Note: re-using existing content is a no-op (returns the existing row).
        """
        import hashlib as _hashlib
        from datetime import datetime, timezone
        new_hash = _hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE snippets SET content = ?, content_hash = ?, updated_at = ? WHERE id = ?",
                (new_content, new_hash, now, snippet_id),
            )
        self.snippets_changed.emit()
        return self.get_by_id(snippet_id)

    def set_archived(self, snippet_id: int, archived: bool) -> None:
        """Set the `is_archived` flag on a snippet (hidden from default search)."""
        self._conn.execute(
            "UPDATE snippets SET is_archived = ? WHERE id = ?",
            (1 if archived else 0, snippet_id),
        )
        self._conn.commit()
        self.snippets_changed.emit()

    def delete_snippet(self, snippet_id: int) -> None:
        """Hard-delete a snippet (and its tag rows via CASCADE)."""
        self._conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
        self._conn.commit()
        self.snippets_changed.emit()
        LOGGER.info("Deleted snippet #%d", snippet_id)

    # -- WS6 tag CRUD (Phase 2) -----------------------------------------

    def add_tag(self, name: str) -> int:
        """Insert a new tag (or return the id of the existing one).

        Tag names are case-folded and trimmed; 'Work' and 'work' are the same tag.
        Returns the tag id.
        """
        norm = name.strip().lower()
        if not norm:
            raise ValueError("tag name must be non-empty")
        row = self._conn.execute("SELECT id FROM tags WHERE name = ?", (norm,)).fetchone()
        if row is not None:
            return int(row["id"])
        with self.transaction() as conn:
            cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (norm,))
            return int(cur.lastrowid)

    def list_tags(self) -> list[dict[str, int | str]]:
        """Return all tags with usage counts. Sorted by count desc, then name asc."""
        rows = self._conn.execute(
            """
            SELECT t.id AS id, t.name AS name, COUNT(st.snippet_id) AS count
            FROM tags t
            LEFT JOIN snippet_tags st ON st.tag_id = t.id
            GROUP BY t.id, t.name
            ORDER BY count DESC, t.name ASC
            """
        ).fetchall()
        return [{"id": int(r["id"]), "name": str(r["name"]), "count": int(r["count"])} for r in rows]

    def get_tags_for_snippet(self, snippet_id: int) -> list[str]:
        """Return the list of tag names attached to the given snippet (sorted)."""
        rows = self._conn.execute(
            """
            SELECT t.name AS name
            FROM tags t
            JOIN snippet_tags st ON st.tag_id = t.id
            WHERE st.snippet_id = ?
            ORDER BY t.name
            """,
            (snippet_id,),
        ).fetchall()
        return [str(r["name"]) for r in rows]

    def set_tags_for_snippet(self, snippet_id: int, tag_names: list[str]) -> None:
        """Replace the tag set for a snippet. Unknown tags are auto-created.

        No-op on an empty list (snippet becomes untagged).
        """
        norm_names = sorted({n.strip().lower() for n in tag_names if n.strip()})
        with self.transaction() as conn:
            # Clear existing
            conn.execute("DELETE FROM snippet_tags WHERE snippet_id = ?", (snippet_id,))
            for name in norm_names:
                # ensure tag exists, then link
                row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
                if row is None:
                    cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
                    tag_id = int(cur.lastrowid)
                else:
                    tag_id = int(row["id"])
                conn.execute(
                    "INSERT OR IGNORE INTO snippet_tags (snippet_id, tag_id) VALUES (?, ?)",
                    (snippet_id, tag_id),
                )
        self.snippets_changed.emit()

    def add_tag_to_snippet(self, snippet_id: int, tag_name: str) -> None:
        """Convenience: add a single tag without removing existing ones."""
        norm = tag_name.strip().lower()
        if not norm:
            return
        with self.transaction() as conn:
            row = conn.execute("SELECT id FROM tags WHERE name = ?", (norm,)).fetchone()
            if row is None:
                cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (norm,))
                tag_id = int(cur.lastrowid)
            else:
                tag_id = int(row["id"])
            conn.execute(
                "INSERT OR IGNORE INTO snippet_tags (snippet_id, tag_id) VALUES (?, ?)",
                (snippet_id, tag_id),
            )

    def tags_by_snippet(self) -> dict[int, set[str]]:
        """Return `{snippet_id: {tag_name, ...}}` for all snippets. WS6/WS7 wiring."""
        rows = self._conn.execute(
            """
            SELECT st.snippet_id AS sid, t.name AS name
            FROM snippet_tags st
            JOIN tags t ON t.id = st.tag_id
            """
        ).fetchall()
        out: dict[int, set[str]] = {}
        for r in rows:
            out.setdefault(int(r["sid"]), set()).add(str(r["name"]))
        return out

    # -- queries ---------------------------------------------------------

    def get_by_id(self, snippet_id: int) -> Snippet | None:
        row = self._conn.execute("SELECT * FROM snippets WHERE id = ?", (snippet_id,)).fetchone()
        return Snippet.from_row(row) if row else None

    def get_by_hash(self, content_hash: str) -> Snippet | None:
        row = self._conn.execute(
            "SELECT * FROM snippets WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return Snippet.from_row(row) if row else None

    def list_recent(self, limit: int = 50, *, include_archived: bool = False) -> list[Snippet]:
        where = "" if include_archived else "WHERE is_archived = 0"
        rows = self._conn.execute(
            f"SELECT * FROM snippets {where} ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Snippet.from_row(r) for r in rows]

    def list_all_for_search(self) -> list[Snippet]:
        """Return all non-archived snippets. Pinned rows come first, ordered
        by pin_order; unpinned rows follow by creation time."""
        rows = self._conn.execute(
            """
            SELECT * FROM snippets
            WHERE is_archived = 0
            ORDER BY is_pinned DESC, pin_order ASC, created_at DESC
            """
        ).fetchall()
        return [Snippet.from_row(r) for r in rows]

    def list_archived(self) -> list[Snippet]:
        """Return all archived snippets (v0.3.x). Used by the History
        window's 'Archived' view to let the user restore snippets.
        """
        rows = self._conn.execute(
            "SELECT * FROM snippets WHERE is_archived = 1 ORDER BY created_at DESC"
        ).fetchall()
        return [Snippet.from_row(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM snippets").fetchone()
        return int(row["c"])

    def stats(self) -> dict[str, int]:
        row = self._conn.execute(
            """
            SELECT
                COUNT(*)                                            AS total,
                SUM(CASE WHEN is_archived = 0 THEN 1 ELSE 0 END)    AS active,
                SUM(CASE WHEN is_pinned   = 1 THEN 1 ELSE 0 END)    AS pinned
            FROM snippets
            """
        ).fetchone()
        return {"total": int(row["total"] or 0), "active": int(row["active"] or 0), "pinned": int(row["pinned"] or 0)}


def _smoke_self_test() -> None:  # pragma: no cover — manual helper
    """Run a quick round-trip on a temp DB. Not invoked from tests."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "smoke.db"
        with Database(path) as db:
            s1 = db.add_snippet("hello world", "text")
            assert s1 is not None
            s2 = db.add_snippet("hello world", "text")  # duplicate
            assert s2 is not None
            assert s2.id == s1.id, "dedup should return the same id"
            assert db.count() == 1
            stats = db.stats()
            print(json.dumps({"stats": stats, "schema": SCHEMA_VERSION}))


if __name__ == "__main__":  # pragma: no cover
    _smoke_self_test()