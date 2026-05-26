"""Unit tests for Database metadata_cache methods.

Tests get_native_language_cache and set_native_language_cache using
a real temporary SQLite database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trimarr.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class TestMetadataCache:
    """Real-DB tests for native language cache round-trips, staleness, and failures."""

    def test_round_trip(self, tmp_path: Path) -> None:
        """set then get returns the same values."""
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, ["chi"], "imdbpie", None)
            result = db.get_native_language_cache(mkv)
            assert result == (["chi"], "imdbpie", None), f"Got {result}"

    def test_cache_miss(self, tmp_path: Path) -> None:
        """Non-existent path returns None."""
        db_path = tmp_path / "cache.db"
        with Database(db_path) as db:
            result = db.get_native_language_cache(tmp_path / "nonexistent.mkv")
            assert result is None

    def test_stale_cache_on_file_change(self, tmp_path: Path) -> None:
        """File content change invalidates the cache."""
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("original data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, ["chi"], "imdbpie", None)
        mkv.write_text("different data")
        with Database(db_path) as db:
            result = db.get_native_language_cache(mkv)
            assert result is None, f"Expected None (stale), got {result}"

    def test_cache_failure(self, tmp_path: Path) -> None:
        """Caching a failed lookup still round-trips correctly."""
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, None, None, "API error")
            result = db.get_native_language_cache(mkv)
            assert result == (None, None, "API error")

    def test_upsert_updates(self, tmp_path: Path) -> None:
        """Re-setting the same path overwrites the previous values."""
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, ["chi"], "imdbpie", None)
            db.set_native_language_cache(mkv, ["chi", "eng"], "imdbpie", None)
            result = db.get_native_language_cache(mkv)
            assert result == (["chi", "eng"], "imdbpie", None)
