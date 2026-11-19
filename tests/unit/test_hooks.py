"""Unit tests for trimarr.hooks."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from trimarr.hooks import _run_hook


class TestRunHook:
    """_run_hook substitutes variables and executes without a shell."""

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

        _args, kwargs = mock_run.call_args
        assert _args[0] == ["echo", "file.mkv", "/some/dir"]
        assert kwargs.get("shell") is False
        assert kwargs.get("timeout") == 300
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True

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

        _args, kwargs = mock_run.call_args
        assert _args[0] == ["echo", "hello"]
        assert kwargs.get("shell") is False
        assert kwargs.get("timeout") == 300
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True

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
        assert kwargs.get("shell") is False
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

    def test_malformed_template_logs_warning(self) -> None:
        """An unclosed quote in the template logs a warning and returns."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                "echo 'unclosed",  # unclosed single quote (no {leaf}/{dir} involved)
                "file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        mock_run.assert_not_called()
        logger.warning.assert_called_once()
        msg = logger.warning.call_args[0][0]
        assert "malformed" in msg.lower()

    def test_pipe_in_value_is_literal_not_shell_pipe(self) -> None:
        """A | in an argument value is treated as literal, not a shell pipe.

        When a user writes ``--media-shares Movies|TV`` the ``|`` is a
        separator for the script, not a shell pipe operator.  The command
        must use ``shell=False`` so shell metacharacters are never
        interpreted.
        """
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                "echo --media-shares Movies|TV",
                "file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        _args, kwargs = mock_run.call_args
        # Must NOT use shell=True — that would interpret | as a pipe
        assert kwargs.get("shell") is False, (
            f"shell must be False to avoid pipe interpretation, got shell={kwargs.get('shell')}"
        )
        # First arg must be a list (no shell string that could be split on |)
        args_list = _args[0]
        assert isinstance(args_list, list), f"Expected list of args, got {type(args_list)}"
        # The | must be preserved as a literal character in a single element
        assert "Movies|TV" in args_list, f"Expected 'Movies|TV' in args list, got {args_list}"

    def test_user_supplied_quotes_around_leaf_are_idempotent(self) -> None:
        """User-supplied single quotes around {leaf} are stripped, not doubled.

        When a user writes ``--include-folders '{leaf}'``, the extra quotes
        are stripped and the value is passed directly in the args list
        (no shell means no quoting needed).
        """
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                "echo '{leaf}'",
                "99 Homes (2014)",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        _args, kwargs = mock_run.call_args
        args_list = _args[0]
        # The leaf value is passed directly as a single arg (no shell)
        assert "99 Homes (2014)" in args_list, f"Expected leaf value in args, got {args_list}"

    def test_unbalanced_quote_is_not_rewritten(self) -> None:
        """An unbalanced quote is reported as malformed instead of silently rewritten."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            _run_hook(
                'echo "{leaf}',  # double-quote before {leaf}, no closing quote
                "some file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        mock_run.assert_not_called()
        logger.warning.assert_called_once()

    def test_balanced_quotes_inside_an_argument_are_preserved(self) -> None:
        """A quoted placeholder followed by more text keeps its argument boundaries."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            _run_hook(
                'notify --message "{leaf} unlocked" --dir "{dir}"',
                "some file.mkv",
                "/some/dir",
                timeout_seconds=300,
                logger=logger,
            )

        args_list = mock_run.call_args[0][0]
        assert args_list == [
            "notify",
            "--message",
            "some file.mkv unlocked",
            "--dir",
            "/some/dir",
        ], f"Argument boundaries changed: {args_list}"
        logger.warning.assert_not_called()

    def test_two_quoted_placeholders_keep_two_arguments(self) -> None:
        """Two quoted placeholders in one argument stay a single argument."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            _run_hook(
                'notify "{leaf} and {dir}"',
                "movie.mkv",
                "/media/movies",
                timeout_seconds=300,
                logger=logger,
            )

        args_list = mock_run.call_args[0][0]
        assert args_list == ["notify", "movie.mkv and /media/movies"], f"Argument boundaries changed: {args_list}"


class TestPlaceholderSubstitutionOrder:
    """A leaf literally named "{dir}" must not be rewritten by the second placeholder."""

    def test_leaf_that_looks_like_a_placeholder_is_not_rewritten(self) -> None:
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            _run_hook(
                "echo {leaf} {dir}",
                "{dir}",
                "/media/{dir}",
                timeout_seconds=300,
                logger=logger,
            )

        assert mock_run.call_args[0][0] == ["echo", "{dir}", "/media/{dir}"]


class TestLogSanitisation:
    """Untrusted names must not be able to forge log lines."""

    def test_control_characters_are_replaced(self) -> None:
        from trimarr.logger import sanitize_for_log

        assert sanitize_for_log("ok\nFAKE | ERROR | injected") == "ok?FAKE | ERROR | injected"
        assert sanitize_for_log("esc\x1b[31m") == "esc?[31m"

    def test_hook_error_warning_names_the_command(self) -> None:
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "boom"
            _run_hook("script {leaf}", "movie.mkv", "/media", timeout_seconds=300, logger=logger)

        message = logger.warning.call_args[0][0]
        assert "script movie.mkv" in message
        assert "boom" in message
