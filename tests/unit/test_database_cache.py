"""Unit tests for Database metadata_cache methods.

Tests get_native_language_cache and set_native_language_cache using
a real temporary SQLite database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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


class TestNegativeCacheExpiry:
    """Failed lookups must expire so a transient error cannot disable retries forever."""

    def _age_entry(self, db: Database, path: Path, days: int) -> None:
        conn = db._require_connection()  # noqa: SLF001 — test needs to backdate the row
        conn.execute(
            "UPDATE metadata_cache SET cached_at = datetime('now', ?) WHERE file_path = ?",
            (f"-{days} days", str(path)),
        )
        conn.commit()

    def test_fresh_failure_is_a_cache_hit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, None, None, "no match")
            assert db.get_native_language_cache(mkv) == (None, None, "no match")

    def test_expired_failure_is_a_cache_miss(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, None, None, "no match")
            self._age_entry(db, mkv, 30)
            assert db.get_native_language_cache(mkv) is None

    def test_successful_result_never_expires(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, ["chi"], "imdbpie", None)
            self._age_entry(db, mkv, 365)
            assert db.get_native_language_cache(mkv) == (["chi"], "imdbpie", None)

    def test_unparseable_timestamp_expires(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, None, None, "no match")
            conn = db._require_connection()  # noqa: SLF001
            conn.execute("UPDATE metadata_cache SET cached_at = ? WHERE file_path = ?", ("garbage", str(mkv)))
            conn.commit()
            assert db.get_native_language_cache(mkv) is None


class TestCacheWriteSuppression:
    """--dry-run opens the database read-only for cache purposes."""

    def test_writes_disabled_keeps_reads_working(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, ["chi"], "imdbpie", None)

        with Database(db_path, cache_writes_enabled=False) as ro_db:
            assert ro_db.get_native_language_cache(mkv) == (["chi"], "imdbpie", None)
            ro_db.set_native_language_cache(mkv, ["jpn"], "tvdb", None)
            assert ro_db.get_native_language_cache(mkv) == (["chi"], "imdbpie", None)


class TestCorruptCacheRow:
    """A damaged metadata_cache row must be a miss, not a crash."""

    def test_corrupt_json_is_treated_as_a_miss(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, ["chi"], "imdbpie", None)
            conn = db._require_connection()  # noqa: SLF001
            conn.execute("UPDATE metadata_cache SET native_languages = ? WHERE file_path = ?", ("{not json", str(mkv)))
            conn.commit()
            assert db.get_native_language_cache(mkv) is None


class TestDamagedCacheRows:
    """A row that is not a JSON list of strings is a miss, not a cached failure."""

    @pytest.mark.parametrize("stored", ["[1, 2]", '["eng", 123]', '{"a": 1}', "null", '"eng"'])
    def test_non_string_payload_is_a_miss(self, tmp_path: Path, stored: str) -> None:
        db_path = tmp_path / "cache.db"
        mkv = tmp_path / "test.mkv"
        mkv.write_text("test data")
        with Database(db_path) as db:
            db.set_native_language_cache(mkv, ["chi"], "imdbpie", None)
            conn = db._require_connection()  # noqa: SLF001
            conn.execute("UPDATE metadata_cache SET native_languages = ? WHERE file_path = ?", (stored, str(mkv)))
            conn.commit()
            assert db.get_native_language_cache(mkv) is None
