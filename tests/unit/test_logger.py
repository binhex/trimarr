"""Unit tests for trimarr.logger."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from trimarr.logger import create_logger


class TestCreateLogger:
    """create_logger configures loguru sinks correctly (mocked to avoid global state)."""

    def test_with_log_path_includes_file_sink(self) -> None:
        """When log_path is set, a file sink is added with rotation/retention."""
        with patch("trimarr.logger._logger.remove") as mock_remove, patch("trimarr.logger._logger.add") as mock_add:
            create_logger(
                log_format="{message}",
                log_level="DEBUG",
                log_path="/tmp/test.log",
            )

        mock_remove.assert_called_once()
        # Verify two sinks were added: console (1st) and file (2nd)
        assert mock_add.call_count == 2
        # Second call is the file sink
        _, file_kwargs = mock_add.call_args_list[1]
        assert file_kwargs["sink"] == "/tmp/test.log"
        assert file_kwargs["level"] == "DEBUG"
        assert file_kwargs["rotation"] == "10 MB"
        assert file_kwargs["retention"] == 3

    def test_bare_filename_skips_makedirs(self, tmp_path: Path) -> None:
        """When log_path is a bare filename (no dir), os.makedirs is not called."""
        with (
            patch("trimarr.logger._logger.remove"),
            patch("trimarr.logger._logger.add"),
            patch("trimarr.logger.os.makedirs") as mock_makedirs,
        ):
            create_logger(
                log_format="{message}",
                log_level="INFO",
                log_path="trimarr.log",
            )

        mock_makedirs.assert_not_called()

    def test_log_path_with_dir_calls_makedirs(self) -> None:
        """When log_path has a directory component, os.makedirs is called."""
        with (
            patch("trimarr.logger._logger.remove"),
            patch("trimarr.logger._logger.add"),
            patch("trimarr.logger.os.makedirs") as mock_makedirs,
        ):
            create_logger(
                log_format="{message}",
                log_level="INFO",
                log_path="/var/log/trimarr/trimarr.log",
            )

        mock_makedirs.assert_called_once_with("/var/log/trimarr", exist_ok=True)

    def test_no_log_path_skips_file_sink(self) -> None:
        """When log_path is None, only one sink (console) is added."""
        with patch("trimarr.logger._logger.remove") as mock_remove, patch("trimarr.logger._logger.add") as mock_add:
            create_logger(
                log_format="{message}",
                log_level="INFO",
                log_path=None,
            )

        mock_remove.assert_called_once()
        mock_add.assert_called_once()  # Only console sink

    def test_console_sink_uses_lambda(self) -> None:
        """Console sink uses a lambda that calls print()."""
        with (
            patch("trimarr.logger._logger.remove"),
            patch("trimarr.logger._logger.add") as mock_add,
        ):
            create_logger(
                log_format="{message}",
                log_level="INFO",
                log_path=None,
            )

        console_kwargs = mock_add.call_args[1]
        assert console_kwargs["level"] == "INFO"
        assert console_kwargs["colorize"] is True
        # Verify the sink callable is a lambda that calls print
        import io
        import sys

        capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = capture
        try:
            console_kwargs["sink"]("test output")
        finally:
            sys.stdout = old_stdout
        assert capture.getvalue() == "test output"


class TestStdlibLoggingInterception:
    """Stdlib ``logging`` records must reach the configured loguru sinks."""

    def test_stdlib_record_reaches_loguru_sink(self) -> None:
        import io
        import logging as std_logging

        from trimarr.logger import create_logger

        logger = create_logger(log_format="{message}", log_level="DEBUG")
        capture = io.StringIO()
        sink_id = logger.add(capture, format="{message}", level="DEBUG")
        try:
            std_logging.getLogger("trimarr._nfo_parser").info("intercepted-record %s", "ok")
        finally:
            logger.remove(sink_id)
            logger.remove()

        assert "intercepted-record ok" in capture.getvalue()

    def test_stdlib_warning_reaches_loguru_sink(self) -> None:
        import io
        import logging as std_logging

        from trimarr.logger import create_logger

        logger = create_logger(log_format="{message}", log_level="DEBUG")
        capture = io.StringIO()
        sink_id = logger.add(capture, format="{message}", level="DEBUG")
        try:
            std_logging.getLogger("trimarr.native_language").warning("imdbpie unavailable")
        finally:
            logger.remove(sink_id)
            logger.remove()

        assert "imdbpie unavailable" in capture.getvalue()


class TestThirdPartyLoggingIsolation:
    """Only trimarr's own stdlib loggers are bridged into loguru."""

    def test_third_party_info_does_not_reach_sink(self) -> None:
        import io
        import logging as std_logging

        from trimarr.logger import create_logger

        logger = create_logger(log_format="{message}", log_level="DEBUG")
        capture = io.StringIO()
        sink_id = logger.add(capture, format="{message}", level="DEBUG")
        try:
            std_logging.getLogger("imdbpie").info("called search_for_title noise")
        finally:
            logger.remove(sink_id)
            logger.remove()

        assert capture.getvalue() == ""


class TestRecordSanitisation:
    """Every log record is sanitised centrally, so no call site can forge lines."""

    def test_message_control_characters_are_neutralised(self) -> None:
        import io

        from trimarr.logger import create_logger

        logger = create_logger(log_format="{message}", log_level="DEBUG")
        capture = io.StringIO()
        sink_id = logger.add(capture, format="{message}", level="DEBUG")
        try:
            logger.info("Processed: evil.mkv\n2026-01-01 | SUCCESS | forged entry")
            logger.warning("esc\x1b[31mred")
        finally:
            logger.remove(sink_id)
            logger.remove()

        output = capture.getvalue()
        assert "forged entry" in output
        assert "\n2026-01-01 | SUCCESS" not in output, "a newline must not create a new record"
        assert "\x1b" not in output

    def test_stdlib_bridge_records_are_sanitised_too(self) -> None:
        import io
        import logging as std_logging

        from trimarr.logger import create_logger

        logger = create_logger(log_format="{message}", log_level="DEBUG")
        capture = io.StringIO()
        sink_id = logger.add(capture, format="{message}", level="DEBUG")
        try:
            std_logging.getLogger("trimarr.native_language").info("name\nforged")
        finally:
            logger.remove(sink_id)
            logger.remove()

        assert "\nforged" not in capture.getvalue()


class TestExceptionFolding:
    """Tracebacks are folded into the sanitised message, never rendered raw."""

    def test_traceback_is_folded_and_sanitised(self) -> None:
        import io

        from trimarr.logger import create_logger

        logger = create_logger(log_format="{message}", log_level="DEBUG")
        capture = io.StringIO()
        sink_id = logger.add(capture, format="{message}", level="DEBUG")
        try:
            try:
                raise RuntimeError("boom\nFORGED | ERROR | injected")
            except RuntimeError as exc:
                logger.opt(exception=exc).error("processing failed")
        finally:
            logger.remove(sink_id)
            logger.remove()

        output = capture.getvalue()
        assert "processing failed" in output
        assert "RuntimeError" in output
        assert "boom" in output
        assert "\nFORGED" not in output, "the traceback must be sanitised into the record"
