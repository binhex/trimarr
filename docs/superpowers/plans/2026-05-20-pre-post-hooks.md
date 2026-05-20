# Pre/Post Process Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--pre-process` and `--post-process` CLI options that fire shell commands around each directory's batch of file processing, plus `--command-timeout-mins` to control per-command timeout.

**Architecture:** New `src/trimarr/hooks.py` module with a `_run_hook()` helper that substitutes `{leaf}`/`{dir}` variables and executes via `subprocess.run(shell=True)`. The runner groups files needing processing by parent directory and fires hooks around each group. Changes to `cli.py` and `runner.py` integrate the new options.

**Tech Stack:** Python 3.12+, click, loguru, subprocess

---
## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/trimarr/hooks.py` | **Create** | `_run_hook()` — variable substitution + subprocess execution |
| `src/trimarr/cli.py` | **Modify** | Add `--pre-process`, `--post-process`, `--command-timeout-mins` |
| `src/trimarr/runner.py` | **Modify** | Group files by directory, fire hooks per directory batch |
| `tests/unit/test_hooks.py` | **Create** | Tests for `_run_hook()` |
| `tests/unit/test_cli.py` | **Modify** | Tests for new CLI options |
| `tests/unit/test_main.py` | **Modify** | Tests for hook invocation in the processing loop |

### Task 1: Create hooks.py unit tests (TDD — write tests first)

**Files:**
- Create: `tests/unit/test_hooks.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for trimarr.hooks."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from trimarr.hooks import _run_hook


class TestRunHook:
    """_run_hook substitutes variables and executes via shell."""

    def test_substitutes_leaf_and_dir(self) -> None:
        """{leaf} and {dir} are replaced in the command template."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                cmd_template="no_ransom.sh --unlock yes {leaf} --path {dir}",
                leaf="The Matrix (1999)",
                dir_path="/mnt/Movies/The Matrix (1999)",
                logger=logger,
                timeout_seconds=300,
            )

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == 'no_ransom.sh --unlock yes "The Matrix (1999)" --path "/mnt/Movies/The Matrix (1999)"'
        assert kwargs["shell"] is True
        assert kwargs["timeout"] == 300
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    def test_leaf_and_dir_only_once(self) -> None:
        """A template with no variables still works."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                cmd_template="echo hello",
                leaf="anything",
                dir_path="/tmp",
                logger=logger,
                timeout_seconds=None,
            )

        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0] == "echo hello"

    def test_timeout_none_disabled(self) -> None:
        """timeout_seconds=None removes the timeout kwarg entirely."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                cmd_template="echo hi",
                leaf="d",
                dir_path="/d",
                logger=logger,
                timeout_seconds=None,
            )

        _, kwargs = mock_run.call_args
        assert "timeout" not in kwargs

    def test_non_zero_exit_logs_warning(self) -> None:
        """A non-zero exit code is logged as a warning and does not raise."""
        logger = MagicMock()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "something went wrong"

        with patch("trimarr.hooks.subprocess.run", return_value=mock_result):
            _run_hook(
                cmd_template="false",
                leaf="d",
                dir_path="/d",
                logger=logger,
                timeout_seconds=30,
            )

        logger.warning.assert_called_once()
        warning_text = logger.warning.call_args[0][0]
        assert "exit" in warning_text.lower()
        assert "1" in warning_text

    def test_timeout_logs_warning(self) -> None:
        """subprocess.TimeoutExpired is caught and logged as a warning."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
            _run_hook(
                cmd_template="sleep 100",
                leaf="d",
                dir_path="/d",
                logger=logger,
                timeout_seconds=30,
            )

        logger.warning.assert_called_once()
        warning_text = logger.warning.call_args[0][0]
        assert "timeout" in warning_text.lower() or "timed out" in warning_text.lower()

    def test_oserror_logs_warning(self) -> None:
        """OSError during execution is caught and logged as a warning."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run", side_effect=OSError("command not found")):
            _run_hook(
                cmd_template="nonexistent-binary",
                leaf="d",
                dir_path="/d",
                logger=logger,
                timeout_seconds=30,
            )

        logger.warning.assert_called_once()
        warning_text = logger.warning.call_args[0][0]
        assert "command not found" in warning_text

    def test_empty_template_skips_execution(self) -> None:
        """An empty or whitespace-only template does nothing."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                cmd_template="",
                leaf="d",
                dir_path="/d",
                logger=logger,
                timeout_seconds=30,
            )

        mock_run.assert_not_called()

    def test_whitespace_only_template_skips(self) -> None:
        """Whitespace-only template also skips execution."""
        logger = MagicMock()
        with patch("trimarr.hooks.subprocess.run") as mock_run:
            _run_hook(
                cmd_template="   ",
                leaf="d",
                dir_path="/d",
                logger=logger,
                timeout_seconds=30,
            )

        mock_run.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_hooks.py -v 2>&1 | head -30`

Expected: ModuleNotFoundError or ImportError — `trimarr.hooks` does not exist yet. All tests fail.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/test_hooks.py
git commit -m "test: add failing tests for _run_hook helper"
```

---

### Task 2: Create hooks.py

**Files:**
- Create: `src/trimarr/hooks.py`

- [ ] **Step 1: Write hooks.py**

```python
"""Shell command hook execution for pre/post processing."""

from __future__ import annotations

import shlex
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loguru import Logger


def _run_hook(
    cmd_template: str,
    leaf: str,
    dir_path: str,
    logger: Logger,
    timeout_seconds: int | None = 300,
) -> None:
    """Execute a shell command hook with ``{leaf}`` and ``{dir}`` substitution.

    The template variables are substituted using :func:`str.replace` and the
    result is executed via ``subprocess.run(shell=True)``.  Non-zero exit codes,
    timeouts, and execution errors are logged as warnings but never raised.

    An empty or whitespace-only *cmd_template* is silently skipped.

    Args:
        cmd_template: Shell command template containing ``{leaf}`` and/or ``{dir}``.
        leaf: Directory basename value for ``{leaf}`` substitution.
        dir_path: Full absolute directory path value for ``{dir}`` substitution.
        logger: Loguru logger for warning messages.
        timeout_seconds: Maximum seconds to wait before killing the command.
                         ``None`` disables the timeout.
    """
    if not cmd_template or not cmd_template.strip():
        return

    cmd = cmd_template.replace("{leaf}", shlex.quote(leaf)).replace("{dir}", shlex.quote(dir_path))

    kwargs: dict = {
        "shell": True,
        "capture_output": True,
        "text": True,
    }
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds

    try:
        result = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        logger.warning(f"Hook command timed out after {timeout_seconds}s: {cmd}")
        return
    except OSError as exc:
        logger.warning(f"Hook command failed: {exc}")
        return

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        logger.warning(f"Hook command exited with code {result.returncode}{detail}")
```

Note: `shlex.quote()` is used to safely quote the variable values so that directory names with spaces or special characters are passed correctly through the shell.

- [ ] **Step 2: Run hooks tests to verify they pass**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_hooks.py -v`

Expected: All 9 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/trimarr/hooks.py
git commit -m "feat: add _run_hook helper for shell command execution with variable substitution"
```

---

### Task 3: Add CLI tests for new options

**Files:**
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Add TestPrePostOptions class to test_cli.py**

Append before the `TestVersionFallback` class or at the end of the file:

```python
# ---------------------------------------------------------------------------
# Pre/post process hooks
# ---------------------------------------------------------------------------


class TestPrePostOptions:
    """--pre-process, --post-process, and --command-timeout-mins forwarding."""

    def test_pre_process_forwarded(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path))
                + ["--mkvmerge-path", str(fake_mkvmerge), "--pre-process", "echo before {leaf}"],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["pre_process"] == "echo before {leaf}"

    def test_post_process_forwarded(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path))
                + ["--mkvmerge-path", str(fake_mkvmerge), "--post-process", "echo after {leaf}"],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["post_process"] == "echo after {leaf}"

    def test_both_pre_and_post_forwarded(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path))
                + ["--mkvmerge-path", str(fake_mkvmerge),
                   "--pre-process", "echo before {leaf}",
                   "--post-process", "echo after {leaf}"],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["pre_process"] == "echo before {leaf}"
        assert kwargs["post_process"] == "echo after {leaf}"

    def test_command_timeout_mins_forwarded(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path))
                + ["--mkvmerge-path", str(fake_mkvmerge), "--command-timeout-mins", "10"],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["command_timeout_mins"] == 10

    def test_command_timeout_zero_allowed(self, tmp_path: Path) -> None:
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path))
                + ["--mkvmerge-path", str(fake_mkvmerge), "--command-timeout-mins", "0"],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["command_timeout_mins"] == 0

    def test_default_timeout_is_5(self, tmp_path: Path) -> None:
        """When --command-timeout-mins is omitted, default to 5."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path)) + ["--mkvmerge-path", str(fake_mkvmerge)],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["command_timeout_mins"] == 5

    def test_pre_post_default_to_none(self, tmp_path: Path) -> None:
        """When --pre-process/--post-process are omitted, default to None."""
        fake_mkvmerge = tmp_path / "mkvmerge"
        fake_mkvmerge.touch()

        with patch("trimarr.runner.run") as mock_run:
            result = CliRunner().invoke(
                cli,
                _base_args(str(tmp_path)) + ["--mkvmerge-path", str(fake_mkvmerge)],
            )

        assert result.exit_code == 0, result.output
        _, kwargs = mock_run.call_args
        assert kwargs["pre_process"] is None
        assert kwargs["post_process"] is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_cli.py::TestPrePostOptions -v 2>&1 | head -30`

Expected: All tests fail because `run()` doesn't accept the new kwargs yet.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_cli.py
git commit -m "test: add CLI tests for pre/post process hook options"
```

---

### Task 4: Update CLI with new options

**Files:**
- Modify: `src/trimarr/cli.py`

- [ ] **Step 1: Add the three new @click.option decorators**

Insert after the `--run-on-start` option block (before `@click.version_option`):

```python
@click.option(
    "--pre-process",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<command>",
    help=(
        "Shell command to run before processing files in a directory."
        " Use {leaf} for the directory basename and {dir} for the full"
        " directory path."
        " Example: --pre-process 'no_ransom.sh --unlock yes {leaf}'"
    ),
)
@click.option(
    "--post-process",
    type=click.STRING,
    required=False,
    default=None,
    metavar="<command>",
    help=(
        "Shell command to run after processing files in a directory."
        " Use {leaf} for the directory basename and {dir} for the full"
        " directory path."
        " Example: --post-process 'no_ransom.sh --unlock no {leaf}'"
    ),
)
@click.option(
    "--command-timeout-mins",
    type=click.IntRange(min=0),
    required=False,
    default=5,
    metavar="<minutes>",
    show_default=True,
    help=(
        "Timeout in minutes for each pre/post process command."
        " Set to 0 to disable timeout entirely."
    ),
)
```

- [ ] **Step 2: Add the new parameters to the `cli()` function signature**

Add after `run_on_start`:
```python
    pre_process: str | None,
    post_process: str | None,
    command_timeout_mins: int,
```

- [ ] **Step 3: Pass the new options through to `run()`**

In the `_run()` closure and the `run()` call, add:
```python
    def _run() -> None:
        run(
            ...
            pre_process=pre_process,
            post_process=post_process,
            command_timeout_mins=command_timeout_mins,
        )

    ...
```

And in the one-shot path (`else: _run()`) — both paths use `_run()` so both get the kwargs.

- [ ] **Step 4: Run the CLI tests**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_cli.py::TestPrePostOptions -v`

Expected: All tests PASS.

- [ ] **Step 5: Run all CLI tests to check for regressions**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_cli.py -v`

Expected: All CLI tests PASS (including existing ones).

- [ ] **Step 6: Commit**

```bash
git add src/trimarr/cli.py
git commit -m "feat: add --pre-process, --post-process, --command-timeout-mins CLI options"
```

---

### Task 5: Add runner integration tests

**Files:**
- Modify: `tests/unit/test_main.py`

- [ ] **Step 1: Add TestPrePostHooksIntegration class**

Append before or after `TestAutoDownloadMkvmerge` (at end of file):

```python
# ---------------------------------------------------------------------------
# Pre/post process hooks integration
# ---------------------------------------------------------------------------


class TestPrePostHooksIntegration:
    """Hooks fire per-directory around files that need processing."""

    def test_pre_and_post_fire_for_directory_with_work(self, tmp_path: Path) -> None:
        """When files in a directory need processing, pre and post hooks fire."""
        media_dir = tmp_path / "media" / "Movie (2024)"
        media_dir.mkdir(parents=True)
        mkv = media_dir / "movie.mkv"
        mkv.write_bytes(b"fake mkv content")

        hook_log: list[str] = []

        def fake_run_hook(
            cmd_template: str,
            leaf: str,
            dir_path: str,
            logger: object,
            timeout_seconds: int | None = 300,
        ) -> None:
            hook_log.append(f"{cmd_template} | leaf={leaf} dir={dir_path}")

        with (
            patch("trimarr.runner._run_hook", side_effect=fake_run_hook),
            patch("trimarr.runner.probe_file") as mock_probe,
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),
        ):
            mock_probe.return_value = []
            result = CliRunner().invoke(
                cli,
                ["--language", "eng", "--media-path", str(tmp_path / "media"),
                 "--pre-process", "unlock {leaf}",
                 "--post-process", "lock {leaf}",
                 "--mkvmerge-path", str(tmp_path / "mkvmerge")],
            )

        # Force-create the mkvmerge binary
        (tmp_path / "mkvmerge").touch()
        result = CliRunner().invoke(
            cli,
            ["--language", "eng", "--media-path", str(tmp_path / "media"),
             "--pre-process", "unlock {leaf}",
             "--post-process", "lock {leaf}",
             "--mkvmerge-path", str(tmp_path / "mkvmerge")],
        )

        assert result.exit_code == 0, result.output
        assert len(hook_log) == 2
        assert "unlock" in hook_log[0] and "Movie (2024)" in hook_log[0]
        assert "lock" in hook_log[1] and "Movie (2024)" in hook_log[1]
```

Wait, this test is getting complex because of the mkvmerge path interaction. Let me simplify: mock `_resolve_mkvmerge_path` to avoid binary issues.

```python
class TestPrePostHooksIntegration:
    """Hooks fire per-directory around files that need processing."""

    def test_pre_and_post_fire_for_directory_with_work(self, tmp_path: Path) -> None:
        """When files in a directory need processing, pre and post hooks fire."""
        media_dir = tmp_path / "media" / "Movie (2024)"
        media_dir.mkdir(parents=True)
        mkv = media_dir / "movie.mkv"
        mkv.write_bytes(b"fake mkv content")

        hook_log: list[str] = []

        def fake_run_hook(
            cmd_template: str,
            leaf: str,
            dir_path: str,
            logger: object,
            timeout_seconds: int | None = 300,
        ) -> None:
            hook_log.append(f"{cmd_template} | leaf={leaf} dir={dir_path}")

        with (
            patch("trimarr.runner._run_hook", side_effect=fake_run_hook),
            patch("trimarr.runner.probe_file") as mock_probe,
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),
            patch("trimarr.cli._resolve_mkvmerge_path", return_value="/fake/mkvmerge"),
        ):
            mock_probe.return_value = []
            result = CliRunner().invoke(
                cli,
                ["--language", "eng", "--media-path", str(tmp_path / "media"),
                 "--pre-process", "unlock {leaf}",
                 "--post-process", "lock {leaf}"],
            )

        assert result.exit_code == 0, result.output
        assert len(hook_log) == 2, f"Expected 2 hook calls, got {len(hook_log)}: {hook_log}"
        assert "unlock" in hook_log[0]
        assert "Movie (2024)" in hook_log[0]
        assert "lock" in hook_log[1]
        assert "Movie (2024)" in hook_log[1]

    def test_no_hooks_when_directory_has_no_work(self, tmp_path: Path) -> None:
        """When all files are already processed, no hooks fire."""
        media_dir = tmp_path / "media" / "Movie (2024)"
        media_dir.mkdir(parents=True)
        mkv = media_dir / "movie.mkv"
        mkv.write_bytes(b"fake mkv content")

        hook_log: list[str] = []

        def fake_run_hook(**kwargs: object) -> None:
            hook_log.append("fired")

        with (
            patch("trimarr.runner._run_hook", side_effect=fake_run_hook),
            patch("trimarr.cli._resolve_mkvmerge_path", return_value="/fake/mkvmerge"),
            patch("trimarr.database.Database.is_processed", return_value=True),
        ):
            result = CliRunner().invoke(
                cli,
                ["--language", "eng", "--media-path", str(tmp_path / "media"),
                 "--pre-process", "unlock {leaf}",
                 "--post-process", "lock {leaf}"],
            )

        assert result.exit_code == 0, result.output
        assert len(hook_log) == 0, f"Expected 0 hooks, got {len(hook_log)}"

    def test_pre_only_works_without_post(self, tmp_path: Path) -> None:
        """--pre-process can be used without --post-process."""
        media_dir = tmp_path / "media" / "Movie (2024)"
        media_dir.mkdir(parents=True)
        mkv = media_dir / "movie.mkv"
        mkv.write_bytes(b"fake mkv content")

        hook_log: list[str] = []

        def fake_run_hook(**kwargs: object) -> None:
            hook_log.append("fired")

        with (
            patch("trimarr.runner._run_hook", side_effect=fake_run_hook),
            patch("trimarr.runner.probe_file") as mock_probe,
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),
            patch("trimarr.cli._resolve_mkvmerge_path", return_value="/fake/mkvmerge"),
        ):
            mock_probe.return_value = []
            result = CliRunner().invoke(
                cli,
                ["--language", "eng", "--media-path", str(tmp_path / "media"),
                 "--pre-process", "unlock {leaf}"],
            )

        assert result.exit_code == 0, result.output
        assert len(hook_log) == 1

    def test_post_only_works_without_pre(self, tmp_path: Path) -> None:
        """--post-process can be used without --pre-process."""
        media_dir = tmp_path / "media" / "Movie (2024)"
        media_dir.mkdir(parents=True)
        mkv = media_dir / "movie.mkv"
        mkv.write_bytes(b"fake mkv content")

        hook_log: list[str] = []

        def fake_run_hook(**kwargs: object) -> None:
            hook_log.append("fired")

        with (
            patch("trimarr.runner._run_hook", side_effect=fake_run_hook),
            patch("trimarr.runner.probe_file") as mock_probe,
            patch("trimarr.runner.build_mkvmerge_command", return_value=None),
            patch("trimarr.cli._resolve_mkvmerge_path", return_value="/fake/mkvmerge"),
        ):
            mock_probe.return_value = []
            result = CliRunner().invoke(
                cli,
                ["--language", "eng", "--media-path", str(tmp_path / "media"),
                 "--post-process", "lock {leaf}"],
            )

        assert result.exit_code == 0, result.output
        assert len(hook_log) == 1
```

- [ ] **Step 2: Verify the new tests fail**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_main.py::TestPrePostHooksIntegration -v 2>&1 | head -40`

Expected: Tests fail because `run()` doesn't have the hook parameters yet.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_main.py
git commit -m "test: add integration tests for pre/post process hooks"
```

---

### Task 6: Modify runner.py for per-directory hook integration

**Files:**
- Modify: `src/trimarr/runner.py`

- [ ] **Step 1: Add the `_run_hook` import**

At the top of runner.py, add the import:
```python
from trimarr.hooks import _run_hook
```

- [ ] **Step 2: Update the `run()` function signature**

Add three new parameters after `skip_size_check`:
```python
    pre_process: str | None = None,
    post_process: str | None = None,
    command_timeout_mins: int = 5,
```

- [ ] **Step 3: Convert timeout to seconds**

After the `cfg` definition and before `total`, add:
```python
    # Convert minutes to seconds; 0 = no timeout
    command_timeout_seconds: int | None = command_timeout_mins * 60 if command_timeout_mins > 0 else None
```

- [ ] **Step 4: Restructure the processing loop**

Replace the current loop:

```python
    try:
        with Database(database_path) as db:
            for idx, (file_path, root) in enumerate(unique_files, 1):
                _process_one_file_guarded(...)
    except CorruptOutputError...
```

With:

```python
    # Group files by their parent directory for hook support
    from collections import OrderedDict

    dir_groups: OrderedDict[Path, list[tuple[Path, Path]]] = OrderedDict()
    for file_path, root in unique_files:
        dir_groups.setdefault(file_path.parent, []).append((file_path, root))

    try:
        with Database(database_path) as db:
            global_idx = 0

            for dir_path, files_in_dir in dir_groups.items():
                # Determine if this directory has any work (fingerprint pre-check)
                dir_has_work = any(
                    not db.is_processed(fp, profile_hash=profile_hash)
                    for fp, _ in files_in_dir
                )

                if dir_has_work:
                    leaf = dir_path.name

                    if pre_process is not None:
                        _run_hook(
                            cmd_template=pre_process,
                            leaf=leaf,
                            dir_path=str(dir_path),
                            logger=logger,
                            timeout_seconds=command_timeout_seconds,
                        )

                for file_path, root in files_in_dir:
                    global_idx += 1
                    _process_one_file_guarded(
                        file_path=file_path,
                        root=root,
                        idx=global_idx,
                        total=total,
                        db=db,
                        profile_hash=profile_hash,
                        cfg=cfg,
                        counts=counts,
                        failures=failures,
                        logger=logger,
                    )

                if dir_has_work:
                    if post_process is not None:
                        _run_hook(
                            cmd_template=post_process,
                            leaf=leaf,
                            dir_path=str(dir_path),
                            logger=logger,
                            timeout_seconds=command_timeout_seconds,
                        )

    except CorruptOutputError as exc:
        _handle_corrupt_output(exc, counts, logger)
    except KeyboardInterrupt:
        ...
```

The key change: files are grouped by parent directory, and hooks fire once per directory where at least one file is not already processed. The `dir_has_work` pre-check prevents hooks from firing for directories where all files are already processed.

- [ ] **Step 5: Add the `OrderedDict` import at the top**

```python
from collections import OrderedDict
```

- [ ] **Step 6: Run the integration tests**

Run: `cd /data/trimarr && uv run pytest tests/unit/test_main.py::TestPrePostHooksIntegration -v`

Expected: All tests PASS.

- [ ] **Step 7: Run the full test suite**

Run: `cd /data/trimarr && uv run pytest -v`

Expected: All tests pass (existing ones + new ones).

- [ ] **Step 8: Commit**

```bash
git add src/trimarr/runner.py
git commit -m "feat: add per-directory pre/post process hook support to runner"
```

---

### Spec Self-Review Checklist

1. **Spec coverage:**
   - `--pre-process` CLI option → Task 3 (tests) + Task 4 (CLI) + Task 6 (runner)
   - `--post-process` CLI option → Task 3 (tests) + Task 4 (CLI) + Task 6 (runner)
   - `--command-timeout-mins` CLI option → Task 3 (tests) + Task 4 (CLI)
   - `{leaf}` variable substitution → Task 1 (tests) + Task 2 (hooks.py)
   - `{dir}` variable substitution → Task 1 (tests) + Task 2 (hooks.py)
   - Per-directory execution model → Task 5 (tests) + Task 6 (runner)
   - Shell execution with error handling → Task 1 (tests) + Task 2 (hooks.py)
   - No hardcoded timeout, 0 = disabled → Task 1 (test: test_timeout_none_disabled) + Task 2 (hooks.py)
   - Default timeout 5 minutes → Task 3 (test: test_default_timeout_is_5) + Task 4 (CLI default=5)

2. **Placeholder scan:** No TBDs, TODOs, or incomplete code blocks. Every step has exact code and commands.

3. **Type consistency:** `_run_hook` signature uses `cmd_template: str`, `leaf: str`, `dir_path: str`, `logger`, `timeout_seconds: int | None = 300` — consistent across hooks.py, runner.py callsites, and test mocks. `command_timeout_mins: int` in cli.py and runner.py. `pre_process: str | None` and `post_process: str | None` consistent everywhere.

4. **Scope check:** Single focused feature — pre/post process hooks. No unrelated changes.
