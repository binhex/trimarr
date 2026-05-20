"""Unit tests for trimarr.hooks."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from trimarr.hooks import _run_hook


class TestRunHook:
    """_run_hook substitutes variables and executes via shell."""

    def test_substitutes_leaf_and_dir(self) -> None:
        """{leaf} and {dir} are replaced in the command template."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                "echo {leaf} {dir}",
                "file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        mock_run.assert_called_once_with(
            "echo file.mkv /some/dir",
            shell=True,
            timeout=300,
            capture_output=True,
            text=True,
        )

    def test_template_without_variables_still_works(self) -> None:
        """A template with no variables still works."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                "echo hello",
                "file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        mock_run.assert_called_once_with(
            "echo hello",
            shell=True,
            timeout=300,
            capture_output=True,
            text=True,
        )

    def test_timeout_none_disabled(self) -> None:
        """timeout_seconds=None removes the timeout kwarg entirely."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                "echo {leaf} {dir}",
                "file.mkv",
                "/some/dir",
                timeout_seconds=None,
                logger=logger,
            )

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert "timeout" not in kwargs
        assert kwargs.get("shell") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True

    def test_non_zero_exit_logs_warning(self) -> None:
        """A non-zero exit code is logged as a warning and does not raise."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "something went wrong"
            _run_hook(
                "echo {leaf} {dir}",
                "file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        logger.warning.assert_called_once()
        msg = logger.warning.call_args[0][0]
        assert "exit" in msg.lower()
        assert "1" in msg
        assert "something went wrong" in msg

    def test_timeout_logs_warning(self) -> None:
        """subprocess.TimeoutExpired is caught and logged as a warning."""
        logger = MagicMock()
        with patch(
            "trimarr.hooks.subprocess.run",
            side_effect=subprocess.TimeoutExpired("echo hello", 300),
        ) as _mock_run:
            _run_hook(
                "echo {leaf} {dir}",
                "file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        logger.warning.assert_called_once()
        msg = logger.warning.call_args[0][0]
        assert "timed out" in msg.lower() or "timeout" in msg.lower()

    def test_oserror_logs_warning(self) -> None:
        """OSError during execution is caught and logged as a warning."""
        logger = MagicMock()
        with patch(
            "trimarr.hooks.subprocess.run",
            side_effect=OSError("permission denied"),
        ) as _mock_run:
            _run_hook(
                "echo {leaf} {dir}",
                "file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        logger.warning.assert_called_once()
        msg = logger.warning.call_args[0][0]
        assert "permission denied" in msg.lower()

    def test_empty_template_skips_execution(self) -> None:
        """An empty or whitespace-only template does nothing."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook("", "file.mkv", "/some/dir", timeout_seconds=300, logger=logger)

        mock_run.assert_not_called()

    def test_whitespace_only_template_skips(self) -> None:
        """Whitespace-only template also skips execution."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook("   ", "file.mkv", "/some/dir", timeout_seconds=300, logger=logger)

        mock_run.assert_not_called()
