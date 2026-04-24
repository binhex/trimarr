"""Unit tests for trimarr.main (run orchestration)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING
from unittest.mock import ANY, MagicMock, patch

import pytest
from loguru import logger as _real_loguru_logger

from trimarr.processor import MkvTrack
from trimarr.runner import _fmt_bytes, run

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


def _logged_messages(mock_fn: MagicMock) -> list[str]:
    """Extract the first positional argument from each call to *mock_fn*.

    Using ``str(call)`` to match substrings is fragile because it includes the
    function name and argument repr — a more precise extraction checks the
    actual argument value.
    """
    return [c.args[0] for c in mock_fn.call_args_list if c.args]


def _run_kwargs(tmp_path: Path, *, dry_run: bool, db_path: str) -> dict:
    return {
        "language": ["eng"],
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
        "strip_lower_channels": False,
        "strip_commentary": False,
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
            patch("trimarr.runner.probe_file", return_value=[]) as _,
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file") as mock_process,
            patch("trimarr.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=True, db_path=db_path))

        mock_process.assert_not_called()
        mock_mark.assert_not_called()

    def test_dry_run_colour_log_does_not_crash_with_angle_brackets(self, tmp_path: Path) -> None:
        """logger.opt(colors=True) must not raise ValueError for any dry-run log message.

        Uses the real loguru parser so the test fails if unescaped angle brackets
        are passed to any opt(colors=True) call — the mock logger used elsewhere
        would silently swallow the crash.
        """
        from io import StringIO

        sink = StringIO()
        handler_id = _real_loguru_logger.add(sink, format="{message}", colorize=False)
        try:
            mkv = tmp_path / "movie.mkv"
            mkv.write_bytes(b"fake mkv")
            db_path = str(tmp_path / "trimarr.db")

            # Path with angle brackets — illegal on Windows but valid on Linux;
            # loguru's colour parser would raise ValueError if they are not escaped.
            dangerous_path = "/media/TV<Series>/episode.mkv"
            fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), dangerous_path]

            with (
                patch("trimarr.runner.probe_file", return_value=[]),
                patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            ):
                # Must not raise — if it does, an angle bracket escaped to loguru's parser.
                run(**{**_run_kwargs(tmp_path, dry_run=True, db_path=db_path), "logger": _real_loguru_logger})
        finally:
            _real_loguru_logger.remove(handler_id)

    def test_dry_run_does_not_mark_processed_when_no_changes_needed(self, tmp_path: Path) -> None:
        """When a file needs no changes, dry run must not call mark_processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),  # no changes needed
            patch("trimarr.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=True, db_path=db_path))

        mock_mark.assert_not_called()

    def test_non_dry_run_marks_processed_when_no_changes_needed(self, tmp_path: Path) -> None:
        """Control: without dry_run, a no-change file IS marked processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),
            patch("trimarr.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        mock_mark.assert_called_once_with(mkv, profile_hash=ANY, bytes_saved=0)

    def test_non_dry_run_marks_processed_after_successful_processing(self, tmp_path: Path) -> None:
        """Control: without dry_run, a successfully processed file IS marked processed."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")

        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", return_value=None),
            patch("trimarr.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        mock_mark.assert_called_once_with(mkv, profile_hash=ANY, bytes_saved=0)

    def test_non_dry_run_records_bytes_saved_accurately(self, tmp_path: Path) -> None:
        """bytes_saved passed to mark_processed must reflect the actual file size reduction."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 2000)  # 2000-byte original
        db_path = str(tmp_path / "trimarr.db")
        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]

        def fake_process_file(*_args: object, file_path: object = None, **_kwargs: object) -> str | None:
            assert hasattr(file_path, "write_bytes")
            file_path.write_bytes(b"B" * 500)  # noqa: PGH003
            return None

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", side_effect=fake_process_file),
            patch("trimarr.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        mock_mark.assert_called_once_with(mkv, profile_hash=ANY, bytes_saved=1500)

    def test_bytes_saved_clamped_to_zero_when_output_larger(self, tmp_path: Path) -> None:
        """I1: bytes_saved must never be negative; if remux produces a larger file,
        bytes_saved should be clamped to 0 rather than storing a negative value."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"A" * 500)  # 500-byte original
        db_path = str(tmp_path / "trimarr.db")
        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]

        def fake_process_file(*_args: object, file_path: object = None, **_kwargs: object) -> str | None:
            assert hasattr(file_path, "write_bytes")
            file_path.write_bytes(b"B" * 2000)  # output larger than input  # noqa: PGH003
            return None

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", side_effect=fake_process_file),
            patch("trimarr.database.Database.mark_processed") as mock_mark,
        ):
            run(**_run_kwargs(tmp_path, dry_run=False, db_path=db_path))

        mock_mark.assert_called_once_with(mkv, profile_hash=ANY, bytes_saved=0)


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
            patch("trimarr.runner.probe_file", side_effect=KeyboardInterrupt),
            patch("trimarr.database.Database.mark_processed"),
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
            patch("trimarr.runner.probe_file", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        warning_msgs = _logged_messages(logger.warning)
        assert any("nterrupted" in msg for msg in warning_msgs)

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
            patch("trimarr.runner.probe_file", side_effect=probe_side_effect),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", return_value=None),
            pytest.raises(SystemExit),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        # The summary (counts line) must mention "Interrupted"
        info_msgs = _logged_messages(logger.info)
        assert any("nterrupted" in msg for msg in info_msgs)


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
        error_msgs = _logged_messages(logger.error)
        assert any("does not exist" in msg for msg in error_msgs)

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
        error_msgs = _logged_messages(logger.error)
        assert any("not a directory" in msg for msg in error_msgs)


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

        def is_processed_side_effect(path: object, *, profile_hash: str) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileNotFoundError(f"No such file: {path}")
            return False

        with (
            patch("trimarr.database.Database.is_processed", side_effect=is_processed_side_effect),
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),
            patch("trimarr.database.Database.mark_processed"),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        # Error must have been logged for the vanished file
        error_msgs = _logged_messages(logger.error)
        assert any("File system error" in msg for msg in error_msgs)
        # The second file must still have been attempted — loop continued past the OSError
        info_msgs = " ".join(_logged_messages(logger.info))
        assert "b.mkv" in info_msgs


# ---------------------------------------------------------------------------
# SQLite error handling (I3)
# ---------------------------------------------------------------------------


class TestSQLiteErrorHandling:
    """sqlite3.Error in the run() loop must not crash the entire batch."""

    def test_db_operational_error_on_mark_processed_logs_and_continues(self, tmp_path: Path) -> None:
        """If mark_processed raises sqlite3.OperationalError, the loop continues."""
        import sqlite3

        mkv_a = tmp_path / "a.mkv"
        mkv_b = tmp_path / "b.mkv"
        mkv_a.write_bytes(b"fake")
        mkv_b.write_bytes(b"fake")
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()

        tracks_no_change = [MkvTrack(id=0, type="video", language=None)]

        call_count = 0

        def probe_side_effect(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            return tracks_no_change

        def mark_side_effect(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        with (
            patch("trimarr.runner.probe_file", side_effect=probe_side_effect),
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),
            patch("trimarr.database.Database.mark_processed", side_effect=mark_side_effect),
        ):
            # Must not raise — should log the error and continue
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        # Both files were probed despite the DB error on the first
        assert call_count == 2


# ---------------------------------------------------------------------------
# _print_summary DB failure after KeyboardInterrupt (I5)
# ---------------------------------------------------------------------------


class TestPrintSummaryDBFailure:
    """_print_summary() DB failure must not crash after KeyboardInterrupt."""

    def test_db_failure_in_summary_does_not_propagate(self, tmp_path: Path) -> None:
        """If the DB can't be opened in _print_summary(), a warning is logged, not a crash."""
        import sqlite3

        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake")
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()

        # The main run() opens Database once (for the loop) then _print_summary opens
        # it again (for all-time savings).  The first call must succeed; the second
        # must raise so we can verify the error is swallowed gracefully.
        real_db_calls: list[int] = [0]

        def database_side_effect(path: str) -> MagicMock:
            real_db_calls[0] += 1
            if real_db_calls[0] == 1:
                # First call: main loop DB — succeed with a context-manager mock
                cm = MagicMock()
                cm.__enter__ = MagicMock(return_value=cm)
                cm.__exit__ = MagicMock(return_value=False)
                cm.is_processed.return_value = False
                return cm
            # Second call: summary DB — fail
            raise sqlite3.OperationalError("disk I/O error")

        with (
            patch("trimarr.runner.probe_file", side_effect=KeyboardInterrupt),
            patch("trimarr.runner.Database", side_effect=database_side_effect),
            contextlib.suppress(SystemExit),
        ):
            run(
                **{
                    **_run_kwargs(tmp_path, dry_run=False, db_path=db_path),
                    "logger": logger,
                }
            )

        # A warning must have been logged about the DB failure — not a crash
        warning_messages = [c.args[0] for c in logger.warning.call_args_list if c.args]
        assert any("database" in m.lower() or "savings" in m.lower() for m in warning_messages), (
            "Expected a warning about DB failure in summary, got: " + str(warning_messages)
        )


# ---------------------------------------------------------------------------
# strip_lower_channels wiring through run() (I4)
# ---------------------------------------------------------------------------


class TestStripLowerChannelsWiring:
    """Verify --strip-lower-channels is passed through run() to build_mkvmerge_command()."""

    def test_strip_lower_channels_true_reaches_build_mkvmerge_command(self, tmp_path: Path) -> None:
        """run(strip_lower_channels=True) must call build_mkvmerge_command with that flag."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake")
        db_path = str(tmp_path / "trimarr.db")

        dummy_track = MkvTrack(id=0, type="video", language=None)

        with (
            patch("trimarr.runner.probe_file", return_value=[dummy_track]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=None) as mock_build,
        ):
            run(
                **{
                    **_run_kwargs(tmp_path, dry_run=False, db_path=db_path),
                    "strip_lower_channels": True,
                }
            )

        assert mock_build.called
        _, kwargs = mock_build.call_args
        assert kwargs.get("strip_lower_channels") is True, (
            "strip_lower_channels=True was not forwarded to build_mkvmerge_command()"
        )


# ---------------------------------------------------------------------------
# strip_commentary wiring through run() (I5)
# ---------------------------------------------------------------------------


class TestStripCommentaryWiring:
    """Verify --strip-commentary is passed through run() to build_mkvmerge_command()."""

    def test_strip_commentary_true_reaches_build_mkvmerge_command(self, tmp_path: Path) -> None:
        """run(strip_commentary=True) must call build_mkvmerge_command with that flag."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake")
        db_path = str(tmp_path / "trimarr.db")

        dummy_track = MkvTrack(id=0, type="video", language=None)

        with (
            patch("trimarr.runner.probe_file", return_value=[dummy_track]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=None) as mock_build,
        ):
            run(
                **{
                    **_run_kwargs(tmp_path, dry_run=False, db_path=db_path),
                    "strip_commentary": True,
                }
            )

        assert mock_build.called
        _, kwargs = mock_build.call_args
        assert kwargs.get("strip_commentary") is True, (
            "strip_commentary=True was not forwarded to build_mkvmerge_command()"
        )


# ---------------------------------------------------------------------------


class TestFailureReport:
    """Verify failure report is printed after summary when files fail processing."""

    def test_process_file_failure_appears_in_failure_report(self, tmp_path: Path) -> None:
        """A process_file failure must be logged in the failure report at the end."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")
        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]
        logger = _make_logger()

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", return_value="mkvmerge failed (exit -6)"),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        all_msgs = _logged_messages(logger.warning) + _logged_messages(logger.error)
        assert any("movie.mkv" in m for m in all_msgs), "Failure report must mention the failed file"
        assert any("mkvmerge failed" in m for m in all_msgs), "Failure report must mention the reason"

    def test_probe_failure_appears_in_failure_report(self, tmp_path: Path) -> None:
        """A probe_file RuntimeError must be logged in the failure report at the end."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()

        with patch("trimarr.runner.probe_file", side_effect=RuntimeError("probe exploded")):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        all_msgs = _logged_messages(logger.warning) + _logged_messages(logger.error)
        assert any("movie.mkv" in m for m in all_msgs), "Failure report must mention the failed file"
        assert any("probe" in m.lower() or "exploded" in m for m in all_msgs), (
            "Failure report must mention the probe error"
        )

    def test_no_failure_report_when_all_succeed(self, tmp_path: Path) -> None:
        """No failure report section should be logged when everything succeeds."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")
        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]
        logger = _make_logger()

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", return_value=None),
            patch("trimarr.database.Database.mark_processed"),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        warn_msgs = _logged_messages(logger.warning)
        assert not any("Failed files" in m for m in warn_msgs), (
            "No failure report section should appear when all files succeeded"
        )

    def test_failure_report_comes_after_summary(self, tmp_path: Path) -> None:
        """Failure report log calls must occur after summary log calls."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")
        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]
        logger = _make_logger()
        call_order: list[str] = []

        def track_info(msg: str, *_a: object, **_kw: object) -> None:
            if "processed" in msg.lower() or "skipped" in msg.lower() or "summary" in msg.lower():
                call_order.append("summary")

        def track_warning(msg: str, *_a: object, **_kw: object) -> None:
            if "Failed files" in msg or "movie.mkv" in msg:
                call_order.append("failure_report")

        logger.info.side_effect = track_info
        logger.warning.side_effect = track_warning

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", return_value="mkvmerge failed (exit -6)"),
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        summary_idx = next((i for i, v in enumerate(call_order) if v == "summary"), None)
        failure_idx = next((i for i, v in enumerate(call_order) if v == "failure_report"), None)
        assert summary_idx is not None, "Summary must be logged"
        assert failure_idx is not None, "Failure report must be logged"
        assert summary_idx < failure_idx, "Summary must come before failure report"


# ---------------------------------------------------------------------------
# No MKV files in directory
# ---------------------------------------------------------------------------


class TestNoMkvFiles:
    """Verify that run() logs a warning and returns early when no .mkv files are found."""

    def test_empty_directory_logs_warning_and_returns(self, tmp_path: Path) -> None:
        """An existing but empty directory (no .mkv files) logs a warning and returns."""
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()
        run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})
        warning_msgs = _logged_messages(logger.warning)
        assert any("No .mkv files" in msg for msg in warning_msgs)


# ---------------------------------------------------------------------------
# Already-processed skip
# ---------------------------------------------------------------------------


class TestAlreadyProcessedSkip:
    """Verify that files already processed with the same profile are skipped."""

    def test_already_processed_file_is_skipped(self, tmp_path: Path) -> None:
        """Files returning True from is_processed must be skipped; probe_file never called."""
        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")
        logger = _make_logger()

        with (
            patch("trimarr.database.Database.is_processed", return_value=True),
            patch("trimarr.runner.probe_file") as mock_probe,
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        mock_probe.assert_not_called()
        debug_msgs = _logged_messages(logger.debug)
        assert any("Already processed" in msg for msg in debug_msgs)


# ---------------------------------------------------------------------------
# CorruptOutputError halt
# ---------------------------------------------------------------------------


class TestCorruptOutputError:
    """Verify that CorruptOutputError from process_file halts all processing."""

    def test_corrupt_output_error_logs_critical_and_exits_2(self, tmp_path: Path) -> None:
        """CorruptOutputError must log a critical diagnostic block and exit with code 2."""
        from trimarr.processor import CorruptOutputError

        mkv = tmp_path / "movie.mkv"
        mkv.write_bytes(b"fake mkv")
        db_path = str(tmp_path / "trimarr.db")
        fake_cmd = ["/usr/bin/mkvmerge", "-o", str(mkv), str(mkv)]
        logger = _make_logger()

        corrupt_tmp = tmp_path / "movie.mkv.trimarr_tmp"
        corrupt_tmp.write_bytes(b"corrupt output")
        err = CorruptOutputError(
            file_path=mkv,
            tmp_path=corrupt_tmp,
            probe_returncode=1,
            probe_output="Invalid MKV structure",
            output_size=corrupt_tmp.stat().st_size,
            input_size=mkv.stat().st_size,
            mkvmerge_path="/usr/bin/mkvmerge",
        )

        with (
            patch("trimarr.runner.probe_file", return_value=[]),
            patch("trimarr.runner.build_mkvmerge_command", return_value=fake_cmd),
            patch("trimarr.runner.process_file", side_effect=err),
            pytest.raises(SystemExit) as exc_info,
        ):
            run(**{**_run_kwargs(tmp_path, dry_run=False, db_path=db_path), "logger": logger})

        assert exc_info.value.code == 2
        assert logger.critical.called


# ---------------------------------------------------------------------------
# _fmt_bytes helper
# ---------------------------------------------------------------------------


class TestFmtBytes:
    """Tests for the _fmt_bytes byte-formatting helper."""

    def test_bytes(self) -> None:
        assert _fmt_bytes(512) == "512.00 B"

    def test_kilobytes(self) -> None:
        assert _fmt_bytes(1024) == "1.00 KB"

    def test_megabytes(self) -> None:
        assert _fmt_bytes(1024 * 1024) == "1.00 MB"

    def test_gigabytes(self) -> None:
        assert _fmt_bytes(1024 * 1024 * 1024) == "1.00 GB"

    def test_terabytes(self) -> None:
        assert "TB" in _fmt_bytes(1024**4)
