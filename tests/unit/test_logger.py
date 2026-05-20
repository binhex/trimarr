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
