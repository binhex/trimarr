"""SQLite database layer for tracking processed MKV files."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

# Number of bytes to read from the start of a file for the partial hash.
# 64 KB is a good balance between speed and collision resistance for large video files.
_PARTIAL_HASH_BYTES = 65_536

_NEGATIVE_CACHE_TTL_DAYS = 7
"""How long a failed native-language lookup is trusted before retrying.

A failure row (``native_languages IS NULL``) is otherwise permanent: a
transient network error, or a run without an API key configured, would stop
``--keep-native-audio`` from ever resolving that file again.
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_files (
    file_path    TEXT    NOT NULL PRIMARY KEY,
    file_hash    TEXT    NOT NULL,
    profile_hash TEXT,
    bytes_saved  INTEGER NOT NULL DEFAULT 0,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# Migration: add bytes_saved to databases created before this column existed.
_MIGRATE_ADD_BYTES_SAVED = "ALTER TABLE processed_files ADD COLUMN bytes_saved INTEGER NOT NULL DEFAULT 0"
# Migration: add profile_hash to databases created before processing-profile tracking.
_MIGRATE_ADD_PROFILE_HASH = "ALTER TABLE processed_files ADD COLUMN profile_hash TEXT"

# Metadata cache table for native language lookups.
_METADATA_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_cache (
    file_path        TEXT PRIMARY KEY,
    file_hash        TEXT NOT NULL,
    native_languages TEXT,
    lookup_source    TEXT,
    lookup_error     TEXT,
    cached_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def fingerprint(path: Path) -> str:
    """Return a fast fingerprint string for *path*.

    Combines the file size, last-modified time (nanoseconds), and the SHA-256
    hash of the first :data:`_PARTIAL_HASH_BYTES` bytes.  Including ``mtime_ns``
    ensures that any in-place edit — even one that leaves the file size
    unchanged — produces a different fingerprint without requiring a full-file
    hash.

    Args:
        path: Path to the file to fingerprint.

    Returns:
        A string in the form ``"<size>:<mtime_ns>:<sha256_of_first_64k>"``.
        The ``size`` and ``mtime_ns`` components detect most edits without
        hashing; the digest covers the leading :data:`_PARTIAL_HASH_BYTES`
        so that same-size, same-mtime rewrites still differ.
    """
    stat = path.stat()
    size = stat.st_size
    mtime_ns = stat.st_mtime_ns
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        hasher.update(fh.read(_PARTIAL_HASH_BYTES))
    return f"{size}:{mtime_ns}:{hasher.hexdigest()}"


def _decode_cached_languages(json_langs: str | None) -> tuple[bool, list[str] | None]:
    """Decode a cached language list.

    Returns:
        ``(ok, languages)``.  ``ok`` is False when the stored value is present
        but not a JSON list of strings — a damaged row that must be treated as
        a cache miss rather than as a cached failure.  ``languages`` is None for
        an empty result.
    """
    if not json_langs:
        return True, None
    try:
        decoded = json.loads(json_langs)
    except (TypeError, ValueError):
        return False, None
    if not isinstance(decoded, list) or not all(isinstance(code, str) for code in decoded):
        return False, None
    return True, list(decoded) or None


def _cache_entry_expired(cached_at: str | None) -> bool:
    """Return True if a negative cache entry is older than the retry TTL.

    ``cached_at`` is the SQLite ``CURRENT_TIMESTAMP`` value (UTC, second
    resolution).  An unparseable or missing timestamp is treated as expired so
    the lookup is retried rather than trusted indefinitely.
    """
    if not cached_at:
        return True
    try:
        stored = datetime.strptime(cached_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return True
    return datetime.now(UTC) - stored > timedelta(days=_NEGATIVE_CACHE_TTL_DAYS)


class Database:
    """SQLite database for tracking which MKV files have been processed.

    Supports use as a context manager::

        with Database(path) as db:
            if not db.is_processed(file_path, profile_hash=profile_hash):
                ...
                db.mark_processed(file_path, profile_hash=profile_hash, bytes_saved=0)

    Args:
        db_path: Path to the SQLite database file.  Parent directories are
            created automatically if they do not exist.
        cache_writes_enabled: When False, :meth:`set_native_language_cache` is a
            no-op.  Used by ``--dry-run`` so lookups still *read* the cache but
            cannot seed it (including failure rows) for a later real run.
    """

    def __init__(self, db_path: str | Path, *, cache_writes_enabled: bool = True) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._cache_writes_enabled = cache_writes_enabled

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> Database:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the database connection and apply the schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL;")
        # Wait up to 5 s before raising "database is locked" so concurrent
        # trimarr processes do not immediately crash each other's DB writes.
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.executescript(_SCHEMA)
        # Migrate existing databases that pre-date the bytes_saved column.
        # Use PRAGMA rather than catching OperationalError so disk I/O and locking
        # errors are never silently swallowed.
        existing_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(processed_files)")}
        if "bytes_saved" not in existing_cols:
            self._conn.execute(_MIGRATE_ADD_BYTES_SAVED)
            self._conn.commit()
        if "profile_hash" not in existing_cols:
            self._conn.execute(_MIGRATE_ADD_PROFILE_HASH)
            self._conn.commit()
        self._conn.executescript(_METADATA_CACHE_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_processed(self, path: Path, *, profile_hash: str) -> bool:
        """Return *True* if *path* has already been processed with its current content
        **and** the same processing profile.

        A file is considered processed only when **both** its fingerprint **and** the
        processing profile (language codes, keep flags, metadata actions) match what was
        recorded.  If either has changed this returns *False*, ensuring that changing
        ``--language`` or other options causes files to be reprocessed.

        Args:
            path: Absolute or relative path to the MKV file.
            profile_hash: SHA-256 hex digest of the current processing configuration
                (see :func:`trimarr.runner._build_profile_hash`).

        Returns:
            ``True`` if the file has been processed with the same content and the same
            profile; ``False`` otherwise.
        """
        conn = self._require_connection()
        row = conn.execute(
            "SELECT file_hash, profile_hash FROM processed_files WHERE file_path = ?",
            (str(path),),
        ).fetchone()
        # Only read the file when it has been processed before — skip the
        # 64 KB partial hash for files that are not yet in the database.
        # This prevents the kernel page cache from ballooning to gigabytes
        # (Docker counts page cache in container memory) when scanning a
        # large library on the first run where every file is unprocessed.
        if row is None:
            return False
        current_hash = fingerprint(path)
        return bool(row[0] == current_hash and row[1] == profile_hash)

    def mark_processed(self, path: Path, *, profile_hash: str, bytes_saved: int = 0) -> None:
        """Record *path* as processed with its current fingerprint and processing profile.

        Performs an upsert: a file keeps a single row, and when its
        fingerprint is unchanged the recorded ``bytes_saved`` accumulates.

        Args:
            path: Absolute or relative path to the MKV file.
            profile_hash: SHA-256 hex digest of the processing configuration used.
            bytes_saved: Bytes removed from the file (original size minus new
                size).  Pass ``0`` when no remux was performed (e.g. the file
                already had the correct tracks).
        """
        conn = self._require_connection()
        current_hash = fingerprint(path)
        conn.execute(
            """
            INSERT INTO processed_files (file_path, file_hash, profile_hash, bytes_saved)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                file_hash    = excluded.file_hash,
                profile_hash = excluded.profile_hash,
                bytes_saved  = CASE
                    WHEN excluded.file_hash = processed_files.file_hash
                    THEN processed_files.bytes_saved + excluded.bytes_saved
                    ELSE excluded.bytes_saved
                END,
                processed_at = CURRENT_TIMESTAMP
            """,
            (str(path), current_hash, profile_hash, bytes_saved),
        )
        conn.commit()

    def total_bytes_saved(self) -> int:
        """Return the cumulative bytes saved across all processed files.

        Returns:
            Sum of ``bytes_saved`` for every row in the database.  Returns
            ``0`` when no files have been processed yet.
        """
        conn = self._require_connection()
        row = conn.execute("SELECT COALESCE(SUM(bytes_saved), 0) FROM processed_files").fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Native language cache
    # ------------------------------------------------------------------

    def get_native_language_cache(self, path: Path) -> tuple[list[str] | None, str | None, str | None] | None:
        """Return (native_languages, lookup_source, lookup_error) or None if not cached.

        Only returns a hit when the stored file_hash matches the current
        fingerprint. A mismatched hash means the file was replaced — caller
        should re-lookup.  Stored *failures* expire after
        :data:`_NEGATIVE_CACHE_TTL_DAYS` so a transient error or a run
        without an API key does not disable lookups permanently.
        """
        conn = self._require_connection()
        row = conn.execute(
            "SELECT file_hash, native_languages, lookup_source, lookup_error, cached_at "
            "FROM metadata_cache WHERE file_path = ?",
            (str(path),),
        ).fetchone()
        if row is None:
            return None
        stored_hash, json_langs, source, error, cached_at = row
        current_hash = fingerprint(path)
        if stored_hash != current_hash:
            return None  # file changed, cache is stale
        decoded_ok, langs = _decode_cached_languages(json_langs)
        if not decoded_ok:
            return None  # damaged row — re-lookup rather than trust it
        if langs is None and _cache_entry_expired(cached_at):
            return None  # failed lookup expired — allow a retry
        return langs, source, error

    def set_native_language_cache(
        self,
        path: Path,
        native_languages: list[str] | None,
        lookup_source: str | None,
        lookup_error: str | None,
    ) -> None:
        """Store native language lookup result for *path*.

        native_languages may be None when the lookup failed — this avoids
        retrying on every subsequent run for the same file.  Writes are
        suppressed entirely when the database was opened with
        ``cache_writes_enabled=False``.
        """
        if not self._cache_writes_enabled:
            return
        conn = self._require_connection()
        current_hash = fingerprint(path)
        conn.execute(
            """
            INSERT INTO metadata_cache (file_path, file_hash, native_languages,
                                        lookup_source, lookup_error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                file_hash        = excluded.file_hash,
                native_languages = excluded.native_languages,
                lookup_source    = excluded.lookup_source,
                lookup_error     = excluded.lookup_error,
                cached_at        = CURRENT_TIMESTAMP
            """,
            (
                str(path),
                current_hash,
                json.dumps(native_languages) if native_languages is not None else None,
                lookup_source,
                lookup_error,
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connection(self) -> sqlite3.Connection:
        """Return the active connection or raise *RuntimeError* if not open."""
        if self._conn is None:
            raise RuntimeError("Database is not open. Call open() or use as a context manager.")
        return self._conn
