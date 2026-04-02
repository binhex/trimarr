"""Unit tests for trimarr.main (run orchestration)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from trimarr.main import run

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    """Return a MagicMock that satisfies the loguru Logger interface used by run()."""
    logger = MagicMock()
    logger.opt.return_value = logger
    return logger


def _run_kwargs(tmp_path: Path, *, dry_run: bool, db_path: str) -> dict:
    return {
        "language": "eng",
        "edit_metadata_title": False,
        "delete_metadata_title": False,
        "keep_subtitles": False,
        "keep_audio": False,
        "media_path": str(tmp_path),
        "mkvmerge_path": "/usr/bin/mkvmerge",
        "database_path": db_path,
        "no_backup": True,
        "dry_run": dry_run,
        "logger": _make_logger(),
    }


# ---------------------------------------------------------------------------
# Dry-run database recording tests
# ---------------------------------------------------------------------------


class TestDryRunDoesNotRecordToDatabase:
    """Verify that dry_run=True never writes to the processed-files database."""

    def test_dry_run_does_not_mark_processed_when_changes_needed(self, tmp_path: Path) -> None:
        """When a file needs track removal, dry run must not call mark_processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        fake_cmd = ["/usr/bin/mkvmerge", "--output", str(mkv), str(mkv)]

        with (
            patch("trimarr.main.probe_file", return_value=[]) as _,
            patch("trimarr.main.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.main.process_file") as mock_process,
            patch("core.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=True, db_path=db_path))

        mock_process.assert_not_called()
        mock_mark.assert_not_called()

    def test_dry_run_does_not_mark_processed_when_no_changes_needed(self, tmp_path: Path) -> None:
        """When a file needs no changes, dry run must not call mark_processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        with (
            patch("trimarr.main.probe_file", return_value=[]),
            patch("trimarr.main.build_mkvmerge_command", return_value=None),  # no changes needed
            patch("core.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=True, db_path=db_path))

        mock_mark.assert_not_called()

    def test_non_dry_run_marks_processed_when_no_changes_needed(self, tmp_path: Path) -> None:
        """Control: without dry_run, a no-change file IS marked processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        with (
            patch("trimarr.main.probe_file", return_value=[]),
            patch("trimarr.main.build_mkvmerge_command", return_value=None),
            patch("core.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        mock_mark.assert_called_once_with(mkv)

    def test_non_dry_run_marks_processed_after_successful_processing(self, tmp_path: Path) -> None:
        """Control: without dry_run, a successfully processed file IS marked processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        fake_cmd = ["/usr/bin/mkvmerge", "--output", str(mkv), str(mkv)]

        with (
            patch("trimarr.main.probe_file", return_value=[]),
            patch("trimarr.main.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.main.process_file", return_value=True),
            patch("core.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        mock_mark.assert_called_once_with(mkv)
