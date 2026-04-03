"""Unit tests for core.database."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import TYPE_CHECKING

import pytest

from core.database import _PARTIAL_HASH_BYTES, Database, fingerprint

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# fingerprint()
# ---------------------------------------------------------------------------


class TestFingerprint:
    """Tests for the fingerprint() helper."""

    def test_returns_size_and_hash(self, tmp_path: Path) -> None:
        content = b"hello trimarr"
        f = tmp_path / "test.mkv"
        f.write_bytes(content)

        result = fingerprint(f)

        # Format: "<size>:<mtime_ns>:<sha256>"
        parts = result.split(":")
        assert len(parts) == 3
        assert parts[0] == str(len(content))
        expected_hash = hashlib.sha256(content).hexdigest()
        assert parts[2] == expected_hash

    def test_changes_when_content_changes(self, tmp_path: Path) -> None:
        f = tmp_path / "test.mkv"
        f.write_bytes(b"original content")
        fp1 = fingerprint(f)

        f.write_bytes(b"changed content!!")
        fp2 = fingerprint(f)

        assert fp1 != fp2

    def test_large_file_only_reads_partial_bytes(self, tmp_path: Path) -> None:
        """Fingerprint uses size + mtime_ns + first _PARTIAL_HASH_BYTES bytes.

        Two files with the same size, same mtime, and same first _PARTIAL_HASH_BYTES
        of content will collide.  This is a known and documented trade-off for speed;
        the mtime component catches most real-world in-place edits.
        """
        shared_prefix = b"A" * _PARTIAL_HASH_BYTES

        f1 = tmp_path / "big1.mkv"
        f1.write_bytes(shared_prefix + b"X" * 100)
        # Capture the exact nanosecond mtime of f1
        ns = f1.stat().st_mtime_ns

        f2 = tmp_path / "big2.mkv"
        f2.write_bytes(shared_prefix + b"Y" * 100)
        os.utime(f2, ns=(ns, ns))  # Force same nanosecond mtime

        # Same size + same mtime_ns + same prefix → same fingerprint
        assert fingerprint(f1) == fingerprint(f2)

        # Different first byte → different fingerprint
        f3 = tmp_path / "big3.mkv"
        f3.write_bytes(b"Z" + shared_prefix[1:] + b"X" * 100)
        os.utime(f3, ns=(ns, ns))
        assert fingerprint(f1) != fingerprint(f3)

        # Different size → different fingerprint
        f4 = tmp_path / "big4.mkv"
        f4.write_bytes(shared_prefix + b"X" * 200)
        os.utime(f4, ns=(ns, ns))
        assert fingerprint(f1) != fingerprint(f4)

        # Different mtime_ns → different fingerprint (even if content is identical)
        f5 = tmp_path / "big5.mkv"
        f5.write_bytes(shared_prefix + b"X" * 100)
        os.utime(f5, ns=(ns + 1, ns + 1))
        assert fingerprint(f1) != fingerprint(f5)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class TestDatabase:
    """Tests for the Database class."""

    def test_context_manager_opens_and_closes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with Database(db_path) as db:
            assert db._conn is not None
        assert db._conn is None

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "deep" / "nested" / "trimarr.db"
        with Database(db_path) as db:
            assert db._conn is not None
        assert db_path.exists()

    def test_is_processed_returns_false_for_new_file(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv data")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            assert db.is_processed(mkv) is False

    def test_mark_processed_then_is_processed_returns_true(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv data")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv)
            assert db.is_processed(mkv) is True

    def test_is_processed_returns_false_when_file_changes(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"original content")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv)
            # Simulate file being updated
            mkv.write_bytes(b"different content now")
            assert db.is_processed(mkv) is False

    def test_mark_processed_is_idempotent(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"content")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv)
            db.mark_processed(mkv)  # Should not raise
            assert db.is_processed(mkv) is True

    def test_mark_processed_updates_hash_when_file_changes(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"v1")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv)
            mkv.write_bytes(b"v2 updated content")
            db.mark_processed(mkv)
            assert db.is_processed(mkv) is True

    def test_requires_open_connection(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        mkv = tmp_path / "x.mkv"
        mkv.write_bytes(b"x")
        with pytest.raises(RuntimeError, match="not open"):
            db.is_processed(mkv)

    def test_persists_across_connections(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"persistent content")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv)

        # Re-open and verify the record is still there
        with Database(db_path) as db2:
            assert db2.is_processed(mkv) is True


# ---------------------------------------------------------------------------
# Bytes-saved tracking
# ---------------------------------------------------------------------------


class TestBytesTracking:
    """Tests for bytes_saved column and total_bytes_saved()."""

    def test_total_bytes_saved_returns_zero_when_empty(self, tmp_path: Path) -> None:
        with Database(tmp_path / "trimarr.db") as db:
            assert db.total_bytes_saved() == 0

    def test_mark_processed_stores_bytes_saved(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"content")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv, bytes_saved=1_000_000)
            conn = db._require_connection()
            row = conn.execute(
                "SELECT bytes_saved FROM processed_files WHERE file_path = ?",
                (str(mkv),),
            ).fetchone()
            assert row is not None
            assert row[0] == 1_000_000

    def test_total_bytes_saved_sums_all_files(self, tmp_path: Path) -> None:
        db_path = tmp_path / "trimarr.db"
        files = []
        for i in range(3):
            f = tmp_path / f"movie{i}.mkv"
            f.write_bytes(b"x" * (i + 1))
            files.append(f)

        with Database(db_path) as db:
            db.mark_processed(files[0], bytes_saved=500_000)
            db.mark_processed(files[1], bytes_saved=1_500_000)
            db.mark_processed(files[2], bytes_saved=2_000_000)
            assert db.total_bytes_saved() == 4_000_000

    def test_upsert_accumulates_bytes_saved(self, tmp_path: Path) -> None:
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"v1 content")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv, bytes_saved=100)
            # Simulate file being updated and re-processed — savings accumulate.
            mkv.write_bytes(b"v2 updated content")
            db.mark_processed(mkv, bytes_saved=200)
            assert db.total_bytes_saved() == 300  # 100 + 200 accumulated

    def test_no_change_preserves_prior_savings(self, tmp_path: Path) -> None:
        """Marking a file as processed with bytes_saved=0 must not erase prior savings."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"content")
        db_path = tmp_path / "trimarr.db"

        with Database(db_path) as db:
            db.mark_processed(mkv, bytes_saved=500_000)
            # Re-run: file unchanged, no remux needed → bytes_saved=0
            db.mark_processed(mkv, bytes_saved=0)
            assert db.total_bytes_saved() == 500_000  # History preserved

    def test_migration_adds_column_to_existing_db(self, tmp_path: Path) -> None:
        """Databases created before bytes_saved existed should be migrated."""
        db_path = tmp_path / "old.db"
        # Create a legacy schema without bytes_saved
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE processed_files ("
            "  file_path TEXT NOT NULL PRIMARY KEY,"
            "  file_hash TEXT NOT NULL,"
            "  processed_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.commit()
        conn.close()

        # Opening via Database should run the migration silently
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"data")
        with Database(db_path) as db:
            db.mark_processed(mkv, bytes_saved=42)
            assert db.total_bytes_saved() == 42
