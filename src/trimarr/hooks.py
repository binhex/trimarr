"""Shell command hook execution for pre/post processing."""

from __future__ import annotations

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
    """Execute a shell command with ``{leaf}`` and ``{dir}`` variable substitution.

    Args:
        cmd_template: The command template, which may contain ``{leaf}`` and
            ``{dir}`` placeholders.
        leaf: The file or directory name to substitute into ``{leaf}``.
        dir_path: The directory path to substitute into ``{dir}``.
        logger: A ``loguru.Logger`` instance used for warning messages.
        timeout_seconds: Maximum execution time in seconds. Passed as the
            ``timeout`` kwarg to :func:`subprocess.run`. If ``None``, no
            timeout is applied.

    Raises:
        None. All expected errors are caught and logged as warnings.
    """
    if not cmd_template.strip():
        return

    cmd = cmd_template.replace("{leaf}", f"'{leaf}'").replace("{dir}", f"'{dir_path}'")

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
        stderr = result.stderr.strip() if result.stderr else ""
        logger.warning(f"Hook command exited with code {result.returncode}: {stderr}")
