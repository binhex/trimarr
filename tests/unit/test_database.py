"""Unit tests for core.database."""

from __future__ import annotations

import hashlib
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
        import os

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
