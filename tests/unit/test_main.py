"""Unit tests for trimarr.main (run orchestration)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

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

        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]

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

        mock_mark.assert_called_once_with(mkv, bytes_saved=0)

    def test_non_dry_run_marks_processed_after_successful_processing(self, tmp_path: Path) -> None:
        """Control: without dry_run, a successfully processed file IS marked processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]

        with (
            patch("trimarr.main.probe_file", return_value=[]),
            patch("trimarr.main.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.main.process_file", return_value=True),
            patch("core.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        mock_mark.assert_called_once_with(mkv, bytes_saved=0)


# ---------------------------------------------------------------------------
# KeyboardInterrupt handling
# ---------------------------------------------------------------------------


class TestKeyboardInterruptHandling:
    """Verify that Ctrl+C produces a graceful partial summary and exits 130."""

    def test_interrupt_exits_130(self, tmp_path: Path) -> None:
        """KeyboardInterrupt must cause sys.exit(130) — not exit 0 or re-raise KI."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        with (
            patch("trimarr.main.probe_file", side_effect=KeyboardInterrupt),
            patch("core.database.Database.mark_processed"),
            pytest.raises(SystemExit) as exc_info,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        assert exc_info.value.code == 130

    def test_interrupt_logs_warning(self, tmp_path: Path) -> None:
        """A warning must be logged when the run is interrupted."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()

        with (
            patch("trimarr.main.probe_file", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        warning_calls = [str(c) for c in logger.warning.call_args_list]
        assert any("nterrupted" in msg for msg in warning_calls)

    def test_interrupt_after_partial_processing_shows_summary(self, tmp_path: Path) -> None:
        """Files processed before the interrupt should appear in the session summary."""
        mkv1 = tmp_path / "a.mkv"
        mkv2 = tmp_path / "b.mkv"
        mkv1.write_bytes(b"fake mkv 1")
        mkv2.write_bytes(b"fake mkv 2")
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()
        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv1), str(mkv1)]

        call_count = 0

        def probe_side_effect(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise KeyboardInterrupt
            return []

        with (
            patch("trimarr.main.probe_file", side_effect=probe_side_effect),
            patch("trimarr.main.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.main.process_file", return_value=True),
            pytest.raises(SystemExit),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        # The summary (counts line) must mention "Interrupted"
        info_calls = [str(c) for c in logger.info.call_args_list]
        assert any("nterrupted" in msg for msg in info_calls)

    def test_interrupt_in_dry_run_exits_130(self, tmp_path: Path) -> None:
        """Ctrl+C in dry-run mode must also exit 130."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        with (
            patch("trimarr.main.probe_file", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            run(**_run_kwargs(tmp_path, dry_run=True, db_path=db_path))

        assert exc_info.value.code == 130


# ---------------------------------------------------------------------------
# Media path validation
# ---------------------------------------------------------------------------


class TestMediaPathValidation:
    """Verify that invalid media_path values produce clear error messages."""

    def test_nonexistent_path_logs_error_and_returns(self, tmp_path: Path) -> None:
        """A path that does not exist at all should log an error without crashing."""
        missing = str(tmp_path / "does_not_exist")
        logger = _make_logger()
        kwargs = {
            **_run_kwargs(tmp_path, dry_run=False, db_path=str(tmp_path / "trimarr.db")),
            "media_path": missing,
            "logger": logger,
        }
        run(**kwargs)
        error_calls = [str(c) for c in logger.error.call_args_list]
        assert any("does not exist" in msg for msg in error_calls)

    def test_file_path_given_instead_of_directory_logs_error(self, tmp_path: Path) -> None:
        """Passing a file path where a directory is expected should log an error."""
        file_path = tmp_path / "movie.mkv"
        file_path.write_bytes(b"data")
        logger = _make_logger()
        kwargs = {
            **_run_kwargs(tmp_path, dry_run=False, db_path=str(tmp_path / "trimarr.db")),
            "media_path": str(file_path),
            "logger": logger,
        }
        run(**kwargs)
        error_calls = [str(c) for c in logger.error.call_args_list]
        assert any("not a directory" in msg for msg in error_calls)


# ---------------------------------------------------------------------------
# Per-file OSError resilience
# ---------------------------------------------------------------------------


class TestPerFileOsErrorResilience:
    """Verify that a file vanishing mid-scan never aborts the entire batch."""

    def test_file_vanishing_during_is_processed_increments_failed_continues(self, tmp_path: Path) -> None:
        """FileNotFoundError from is_processed() must be caught; remaining files processed."""
        mkv1 = tmp_path / "a.mkv"
        mkv2 = tmp_path / "b.mkv"
        mkv1.write_bytes(b"fake mkv 1")
        mkv2.write_bytes(b"fake mkv 2")
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()

        call_count = 0

        def is_processed_side_effect(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileNotFoundError(f"No such file: {path}")
            return False

        with (
            patch("core.database.Database.is_processed", side_effect=is_processed_side_effect),
            patch("trimarr.main.probe_file", return_value=[]),
            patch("trimarr.main.build_mkvmerge_command", return_value=None),
            patch("core.database.Database.mark_processed"),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        # Error must have been logged for the vanished file
        error_calls = [str(c) for c in logger.error.call_args_list]
        assert any("File system error" in msg for msg in error_calls)
        # The second file must still have been attempted — loop continued past the OSError
        info_calls = " ".join(str(c) for c in logger.info.call_args_list)
        assert "b.mkv" in info_calls
